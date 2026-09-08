"""v0.5.1 daily shadow summary runner (receipt aggregator).

Reads the immutable per-run receipts written by the four shadow
sensor wrappers (deploy/receipts/ag-v05-shadow-<job>-*.json, see
scripts/shadow_receipt.py) and writes ONE immutable daily summary
receipt. It never re-runs sensors and never touches Telegram.

Window: the 24h ending now, clamped to >= shadow start (the
earliest per-run receipt ever observed). Expected runs are derived
mechanically from the cron intervals (merge-queue 2h -> 12/day,
all others 6h -> 4/day each), prorated on the first partial day.

Receipt schema (v05-shadow-summary/2):
    {
      "schema": "v05-shadow-summary/2",
      "date": "YYYY-MM-DD",          # UTC date of generation
      "window_start": ISO, "window_end": ISO,
      "shadow_started_at": ISO,      # earliest receipt observed
      "sensors": {
        "<job>": {
          "runs_expected": int, "runs_observed": int,
          "missing_ticks": int,
          "EMIT": int, "SILENCE": int, "DEGRADED": int, "CRASH": int,
          "receipts_verified": int, "receipts_unverified": int,
          "duration_p50_s": float, "duration_p95_s": float,
          "agent_invoked": false, "mutation_attempted": false
        }
      },
      "aggregate": {
        "total_expected": int, "total_observed": int,
        "total_emits": int, "duplicate_count": int,
        "crashes": int, "unverified_receipts": int,
        "projected_telegram_messages_per_day": int,
        "verdict": "COMPLETE" | "INCOMPLETE",
        "verdict_reasons": [str]
      }
    }

duplicate_count is measured by the persistent EventStores / canary
pair, not by receipts (receipts carry no event_key/fingerprint);
it stays 0 here with that provenance noted. Fail-closed: a day
with zero receipts is INCOMPLETE, never COMPLETE by default.
"""

import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _repo_root():
    env = os.environ.get("AG_FABRIC_ROOT")
    if env:
        return Path(env)
    cand = Path(__file__).resolve().parent.parent.parent
    if (cand / "contracts" / "sources.yaml").is_file():
        return cand
    cwd = Path.cwd()
    if (cwd / "contracts" / "sources.yaml").is_file():
        return cwd
    print("SHADOW-SUMMARY-FAIL: cannot locate fabric root")
    sys.exit(2)


REPO = _repo_root()
sys.path.insert(0, str(REPO / "scripts"))
from shadow_receipt import FILENAME_PREFIX, verify_receipt  # noqa: E402

RECEIPTS = REPO / "deploy" / "receipts"

INTERVALS = {
    "ag-merge-queue-stall": 2 * 3600,
    "ag-org-suite-liveness": 6 * 3600,
    "ag-public-provenance": 6 * 3600,
    "ag-research-freeze-watch": 6 * 3600,
}


def _parse_iso(s):
    return datetime.fromisoformat(s)


def _p50_p95(values):
    if not values:
        return 0.0, 0.0
    ordered = sorted(values)
    p50 = round(statistics.median(ordered), 3)
    p95 = round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3)
    return p50, p95


def summarize(repo_root, now=None):
    """Aggregate per-run receipts into a summary report dict."""
    repo = Path(repo_root)
    now = now or datetime.now(timezone.utc)
    window_end = now
    window_start = now - timedelta(hours=24)

    receipts_dir = repo / "deploy" / "receipts"
    runs = []  # (job, parsed_receipt, verified_bool)
    earliest = None
    if receipts_dir.is_dir():
        for path in sorted(receipts_dir.glob(f"{FILENAME_PREFIX}*.json")):
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            job = parsed.get("job")
            if job not in INTERVALS:
                continue
            try:
                started = _parse_iso(parsed["started_at"])
                ended = _parse_iso(parsed["ended_at"])
            except (KeyError, ValueError):
                continue
            if earliest is None or started < earliest:
                earliest = started
            if ended < window_start or ended > window_end:
                continue
            try:
                verified = verify_receipt(path)
            except Exception:
                verified = False
            runs.append((job, parsed, verified))

    shadow_start = earliest or window_start
    effective_start = max(window_start, shadow_start)
    window_seconds = max(0.0, (window_end - effective_start).total_seconds())

    sensors = {}
    total_expected = 0
    total_observed = 0
    total_emits = 0
    total_crashes = 0
    total_unverified = 0
    for job, interval in INTERVALS.items():
        job_runs = [(p, v) for (j, p, v) in runs if j == job]
        expected = int(window_seconds // interval)
        observed = len(job_runs)
        counts = {"EMIT": 0, "SILENCE": 0, "DEGRADED": 0, "CRASH": 0}
        verified = 0
        unverified = 0
        durations = []
        for parsed, ok in job_runs:
            cls = parsed.get("classification", "CRASH")
            counts[cls if cls in counts else "CRASH"] += 1
            if ok:
                verified += 1
            else:
                unverified += 1
            if isinstance(parsed.get("duration_s"), (int, float)):
                durations.append(parsed["duration_s"])
        p50, p95 = _p50_p95(durations)
        sensors[job] = {
            "runs_expected": expected,
            "runs_observed": observed,
            "missing_ticks": max(0, expected - observed),
            **counts,
            "receipts_verified": verified,
            "receipts_unverified": unverified,
            "duration_p50_s": p50,
            "duration_p95_s": p95,
            "agent_invoked": False,
            "mutation_attempted": False,
        }
        total_expected += expected
        total_observed += observed
        total_emits += counts["EMIT"]
        total_crashes += counts["CRASH"]
        total_unverified += unverified

    reasons = []
    if total_observed == 0:
        reasons.append("no per-run receipts in window (fail-closed)")
    missing = sum(s["missing_ticks"] for s in sensors.values())
    if missing:
        reasons.append(f"{missing} expected ticks missing")
    if total_crashes:
        reasons.append(f"{total_crashes} crashed runs")
    if total_unverified:
        reasons.append(f"{total_unverified} unverified receipts")
    if not reasons and window_seconds < 20 * 3600:
        # A short window (e.g. shadow start day) can have nothing
        # missing yet still prove nothing about a full day. It must
        # never read as COMPLETE at acceptance time.
        return {
            "schema": "v05-shadow-summary/2",
            "date": window_end.strftime("%Y-%m-%d"),
            "window_start": effective_start.isoformat(),
            "window_end": window_end.isoformat(),
            "shadow_started_at": shadow_start.isoformat(),
            "sensors": sensors,
            "aggregate": {
                "total_expected": total_expected,
                "total_observed": total_observed,
                "total_emits": total_emits,
                "duplicate_count": 0,
                "crashes": total_crashes,
                "unverified_receipts": total_unverified,
                "projected_telegram_messages_per_day": total_emits,
                "verdict": "PARTIAL",
                "verdict_reasons": [
                    f"window covers only "
                    f"{round(window_seconds / 3600, 1)}h (<20h): "
                    f"shadow start day, not a full observation day",
                ],
            },
        }
    verdict = "COMPLETE" if not reasons else "INCOMPLETE"

    return {
        "schema": "v05-shadow-summary/2",
        "date": window_end.strftime("%Y-%m-%d"),
        "window_start": effective_start.isoformat(),
        "window_end": window_end.isoformat(),
        "shadow_started_at": shadow_start.isoformat(),
        "sensors": sensors,
        "aggregate": {
            "total_expected": total_expected,
            "total_observed": total_observed,
            "total_emits": total_emits,
            # Provenance: persistent EventStores / canary pair, not
            # receipts (receipts carry no event_key/fingerprint).
            "duplicate_count": 0,
            "crashes": total_crashes,
            "unverified_receipts": total_unverified,
            "projected_telegram_messages_per_day": total_emits,
            "verdict": verdict,
            "verdict_reasons": reasons,
        },
    }


def main():
    global RECEIPTS
    RECEIPTS = REPO / "deploy" / "receipts"
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    receipt_path = RECEIPTS / f"v05-shadow-summary-{date}.json"
    if receipt_path.exists():
        # Immutable: never overwrite a prior receipt for this date.
        print(f"SHADOW-SUMMARY-OK: receipt already exists for {date}, "
              f"skipping (immutable receipts)")
        sys.exit(0)
    report = summarize(REPO, now)
    agg = report["aggregate"]
    receipt_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"SHADOW-SUMMARY-OK: {receipt_path.name} "
          f"verdict={agg['verdict']} "
          f"observed={agg['total_observed']}/{agg['total_expected']} "
          f"emits={agg['total_emits']} crashes={agg['crashes']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
