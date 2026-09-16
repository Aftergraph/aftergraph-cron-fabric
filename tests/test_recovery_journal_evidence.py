import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recovery_control import (
    ExecutionReceipt, RecoveryIntent, RecoveryJournal, RecoveryProposal,
    VerificationReceipt, evaluate_pilot_authority,
)

NOW = datetime(2026, 9, 16, 20, 30, tzinfo=timezone.utc)


def test_journal_persists_full_recovery_evidence_chain():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "r.sqlite"
        j = RecoveryJournal(db)
        p = RecoveryProposal.create(
            session_id="pilot-1", subject_ref="mission:pilot-1",
            stall_occurrence_refs=("a", "b"), baseline_last_turn_at=10,
            baseline_last_activity_at=11, attempt_number=1, risk="low",
            created_at=NOW, expires_at=NOW + timedelta(minutes=10))
        assert j.claim_proposal(p)
        a = evaluate_pilot_authority(
            p, {"pilot": True, "session_id": "pilot-1"}, NOW)
        assert j.record_authority(a) is True
        assert j.record_authority(a) is False
        i = RecoveryIntent.create(p, a, "continue", NOW)
        assert j.claim_intent(i, a, NOW)
        e = ExecutionReceipt(
            "exec-1", i.intent_id, "pilot-1", "hermes:cli",
            NOW.isoformat(), (NOW + timedelta(seconds=1)).isoformat(), 0,
            "sha256:o", "sha256:e", 10, 11)
        assert j.record_execution(e) is True
        v = VerificationReceipt(
            "verify-1", p.proposal_id, "pilot-1", "VERIFIED_RECOVERED",
            ("last_turn_at:10->12",), (NOW + timedelta(seconds=2)).isoformat())
        assert j.record_verification(v) is True
        j.close()
        j = RecoveryJournal(db)
        assert j.evidence_kinds() == ["authority", "execution", "verification"]
        j.close()


if __name__ == "__main__":
    test_journal_persists_full_recovery_evidence_chain()
    print("  ok: test_journal_persists_full_recovery_evidence_chain")
    print("RECOVERY-JOURNAL-EVIDENCE-OK: 1 check")
