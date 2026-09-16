import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "adapters"))

from hermes_goal_watchdog import normalize_goal_observation


def sample(idle=31, status="running"):
    return {
        "session_id": "goal-123",
        "status": status,
        "idle_minutes": idle,
        "observed_at": "2026-09-16T18:00:00Z",
        "source_ref": "hermes:state-meta:goal:goal-123",
    }


def test_normalize_stalled_goal():
    occ = normalize_goal_observation(sample(31))
    assert occ.subject_ref == "mission:goal-123"
    assert "idle_minutes:31" in occ.evidence_refs
    assert occ.trigger_id == "mission.continuity.observed"


def test_normalize_active_goal():
    occ = normalize_goal_observation(sample(3))
    assert "idle_minutes:3" in occ.evidence_refs


def test_missing_session_fails_closed():
    data = sample()
    del data["session_id"]
    try:
        normalize_goal_observation(data)
    except ValueError as exc:
        assert "session_id" in str(exc)
    else:
        raise AssertionError("missing session accepted")


def test_cli_writes_shadow_receipt_only():
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "goal.json"
        out = Path(tmp) / "receipts"
        inp.write_text(json.dumps(sample(31)), encoding="utf-8")
        run = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "trigger_shadow.py"),
             "--input", str(inp), "--output-dir", str(out),
             "--prior-state", "SUSPECTED_STALL"],
            capture_output=True, text=True, cwd=str(ROOT))
        assert run.returncode == 0, run.stdout + run.stderr
        docs = list(out.glob("*.json"))
        assert len(docs) == 1
        doc = json.loads(docs[0].read_text(encoding="utf-8"))
        assert doc["decision_state"] == "STALLED_CONFIRMED"
        assert doc["shadow_only"] is True
        assert "telegram" not in run.stdout.lower()
        assert "dispatch" not in run.stdout.lower()


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok: {test.__name__}")
    print(f"HERMES-ADAPTER-OK: {len(tests)} checks")
