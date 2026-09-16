from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recovery_control import RecoveryIntent, RecoveryProposal, evaluate_pilot_authority

NOW = datetime(2026, 9, 16, 20, 15, tzinfo=timezone.utc)


def make_proposal(created_at, attempt=1):
    return RecoveryProposal.create(
        session_id="pilot-1", subject_ref="mission:pilot-1",
        stall_occurrence_refs=("same-a", "same-b"),
        baseline_last_turn_at=100.0, baseline_last_activity_at=101.0,
        attempt_number=attempt, risk="low", created_at=created_at,
        expires_at=created_at + timedelta(minutes=10))


def test_same_incident_same_attempt_has_stable_intent_key():
    p1 = make_proposal(NOW, attempt=1)
    p2 = make_proposal(NOW + timedelta(minutes=1), attempt=1)
    assert p1.proposal_id != p2.proposal_id
    a1 = evaluate_pilot_authority(p1, {"pilot": True, "session_id": "pilot-1"}, NOW)
    a2 = evaluate_pilot_authority(p2, {"pilot": True, "session_id": "pilot-1"}, NOW + timedelta(minutes=1))
    i1 = RecoveryIntent.create(p1, a1, "continue", NOW)
    i2 = RecoveryIntent.create(p2, a2, "continue", NOW + timedelta(minutes=1))
    assert i1.idempotency_key == i2.idempotency_key
    assert i1.intent_id == i2.intent_id


def test_second_attempt_gets_new_intent_key():
    p1 = make_proposal(NOW, attempt=1)
    p2 = make_proposal(NOW + timedelta(minutes=31), attempt=2)
    a1 = evaluate_pilot_authority(p1, {"pilot": True, "session_id": "pilot-1"}, NOW)
    a2 = evaluate_pilot_authority(p2, {"pilot": True, "session_id": "pilot-1"}, NOW + timedelta(minutes=31))
    i1 = RecoveryIntent.create(p1, a1, "continue", NOW)
    i2 = RecoveryIntent.create(p2, a2, "continue", NOW + timedelta(minutes=31))
    assert i1.idempotency_key != i2.idempotency_key


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test(); print(f"  ok: {test.__name__}")
    print(f"RECOVERY-IDEMPOTENCY-OK: {len(tests)} checks")
