"""Fleet status card: one Telegram card for the four v0.5 sensors.

Reads the latest immutable shadow receipt per sensor job from
deploy/receipts/ag-v05-shadow-<job>-*.json, verifies each receipt's
sha256 attestation, and publishes a single edit-in-place status card
via the telegram-live-status renderer (`hermes statuscard`,
task ag-fleet). Fresh EMIT/CRASH receipts also produce one-shot
exception cards, deduped in state/fleet_alerts.json so each incident
alerts exactly once.

Exit codes: 0 = aggregation published (even when the fleet is WARN;
an unhealthy fleet is a finding, not an aggregator failure);
1 = aggregator's own failure (no receipts, tampered receipt,
card send failed, hermes CLI missing); 2 = usage/config error.

--dry-run prints the card payloads instead of sending (tests, probes).
The script never claims delivery it did not observe: a card is only
reported OK on renderer exit 0.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


FLEET_JOBS = [
    "ag-merge-queue-stall",
    "ag-org-suite-liveness",
    "ag-public-provenance",
    "ag-research-freeze-watch",
]

FLEET_TASK = "ag-fleet"
ALERTS_FILE = "fleet_alerts.json"

# Classifications that deserve a one-shot exception card.
ALERT_CLASSES = {"EMIT", "CRASH", "DEGRADED"}


def _repo_root():
    env = os.environ.get("AG_FABRIC_ROOT")
    if env:
        return Path(env)
    cand = Path(__file__).resolve().parent.parent
    if (cand / "contracts" / "sources.yaml").is_file():
        return cand
    cwd = Path.cwd()
    if (cwd / "contracts" / "sources.yaml").is_file():
        return cwd
    print("FLEET-FAIL: cannot locate fabric root (set AG_FABRIC_ROOT)")
    sys.exit(2)


REPO = _repo_root()
sys.path.insert(0, str(REPO / "scripts"))
from shadow_receipt import (  # noqa: E402
    FILENAME_PREFIX, verify_receipt,
)

RECEIPTS_DIR = REPO / "deploy" / "receipts"
STATE_DIR = REPO / "state"


def _latest_receipt(job):
    cands = sorted(RECEIPTS_DIR.glob(f"{FILENAME_PREFIX}{job}-*.json"))
    return cands[-1] if cands else None


def _send_card(task_id, status, message, detail="", next_step="",
               dry_run=False):
    """Send (or print, under --dry-run) one status card. Returns ok."""
    event = {
        "task_id": task_id,
        "status": status,
        "message": message,
    }
    if detail:
        event["detail"] = detail
    if next_step:
        event["next_step"] = next_step
    if dry_run:
        print(f"FLEET-CARD-DRYRUN: {task_id} [{status}] {message}")
        if detail:
            print(f"FLEET-CARD-DETAIL: {detail}")
        return True
    hermes = shutil.which("hermes")
    if not hermes:
        print("FLEET-FAIL: hermes CLI not found on PATH")
        return False
    cmd = [hermes, "statuscard", "--to", "telegram:Jonas",
           "--task", task_id, "--event", json.dumps(event)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=60)
    except subprocess.TimeoutExpired:
        print("FLEET-FAIL: statuscard timed out after 60s")
        return False
    if out.returncode != 0:
        print(f"FLEET-FAIL: statuscard rc={out.returncode} "
              f"stderr={out.stderr.strip()[:200]}")
        return False
    return True


def _load_alerts():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / ALERTS_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_alerts(alerts):
    path = STATE_DIR / ALERTS_FILE
    path.write_text(json.dumps(alerts, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print card payloads instead of sending")
    args = ap.parse_args()

    if not RECEIPTS_DIR.is_dir():
        print("FLEET-FAIL: no receipts dir "
              f"(nothing has run yet): {RECEIPTS_DIR}")
        sys.exit(1)

    rows = []
    alerts = _load_alerts()
    new_alerts = []
    fleet_bad = False
    for job in FLEET_JOBS:
        path = _latest_receipt(job)
        if path is None:
            rows.append(f"{job}: PENDING (no receipt yet)")
            continue
        try:
            ok = verify_receipt(path)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            print(f"FLEET-FAIL: unreadable receipt {path.name}: {e}")
            sys.exit(1)
        if not ok:
            print(f"FLEET-FAIL: tampered receipt {path.name} "
                  f"(sha256 mismatch)")
            sys.exit(1)
        parsed = json.loads(path.read_text(encoding="utf-8"))
        cls = parsed.get("classification", "UNKNOWN")
        rc = parsed.get("returncode", -1)
        rows.append(f"{job}: {cls} (rc={rc}, {path.name})")
        if cls in ALERT_CLASSES or rc != 0:
            fleet_bad = True
            key = f"{job}:{parsed.get('run_id', path.name)}"
            if key not in alerts:
                alert_task = f"ag-fleet-alert-{job}"
                alert_msg = (f"{job}: {cls} "
                             f"(rc={rc}, run {parsed.get('run_id')})")
                detail = "\n".join(
                    parsed.get("stdout_tail", []))[:800]
                if _send_card(alert_task, "FAIL", alert_msg,
                              detail=detail,
                              next_step="See fleet card + repo receipts.",
                              dry_run=args.dry_run):
                    alerts[key] = alert_task
                    new_alerts.append(key)
                else:
                    print("FLEET-FAIL: exception card send failed")
                    sys.exit(1)
    _save_alerts(alerts)

    status = "WARN" if fleet_bad else "OK"
    summary = (f"fleet {status}: "
               + "; ".join(r.split(" (")[0] for r in rows))
    if not _send_card(FLEET_TASK, status, summary,
                      detail=" | ".join(rows)[:1500],
                      next_step=("Investigate exception cards."
                                 if fleet_bad else
                                 "No action; next tick auto-updates."),
                      dry_run=args.dry_run):
        print("FLEET-FAIL: fleet card send failed")
        sys.exit(1)
    print(f"FLEET-{status}: {summary} "
          f"(alerts_new={len(new_alerts)})")
    sys.exit(0)


if __name__ == "__main__":
    main()
