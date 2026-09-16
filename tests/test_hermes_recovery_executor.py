from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "adapters"))

from recovery_control import RecoveryIntent, RecoveryProposal, evaluate_pilot_authority
from hermes_recovery_executor import build_resume_argv, execute_recovery

NOW = datetime(2026, 9, 16, 19, 0, tzinfo=timezone.utc)


def make_intent():
    p = RecoveryProposal.create(
        session_id="pilot-abc_123", subject_ref="mission:pilot-abc_123",
        stall_occurrence_refs=("a", "b"), baseline_last_turn_at=100,
        baseline_last_activity_at=101, attempt_number=1, risk="low",
        created_at=NOW, expires_at=NOW.replace(minute=10))
    a = evaluate_pilot_authority(
        p, {"pilot": True, "session_id": p.session_id}, NOW)
    return p, RecoveryIntent.create(
        p, a, "Continue from the last verified checkpoint.", NOW)

def test_exact_shell_free_classic_cli_argv():
    _, intent = make_intent()
    argv = build_resume_argv(intent.session_id, intent.continuation_prompt)
    assert argv == ["hermes", "--resume", "pilot-abc_123", "--cli"]
    assert "--oneshot" not in argv
    assert isinstance(argv, list)


def test_invalid_session_id_rejected():
    for value in ("", "bad session", "x;rm -rf /", "../escape"):
        try:
            build_resume_argv(value, "continue")
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe session id accepted: {value!r}")


def test_execute_recovery_uses_classic_cli_transport():
    proposal, intent = make_intent()
    seen = {}

    def fake_run(argv, prompt, timeout_seconds):
        seen["argv"] = argv
        seen["prompt"] = prompt
        seen["timeout"] = timeout_seconds
        return subprocess.CompletedProcess(argv, 0, stdout="continued", stderr="")

    receipt = execute_recovery(
        intent, proposal.baseline_last_turn_at,
        proposal.baseline_last_activity_at, runner=fake_run,
        now_fn=lambda: NOW, timeout_seconds=77)
    assert seen["argv"] == ["hermes", "--resume", "pilot-abc_123", "--cli"]
    assert seen["prompt"] == intent.continuation_prompt
    assert seen["timeout"] == 77
    assert receipt.exit_code == 0
    assert receipt.runtime_ref == "hermes:classic-cli-pty"
    assert receipt.session_id == intent.session_id
    assert receipt.baseline_last_turn_at == 100
    assert receipt.stdout_ref.startswith("sha256:")
    assert not hasattr(receipt, "recovered")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"  ok: {test.__name__}")
    print(f"HERMES-RECOVERY-EXECUTOR-OK: {len(tests)} checks")
