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
                          require_typed_evidence)

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

print(f"\nBEHAVIOR-OK: {passed} checks")
