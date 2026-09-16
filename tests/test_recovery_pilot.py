import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recovery_control import RecoveryJournal
from recovery_pilot import run_recovery_pilot

NOW = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


def subject(session="pilot-live"):
    return {
        "session_id": session, "subject_ref": f"mission:{session}",
        "status": "active", "session_exists": True, "ended": False,
        "archived": False, "hidden": False, "last_turn_at": 100.0,
        "last_activity_at": 101.0, "lease_active": False,
        "paused": False, "revoked": False, "budget_blocked": False,
        "observations_equivalent": True, "observations_fresh": True,
        "stall_occurrence_refs": ["stall-a", "stall-b"], "risk": "low",
    }


def test_dry_run_never_executes():
    called = []
    with tempfile.TemporaryDirectory() as tmp:
        j = RecoveryJournal(Path(tmp) / "r.sqlite")
        result = run_recovery_pilot(
            subject(), {"pilot": True, "session_id": "pilot-live"}, j,
            now=NOW, execute=False,
            executor=lambda *a, **k: called.append(True))
        assert result["state"] == "AUTHORIZED_NOT_DISPATCHED"
        assert called == []
        j.close()


def test_end_to_end_progress_becomes_verified_recovered():
    with tempfile.TemporaryDirectory() as tmp:
        j = RecoveryJournal(Path(tmp) / "r.sqlite")
        def executor(intent, baseline_last_turn_at, baseline_last_activity_at):
            from recovery_control import ExecutionReceipt
            return ExecutionReceipt(
                "exec-1", intent.intent_id, intent.session_id, "hermes:test",
                NOW.isoformat(), (NOW + timedelta(seconds=1)).isoformat(),
                0, "sha256:o", "sha256:e", baseline_last_turn_at,
                baseline_last_activity_at)
        def post_observer(session_id):
            return {"session_id": session_id, "last_turn_at": 102.0,
                    "last_activity_at": 103.0,
                    "observed_at": (NOW + timedelta(seconds=2)).isoformat()}
        result = run_recovery_pilot(
            subject(), {"pilot": True, "session_id": "pilot-live"}, j,
            now=NOW, execute=True, executor=executor,
            post_observer=post_observer)
        assert result["state"] == "VERIFIED_RECOVERED"
        assert result["verification"]["verdict"] == "VERIFIED_RECOVERED"
        j.close()


def test_wrong_pilot_session_is_denied_before_executor():
    called = []
    with tempfile.TemporaryDirectory() as tmp:
        j = RecoveryJournal(Path(tmp) / "r.sqlite")
        result = run_recovery_pilot(
            subject(), {"pilot": True, "session_id": "other"}, j,
            now=NOW, execute=True,
            executor=lambda *a, **k: called.append(True))
        assert result["state"] == "RECOVERY_DENIED"
        assert called == []
        j.close()


def test_exhausted_budget_escalates_without_dispatch():
    called = []
    with tempfile.TemporaryDirectory() as tmp:
        j = RecoveryJournal(Path(tmp) / "r.sqlite")
        # First dry run gives us the causal dedupe key without dispatch.
        first = run_recovery_pilot(
            subject(), {"pilot": True, "session_id": "pilot-live"}, j,
            now=NOW, execute=False)
        key = first["proposal"]["dedupe_key"]
        j.record_unsuccessful_attempt(key)
        j.record_unsuccessful_attempt(key)
        result = run_recovery_pilot(
            subject(), {"pilot": True, "session_id": "pilot-live"}, j,
            now=NOW + timedelta(minutes=31), execute=True,
            executor=lambda *a, **k: called.append(True))
        assert result["state"] == "ESCALATION_REQUIRED"
        assert called == []
        j.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"  ok: {test.__name__}")
    print(f"RECOVERY-PILOT-OK: {len(tests)} checks")
