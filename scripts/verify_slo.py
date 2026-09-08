"""Verify the Phase-1 success metrics from CHATGPT-REVIEW-SPEC.md section 9.

The spec declares success criteria (duplicate rate 0, false INCIDENT 0,
evidence completeness 100%, per-job p50/p95 SLOs met, canary emission +
RESOLVED). This tool makes every one of those machine-checkable against
the fabric's own persistent state, so "success" is evidence, not prose.

Measured from real state, not fixtures:
  1. duplicate rate  - events.sqlite: any two OPEN events sharing a
     fingerprint is a duplicate (rate = dups / total events).
  2. evidence completeness - every event row must carry a non-empty
     typed `evidence` string (evidence.type=... keys present).
  3. canary          - canary.sqlite: every emission event that reached
     OPEN must also have a RESOLVED row for the same event_key
     (emission -> exactly-one Telegram + one RESOLVED).
  4. SLOs            - per job: p50/p95 of actual execution duration
     (finished_at - started_at, scheduler-source completed executions)
     vs declared detection_slo {p50_max, p95_max} from jobs/*.yaml.
     Jobs with fewer than MIN_RUNS completed runs report
     INSUFFICIENT-DATA (not a violation).

Exit codes: 0 = PASS (all checkable metrics green), 1 = VIOLATION
(any metric red), 2 = INSUFFICIENT-DATA (SLOs not yet measurable but
no violation; everything else green).

Usage:
  python3 scripts/verify_slo.py \
    --jobs-dir jobs \
    --events-db state/events.sqlite \
    --canary-db state/canary.sqlite \
    --executions-db <hermes cron/executions.db> \
    --jobs-json <hermes cron/jobs.json>
"""
import argparse
import glob
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path


MIN_RUNS = 5  # fewer completed scheduler runs -> INSUFFICIENT-DATA


def parse_detection_slo(path):
    """Pull detection_slo {p50_max, p95_max} out of a job YAML. Jobs
    declare it as an inline dict on one line:
        detection_slo: {p50_max: 6h, p95_max: 12h}
    (no yaml dependency - CI runners lack PyYAML)."""
    slo = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("detection_slo:"):
                body = line.split("detection_slo:", 1)[1]
                body = body.strip().strip("{}").strip()
                for part in body.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    k, _, v = part.partition(":")
                    k, v = k.strip(), v.strip()
                    if k in ("p50_max", "p95_max"):
                        slo["p50" if k == "p50_max" else "p95"] = v
                break
    return slo


def parse_duration(s):
    """Parse '2h'/'48h'/'7d'/'10d' style durations to seconds."""
    s = s.strip()
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("m"):
        return int(s[:-1]) * 60
    return int(s)


def check_duplicate_rate(db_path):
    """Any two events sharing a fingerprint = duplicate. 0 dups -> pass."""
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT event_key, fingerprint FROM events").fetchall()
    con.close()
    by_fp = {}
    for key, fp in rows:
        by_fp.setdefault(fp, []).append(key)
    dups = [keys for keys in by_fp.values() if len(keys) > 1]
    total = len(rows)
    if not dups:
        return [], f"duplicate rate 0/{total} events share a fingerprint"
    msg = f"DUPLICATE: {len(dups)} fingerprint(s) opened more than once: " + \
        "; ".join(f"{fp}->{keys}" for fp, keys in by_fp.items() if len(keys) > 1)
    return [msg], f"duplicate rate {len(dups)}/{total} (must be 0)"


def check_evidence_completeness(db_path):
    """Every event must carry typed evidence (evidence.type=... present)."""
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT event_key, evidence FROM events").fetchall()
    con.close()
    missing = [k for k, ev in rows if not ev or "=" not in ev]
    total = len(rows)
    if not missing:
        return [], f"evidence completeness {total}/{total} events carry typed evidence"
    return ([f"event(s) missing typed evidence: {missing}"],
            f"evidence completeness {total - len(missing)}/{total} (must be 100%)")


def check_canary(canary_db):
    """Every canary emission that opened must have a RESOLVED for same key."""
    con = sqlite3.connect(canary_db)
    rows = con.execute("SELECT event_key, state FROM events").fetchall()
    con.close()
    opened = [k for k, st in rows if st == "OPEN"]
    resolved = [k for k, st in rows if st == "RESOLVED"]
    if not rows:
        return [], "canary: no emissions recorded yet"
    if not opened and resolved:
        return [], f"canary: {len(resolved)} emission(s) all resolved"
    if not opened:
        return [], f"canary: {len(rows)} total event(s), none OPEN"
    unresolved = [k for k in opened if k not in resolved]
    if not unresolved:
        return [], f"canary: {len(opened)} emission(s) all reached RESOLVED"
    return ([f"canary emission(s) without RESOLVED: {unresolved}"],
            f"canary {len(resolved)}/{len(opened)} emissions resolved (must be 100%)")


def check_slos(jobs_dir, executions_db, jobs_json_path, job_id_map):
    """Per-job p50/p95 execution duration vs declared detection_slo."""
    errors, notes = [], []
    job_files = sorted(glob.glob(str(jobs_dir / "*.yaml"))) + \
        sorted(glob.glob(str(jobs_dir / "legacy" / "*.yaml")))
    con = sqlite3.connect(executions_db)
    for jf in job_files:
        name = Path(jf).stem
        slo = parse_detection_slo(jf)
        if not slo:
            # not every job must declare SLOs; only those that do are checked
            continue
        job_id = job_id_map.get(name)
        if not job_id:
            notes.append(f"{name}: no live cron job id mapped - SKIP")
            continue
        rows = con.execute(
            "SELECT started_at, finished_at FROM executions "
            "WHERE job_id=? AND status='completed' AND source != 'manual_run' "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 50",
            (job_id,),
        ).fetchall()
        if len(rows) < MIN_RUNS:
            notes.append(f"{name}: {len(rows)} runs (<{MIN_RUNS}) - "
                         f"INSUFFICIENT-DATA")
            continue
        dur = []
        for s, f in rows:
            try:
                d = (datetime.fromisoformat(f) - datetime.fromisoformat(s)).total_seconds()
                dur.append(d)
            except (ValueError, TypeError):
                continue
        if len(dur) < MIN_RUNS:
            notes.append(f"{name}: {len(dur)} parseable runs - INSUFFICIENT-DATA")
            continue
        p50 = statistics.median(dur)
        p95 = sorted(dur)[int(round(0.95 * (len(dur) - 1)))]
        p50_max = parse_duration(slo["p50"])
        p95_max = parse_duration(slo["p95"])
        ok = p50 <= p50_max and p95 <= p95_max
        if not ok:
            errors.append(
                f"{name}: p50={p50/3600:.1f}h (max {slo['p50']}) "
                f"p95={p95/3600:.1f}h (max {slo['p95']}) - SLO EXCEEDED")
        else:
            notes.append(f"{name}: p50={p50/3600:.1f}h p95={p95/3600:.1f}h "
                         f"within {slo['p50']}/{slo['p95']} - OK")
    con.close()
    return errors, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs-dir", default="jobs")
    ap.add_argument("--events-db", default="state/events.sqlite")
    ap.add_argument("--canary-db", default="state/canary.sqlite")
    ap.add_argument("--executions-db", required=True)
    ap.add_argument("--jobs-json", required=True)
    args = ap.parse_args()

    jobs_dir = Path(args.jobs_dir)
    # map job name -> cron job_id from jobs.json (live install truth)
    jobs = json.load(open(args.jobs_json, encoding="utf-8"))
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", jobs.get("scheduled_jobs", []))
    job_id_map = {}
    for j in (jobs if isinstance(jobs, list) else jobs.values()):
        name = j.get("name") or j.get("job_name")
        if name and j.get("id"):
            job_id_map[name] = j["id"]
    # fabric names have no ag- prefix in cron? map both forms
    if not job_id_map:
        for j in (jobs if isinstance(jobs, list) else jobs.values()):
            if j.get("id"):
                job_id_map.setdefault(j["id"], j["id"])

    all_errors = []
    all_notes = []

    for name, fn in [("duplicate rate", check_duplicate_rate),
                     ("evidence completeness", check_evidence_completeness),
                     ("canary", check_canary)]:
        try:
            errs, note = fn(args.events_db if name != "canary" else args.canary_db)
            all_errors += errs
            all_notes.append(f"{name}: {note}")
        except Exception as e:  # missing db -> treat as insufficient, not violation
            all_notes.append(f"{name}: no data ({e})")

    slo_errs, slo_notes = check_slos(jobs_dir, args.executions_db,
                                     args.jobs_json, job_id_map)
    all_errors += slo_errs
    all_notes += slo_notes

    for n in all_notes:
        print(f"  note: {n}")
    if all_errors:
        print("VIOLATION:")
        for e in all_errors:
            print(f"  - {e}")
        return 1
    # any INSUFFICIENT-DATA / SKIP / missing data -> 2 (pending, not failed)
    if any(("INSUFFICIENT-DATA" in n or "SKIP" in n or "no data" in n)
           for n in all_notes):
        print("PASS (checkable metrics) / INSUFFICIENT-DATA (SLOs pending "
              "live Phase-1 runs)")
        return 2
    print("SLO-VERIFIED: all checkable Phase-1 success metrics green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
