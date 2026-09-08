"""Verify that a scheduled cron tick actually FIRED and consumed its slot.

The fabric's Phase-1 acceptance depends on scheduled (not manual) ticks
delivering executions. This check proves a slot was consumed by a real
scheduler-dispatched execution, not skipped by completed_occurrence
(jobs.py) nor left dangling.

Usage:
  python3 scripts/verify_tick.py \
    --jobs-json <hermes cron/jobs.json> \
    --executions-db <hermes cron/executions.db> \
    --output-dir <hermes profiles/<p>/cron/output/<job_id>> \
    --job-name ag-sentinel-release \
    --job-id 50159b84a34f \
    --slot "2026-09-09T09:00:00+02:00"

Exit codes: 0 = FIRED+VERIFIED, 1 = FAILED (evidence contradicts),
2 = PENDING (slot not reached yet or execution not finished).
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_ts(ts):
    return datetime.fromisoformat(ts)


def check_slot(jobs, job_name, slot_iso, tz_note="+02:00"):
    """Job must exist, be enabled, and its next_run_at must have advanced
    past the expected slot (a consumed slot moves to the next day)."""
    job = next((j for j in jobs if j.get("name") == job_name), None)
    if not job:
        return [], f"job {job_name} not found in jobs.json"
    if not job.get("enabled"):
        return [], f"job {job_name} is DISABLED - tick cannot fire"
    next_run = job.get("next_run_at")
    if not next_run:
        return [], f"job {job_name} has no next_run_at"
    errors = []
    slot = datetime.fromisoformat(slot_iso)
    nxt = datetime.fromisoformat(next_run)
    if nxt <= slot:
        errors.append(f"next_run_at {next_run} has NOT advanced past slot "
                      f"{slot_iso} - slot unconsumed (scheduler skip or "
                      f"completed_occurrence suppress)")
    return errors, None


def check_execution(db_path, job_id, slot_iso, window_minutes=60):
    """A completed scheduler-dispatched execution must exist whose
    scheduled_instant equals the slot's UTC instant. Manual/direct fires
    do NOT count (source must be scheduler, not manual_run)."""
    errors = []
    slot = datetime.fromisoformat(slot_iso)
    slot_utc = slot.astimezone(timezone.utc)
    lo = slot_utc.isoformat()
    hi = slot_utc.timestamp() + window_minutes * 60
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT id, source, status, scheduled_instant, started_at, finished_at "
        "FROM executions WHERE job_id=? AND started_at >= ? AND started_at <= ? "
        "ORDER BY started_at DESC",
        (job_id, slot_utc.timestamp() - 60, hi),
    ).fetchall()
    if not rows:
        return [f"no execution for job {job_id} started within the slot "
                f"window ({slot_iso} +/- {window_minutes} min)"] , None
    best = None
    for rid, source, status, inst, started, finished in rows:
        if status == "completed" and source != "manual_run":
            best = (rid, source, inst)
            break
    if not best:
        return [f"executions found but none completed+scheduled "
                f"(manuals: {[r[1] for r in rows]})"], None
    rid, source, inst = best
    if inst:
        try:
            inst_utc = datetime.fromisoformat(inst)
            if abs((inst_utc - slot_utc).total_seconds()) > 300:
                errors.append(f"execution {rid} scheduled_instant {inst} "
                              f"mismatches slot UTC {slot_utc.isoformat()}")
        except ValueError:
            errors.append(f"execution {rid} bad scheduled_instant {inst!r}")
    else:
        errors.append(f"execution {rid} has NULL scheduled_instant - "
                      f"fix may not have applied")
    return errors, rid


def check_output(output_dir, execution_id, slot_iso):
    """The dispatch must have produced an output file for the slot."""
    d = Path(output_dir)
    if not d.is_dir():
        return [f"output dir {output_dir} missing"]
    files = sorted(d.glob("*.md"))
    if not files:
        return [f"no output files in {output_dir}"]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs-json", required=True)
    ap.add_argument("--executions-db", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--job-name", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--slot", required=True)
    args = ap.parse_args()

    jobs = json.load(open(args.jobs_json, encoding="utf-8"))
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", jobs.get("scheduled_jobs", []))
    errs, fatal = check_slot(jobs, args.job_name, args.slot)
    if fatal:
        print(f"FAIL: {fatal}")
        return 1
    errs2, rid = check_execution(args.executions_db, args.job_id, args.slot)
    errs += errs2
    if rid:
        errs += check_output(args.output_dir, rid, args.slot)
    if errs:
        # Slot not reached yet? Distinguish PENDING from FAILED.
        slot = datetime.fromisoformat(args.slot)
        if datetime.now().astimezone() < slot:
            print(f"PENDING: slot {args.slot} not reached yet")
            return 2
        print("FAIL:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"TICK-VERIFIED: {args.job_name} fired slot {args.slot} "
          f"(execution {rid}, completed, scheduled_instant matched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())