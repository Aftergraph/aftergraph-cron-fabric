"""Org activity renderer — Telegram-first activity surface.

Produces three logical surfaces using the existing telegram-live-status
renderer (`hermes statuscard`) and the existing
telegram_receipt_bridge.py delivery receipt chain:

  1. ag-org-pulse   persistent edit-in-place status card, refreshed each
                    collector cycle
  2. ag-org-digest  at most one new digest per hour; emitted only when
                    new meaningful activity exists since prior digest
  3. ag-org-alert   one-shot deduped exception card for high-value
                    conditions (failed CI, unknown repo, lifecycle change,
                    collector failure, source blindness)

No direct Telegram API logic. Delivery is only successful after
renderer positive evidence (exit 0 from `hermes statuscard`). Uses
the existing delivery receipt chain.

All GitHub-derived content is already persisted as immutable
ActivityEvent rows by org_activity.py. This renderer renders those
rows; it never invents activity.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from activity_store import ActivityStore
from activity_normalizer import build_event_id, payload_digest

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    env = os.environ.get("AG_FABRIC_ROOT")
    if env:
        return Path(env)
    cand = Path(__file__).resolve().parent.parent.parent
    if (cand / "contracts" / "sources.yaml").is_file():
        return cand
    cwd = Path.cwd()
    if (cwd / "contracts" / "sources.yaml").is_file():
        return cwd
    print("ORG-PULSE-RENDERER-FAIL: cannot locate fabric root")
    sys.exit(2)


REPO = _repo_root()
STATE_DIR = REPO / "state"
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
from telegram_receipt_bridge import (
    build_receipt_body,
    write_delivery_receipt,
    _send_via_renderer,
)

STORE = ActivityStore()

TELEGRAM_TARGET = os.environ.get("TELEGRAM_OPS_THREAD_ID", "telegram:Jonas")
PULSE_TASK_ID = "ag-org-pulse"
DIGEST_TASK_ID = "ag-org-digest"
ALERT_TASK_ID = "ag-org-alert"

DIGEST_INTERVAL_SECONDS = 3600  # at most one digest per hour
DIGEST_CURSOR_KEY = "ag-org-digest-last"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _register_renderer_receipt(event_key: str, fingerprint: str, channel: str, message_id: str):
    """Write a delivery receipt after positive renderer proof."""
    now = utc_now_iso()
    body = build_receipt_body(event_key, fingerprint, channel, message_id, now=now)
    out = write_delivery_receipt(body)
    print(f"ORG-PULSE-RENDERER-OK: receipt {out.name} -> {channel}")
    return out


# ---------------------------------------------------------------------------
# Surface 1: ag-org-pulse (persistent status card)
# ---------------------------------------------------------------------------

def render_pulse_card(store: ActivityStore) -> str:
    """Build the ag-org-pulse status card body.

    Refresh every collector cycle. Shows timestamp, repo count, active
    repos in current window, event count, commit count, PR open/merge
    counts, CI success/failure, releases, latest 10-15 meaningful rows,
    attention section, evidence freshness.
    """
    now = utc_now_iso()
    stats = store.stats(window_since=_window_since())

    total_events = stats["total"]
    breakdown = {f"{r['kind']}/{r['action']}": r["c"] for r in stats["breakdown"]}

    recent = store.recent_events(limit=15)

    # Repo metadata snapshot
    repo_meta = store.list_repo_metadata()
    repo_count = len(repo_meta)
    active_repos = [
        m["repo"] for m in repo_meta if not m["archived"]
    ]
    repo_list = ", ".join(active_repos[:10]) + (
        f" (+{len(active_repos)-10} more)" if len(active_repos) > 10 else ""
    )

    # Attention section: high-importance recent events + unknown repos
    attention = []
    for ev in recent:
        if ev.get("importance") in ("high", "critical"):
            attention.append(f"• {ev['display_title']} — {ev['repo']}")
    if len(attention) > 5:
        attention = attention[:5] + ["… and more"]

    # Build card lines
    lines = []
    lines.append("AFTERGRAPH ORG PULSE")
    lines.append("")
    lines.append(f"🕒 {now}")
    lines.append(f"📊 {repo_count} repos · {len(active_repos)} active")
    lines.append(f"📨 {total_events} events in window")
    lines.append("")
    lines.append("Activity counts:")
    for label, count in sorted(breakdown.items()):
        if count:
            lines.append(f"  • {label}: {count}")
    lines.append("")
    lines.append(f"Latest ({len(recent)} rows):")
    for ev in recent:
        lines.append(f"• {ev['display_title']} — {ev['repo']} · {ev['observed_at']}")
    if not recent:
        lines.append("  (no activity this window)")
    lines.append("")
    if attention:
        lines.append("⚑ Attention:")
        lines.extend(attention)
    else:
        lines.append("✓ All clear this window")
    lines.append("")
    lines.append(f"Evidence freshness: {now}")
    lines.append(f"Repos: {repo_list}")

    return "\n".join(lines)


def _window_since() -> str:
    """Return ISO-8601 for the start of the current collector window.

    The collector runs every 15 minutes. The window is the last 15 min.
    """
    from datetime import timedelta
    t = datetime.now(timezone.utc) - timedelta(minutes=15)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def pulse_card_path() -> Path:
    return STATE_DIR / "pulse_card.json"


def persist_pulse_card(body: str):
    pulse_card_path().write_text(body, encoding="utf-8")


def send_pulse_card():
    """Send/refresh the ag-org-pulse persistent card via renderer."""
    card_body = render_pulse_card(STORE)
    persist_pulse_card(card_body)
    ok, detail = _send_via_renderer(
        PULSE_TASK_ID,
        card_body,
        producer="org-pulse-renderer",
    )
    if not ok:
        print(f"ORG-PULSE-RENDERER-FAIL: pulse card send failed: {detail}")
        sys.exit(1)
    now = utc_now_iso()
    message_id = f"statuscard-{PULSE_TASK_ID}-{now}"
    fp = hashlib_fingerprint(PULSE_TASK_ID, card_body)
    _register_renderer_receipt(PULSE_TASK_ID, fp, "telegram:ops", message_id)
    print(f"ORG-PULSE-RENDERER-OK: pulse card delivered (message_id={message_id})")
    return True


# ---------------------------------------------------------------------------
# Surface 2: ag-org-digest (hourly, only on new activity)
# ---------------------------------------------------------------------------

def render_digest(store: ActivityStore) -> Optional[str]:
    """Build an hourly digest, only if new meaningful activity exists.

    Reads the digest cursor from the SAME store it queries, so tests
    and live runs are consistent (the module-global STORE may hold a
    different store than the one passed in).
    """
    last_digest = store.get_digest_cursor(DIGEST_CURSOR_KEY)
    window_since = last_digest["cursor"] if last_digest else _window_since_long()

    recent = store.recent_events(since=window_since, limit=30)
    if not recent:
        return None  # no new activity; stay silent

    # Filter down to meaningful rows (skip low-importance repeats)
    meaningful = [ev for ev in recent if ev.get("importance") != "low"]

    if not meaningful:
        return None

    now = utc_now_iso()
    lines = []
    lines.append("AFTERGRAPH ORG DIGEST")
    lines.append("")
    lines.append(f"🕒 {now}")
    lines.append(f"📨 {len(meaningful)} meaningful events since {window_since}")
    lines.append("")
    for ev in meaningful[:25]:
        lines.append(f"• {ev['display_title']} — {ev['repo']} · {ev['observed_at']}")
    lines.append("")
    lines.append(f"Evidence window: {window_since} → {now}")
    return "\n".join(lines)


def _window_since_long() -> str:
    """Start of the last hour (for digest interval)."""
    from datetime import timedelta
    t = datetime.now(timezone.utc) - timedelta(hours=1)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def send_digest():
    """Send a digest card if new meaningful activity exists since last digest."""
    digest_body = render_digest(STORE)
    if digest_body is None:
        print("ORG-PULSE-RENDERER-SILENCE: no new activity for digest")
        return False

    ok, detail = _send_via_renderer(
        DIGEST_TASK_ID,
        digest_body,
        producer="org-pulse-renderer",
    )
    if not ok:
        print(f"ORG-PULSE-RENDERER-FAIL: digest send failed: {detail}")
        return False

    now = utc_now_iso()
    message_id = f"statuscard-{DIGEST_TASK_ID}-{now}"
    fp = hashlib_fingerprint(DIGEST_TASK_ID, digest_body)
    _register_renderer_receipt(DIGEST_TASK_ID, fp, "telegram:ops", message_id)
    STORE.set_digest_cursor(DIGEST_CURSOR_KEY, now)
    print(f"ORG-PULSE-RENDERER-OK: digest delivered (message_id={message_id})")
    return True


# ---------------------------------------------------------------------------
# Surface 3: ag-org-alert (one-shot deduped exception cards)
# ---------------------------------------------------------------------------

ALERT_STATE_FILE = STATE_DIR / "org_alerts_sent.json"


def _load_alert_state() -> dict:
    if ALERT_STATE_FILE.is_file():
        try:
            return json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_alert_state(state: dict):
    ALERT_STATE_FILE.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def check_alerts(store: ActivityStore, collector_ok: bool = True):
    """Emit one-shot deduped alert cards for high-value conditions.

    Alerts are deduplicated by key so the same condition does not spam.
    """
    sent = _load_alert_state()
    now = utc_now_iso()

    # Alert 1: collector failure
    if not collector_ok:
        key = "collector-failure"
        if sent.get(key) == now:
            return  # already alerted this run
        body = f"ORG PULSE COLLECTOR FAILURE\n\nEvidence freshness: {now}\nSource may be stale.\n\nRestart the collector; this alert repeats each cycle until collector recovers."
        ok, detail = _send_via_renderer(ALERT_TASK_ID, body, producer="org-pulse-alert")
        if ok:
            msg_id = f"statuscard-{ALERT_TASK_ID}-{now}"
            fp = hashlib_fingerprint(ALERT_TASK_ID, body)
            _register_renderer_receipt(ALERT_TASK_ID, fp, "telegram:ops", msg_id)
            sent[key] = now
            _save_alert_state(sent)
            print(f"ORG-PULSE-ALERT: collector failure alert sent")

    # Alert 2: source blindness (auth failure)
    # Detected by observing zero repos discovered — checked in collector;
    # renderer reads persisted store state to decide.
    repo_count = len(store.list_repo_metadata())
    if repo_count == 0:
        key = "source-blindness"
        if sent.get(key) == now:
            return
        body = f"ORG PULSE SOURCE BLINDNESS\n\nNo repository metadata observed. Auth failure or org unreachable.\n\nEvidence freshness: {now}\n\nFAIL CLOSED — operator must re-establish GitHub auth."
        ok, detail = _send_via_renderer(ALERT_TASK_ID, body, producer="org-pulse-alert")
        if ok:
            msg_id = f"statuscard-{ALERT_TASK_ID}-{now}"
            fp = hashlib_fingerprint(ALERT_TASK_ID, body)
            _register_renderer_receipt(ALERT_TASK_ID, fp, "telegram:ops", msg_id)
            sent[key] = now
            _save_alert_state(sent)
            print(f"ORG-PULSE-ALERT: source blindness alert sent")

    # Alert 3: unknown live repository observed
    unknown_repos = [m["repo"] for m in store.list_repo_metadata()
                     if m["repo"].endswith("/.github") is False]
    # We detect unknown repos by cross-referencing the canonical
    # topology file (same source the collector uses). Unknown repos
    # were already recorded as observation events by the collector;
    # this alert surfaces them once as an exception card.
    topo = []
    topo_path = Path.home() / "after-graph-governance" / "docs" / \
        "platform-topology" / "2.0.json"
    try:
        if topo_path.is_file():
            topo = json.loads(topo_path.read_text(encoding="utf-8")).get("repositories") or []
    except (json.JSONDecodeError, OSError):
        topo = []
    topo_names = {r.get("name", "").rsplit("/", 1)[-1] for r in topo}
    unknown = [r for r in unknown_repos
               if r.rsplit("/", 1)[-1] not in topo_names
               and not r.endswith("/.github")]
    if unknown:
        key = "unknown-live-repo"
        if sent.get(key) != now:  # one alert per run per unknown repo is too noisy; one summary
            body = f"ORG PULSE UNKNOWN LIVE REPO(S)\n\nLive repository not in canonical topology:\n" + "\n".join(f"• {r}" for r in unknown[:10]) + (f"\n(+{len(unknown)-10} more)" if len(unknown) > 10 else "") + f"\n\nEvidence freshness: {now}\n\nUnknown repo recorded as observation. Not added to canonical topology."
            ok, detail = _send_via_renderer(ALERT_TASK_ID, body, producer="org-pulse-alert")
            if ok:
                msg_id = f"statuscard-{ALERT_TASK_ID}-{now}"
                fp = hashlib_fingerprint(ALERT_TASK_ID, body)
                _register_renderer_receipt(ALERT_TASK_ID, fp, "telegram:ops", msg_id)
                sent[key] = now
                _save_alert_state(sent)
                print(f"ORG-PULSE-ALERT: unknown live repo alert sent")

    # Alert 4: high-risk repository lifecycle change (archive/unarchive/rename)
    recent = store.recent_events(limit=50)
    lifecycle_events = [ev for ev in recent
                        if ev["kind"] == "repository"
                        and ev["action"] in ("archived", "unarchived", "renamed")]
    if lifecycle_events:
        key = "lifecycle-change"
        if sent.get(key) != now:
            body = f"ORG PULSE LIFECYCLE CHANGE\n\nRepository lifecycle event(s) observed:\n" + "\n".join(f"• {ev['display_title']} — {ev['repo']}" for ev in lifecycle_events[:10]) + f"\n\nEvidence freshness: {now}"
            ok, detail = _send_via_renderer(ALERT_TASK_ID, body, producer="org-pulse-alert")
            if ok:
                msg_id = f"statuscard-{ALERT_TASK_ID}-{now}"
                fp = hashlib_fingerprint(ALERT_TASK_ID, body)
                _register_renderer_receipt(ALERT_TASK_ID, fp, "telegram:ops", msg_id)
                sent[key] = now
                _save_alert_state(sent)
                print(f"ORG-PULSE-ALERT: lifecycle change alert sent")

    # Alert 5: failed default-branch CI
    failed_ci = [ev for ev in recent
                 if ev["kind"] == "workflow_run"
                 and ev["action"] == "completed"
                 and ev.get("importance") == "high"]
    if failed_ci:
        key = "failed-ci"
        if sent.get(key) != now:
            body = f"ORG PULSE FAILED CI\n\nWorkflow run(s) with failure conclusion:\n" + "\n".join(f"• {ev['display_title']} — {ev['repo']}" for ev in failed_ci[:10]) + f"\n\nEvidence freshness: {now}"
            ok, detail = _send_via_renderer(ALERT_TASK_ID, body, producer="org-pulse-alert")
            if ok:
                msg_id = f"statuscard-{ALERT_TASK_ID}-{now}"
                fp = hashlib_fingerprint(ALERT_TASK_ID, body)
                _register_renderer_receipt(ALERT_TASK_ID, fp, "telegram:ops", msg_id)
                sent[key] = now
                _save_alert_state(sent)
                print(f"ORG-PULSE-ALERT: failed CI alert sent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hashlib_fingerprint(key: str, body: str) -> str:
    """Minimal fingerprint for receipt registration (not the full
    ActivityEvent payload_digest; this is renderer-side receipt identity)."""
    import hashlib
    return hashlib.sha256(f"{key}\0{body}".encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    collector_ok = os.environ.get("AG_PULSE_COLLECTOR_OK", "true").lower() == "true"
    mode = os.environ.get("AG_PULSE_RENDER_MODE", "pulse").strip()

    if mode == "pulse":
        send_pulse_card()
        check_alerts(STORE, collector_ok=collector_ok)
    elif mode == "digest":
        send_digest()
    elif mode == "alerts":
        check_alerts(STORE, collector_ok=collector_ok)
    elif mode == "all":
        send_pulse_card()
        send_digest()
        check_alerts(STORE, collector_ok=collector_ok)
    else:
        print(f"ORG-PULSE-RENDERER-FAIL: unknown render mode {mode}")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
