from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recovery_control import (
    ExecutionReceipt, RecoveryProposal, evaluate_pilot_authority,
    observe_progress, verify_recovery,
)

NOW = datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc)


def test_activity_only_does_not_verify_recovery():
    p = RecoveryProposal.create(
        session_id="pilot", subject_ref="mission:pilot",
        stall_occurrence_refs=("a", "b"), baseline_last_turn_at=100,
        baseline_last_activity_at=101, attempt_number=1, risk="low",
        created_at=NOW, expires_at=NOW + timedelta(minutes=10))
    a = evaluate_pilot_authority(p, {"pilot": True, "session_id": "pilot"}, NOW)
    e = ExecutionReceipt(
        "exec", "intent", "pilot", "hermes:cli", NOW.isoformat(),
        (NOW + timedelta(seconds=1)).isoformat(), 0,
        "sha256:o", "sha256:e", 100, 101)
    progress = observe_progress(p, {
        "session_id": "pilot", "last_turn_at": 100,
        "last_activity_at": 999,
        "observed_at": (NOW + timedelta(seconds=2)).isoformat(),
    })
    assert progress.has_progress is True
    v = verify_recovery(p, a, e, progress, NOW + timedelta(seconds=2))
    assert v.verdict == "RECOVERY_UNVERIFIED"


if __name__ == "__main__":
    test_activity_only_does_not_verify_recovery()
    print("  ok: test_activity_only_does_not_verify_recovery")
    print("RECOVERY-PROGRESS-STRENGTH-OK: 1 check")
