"""Tests for scripts/shadow_receipt.py (Phase B per-run receipts)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from shadow_receipt import (classify_output, count_emit_lines,
                            verify_receipt, write_run_receipt)


def _run(tmp_path, job="ag-merge-queue-stall", rc=0, out=""):
    t0 = datetime(2026, 9, 8, 21, 47, 16, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 8, 21, 47, 29, tzinfo=timezone.utc)
    return write_run_receipt(tmp_path, job, t0, t1, rc, out)


def test_silence_receipt(tmp_path):
    out = ("MERGE-QUEUE-STALL-OK: checked=12 emitted=0 resolved=0\n")
    p = _run(tmp_path, out=out)
    assert p.name.startswith("ag-v05-shadow-ag-merge-queue-stall-")
    assert verify_receipt(p)


def test_emit_classification(tmp_path):
    out = ("MERGE-QUEUE-STALL-EMIT: org/repo#7 age=95.2m\n"
           "MERGE-QUEUE-STALL-OK: checked=12 emitted=1 resolved=0\n")
    p = _run(tmp_path, out=out)
    assert classify_output(0, out) == "EMIT"
    assert count_emit_lines(out) == 1
    assert verify_receipt(p)


def test_crash_classification(tmp_path):
    assert classify_output(2, "") == "CRASH"
    p = _run(tmp_path, rc=2, out="traceback ...")
    assert verify_receipt(p)


def test_degraded_classification(tmp_path):
    out = "MERGE-QUEUE-STALL-SKIP: org/repo: gh api unavailable\n"
    assert classify_output(0, out) == "DEGRADED"


def test_receipt_fields(tmp_path):
    import json
    p = _run(tmp_path, out="MERGE-QUEUE-STALL-OK: checked=1 emitted=0\n")
    r = json.loads(p.read_text(encoding="utf-8"))
    assert r["schema"] == "shadow-run-receipt/1"
    assert r["agent_invoked"] is False
    assert r["mutation_attempts"] == 0
    assert r["duration_s"] == 13.0
    assert "sha256" in r


def test_tamper_fails_closed(tmp_path):
    p = _run(tmp_path, out="MERGE-QUEUE-STALL-OK: checked=1 emitted=0\n")
    blob = p.read_text(encoding="utf-8").replace("SILENCE", "EMIT!!")
    p.write_text(blob, encoding="utf-8")
    assert not verify_receipt(p)
