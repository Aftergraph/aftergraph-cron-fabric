import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recovery_control import ExecutionReceipt, RecoveryJournal
from recovery_pilot import run_recovery_pilot

NOW = datetime(2026, 9, 16, 20, 45, tzinfo=timezone.utc)


def test_pilot_records_authority_execution_and_verification():
    with tempfile.TemporaryDirectory() as tmp:
        j = RecoveryJournal(Path(tmp) / "r.sqlite")
        subject = {
            "session_id": "pilot-journal", "subject_ref": "mission:pilot-journal",
            "status": "active", "session_exists": True, "ended": False,
            "archived": False, "hidden": False, "last_turn_at": 10.0,
            "last_activity_at": 11.0, "lease_active": False,
            "paused": False, "revoked": False, "budget_blocked": False,
            "observations_equivalent": True, "observations_fresh": True,
            "stall_occurrence_refs": ["s1", "s2"], "risk": "low",
        }
        def executor(intent, lt, la):
            return ExecutionReceipt("exec-j", intent.intent_id, intent.session_id,
                "hermes:test", NOW.isoformat(), (NOW + timedelta(seconds=1)).isoformat(),
                0, "sha256:o", "sha256:e", lt, la)
        def observer(session_id):
            return {"session_id": session_id, "last_turn_at": 12.0,
                    "last_activity_at": 13.0,
                    "observed_at": (NOW + timedelta(seconds=2)).isoformat()}
        result = run_recovery_pilot(subject,
            {"pilot": True, "session_id": "pilot-journal"}, j,
            now=NOW, execute=True, executor=executor, post_observer=observer)
        assert result["state"] == "VERIFIED_RECOVERED"
        assert j.evidence_kinds() == ["authority", "execution", "verification"]
        j.close()


if __name__ == "__main__":
    test_pilot_records_authority_execution_and_verification()
    print("  ok: test_pilot_records_authority_execution_and_verification")
    print("RECOVERY-PILOT-JOURNAL-OK: 1 check")
