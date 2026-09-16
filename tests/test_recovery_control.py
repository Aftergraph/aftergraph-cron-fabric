import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recovery_control import (
    RecoveryJournal, RecoveryProposal, RecoveryIntent,
    evaluate_recovery_eligibility, evaluate_pilot_authority,
)

NOW = datetime(2026, 9, 16, 18, 30, tzinfo=timezone.utc)


def proposal(session="pilot-1", attempt=1, risk="low"):
    return RecoveryProposal.create(
        session_id=session, subject_ref=f"mission:{session}",
        stall_occurrence_refs=("occ-a", "occ-b"),
        baseline_last_turn_at=100.0, baseline_last_activity_at=101.0,
        attempt_number=attempt, risk=risk, created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def eligible_subject(**overrides):
    base = dict(status="active", session_exists=True, ended=False,
                archived=False, hidden=False, last_turn_at=100.0,
                lease_active=False, paused=False, revoked=False,
                budget_blocked=False, observations_equivalent=True,
                observations_fresh=True)
    base.update(overrides)
    return base
def test_journal_dedupes_and_persists():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "recovery.sqlite"
        j = RecoveryJournal(db)
        p = proposal()
        assert j.claim_proposal(p) is True
        assert j.claim_proposal(p) is False
        j.close()
        j2 = RecoveryJournal(db)
        assert j2.claim_proposal(p) is False
        assert j2.unsuccessful_attempts(p.dedupe_key) == 0
        j2.close()


def test_attempt_budget_survives_reopen():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "recovery.sqlite"
        p = proposal()
        j = RecoveryJournal(db)
        j.claim_proposal(p)
        j.record_unsuccessful_attempt(p.dedupe_key)
        j.record_unsuccessful_attempt(p.dedupe_key)
        j.close()
        j = RecoveryJournal(db)
        assert j.unsuccessful_attempts(p.dedupe_key) == 2
        assert j.attempt_budget_exhausted(p.dedupe_key) is True
        j.close()


def test_eligibility_fails_closed_for_blockers():
    with tempfile.TemporaryDirectory() as tmp:
        j = RecoveryJournal(Path(tmp) / "r.sqlite")
        blockers = [
            {"status": "paused"}, {"status": "done"}, {"status": "cleared"},
            {"lease_active": True}, {"revoked": True}, {"paused": True},
            {"budget_blocked": True}, {"observations_equivalent": False},
            {"observations_fresh": False}, {"session_exists": False},
        ]
        for block in blockers:
            result = evaluate_recovery_eligibility(
                eligible_subject(**block), j, "incident-x", NOW)
            assert result.allowed is False, block
        j.close()
def test_pilot_authority_exact_session_only():
    p = proposal()
    ok = evaluate_pilot_authority(
        p, {"pilot": True, "session_id": "pilot-1"}, NOW)
    assert ok.outcome == "AUTHORIZED"
    wrong = evaluate_pilot_authority(
        p, {"pilot": True, "session_id": "other"}, NOW)
    assert wrong.outcome == "DENIED"
    prod = evaluate_pilot_authority(
        p, {"pilot": False, "session_id": "pilot-1"}, NOW)
    assert prod.outcome == "DENIED"


def test_authority_expiry_and_high_risk_fail_closed():
    p = proposal(risk="high")
    receipt = evaluate_pilot_authority(
        p, {"pilot": True, "session_id": "pilot-1"}, NOW)
    assert receipt.outcome != "AUTHORIZED"
    receipt2 = evaluate_pilot_authority(
        proposal(), {"pilot": True, "session_id": "pilot-1"},
        NOW + timedelta(hours=1))
    assert receipt2.outcome == "DENIED"


def test_only_authorized_unexpired_receipt_can_claim_intent():
    with tempfile.TemporaryDirectory() as tmp:
        j = RecoveryJournal(Path(tmp) / "r.sqlite")
        p = proposal()
        j.claim_proposal(p)
        auth = evaluate_pilot_authority(
            p, {"pilot": True, "session_id": "pilot-1"}, NOW)
        intent = RecoveryIntent.create(
            p, auth, "Continue the existing goal.", NOW)
        assert j.claim_intent(intent, auth, NOW) is True
        assert j.claim_intent(intent, auth, NOW) is False
        j.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"  ok: {test.__name__}")
    print(f"RECOVERY-CONTROL-OK: {len(tests)} checks")
