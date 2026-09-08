"""Verify machine-checkable Cron Fabric success metrics.

Measured from persistent state:
  1. duplicate rate - events.sqlite fingerprints must not be duplicated.
  2. evidence completeness - every event row carries typed evidence text.
  3. state canary - canary.sqlite must finish HEALTHY after its synthetic
     claim/silence/resolution lifecycle. Historical RESOLVED fixture rows are
     accepted for compatibility.
  4. canary receipts - deploy/receipts/ag-fabric-canary-*.json: every file
     schema-valid with matching sha256; unresolved emissions must be covered
     by a receipt (pre-receipt resolved emissions grandfathered, listed).
  5. baseline volume - trailing-window first-open rate vs frozen
     state/baseline.json (first-opens only, by events-table design).
  6. SLOs - per-job p50/p95 SCHEDULED execution duration
     (scheduler/builtin sources only; manual/direct fires would fake SLOs
     green) vs detection_slo. Jobs with fewer than MIN_RUNS scheduled runs
     report INSUFFICIENT-DATA (not a violation).

Fail-closed: unknown execution sources, SLO jobs with no live mapping,
absent/unreadable state, malformed receipts, unresolved emissions without
receipts, and missing/invalid baselines are ERRORS, never silent skips.
Insufficient scheduled history is exit 2 (pending, explicitly not PASS).

This verifier does NOT claim end-to-end Telegram exactly-once delivery. That
requires a separate delivery receipt surface/canary. False-INCIDENT rate
likewise requires an incident tier, which does not exist (highest severity
is warning/digest); the claim was removed from CHATGPT-REVIEW-SPEC.md
section 9. Volume reduction is measured against the frozen baseline log.

Exit codes: 0 = PASS, 1 = VIOLATION, 2 = INSUFFICIENT-DATA.
"""
import argparse
import glob
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path


MIN_RUNS = 5

# Execution sources verify_slo understands. Anything else in the
# executions table is a fail-closed error (unknown provenance must never
# silently count toward -- or be silently excluded from -- SLO evidence).
KNOWN_SOURCES = {"scheduler", "builtin", "direct", "manual_run"}
# Sources accepted as *scheduled* runs for SLO durations. Manual/direct
# fires complete in seconds and would fake SLOs green; they are excluded
# from durations (but never silently: see check_sources).
SCHEDULER_SOURCES = {"scheduler", "builtin"}


def check_sources(executions_db):
    """Fail closed on unknown execution sources.

    Returns (errors, notes). An empty/unknown source set is an error:
    SLO evidence with unclassifiable provenance cannot verify.
    """
    try:
        con = sqlite3.connect(executions_db)
        try:
            found = {r[0] for r in
                     con.execute("SELECT DISTINCT source FROM executions").fetchall()}
        except sqlite3.Error as e:
            return ([f"executions table unreadable ({e}) - cannot verify"], "")
        finally:
            con.close()
    except Exception as e:
        return ([f"executions db absent/unreadable ({e}) - cannot verify"], "")
    unknown = {s for s in found if s not in KNOWN_SOURCES} | \
        {s for s in found if s is None or (isinstance(s, str) and not s.strip())}
    if unknown:
        return ([f"unknown execution source(s): {sorted(map(str, unknown))} - "
                 f"add to KNOWN_SOURCES/SCHEDULER_SOURCES explicitly or investigate"],
                "")
    if not found:
        return ([], "sources: no executions recorded yet")
    return ([], f"sources: all classified ({sorted(found)})")


def parse_detection_slo(path):
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
    s = s.strip()
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("m"):
        return int(s[:-1]) * 60
    return int(s)


def check_duplicate_rate(db_path):
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
    """State canary succeeds only when no synthetic event remains OPEN."""
    con = sqlite3.connect(canary_db)
    rows = con.execute("SELECT event_key, state FROM events").fetchall()
    con.close()
    if not rows:
        return [], "state canary: no run recorded yet"
    unresolved = [k for k, st in rows if st not in ("HEALTHY", "RESOLVED")]
    if unresolved:
        return ([f"state canary unresolved event(s): {unresolved}"],
                f"state canary {len(rows) - len(unresolved)}/{len(rows)} resolved")
    return [], f"state canary {len(rows)}/{len(rows)} resolved/HEALTHY"


RECEIPT_SCHEMA = "canary-receipt/1"
RECEIPT_REQUIRED_KEYS = {"schema", "run_id", "at", "key", "emitted",
                         "deliveries", "resolved"}


def _receipt_valid(path):
    """Fail-closed receipt validation. Returns (receipt|None, error|None)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        return None, f"receipt unreadable {Path(path).name} ({e})"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"receipt malformed {Path(path).name} ({e})"
    if not isinstance(doc, dict):
        return None, f"receipt malformed {Path(path).name} (not an object)"
    if doc.get("schema") != RECEIPT_SCHEMA:
        return None, f"receipt {Path(path).name} has unknown schema " \
                     f"({doc.get('schema')!r}, want {RECEIPT_SCHEMA!r})"
    missing = RECEIPT_REQUIRED_KEYS - set(doc)
    if missing:
        return None, f"receipt {Path(path).name} missing keys {sorted(missing)}"
    expect = hashlib.sha256(json.dumps(
        {k: v for k, v in doc.items() if k != "sha256"},
        indent=2, sort_keys=True).encode("utf-8")).hexdigest()
    if doc.get("sha256") != expect:
        return None, f"receipt {Path(path).name} sha256 mismatch (tampered?)"
    return doc, None


def check_canary_receipts(receipts_dir, canary_db):
    """Reconcile per-run receipts against canary.sqlite. Fail closed.

    Rules: every receipt file must be schema-valid with matching sha256;
    every self-test receipt's key must exist in the DB; a receipt claiming
    resolved=True must have no OPEN row for its key; every UNRESOLVED
    (OPEN) emission must be covered by a receipt. Resolved pre-receipt
    emissions are grandfathered (listed, never violations).
    """
    errors, notes = [], []
    rdir = Path(receipts_dir)
    files = sorted(rdir.glob("ag-fabric-canary-*.json")) if rdir.is_dir() else []
    docs = []
    for f in files:
        doc, err = _receipt_valid(f)
        if err:
            errors.append(err)
        else:
            docs.append(doc)
    try:
        con = sqlite3.connect(canary_db)
        try:
            rows = con.execute(
                "SELECT event_key, state FROM events").fetchall()
        except sqlite3.Error as e:
            return errors + [f"canary db unreadable ({e}) - cannot verify"], ""
        finally:
            con.close()
    except Exception as e:
        return errors + [f"canary db absent/unreadable ({e}) - cannot verify"], ""
    by_key = {}
    for k, st in rows:
        by_key.setdefault(k, set()).add(st)
    covered = {d["key"] for d in docs if d.get("mode") == "self-test"}
    for d in docs:
        if d.get("mode") == "self-test" and d["key"] not in by_key:
            errors.append(f"receipt {d['run_id']} claims key {d['key']!r} "
                          f"absent from canary db")
        if d.get("resolved") is True and "OPEN" in by_key.get(d["key"], set()):
            errors.append(f"receipt {d['run_id']} claims resolved but key "
                          f"{d['key']!r} still OPEN")
    uncovered_open = [k for k, sts in by_key.items()
                      if "OPEN" in sts and k not in covered]
    if uncovered_open:
        errors.append(f"unresolved emission(s) without covering receipt: "
                      f"{uncovered_open}")
    grandfathered = [k for k in by_key if k not in covered]
    notes.append(f"receipts: {len(docs)} valid file(s); "
                 f"{len(grandfathered)} pre-receipt key(s) grandfathered")
    return errors, "; ".join(notes)


def check_baseline_volume(events_db, baseline_path):
    """Trailing-window emission volume vs frozen baseline. Fail closed.

    Baseline JSON: {"frozen_at": iso, "window_days": 7,
    "max_daily_emissions": number}. Missing/invalid baseline is an ERROR
    with remediation (it means nobody froze the reference, not that
    volume is fine). Counts DISTINCT event keys first opened inside the
    trailing window (first-opens only: the events table keeps one row per
    key, so re-emissions are invisible here by schema design).
    """
    from datetime import timedelta, timezone
    try:
        spec = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ([f"baseline file absent ({baseline_path}) - freeze one from "
                 f"live data, then re-run"], "")
    except (OSError, json.JSONDecodeError) as e:
        return ([f"baseline file invalid ({e})"], "")
    try:
        window = int(spec["window_days"])
        maximum = float(spec["max_daily_emissions"])
        assert window > 0 and maximum >= 0
    except (KeyError, TypeError, ValueError, AssertionError):
        return ([f"baseline file {baseline_path} must define positive "
                 f"window_days and non-negative max_daily_emissions"], "")
    try:
        con = sqlite3.connect(events_db)
        try:
            rows = con.execute(
                "SELECT event_key, opened_at FROM events").fetchall()
        except sqlite3.Error as e:
            return ([f"events db unreadable ({e}) - cannot verify"], "")
        finally:
            con.close()
    except Exception as e:
        return ([f"events db absent/unreadable ({e}) - cannot verify"], "")
    cutoff = time.time() - window * 86400
    opened = {k for k, ts in rows
              if isinstance(ts, (int, float)) and ts >= cutoff}
    daily = len(opened) / window
    if daily <= maximum:
        return ([], f"baseline volume {daily:.2f}/day <= {maximum}/day "
                    f"({len(opened)} first-opens / {window}d)")
    return ([f"baseline volume {daily:.2f}/day exceeds {maximum}/day "
             f"({len(opened)} first-opens / {window}d)"],
            "")


def check_slos(jobs_dir, executions_db, jobs_json_path, job_id_map):
    errors, notes = [], []
    job_files = sorted(glob.glob(str(jobs_dir / "*.yaml"))) + \
        sorted(glob.glob(str(jobs_dir / "legacy" / "*.yaml")))
    con = sqlite3.connect(executions_db)
    installed = bool(job_id_map)
    for jf in job_files:
        name = Path(jf).stem
        slo = parse_detection_slo(jf)
        if not slo:
            continue
        job_id = job_id_map.get(name)
        if not job_id:
            if not installed:
                # No live install at all: nothing to verify against.
                # Pending, not a violation (fixtures use an empty install).
                notes.append(f"{name}: no live install mapped - "
                             f"INSUFFICIENT-DATA")
                continue
            # Fail closed: a job declaring an SLO but invisible to a live
            # scheduler cannot verify. Silence here once hid exactly this.
            errors.append(f"{name}: declares detection_slo but has no live "
                          f"cron job id mapped - cannot verify")
            continue
        rows = con.execute(
            "SELECT started_at, finished_at, source FROM executions "
            "WHERE job_id=? AND status='completed' "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 50",
            (job_id,),
        ).fetchall()
        # Scheduled-only durations: manual/direct fires finish in seconds
        # and would fake SLOs green. Source is classified by check_sources;
        # here we measure only scheduler-accepted provenance.
        sched_rows = [(s, f) for (s, f, src) in rows
                      if src in SCHEDULER_SOURCES]
        if len(sched_rows) < MIN_RUNS:
            notes.append(f"{name}: {len(sched_rows)} scheduled runs (<{MIN_RUNS}) - "
                         f"INSUFFICIENT-DATA")
            continue
        dur = []
        for s, f in sched_rows:
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
    ap.add_argument("--receipts-dir", default="deploy/receipts")
    ap.add_argument("--baseline", default="state/baseline.json")
    args = ap.parse_args()

    jobs_dir = Path(args.jobs_dir)
    jobs = json.load(open(args.jobs_json, encoding="utf-8"))
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", jobs.get("scheduled_jobs", []))
    job_id_map = {}
    for j in (jobs if isinstance(jobs, list) else jobs.values()):
        name = j.get("name") or j.get("job_name")
        if name and j.get("id"):
            job_id_map[name] = j["id"]
    if not job_id_map:
        for j in (jobs if isinstance(jobs, list) else jobs.values()):
            if j.get("id"):
                job_id_map.setdefault(j["id"], j["id"])

    all_errors = []
    all_notes = []
    for name, fn in [("duplicate rate", check_duplicate_rate),
                     ("evidence completeness", check_evidence_completeness),
                     ("state canary", check_canary)]:
        try:
            errs, note = fn(args.events_db if name != "state canary" else args.canary_db)
            all_errors += errs
            all_notes.append(f"{name}: {note}")
        except FileNotFoundError as e:
            # Absent state cannot verify: fail closed, never silent.
            all_errors.append(f"{name}: state file absent ({e}) - cannot verify")
        except Exception as e:
            all_errors.append(f"{name}: check crashed ({e}) - cannot verify")

    for name, fn in [("sources", lambda: check_sources(args.executions_db)),
                     ("canary receipts",
                      lambda: check_canary_receipts(args.receipts_dir,
                                                    args.canary_db)),
                     ("baseline volume",
                      lambda: check_baseline_volume(args.events_db,
                                                    args.baseline))]:
        try:
            errs, note = fn()
            all_errors += errs
            if note:
                all_notes.append(f"{name}: {note}")
        except Exception as e:
            all_errors.append(f"{name}: check crashed ({e}) - cannot verify")

    try:
        slo_errs, slo_notes = check_slos(jobs_dir, args.executions_db,
                                         args.jobs_json, job_id_map)
    except Exception as e:
        slo_errs, slo_notes = ([f"slos: check crashed ({e}) - cannot verify"], [])
    all_errors += slo_errs
    all_notes += slo_notes

    for n in all_notes:
        print(f"  note: {n}")
    if all_errors:
        print("VIOLATION:")
        for e in all_errors:
            print(f"  - {e}")
        return 1
    # any INSUFFICIENT-DATA / missing-data note -> exit 2 (pending, not failed).
    # The message never claims PASS: exit 2 means "not yet verifiable".
    if any(("INSUFFICIENT-DATA" in n or "no data" in n or "no executions" in n)
           for n in all_notes):
        print("INSUFFICIENT-DATA: checkable metrics green, SLO history "
              "pending live Phase-1 runs")
        return 2
    print("SLO-VERIFIED: all machine-checkable metrics green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
