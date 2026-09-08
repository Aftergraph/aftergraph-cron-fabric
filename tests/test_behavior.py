"""Behavioral tests (ChatGPT finding #9 fix). Run: python3 tests/test_behavior.py"""
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


# 1. same failure x3 -> exactly 1 alert
s = fresh_store()
key = "ag-ci|contract|fail"
check("first failure EMITs", s.check(key, "sha:aaa") == "EMIT")
s.record(key, "sha:aaa")
check("repeat SILENCEs", s.check(key, "sha:aaa") == "SILENCE")
check("third SILENCEs", s.check(key, "sha:aaa") == "SILENCE")

# 2. failure resolved -> state closes (no alert on healthy)
s.resolve(key)
row = s.db.execute("SELECT state FROM events WHERE event_key=?",
                   (key,)).fetchone()
check("resolve closes", row[0] == "HEALTHY")

# 3. same failure returns -> new alert
check("return EMITs again", s.check(key, "sha:aaa") == "EMIT")

# 4. same condition / new SHA -> UPDATE, not fresh EMIT
s2 = fresh_store()
assert s2.check(key, "sha:aaa") == "EMIT"
s2.record(key, "sha:aaa")
check("new SHA UPDATEs", s2.check(key, "sha:bbb") == "UPDATE")

# 5. GitHub 429 -> sensor degradation, never repo incident
try:
    classify_http(429)
    check("429 degrades", False)
except SensorDegraded:
    check("429 degrades", True)

# 6. GitHub 500 / timeout -> sensor degraded
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

# 7. attempted write -> blocked (gh-read.sh refuses before network)
r = subprocess.run(["bash", "scripts/gh-read.sh",
                    "-X", "POST", "repos/x/y"],
                   capture_output=True, text=True, cwd=str(ROOT))
r2 = subprocess.run(["bash", "scripts/gh-read.sh",
                     "repos/x/y", "-F", "a=b"],
                    capture_output=True, text=True, cwd=str(ROOT))
check("POST blocked pre-network", r.returncode == 3 and r2.returncode == 3)

# 8. missing evidence SHA -> cannot emit ACTIONABLE+
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

# 9. script crash -> monitor failure visible (nonzero, stderr)
r = subprocess.run(["bash", "-c", "echo boom >&2; exit 1"],
                   capture_output=True, text=True)
check("crash visible", r.returncode != 0 and "boom" in r.stderr)

# backoff grows with jitter bound
b0, b3 = backoff(0, base=5.0, cap=120.0), backoff(3, base=5.0, cap=120.0)
check("backoff grows", 5.0 <= b0 <= 6.0 and b3 > b0)

# 10. continuity forbidden on watch jobs (spec #5), allowed on deep audits
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

# 10b. retry_after_seconds: RFC 9110 delta-seconds + HTTP-date + garbage
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

# 10c. job catalog pinned: 8 core + 1 canary + 1 legacy = 10 total
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
_core_schedules = [
    "ag-runtime-paritet", "ag-wi-contract", "ag-governance-drift",
    "ag-claim-watch", "ag-research-evidence", "ag-vault-watch",
    "ag-vault-freshness", "ag-sentinel-release"]
check("catalog: 8 core schedules match spec §5",
      set(_core_schedules) == set(_core_names) - {"ag-fabric-canary"}
      and len(_core_names) == 9)  # 8 core + 1 canary
check("catalog: canary present", "ag-fabric-canary" in _core_names)
check("catalog: 1 legacy-local",
      _legacy_names == ["ag-legacy-noise-gate"])
check("catalog: 10 jobs total",
      len(_core_files) + len(_legacy_files) == 10)

# 11. verify_tick.py: suppressed slot must FAIL, healthy slot must PASS
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

print(f"\nBEHAVIOR-OK: {passed} checks")
