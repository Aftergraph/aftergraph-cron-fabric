"""Hermes exact-session recovery executor for Trigger Fabric v0.2."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import subprocess

from recovery_control import ExecutionReceipt, RecoveryIntent, _iso, _stable_id

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def build_resume_argv(session_id: str, continuation_prompt: str) -> list[str]:
    if not SESSION_ID_RE.fullmatch(str(session_id or "")):
        raise ValueError("invalid Hermes session_id")
    prompt = str(continuation_prompt or "").strip()
    if not prompt:
        raise ValueError("continuation_prompt must not be empty")
    return ["hermes", "--resume", session_id, "--cli"]


def _ref(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_classic_cli_pty(argv: list[str], prompt: str,
                         timeout_seconds: int) -> subprocess.CompletedProcess:
    try:
        import pexpect
    except ImportError as exc:
        raise RuntimeError("pexpect is required for Hermes classic CLI recovery") from exc
    child = pexpect.spawn(
        argv[0], argv[1:], encoding="utf-8", timeout=timeout_seconds)
    output = []
    try:
        child.expect_exact("❯")
        output.append(child.before)
        child.sendline(prompt)
        child.expect_exact("❯")
        output.append(child.before)
        child.sendline("/exit")
        child.expect(pexpect.EOF)
        output.append(child.before)
        return subprocess.CompletedProcess(
            argv, int(child.exitstatus or 0), stdout="".join(output), stderr="")
    except pexpect.TIMEOUT:
        output.append(child.before or "")
        child.close(force=True)
        return subprocess.CompletedProcess(
            argv, 124, stdout="".join(output), stderr="Hermes classic CLI timed out")
    except pexpect.EOF:
        output.append(child.before or "")
        code = child.exitstatus if child.exitstatus is not None else 1
        return subprocess.CompletedProcess(
            argv, int(code), stdout="".join(output), stderr="Hermes classic CLI exited early")
    finally:
        if child.isalive():
            child.close(force=True)


def execute_recovery(intent: RecoveryIntent,
                     baseline_last_turn_at: float,
                     baseline_last_activity_at: float,
                     runner=_run_classic_cli_pty,
                     now_fn=lambda: datetime.now(timezone.utc),
                     timeout_seconds: int = 900) -> ExecutionReceipt:
    argv = build_resume_argv(intent.session_id, intent.continuation_prompt)
    started = now_fn()
    result = runner(argv, intent.continuation_prompt, timeout_seconds)
    finished = now_fn()
    payload = {"intent_id": intent.intent_id, "session_id": intent.session_id,
               "started_at": _iso(started), "finished_at": _iso(finished),
               "exit_code": int(result.returncode)}
    return ExecutionReceipt(
        execution_receipt_id=_stable_id("execution", payload),
        intent_id=intent.intent_id,
        session_id=intent.session_id,
        runtime_ref="hermes:classic-cli-pty",
        started_at=_iso(started), finished_at=_iso(finished),
        exit_code=int(result.returncode), stdout_ref=_ref(result.stdout or ""),
        stderr_ref=_ref(result.stderr or ""),
        baseline_last_turn_at=float(baseline_last_turn_at),
        baseline_last_activity_at=float(baseline_last_activity_at),
    )
