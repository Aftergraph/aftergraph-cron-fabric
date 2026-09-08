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


def _seed(tmp_path, job, n, now, out="MERGE-QUEUE-STALL-OK: checked=1\n"):
    from datetime import timedelta
    from shadow_receipt import write_run_receipt
    for i in range(n):
        end = now - timedelta(minutes=5 * i + 1)
        start = end - timedelta(seconds=3)
        write_run_receipt(tmp_path, job, start, end, 0, out)


def test_summary_empty_is_incomplete(tmp_path):
    from v05_shadow_summary import summarize
    now = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)
    rep = summarize(tmp_path, now)
    assert rep["schema"] == "v05-shadow-summary/2"
    assert rep["aggregate"]["verdict"] == "INCOMPLETE"
    assert any("no per-run receipts" in r
               for r in rep["aggregate"]["verdict_reasons"])


def test_summary_full_day_complete(tmp_path):
    from v05_shadow_summary import summarize
    now = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
    # Seed a full 24h-equivalent spread ending just inside the window.
    from datetime import timedelta
    from shadow_receipt import write_run_receipt
    jobs = {"ag-merge-queue-stall": 12, "ag-org-suite-liveness": 4,
            "ag-public-provenance": 4, "ag-research-freeze-watch": 4}
    for job, n in jobs.items():
        interval = (24 * 3600) // n
        for i in range(n):
            end = now - timedelta(seconds=interval * i + 60)
            start = end - timedelta(seconds=3)
            write_run_receipt(tmp_path, job, start, end, 0,
                              "MERGE-QUEUE-STALL-OK: checked=1\n")
    rep = summarize(tmp_path, now)
    agg = rep["aggregate"]
    # Prorated expectation: earliest seed defines shadow start, so
    # expected <= observed; nothing missing -> COMPLETE.
    assert agg["total_observed"] == 24, agg
    assert agg["verdict"] == "COMPLETE", agg
    assert sum(s["missing_ticks"]
               for s in rep["sensors"].values()) == 0, agg


def test_summary_tamper_incomplete(tmp_path):
    import json
    from v05_shadow_summary import summarize
    now = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)
    _seed(tmp_path, "ag-merge-queue-stall", 2, now)
    # Corrupt one receipt's payload (parses, but sha fails).
    first = sorted((tmp_path / "deploy" / "receipts")
                   .glob("ag-v05-shadow-*.json"))[0]
    parsed = json.loads(first.read_text(encoding="utf-8"))
    parsed["classification"] = "EMIT"
    first.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    rep = summarize(tmp_path, now)
    agg = rep["aggregate"]
    assert agg["unverified_receipts"] == 1, agg
    assert agg["verdict"] == "INCOMPLETE", agg


def test_summary_short_window_partial(tmp_path):
    from v05_shadow_summary import summarize
    now = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)
    _seed(tmp_path, "ag-merge-queue-stall", 1, now)
    rep = summarize(tmp_path, now)
    agg = rep["aggregate"]
    # Nothing missing, but the window is minutes old: PARTIAL,
    # never COMPLETE (shadow start day must not pass as full day).
    assert agg["verdict"] == "PARTIAL", agg
