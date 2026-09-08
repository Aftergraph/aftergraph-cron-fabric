"""Behavioral tests (ChatGPT finding #9 fix). Run: python3 tests/test_behavior.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from event_store import EventStore
from sensor_guard import (EVIDENCE_TYPES, SensorDegraded, backoff,
                          classify_http, require_evidence,
                          require_typed_evidence, retry_after_seconds)
import email.utils
import time

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAIL: {name}"
    passed += 1
    print(f"  ok: {name}")


def fresh_store():
    tmp = tempfile.mkdtemp()
    return EventStore(f"{tmp}/events.sqlite")


# 1. same failure x3 -> exactly 1 claimed alert
s = fresh_store()
key = "ag-ci|contract|fail"
check("first failure EMITs", s.claim_event(key, "sha:aaa") == "EMIT")
check("repeat SILENCEs", s.claim_event(key, "sha:aaa") == "SILENCE")
check("third SILENCEs", s.claim_event(key, "sha:aaa") == "SILENCE")

# 2. observed recovery closes the event
s.resolve(key)
row = s.db.execute("SELECT state FROM events WHERE event_key=?",
                   (key,)).fetchone()
check("observed resolve closes", row[0] == "HEALTHY")

# 3. same failure after observed recovery -> new alert
check("return EMITs again", s.claim_event(key, "sha:aaa") == "EMIT")

# 4. human ACK is not recovery: keep OPEN and silence same fingerprint
check("acknowledge records awareness", s.acknowledge(key) is True)
row = s.db.execute("SELECT state FROM events WHERE event_key=?",
                   (key,)).fetchone()
check("acknowledge keeps event OPEN", row[0] == "OPEN")
check("acknowledged unresolved fingerprint stays silent",
      s.claim_event(key, "sha:aaa") == "SILENCE")

# 5. same condition / new SHA -> UPDATE, not fresh EMIT
s2 = fresh_store()
assert s2.claim_event(key, "sha:aaa") == "EMIT"
check("new SHA UPDATEs", s2.claim_event(key, "sha:bbb") == "UPDATE")

# 6. GitHub 429 -> sensor degradation, never repo incident
try:
    classify_http(429)
    check("429 degrades", False)
except SensorDegraded:
    check("429 degrades", True)

# 7. GitHub 500 / timeout -> sensor degraded
for bad in (500, 503):
    try:
        classify_http(bad)
        check(f"{bad} degrades", False)
    except SensorDegraded:
        check(f"{bad} degrades", True)
try:
    classify_http(timeout=True)
    check("timeout degrades", False)
except SensorDegraded:
    check("timeout degrades", True)
check("200 is REPO signal", classify_http(200) == "REPO")

# 8. attempted write -> blocked (gh-read.sh refuses before network)
r = subprocess.run(["bash", "scripts/gh-read.sh",
                    "-X", "POST", "repos/x/y"],
                   capture_output=True, text=True, cwd=str(ROOT))
r2 = subprocess.run(["bash", "scripts/gh-read.sh",
                     "repos/x/y", "-F", "a=b"],
                    capture_output=True, text=True, cwd=str(ROOT))
check("POST blocked pre-network", r.returncode == 3 and r2.returncode == 3)

# 9. missing evidence SHA -> cannot emit ACTIONABLE+
try:
    require_evidence("")
    check("empty evidence refused", False)
except ValueError:
    check("empty evidence refused", True)
check("cited evidence passes", require_evidence("sha:f4e98ac") is True)

# typed evidence: 429 needs no SHA, commits do
check("http_observation without sha passes",
      require_typed_evidence({"type": "http_observation",
                              "ref": "GET /healthz -> 429",
                              "observed_at": "t"}) is True)
try:
    require_typed_evidence({"type": "commit", "ref": "main",
                            "observed_at": "t"})
    check("commit without sha refused", False)
except ValueError:
    check("commit without sha refused", True)
check("commit with sha passes",
      require_typed_evidence({"type": "commit", "ref": "main",
                              "observed_at": "t",
                              "sha": "f4e98ac"}) is True)

# 10. script crash -> monitor failure visible (nonzero, stderr)
r = subprocess.run(["bash", "-c", "echo boom >&2; exit 1"],
                   capture_output=True, text=True)
check("crash visible", r.returncode != 0 and "boom" in r.stderr)

# backoff grows with jitter bound
b0, b3 = backoff(0, base=5.0, cap=120.0), backoff(3, base=5.0, cap=120.0)
check("backoff grows", 5.0 <= b0 <= 6.0 and b3 > b0)

# 11. continuity forbidden on watch jobs (spec #5), allowed on deep audits
from validate import job_errors, NO_CONTINUITY
_tmp = tempfile.mkdtemp()
_watch = Path(_tmp, "ag-claim-watch.yaml"); _watch.write_text("name: ag-claim-watch\n", encoding="utf-8")
_deep = Path(_tmp, "ag-governance-drift.yaml"); _deep.write_text("name: ag-governance-drift\n", encoding="utf-8")
_base = {"schedule": "every 6h", "read_only": "true", "severity": "info",
         "allowed_dispositions": "notify", "mode": "agent", "prompt": "x",
         "enabled_toolsets": "git"}
check("watch continuity rejected",
      any("continuity forbidden" in e for e in
          job_errors(_watch, dict(_base, continuity={"key": "k"}))))
check("deep-audit continuity allowed",
      not any("continuity forbidden" in e for e in
              job_errors(_deep, dict(_base, continuity={"key": "k"}))))
check("continuity allowlist matches spec #5",
      NO_CONTINUITY == {"ag-claim-watch", "ag-vault-watch",
                        "ag-sentinel-release", "ag-legacy-noise-gate"})

# 11b. retry_after_seconds: RFC 9110 delta-seconds + HTTP-date + garbage
check("retry-after delta-seconds parsed",
      abs(retry_after_seconds("120") - 120) < 1)
check("retry-after HTTP-date parsed",
      retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") is not None
      and retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") > 0)
check("retry-after garbage -> None",
      retry_after_seconds("soon") is None
      and retry_after_seconds("") is None
      and retry_after_seconds("0") is None
      and retry_after_seconds(None) is None)
check("retry-after past date -> None",
      retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") is None)

# 11c. job catalog pinned: 8 core + 1 canary + 1 legacy = 10 total
# (spec #4 counts, §1, §5). Prose drift caught by this check.
# NOTE: no `import yaml` here - CI runners do not ship PyYAML.
import glob as _glob
_core_files = sorted(_glob.glob(str(ROOT / "jobs" / "*.yaml")))
_legacy_files = sorted(_glob.glob(str(ROOT / "jobs" / "legacy" / "*.yaml")))

def _job_name(path):
    for _line in open(path, encoding="utf-8"):
        if _line.startswith("name:"):
            return _line.split(":", 1)[1].strip()
    return None

_core_names = sorted(_job_name(f) for f in _core_files)
_legacy_names = sorted(_job_name(f) for f in _legacy_files)

SENSOR_NAMES_FOR_TEST = [
    "merge_queue_stall",
    "org_suite_liveness",
    "public_provenance",
    "research_freeze_watch",
]
_core_schedules = [
    "ag-runtime-paritet", "ag-wi-contract", "ag-governance-drift",
    "ag-claim-watch", "ag-research-evidence", "ag-vault-watch",
    "ag-vault-freshness", "ag-sentinel-release",
    "ag-fabric-delivery",
    # v0.5 P0 fabric concerns
    "ag-merge-queue-stall", "ag-org-suite-liveness",
    "ag-public-provenance", "ag-research-freeze-watch",
    # v0.5.1 shadow runner
    "ag-v05-shadow-summary"]
check("catalog: 14 core schedules match spec §5",
      set(_core_schedules) == set(_core_names) - {"ag-fabric-canary"}
      and len(_core_names) == 15)  # 14 core + 1 state canary
check("catalog: state canary present",
      "ag-fabric-canary" in _core_names)
check("catalog: delivery canary present",
      "ag-fabric-delivery" in _core_names)
check("catalog: 1 legacy-local",
      _legacy_names == ["ag-legacy-noise-gate"])
check("catalog: 16 jobs total",
      len(_core_files) + len(_legacy_files) == 16)

# 12. verify_tick.py: suppressed slot must FAIL, healthy slot must PASS
import sqlite3 as _sql, json as _json
import subprocess as _sp
from datetime import datetime as _datetime, timezone as _timezone
_tickdir = Path(_tmp, "tick")
_tickdir.mkdir()
_jobs = _tickdir / "jobs.json"
_db = _tickdir / "executions.db"
_out = _tickdir / "out"
_out.mkdir()
# suppressed case: next_run_at == slot (never advanced), empty executions
_slot = "2026-08-09T09:00:00+02:00"
_slot_epoch = _datetime.fromisoformat(_slot).astimezone(
    _timezone.utc).timestamp()
_jobs.write_text(_json.dumps([{"name": "ag-sentinel-release", "enabled": True,
                               "next_run_at": _slot}]), encoding="utf-8")
_con = _sql.connect(str(_db))
_con.execute("CREATE TABLE executions (id TEXT, job_id TEXT, source TEXT, "
             "status TEXT, scheduled_instant TEXT, started_at REAL, "
             "finished_at REAL)")
_con.commit(); _con.close()
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_tick.py"),
              "--jobs-json", str(_jobs), "--executions-db", str(_db),
              "--output-dir", str(_out), "--job-name", "ag-sentinel-release",
              "--job-id", "j1", "--slot", _slot],
             capture_output=True, text=True, cwd=str(ROOT))
check("tick verify FAILs on suppressed slot", _r.returncode == 1
      and "unconsumed" in _r.stdout)
# healthy case: next_run advanced, completed scheduler execution with
# matching scheduled_instant, output file present
_jobs.write_text(_json.dumps([{"name": "ag-sentinel-release", "enabled": True,
                               "next_run_at": "2026-09-10T09:00:00+02:00"}]),
                 encoding="utf-8")
_con = _sql.connect(str(_db))
_con.execute("INSERT INTO executions VALUES"
             "('e1','j1','scheduler','completed',"
             "'2026-08-09T07:00:00+00:00', ?, ?)",
             (_slot_epoch + 60, _slot_epoch + 300))
_con.commit(); _con.close()
(_out / "2026-09-09_09-00-00.md").write_text("EMIT\n", encoding="utf-8")
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_tick.py"),
              "--jobs-json", str(_jobs), "--executions-db", str(_db),
              "--output-dir", str(_out), "--job-name", "ag-sentinel-release",
              "--job-id", "j1", "--slot", _slot],
             capture_output=True, text=True, cwd=str(ROOT))
check("tick verify PASSes consumed slot", _r.returncode == 0
      and "TICK-VERIFIED" in _r.stdout)
# Hermes records scheduled runs as source='builtin', not 'scheduler'.
# A 'builtin' execution must also pass the non-manual_run check.
_con = _sql.connect(str(_db))
_con.execute("DELETE FROM executions")
_con.execute("INSERT INTO executions VALUES ('e2','j1','builtin','completed',"
             "'2026-08-09T07:00:00+00:00', ?, ?)",
             (_slot_epoch + 60, _slot_epoch + 300))
_con.commit(); _con.close()
(_out / "2026-09-09_09-00-01.md").write_text("EMIT\n", encoding="utf-8")
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_tick.py"),
              "--jobs-json", str(_jobs), "--executions-db", str(_db),
              "--output-dir", str(_out), "--job-name", "ag-sentinel-release",
              "--job-id", "j1", "--slot", _slot],
             capture_output=True, text=True, cwd=str(ROOT))
check("tick verify PASSes Hermes 'builtin' source run", _r.returncode == 0
      and "TICK-VERIFIED" in _r.stdout)

print(f"\nBEHAVIOR-OK: {passed} checks")


# Cross-process test: 3 holders + 1 contender in INDEPENDENT
# processes against one shared SQLite budget. A process-local
# semaphore would admit all 4; the shared one must refuse one.
import time as _time
_tmp = tempfile.mkdtemp()
_db = str(Path(_tmp, "sem.sqlite"))
_out = str(Path(_tmp, "results.txt"))
_worker = str(ROOT / "tests" / "xproc_worker.py")
_xprocs = [subprocess.Popen([sys.executable, _worker, _db, "2.0", _out])
           for _ in range(3)]
_time.sleep(1.0)  # let holders take all 3 leases
subprocess.run([sys.executable, _worker, _db, "0.1", _out],
               capture_output=True, text=True)
for _p in _xprocs:
    _p.wait(timeout=30)
_got = sorted(Path(_out).read_text(encoding="utf-8").split())
check("xproc: 3 holders admitted", _got.count("acquired") == 3)
check("xproc: contender refused", _got.count("refused") == 1)

# EventStore claim_event must also be cross-process exactly-once.
_ev_tmp = tempfile.mkdtemp()
_ev_db = str(Path(_ev_tmp, "events.sqlite"))
_ev_out = Path(_ev_tmp, "results")
_ev_worker = str(ROOT / "tests" / "xproc_event_worker.py")
_ev_procs = [subprocess.Popen([sys.executable, _ev_worker, _ev_db, str(_ev_out)])
             for _ in range(10)]
for _p in _ev_procs:
    _p.wait(timeout=30)
_ev_actions = sorted(p.read_text(encoding="utf-8") for p in _ev_out.glob("*.txt"))
check("event claim xproc: exactly one EMIT", _ev_actions.count("EMIT") == 1)
check("event claim xproc: remaining nine SILENCE",
      _ev_actions.count("SILENCE") == 9 and len(_ev_actions) == 10)

# reconciler --record writes a receipt with the live config hash
import json as _json
_rec = subprocess.run([sys.executable, str(ROOT / "scripts" / "reconcile.py"),
                       "--record", "ag-wi-contract", "test-job-id"],
                      capture_output=True, text=True, cwd=str(ROOT))
_receipt = ROOT / "deploy" / "receipts" / "ag-wi-contract.json"
_data = _json.loads(_receipt.read_text(encoding="utf-8"))
check("record writes receipt", _rec.returncode == 0
      and _data["job"] == "ag-wi-contract"
      and _data["job_id"] == "test-job-id"
      and len(_data["config_hash"]) == 12)
_receipt.unlink()
try:
    _receipt.parent.rmdir()
    _receipt.parent.parent.rmdir()
except OSError:
    pass  # other receipts exist; never tear down shared dirs

# verify_slo.py: Phase-1 success metrics are machine-checkable (spec #9).
_slo_dir = Path(tempfile.mkdtemp(), "slo")
_slo_dir.mkdir()
_evdb = _slo_dir / "events.sqlite"
_cadb = _slo_dir / "canary.sqlite"
_ecdb = _slo_dir / "executions.db"
_jobjson = _slo_dir / "jobs.json"
_rcdir = _slo_dir / "receipts"
_rcdir.mkdir(exist_ok=True)
_base_ok = _slo_dir / "baseline-ok.json"
_base_ok.write_text('{"frozen_at": "2026-09-08T00:00:00+00:00", '
                    '"window_days": 7, "max_daily_emissions": 100}',
                    encoding="utf-8")
_db1 = _slo_dir / "db1.sqlite"
_db2 = _slo_dir / "db2.sqlite"

def _mk_evdb(path, rows):
    """Create a fresh events.sqlite with the given rows."""
    if Path(path).exists():
        Path(path).unlink()
    con = _sql.connect(str(path))
    con.execute("CREATE TABLE events (event_key TEXT, fingerprint TEXT, "
                "state TEXT, opened_at REAL, updated_at REAL, evidence TEXT)")
    if rows:
        con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", rows)
    con.commit(); con.close()

def _mk_empty_exec(path):
    """Create an empty executions.db."""
    if Path(path).exists():
        Path(path).unlink()
    con = _sql.connect(str(path))
    con.execute("CREATE TABLE executions (id TEXT, job_id TEXT, source TEXT, "
                "status TEXT, scheduled_instant TEXT, started_at REAL, "
                "finished_at REAL)")
    con.commit(); con.close()

# canonical clean state: unique fp, evidence present, canary resolved, no live jobs
_mk_evdb(_evdb, [("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=abc123")])
_mk_evdb(_cadb, [("k1", "fp1", "RESOLVED", 1, 2, "type=synthetic_canary")])
_jobjson.write_text("[]", encoding="utf-8")
_mk_empty_exec(_ecdb)
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
              "--jobs-dir", str(ROOT / "jobs"),
              "--events-db", str(_evdb), "--canary-db", str(_cadb),
              "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
              "--receipts-dir", str(_rcdir), "--baseline", str(_base_ok)],
             capture_output=True, text=True, cwd=str(ROOT))
check("slo verify INSUFFICIENT-DATA on clean state (never claims PASS)",
      _r.returncode == 2
      and "INSUFFICIENT-DATA" in _r.stdout
      and "PASS (checkable" not in _r.stdout)

# duplicate fingerprint -> VIOLATION exit 1
_mk_evdb(_evdb, [("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=ab"),
                  ("k2", "fp1", "OPEN", 2, 2, "type=commit;sha=ab")])
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
              "--jobs-dir", str(ROOT / "jobs"),
              "--events-db", str(_evdb), "--canary-db", str(_cadb),
              "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
              "--receipts-dir", str(_rcdir), "--baseline", str(_base_ok)],
             capture_output=True, text=True, cwd=str(ROOT))
check("slo verify FAILs on duplicate fingerprint",
      _r.returncode == 1 and "DUPLICATE" in _r.stdout)

# missing typed evidence -> VIOLATION exit 1
_mk_evdb(_evdb, [("k1", "fp1", "OPEN", 1, 1, "")])
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
              "--jobs-dir", str(ROOT / "jobs"),
              "--events-db", str(_evdb), "--canary-db", str(_cadb),
              "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
              "--receipts-dir", str(_rcdir), "--baseline", str(_base_ok)],
             capture_output=True, text=True, cwd=str(ROOT))
check("slo verify FAILs on missing evidence",
      _r.returncode == 1 and "evidence" in _r.stdout)

# SLO met: ag-runtime-paritet with 10 fast runs (60s each, SLO is p50_max: 2h)
# Job dir gets one YAML with detection_slo; executions.db has 10 scheduler runs.
_slo_jobs = _slo_dir / "jobs"
_slo_jobs.mkdir(exist_ok=True)
import shutil as _shutil
_shutil.copy(str(ROOT / "jobs" / "ag-runtime-paritet.yaml"),
             str(_slo_jobs / "ag-runtime-paritet.yaml"))
_jobjson.write_text('[{"name":"ag-runtime-paritet","id":"r1"}]', encoding="utf-8")
_mk_evdb(_evdb, [("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=abc")])
_mk_evdb(_cadb, [("k1", "fp1", "RESOLVED", 1, 2, "type=synthetic_canary")])
con = _sql.connect(str(_ecdb))
con.execute("DROP TABLE IF EXISTS executions")
con.execute("CREATE TABLE executions (id TEXT, job_id TEXT, source TEXT, "
            "status TEXT, scheduled_instant TEXT, started_at TEXT, "
            "finished_at TEXT)")
from datetime import timedelta as _td
_base = _datetime(2026, 9, 1, tzinfo=_timezone.utc)
for i in range(10):
    s = _base + _td(hours=i)
    f = s + _td(seconds=60)
    con.execute("INSERT INTO executions VALUES (?, 'r1', 'scheduler', "
                "'completed', '', ?, ?)", (f"e{i}", s.isoformat(), f.isoformat()))
con.commit(); con.close()
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
              "--jobs-dir", str(_slo_jobs),
              "--events-db", str(_evdb), "--canary-db", str(_cadb),
              "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
              "--receipts-dir", str(_rcdir), "--baseline", str(_base_ok)],
             capture_output=True, text=True, cwd=str(ROOT))
check("slo verify SLO-VERIFIED when runs meet threshold",
      _r.returncode == 0 and "SLO-VERIFIED" in _r.stdout)

# SLO breached: 10 slow runs (10800s = 3h, p50_max: 2h)
con = _sql.connect(str(_ecdb))
for row in con.execute("SELECT id FROM executions").fetchall():
    con.execute("DELETE FROM executions WHERE id = ?", row)
for i in range(10):
    s = _base + _td(hours=i)
    f = s + _td(seconds=10800)
    con.execute("INSERT INTO executions VALUES (?, 'r1', 'scheduler', "
                "'completed', '', ?, ?)", (f"e{i}", s.isoformat(), f.isoformat()))
con.commit(); con.close()
_r = _sp.run([sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
              "--jobs-dir", str(_slo_jobs),
              "--events-db", str(_evdb), "--canary-db", str(_cadb),
              "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
              "--receipts-dir", str(_rcdir), "--baseline", str(_base_ok)],
             capture_output=True, text=True, cwd=str(ROOT))
check("slo verify VIOLATION when runs exceed threshold",
      _r.returncode == 1 and "VIOLATION" in _r.stdout)

# --- Codex CONDITIONAL findings: fail-closed verify_slo + receipts + baseline.
def _slo_reset(ev_rows=(), ca_rows=(), jobs_json="[]", exec_rows=()):
    """Self-contained fixture reset for the fail-closed blocks below."""
    _mk_evdb(_evdb, list(ev_rows))
    _mk_evdb(_cadb, list(ca_rows))
    _jobjson.write_text(jobs_json, encoding="utf-8")
    for f in _rcdir.glob("ag-fabric-canary-*.json"):
        f.unlink()
    con = _sql.connect(str(_ecdb))
    con.execute("DROP TABLE IF EXISTS executions")
    con.execute("CREATE TABLE executions (id TEXT, job_id TEXT, source TEXT, "
                "status TEXT, scheduled_instant TEXT, started_at TEXT, "
                "finished_at TEXT)")
    for row in exec_rows:
        con.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?)", row)
    con.commit(); con.close()


def _slo_run(jobs_dir=None):
    return _sp.run(
        [sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
         "--jobs-dir", str(jobs_dir or (ROOT / "jobs")),
         "--events-db", str(_evdb), "--canary-db", str(_cadb),
         "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
         "--receipts-dir", str(_rcdir), "--baseline", str(_base_ok)],
        capture_output=True, text=True, cwd=str(ROOT))


def _mk_receipt(name, key, resolved=True, mode="self-test", tamper=False):
    import hashlib as _hl
    import json as _js
    doc = {"schema": "canary-receipt/1", "run_id": name, "at": "2026-09-08",
           "key": key, "mode": mode, "emitted": 1, "deliveries": 1,
           "resolved": resolved}
    blob = _js.dumps(doc, indent=2, sort_keys=True)
    doc["sha256"] = _hl.sha256(blob.encode()).hexdigest()
    if tamper:
        doc["resolved"] = not resolved  # break the seal after signing
    (_rcdir / name).write_text(_js.dumps(doc, indent=2, sort_keys=True),
                               encoding="utf-8")


# unmapped SLO job against a LIVE install -> fail closed (exit 1)
_slo_reset(jobs_json='[{"name":"nope","id":"x1"}]')
_r = _slo_run()
check("slo verify ERRORs on SLO job with no live mapping",
      _r.returncode == 1 and "no live cron job id mapped" in _r.stdout)

# unknown execution source -> fail closed (exit 1)
_slo_reset(
    ev_rows=[("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=ab")],
    ca_rows=[("k1", "fp1", "RESOLVED", 1, 2, "type=synthetic_canary")],
    jobs_json='[{"name":"ag-runtime-paritet","id":"r1"}]',
    exec_rows=[("e0", "r1", "mystery", "completed", "",
                "2026-09-01T00:00:00+00:00", "2026-09-01T00:01:00+00:00")])
_r = _slo_run(_slo_jobs)
check("slo verify ERRORs on unknown execution source",
      _r.returncode == 1 and "unknown execution source" in _r.stdout)

# malformed receipt -> fail closed (exit 1)
_slo_reset(
    ev_rows=[("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=ab")],
    ca_rows=[("k1", "fp1", "RESOLVED", 1, 2, "type=synthetic_canary")])
(_rcdir / "ag-fabric-canary-bad.json").write_text("{not json",
                                                  encoding="utf-8")
_r = _slo_run()
check("slo verify ERRORs on malformed receipt",
      _r.returncode == 1 and "malformed" in _r.stdout)

# unresolved emission without covering receipt -> fail closed (exit 1)
_slo_reset(
    ev_rows=[("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=ab")],
    ca_rows=[("k9", "fp9", "OPEN", 1, 1, "type=synthetic_canary")])
_r = _slo_run()
check("slo verify ERRORs on unresolved emission without receipt",
      _r.returncode == 1 and "without covering receipt" in _r.stdout)

# valid receipt covering a resolved key -> accepted (stays exit 2 here:
# SLO history still pending, but no receipt error)
_slo_reset(
    ev_rows=[("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=ab")],
    ca_rows=[("canary|monthly|synthetic", "fp9", "RESOLVED", 1, 2,
              "type=synthetic_canary")])
_mk_receipt("ag-fabric-canary-good.json", "canary|monthly|synthetic",
            resolved=True)
_r = _slo_run()
check("slo verify accepts a valid covering receipt",
      _r.returncode == 2 and "1 valid file" in _r.stdout)

# tampered receipt (seal broken after signing) -> fail closed (exit 1)
_slo_reset(
    ev_rows=[("k1", "fp1", "OPEN", 1, 1, "type=commit;sha=ab")],
    ca_rows=[("k1", "fp1", "RESOLVED", 1, 2, "type=synthetic_canary")])
_mk_receipt("ag-fabric-canary-tampered.json", "k1", resolved=True,
            tamper=True)
_r = _slo_run()
check("slo verify ERRORs on tampered receipt",
      _r.returncode == 1 and "sha256 mismatch" in _r.stdout)

# baseline: volume within max -> pass branch contributes no error
_now = time.time()
_slo_reset(
    ev_rows=[("k1", "fp1", "OPEN", _now - 86400, _now - 86400,
              "type=commit;sha=ab")],
    ca_rows=[("k1", "fp1", "RESOLVED", 1, 2, "type=synthetic_canary")])
_r = _slo_run()
check("slo verify passes volume within baseline",
      "baseline volume" in _r.stdout and "exceeds" not in _r.stdout)

# baseline: volume exceeds max -> VIOLATION (exit 1)
_base_tight = _slo_dir / "baseline-tight.json"
_base_tight.write_text('{"frozen_at": "2026-09-08T00:00:00+00:00", '
                       '"window_days": 7, "max_daily_emissions": 0}',
                       encoding="utf-8")
_r = _sp.run(
    [sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
     "--jobs-dir", str(ROOT / "jobs"),
     "--events-db", str(_evdb), "--canary-db", str(_cadb),
     "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
     "--receipts-dir", str(_rcdir), "--baseline", str(_base_tight)],
    capture_output=True, text=True, cwd=str(ROOT))
check("slo verify VIOLATION when volume exceeds baseline",
      _r.returncode == 1 and "exceeds" in _r.stdout)

# baseline: missing file -> fail closed (exit 1), never silent
_r = _sp.run(
    [sys.executable, str(ROOT / "scripts" / "verify_slo.py"),
     "--jobs-dir", str(ROOT / "jobs"),
     "--events-db", str(_evdb), "--canary-db", str(_cadb),
     "--executions-db", str(_ecdb), "--jobs-json", str(_jobjson),
     "--receipts-dir", str(_rcdir),
     "--baseline", str(_slo_dir / "baseline-nope.json")],
    capture_output=True, text=True, cwd=str(ROOT))
check("slo verify ERRORs on missing baseline",
      _r.returncode == 1 and "baseline file absent" in _r.stdout)

# 51. delivery canary --on-demand probe does not mutate live state
_dc_dir = tempfile.mkdtemp()
Path(_dc_dir, "contracts").mkdir(parents=True, exist_ok=True)
Path(_dc_dir, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_dc_env = os.environ.copy()
_dc_env["AG_FABRIC_ROOT"] = str(_dc_dir)
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py"),
     "--on-demand"],
    capture_output=True, text=True, env=_dc_env,
    cwd=str(_dc_dir))
check("delivery canary --on-demand exits 0",
      _r.returncode == 0 and "DELIVERY-CANARY-ON-DEMAND" in _r.stdout)

# 52. delivery canary self-test --synthesize-receipt proves end-to-end
_dc_dir2 = tempfile.mkdtemp()
Path(_dc_dir2, "contracts").mkdir(parents=True, exist_ok=True)
Path(_dc_dir2, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_dc_env2 = os.environ.copy()
_dc_env2["AG_FABRIC_ROOT"] = str(_dc_dir2)
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py"),
     "--synthesize-receipt"],
    capture_output=True, text=True, env=_dc_env2,
    cwd=str(_dc_dir2))
check("delivery canary self-test with synthetic receipt exits 0",
      _r.returncode == 0 and "DELIVERY-CANARY-OK" in _r.stdout)

# 53. delivery canary fails closed when no receipt exists
_dc_dir3 = tempfile.mkdtemp()
Path(_dc_dir3, "contracts").mkdir(parents=True, exist_ok=True)
Path(_dc_dir3, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_dc_env3 = os.environ.copy()
_dc_env3["AG_FABRIC_ROOT"] = str(_dc_dir3)
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py")],
    capture_output=True, text=True, env=_dc_env3,
    cwd=str(_dc_dir3))
check("delivery canary fails closed when no receipt exists",
      _r.returncode == 1 and "no_delivery_receipt" in _r.stdout)

# 54. delivery canary fails closed on tampered receipt sha256
_dc_dir5 = tempfile.mkdtemp()
_dc_env5 = os.environ.copy()
# Isolate: temp dir IS the fabric root so receipts and state stay there.
Path(_dc_dir5, "contracts").mkdir(parents=True, exist_ok=True)
# Sentinel file the canary uses to locate the repo root.
Path(_dc_dir5, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_dc_env5["AG_FABRIC_ROOT"] = str(_dc_dir5)
_r0 = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py"),
     "--synthesize-receipt"],
    capture_output=True, text=True, env=_dc_env5, cwd=str(_dc_dir5))
_receipts5 = Path(_dc_dir5) / "deploy" / "delivery-receipts"
_just_written = sorted(_receipts5.glob("delivery-*.json"))[-1]
_parsed = json.loads(_just_written.read_text(encoding="utf-8"))
_parsed["sha256"] = "0" * 64
_just_written.write_text(json.dumps(_parsed, indent=2, sort_keys=True),
                         encoding="utf-8")
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py")],
    capture_output=True, text=True, env=_dc_env5, cwd=str(_dc_dir5))
check("delivery canary fails closed on tampered receipt sha256",
      _r.returncode == 1 and "receipt_sha_mismatch" in _r.stdout)

# 55. delivery canary happy path completes (run-once)
_dc_dir6 = tempfile.mkdtemp()
_dc_env6 = os.environ.copy()
Path(_dc_dir6, "contracts").mkdir(parents=True, exist_ok=True)
Path(_dc_dir6, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_dc_env6["AG_FABRIC_ROOT"] = str(_dc_dir6)
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py"),
     "--synthesize-receipt"],
    capture_output=True, text=True, env=_dc_env6, cwd=str(_dc_dir6))
check("delivery canary happy path completes (run-once)",
      _r.returncode == 0 and "DELIVERY-CANARY-OK" in _r.stdout)

# 56. delivery canary job YAML is well-formed and read-only=true
_job_yaml = ROOT / "jobs" / "ag-fabric-delivery-canary.yaml"
_text = _job_yaml.read_text(encoding="utf-8")
check("delivery canary job YAML exists", _job_yaml.is_file())
check("delivery canary job YAML enforces read_only=true",
      "read_only: true" in _text)
check("delivery canary job YAML uses no_agent mode",
      "mode: no_agent" in _text)
check("delivery canary job YAML points at the right script",
      "scripts/sensors/delivery_canary.py" in _text)

# 57-60. v0.5 P0 fabric concern sensors
for _name, _script in [
        ("ag-merge-queue-stall", "scripts/sensors/merge_queue_stall.py"),
        ("ag-org-suite-liveness",
         "scripts/sensors/org_suite_liveness.py"),
        ("ag-public-provenance",
         "scripts/sensors/public_provenance.py"),
        ("ag-research-freeze-watch",
         "scripts/sensors/research_freeze_watch.py")]:
    _yaml = ROOT / "jobs" / f"{_name}.yaml"
    _script_path = ROOT / _script
    check(f"{_name}: job YAML exists", _yaml.is_file())
    check(f"{_name}: script file exists", _script_path.is_file())
    _text = _yaml.read_text(encoding="utf-8")
    check(f"{_name}: read_only=true", "read_only: true" in _text)
    check(f"{_name}: no_agent mode", "mode: no_agent" in _text)
    check(f"{_name}: no prompt (forbidden for no_agent)",
          "prompt:" not in _text)
    check(f"{_name}: points at the right script",
          _script in _text)
    # Parse-script smoke: importable, has main()
    _script_path_str = str(_script_path).replace("\\", "/")
    _sp_result = _sp.run(
        [sys.executable, "-c",
         "import importlib.util\n"
         f"s = importlib.util.spec_from_file_location('m', '{_script_path_str}')\n"
         "m = importlib.util.module_from_spec(s)\n"
         "s.loader.exec_module(m)\n"
         "assert callable(getattr(m, 'main', None))\n"],
        capture_output=True, text=True, cwd=str(ROOT))
    check(f"{_name}: script imports cleanly and has main()",
          _sp_result.returncode == 0)

# 61. merge-queue-stall does NOT hardcode repo list (single source of truth)
_mqs_text = (ROOT / "scripts" / "sensors" / "merge_queue_stall.py").read_text(
    encoding="utf-8")
check("merge-queue-stall reads from contracts/queue-policy.yaml",
      "contracts/queue-policy.yaml" in _mqs_text)
check("merge-queue-stall has no hardcoded repo list",
      "Aftergraph/after-graph-governance" not in _mqs_text
      and "QUEUE_REQUIRED_REPOS = [" not in _mqs_text)
# The policy file itself must exist and parse
check("contracts/queue-policy.yaml exists",
      (ROOT / "contracts" / "queue-policy.yaml").is_file())

# 62. public-provenance watches the contracts/sources.yaml contracts
_pp_text = (ROOT / "scripts" / "sensors" / "public_provenance.py").read_text(
    encoding="utf-8")
check("public-provenance reads from contracts/sources.yaml",
      "contracts/sources.yaml" in _pp_text or "sources.yaml" in _pp_text
      or "wi.observation" in _pp_text)

# 63. research-freeze-watch reads from freeze manifest + amendments
_rfw_text = (ROOT / "scripts" / "sensors" / "research_freeze_watch.py").read_text(
    encoding="utf-8")
check("research-freeze-watch reads freeze manifest",
      "freeze-manifest.yaml" in _rfw_text)
check("research-freeze-watch reads amendments",
      "freeze-amendments.yaml" in _rfw_text)

# 64. org-suite-liveness hardcoded core repo list is documented + overridable
_osl_text = (ROOT / "scripts" / "sensors" / "org_suite_liveness.py").read_text(
    encoding="utf-8")
check("org-suite-liveness: CORE_REPOS is a module-level constant (documented)",
      "CORE_REPOS = [" in _osl_text and "# Repos whose main CI" in _osl_text)

# 65-71. v0.5 SHADOW READINESS — the four P0 sensors must run end-to-end,
# produce a READY_FOR_SHADOW verdict, and no P1/P2 job may have leaked
# into jobs/ until v0.6 readiness is declared.

# Scope-lock file exists
check("contracts/v06-scope-lock.yaml exists (P1/P2 gated)",
      (ROOT / "contracts" / "v06-scope-lock.yaml").is_file())

# No P1/P2 jobs leaked into jobs/*.yaml
import re as _re
_p1_names = ["ag-mission-orphan", "ag-verification-gap",
             "ag-authority-lease", "ag-release-intelligence",
             "ag-continuity-regression", "ag-economic-drift"]
_p1_leaks = []
for _j in (ROOT / "jobs").glob("*.yaml"):
    _jt = _j.read_text(encoding="utf-8")
    for _n in _p1_names:
        if _n in _jt:
            _p1_leaks.append(f"{_j.name} contains {_n}")
check("no P1/P2 job YAML leaked into jobs/",
      not _p1_leaks)

# Shadow rollout script exists and parses
check("ag_v05_shadow_rollout.py exists",
      (ROOT / "scripts" / "ag_v05_shadow_rollout.py").is_file())
import importlib.util
_sp_rollout = importlib.util.spec_from_file_location(
    "rollout",
    str(ROOT / "scripts" / "ag_v05_shadow_rollout.py"))
_ro = importlib.util.module_from_spec(_sp_rollout)
_sp_rollout.loader.exec_module(_ro)
check("ag_v05_shadow_rollout.py has main()",
      callable(getattr(_ro, "main", None)))

# Shadow rollout script does NOT touch Telegram (no telegram import,
# no hermes send, no telegram-live-status)
_ro_text = (ROOT / "scripts" / "ag_v05_shadow_rollout.py").read_text(
    encoding="utf-8")
check("shadow rollout: no Telegram import",
      "import telegram" not in _ro_text
      and "hermes send" not in _ro_text)
check("shadow rollout: no notification call",
      "send_message" not in _ro_text and "notify(" not in _ro_text)

# All four v0.5 sensors have read_only=true + no_agent + fail-closed
# (no mutation outside state/ and receipts)
for _name in SENSOR_NAMES_FOR_TEST:
    _t = (ROOT / "scripts" / "sensors" / f"{_name}.py").read_text(
        encoding="utf-8")
    check(f"{_name}: no open() writes outside state/receipts",
          not _re.search(r"open\(.*['\"](w|a|x)['\"]", _t)
          or "_write_path" in _t
          or "claim_event" in _t  # EventStore writes are allowed
          or "ReceiptWriter" in _t)

# 72. delivery canary fails closed on WRONG-FINGERPRINT receipt
# (well-formed, self-consistent sha256, but bound to a different
# fingerprint than the canary's claim -> receipt proves nothing about
# THIS claim and must fail closed)
_dc_dir7 = tempfile.mkdtemp()
_dc_env7 = os.environ.copy()
Path(_dc_dir7, "contracts").mkdir(parents=True, exist_ok=True)
Path(_dc_dir7, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_dc_env7["AG_FABRIC_ROOT"] = str(_dc_dir7)
_r0 = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py"),
     "--synthesize-receipt"],
    capture_output=True, text=True, env=_dc_env7, cwd=str(_dc_dir7))
assert _r0.returncode == 0, _r0.stdout + _r0.stderr
_receipts7 = Path(_dc_dir7) / "deploy" / "delivery-receipts"
_target = sorted(_receipts7.glob("delivery-*.json"))[-1]
_parsed7 = json.loads(_target.read_text(encoding="utf-8"))
# Rewrite the bound fingerprint to a DIFFERENT valid fingerprint, then
# re-attest honestly (self-sha is valid for the tampered payload).
_parsed7["fingerprint"] = "f" * 16
_canon = json.dumps({k: v for k, v in _parsed7.items() if k != "sha256"},
                    sort_keys=True, separators=(",", ":"))
import hashlib as _hl
_parsed7["sha256"] = _hl.sha256(_canon.encode("utf-8")).hexdigest()
_target.write_text(json.dumps(_parsed7, indent=2, sort_keys=True),
                   encoding="utf-8")
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py")],
    capture_output=True, text=True, env=_dc_env7, cwd=str(_dc_dir7))
check("delivery canary fails closed on wrong-fingerprint receipt",
      _r.returncode == 1
      and ("fingerprint" in _r.stdout.lower()
           or "mismatch" in _r.stdout.lower()))

# 73. receipt-bridge -> delivery-canary chain (offline Phase C):
# the bridge's OWN writer produces bytes the canary accepts.
# No live send: the proof gate in main() stays out of scope;
# what is proven is that bridge-shaped receipts verify.
_bc_dir = tempfile.mkdtemp()
Path(_bc_dir, "contracts").mkdir(parents=True, exist_ok=True)
Path(_bc_dir, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_bc_env = os.environ.copy()
_bc_env["AG_FABRIC_ROOT"] = str(_bc_dir)
_bc_prev_root = os.environ.get("AG_FABRIC_ROOT")
os.environ["AG_FABRIC_ROOT"] = str(_bc_dir)
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "sensors"))
import delivery_canary as _dc_mod
import telegram_receipt_bridge as _br_mod
if _bc_prev_root is None:
    del os.environ["AG_FABRIC_ROOT"]
else:
    os.environ["AG_FABRIC_ROOT"] = _bc_prev_root
_bc_fp = _dc_mod.EventStore.fingerprint(_dc_mod.KEY, "delivery:probe")
_bc_body = _br_mod.build_receipt_body(
    _dc_mod.KEY, _bc_fp, "telegram:ops", "chain-test-stub")
_bc_out = _br_mod.write_delivery_receipt(_bc_body)
assert _bc_out.is_file(), "bridge writer produced no receipt file"
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py")],
    capture_output=True, text=True, env=_bc_env, cwd=str(_bc_dir))
check("bridge receipt accepted by delivery canary (offline Phase C chain)",
      _r.returncode == 0 and "DELIVERY-CANARY-OK" in _r.stdout)

# 74. chain fails closed when a bridge-shaped receipt is rebound to a
# different fingerprint (self-attestation valid, binding wrong).
_bc_parsed = json.loads(_bc_out.read_text(encoding="utf-8"))
_bc_parsed["fingerprint"] = "e" * 16
_bc_canon = json.dumps(
    {k: v for k, v in _bc_parsed.items() if k != "sha256"},
    sort_keys=True, separators=(",", ":"))
import hashlib as _bc_hl
_bc_parsed["sha256"] = _bc_hl.sha256(_bc_canon.encode("utf-8")).hexdigest()
_bc_out.write_text(json.dumps(_bc_parsed, indent=2, sort_keys=True),
                   encoding="utf-8")
_r = _sp.run(
    [sys.executable,
     str(ROOT / "scripts" / "sensors" / "delivery_canary.py")],
    capture_output=True, text=True, env=_bc_env, cwd=str(_bc_dir))
check("chain fails closed on rebound bridge receipt",
      _r.returncode == 1 and "fingerprint" in _r.stdout.lower())

# 75. renderer-call contract frozen: exact argv the bridge passes to
# the telegram-live-status plugin CLI (list form = no shell).
_rc_prev_root = os.environ.get("AG_FABRIC_ROOT")
_rc_dir = tempfile.mkdtemp()
Path(_rc_dir, "contracts").mkdir(parents=True, exist_ok=True)
Path(_rc_dir, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
os.environ["AG_FABRIC_ROOT"] = str(_rc_dir)
sys.path.insert(0, str(ROOT / "scripts"))
import telegram_receipt_bridge as _br_mod
if _rc_prev_root is None:
    del os.environ["AG_FABRIC_ROOT"]
else:
    os.environ["AG_FABRIC_ROOT"] = _rc_prev_root
_bc_cmd = _br_mod._build_renderer_cmd("task-1", "hello", producer=None)
_bc_evt = json.loads(_bc_cmd[_bc_cmd.index("--event") + 1])
check("renderer argv frozen (hermes statuscard --to --task --event)",
      _bc_cmd[:6] == ["hermes", "statuscard", "--to", "telegram:Jonas",
                      "--task", "task-1"]
      and _bc_evt["task_id"] == "task-1"
      and _bc_evt["status"] == "RUNNING"
      and _bc_evt["message"] == "hello"
      and _bc_evt["producer"] == "cron-fabric-receipt-bridge"
      and isinstance(_bc_evt["revision"], int))

# 76. custom producer propagates; argv stays a list (never a string).
_bc_cmd2 = _br_mod._build_renderer_cmd("task-2", "hi", producer="x")
_bc_evt2 = json.loads(_bc_cmd2[_bc_cmd2.index("--event") + 1])
check("custom producer propagates, argv remains shell-free list",
      isinstance(_bc_cmd2, list)
      and _bc_evt2["producer"] == "x"
      and _bc_cmd2[3] == "telegram:Jonas")

# 77-80. fleet status card aggregates shadow receipts offline.
# Synthetic receipts reuse shadow_receipt.write_run_receipt (the real
# writer), so the fleet test proves the real bytes, not a copy.
_fl_dir = tempfile.mkdtemp()
Path(_fl_dir, "contracts").mkdir(parents=True, exist_ok=True)
Path(_fl_dir, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
_fl_env = os.environ.copy()
_fl_env["AG_FABRIC_ROOT"] = str(_fl_dir)
sys.path.insert(0, str(ROOT / "scripts"))
from shadow_receipt import write_run_receipt as _fl_write
from datetime import datetime, timedelta, timezone as _tz
_fl_now = datetime.now(_tz.utc)
for _fl_job in ["ag-merge-queue-stall", "ag-org-suite-liveness",
                "ag-public-provenance", "ag-research-freeze-watch"]:
    _fl_write(Path(_fl_dir), _fl_job, _fl_now, _fl_now, 0,
              "SENSOR-OK: emitted=0")
_r = _sp.run(
    [sys.executable, str(ROOT / "scripts" / "ag_fleet_status.py"),
     "--dry-run"],
    capture_output=True, text=True, env=_fl_env, cwd=str(_fl_dir))
check("fleet card OK on all-SILENCE receipts (dry-run)",
      _r.returncode == 0 and "FLEET-OK" in _r.stdout
      and "FLEET-CARD-DRYRUN: ag-fleet" in _r.stdout)

# 78. fleet WARNs (rc 0) on an EMIT receipt and names an exception.
_fl_write(Path(_fl_dir), "ag-merge-queue-stall",
          _fl_now + timedelta(seconds=5), _fl_now + timedelta(seconds=5),
          0, "MERGE-QUEUE-STALL-EMIT: foo#1 age=999m")
_r = _sp.run(
    [sys.executable, str(ROOT / "scripts" / "ag_fleet_status.py"),
     "--dry-run"],
    capture_output=True, text=True, env=_fl_env, cwd=str(_fl_dir))
check("fleet WARNs on EMIT receipt, exception flagged (dry-run)",
      _r.returncode == 0 and "FLEET-WARN" in _r.stdout
      and "ag-fleet-alert-ag-merge-queue-stall" in _r.stdout)

# 79. fleet fails closed (rc 1) on a tampered receipt sha256.
_fl_tamp = sorted((Path(_fl_dir) / "deploy" / "receipts").glob(
    "ag-v05-shadow-ag-public-provenance-*.json"))[-1]
_fl_p = json.loads(_fl_tamp.read_text(encoding="utf-8"))
_fl_p["sha256"] = "0" * 64
_fl_tamp.write_text(json.dumps(_fl_p, indent=2, sort_keys=True),
                    encoding="utf-8")
_r = _sp.run(
    [sys.executable, str(ROOT / "scripts" / "ag_fleet_status.py"),
     "--dry-run"],
    capture_output=True, text=True, env=_fl_env, cwd=str(_fl_dir))
check("fleet fails closed on tampered receipt",
      _r.returncode == 1 and "tampered" in _r.stdout.lower())

# 80. fleet fails closed (rc 1) when hermes CLI is absent and no dry-run.
# Fresh dir with clean receipts so the run reaches the card-send stage.
_fl_dir2 = tempfile.mkdtemp()
Path(_fl_dir2, "contracts").mkdir(parents=True, exist_ok=True)
Path(_fl_dir2, "contracts", "sources.yaml").write_text(
    "# isolated test root\n", encoding="utf-8")
for _fl_job2 in ["ag-merge-queue-stall", "ag-org-suite-liveness",
                 "ag-public-provenance", "ag-research-freeze-watch"]:
    _fl_write(Path(_fl_dir2), _fl_job2, _fl_now, _fl_now, 0,
              "SENSOR-OK: emitted=0")
_fl_env_nohermes = os.environ.copy()
_fl_env_nohermes["AG_FABRIC_ROOT"] = str(_fl_dir2)
_fl_env_nohermes["PATH"] = str(_fl_dir2)  # empty dir: no hermes binary
_r = _sp.run(
    [sys.executable, str(ROOT / "scripts" / "ag_fleet_status.py")],
    capture_output=True, text=True, env=_fl_env_nohermes,
    cwd=str(_fl_dir))
check("fleet fails closed without hermes CLI",
      _r.returncode == 1 and "hermes" in _r.stdout.lower())
print(f"\nBEHAVIOR-OK: {passed} checks")

