from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recovery_control import (
    ExecutionReceipt, RecoveryProposal, evaluate_pilot_authority,
    observe_progress, verify_recovery,
)

NOW = datetime(2026, 9, 16, 19, 30, tzinfo=timezone.utc)


def make_proposal():
    return RecoveryProposal.create(
        session_id="pilot-1", subject_ref="mission:pilot-1",
        stall_occurrence_refs=("occ-a", "occ-b"),
        baseline_last_turn_at=100.0, baseline_last_activity_at=101.0,
        attempt_number=1, risk="low", created_at=NOW,
        expires_at=NOW + timedelta(minutes=10))


def make_execution(exit_code=0):
    return ExecutionReceipt(
        "execution-1", "intent-1", "pilot-1", "hermes:cli",
        NOW.isoformat(), (NOW + timedelta(seconds=2)).isoformat(),
        exit_code, "sha256:out", "sha256:err", 100.0, 101.0)


def auth_for(p):
    return evaluate_pilot_authority(
        p, {"pilot": True, "session_id": "pilot-1"}, NOW)

def test_exit_zero_without_progress_is_unverified():
    p = make_proposal()
    progress = observe_progress(
        p, {"session_id": "pilot-1", "last_turn_at": 100.0,
            "last_activity_at": 101.0, "observed_at": NOW.isoformat()})
    vr = verify_recovery(p, auth_for(p), make_execution(0), progress, NOW)
    assert vr.verdict == "RECOVERY_UNVERIFIED"


def test_last_turn_advance_same_session_verifies_recovery():
    p = make_proposal()
    progress = observe_progress(
        p, {"session_id": "pilot-1", "last_turn_at": 102.0,
            "last_activity_at": 103.0,
            "observed_at": (NOW + timedelta(seconds=5)).isoformat()})
    vr = verify_recovery(p, auth_for(p), make_execution(0), progress,
                         NOW + timedelta(seconds=5))
    assert vr.verdict == "VERIFIED_RECOVERED"
    assert any("last_turn_at" in ref for ref in vr.evidence_refs)


def test_wrong_session_cannot_verify():
    p = make_proposal()
    progress = observe_progress(
        p, {"session_id": "other", "last_turn_at": 999.0,
            "last_activity_at": 999.0,
            "observed_at": (NOW + timedelta(seconds=5)).isoformat()})
    vr = verify_recovery(p, auth_for(p), make_execution(0), progress,
                         NOW + timedelta(seconds=5))
    assert vr.verdict != "VERIFIED_RECOVERED"


def test_nonzero_execution_is_failed():
    p = make_proposal()
    progress = observe_progress(
        p, {"session_id": "pilot-1", "last_turn_at": 102.0,
            "last_activity_at": 103.0,
            "observed_at": (NOW + timedelta(seconds=5)).isoformat()})
    vr = verify_recovery(p, auth_for(p), make_execution(1), progress,
                         NOW + timedelta(seconds=5))
    assert vr.verdict == "RECOVERY_FAILED"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"  ok: {test.__name__}")
    print(f"RECOVERY-VERIFICATION-OK: {len(tests)} checks")
