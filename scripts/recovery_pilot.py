"""Controlled Trigger Fabric v0.2 recovery pilot orchestration."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from recovery_control import (
    RecoveryIntent, RecoveryJournal, RecoveryProposal,
    evaluate_pilot_authority, evaluate_recovery_eligibility,
    observe_progress, verify_recovery,
)


def run_recovery_pilot(subject: dict, policy: dict, journal: RecoveryJournal,
                       *, now: datetime | None = None, execute: bool = False,
                       executor=None, post_observer=None) -> dict:
    now = now or datetime.now(timezone.utc)
    refs = tuple(subject.get("stall_occurrence_refs") or ())
    if len(set(refs)) < 2:
        return {"state": "STALL_NOT_CONFIRMED", "reason": "two observations required"}

    attempt_number = journal.unsuccessful_attempts(
        _incident_key(subject, refs)) + 1
    proposal = RecoveryProposal.create(
        session_id=str(subject.get("session_id") or ""),
        subject_ref=str(subject.get("subject_ref") or ""),
        stall_occurrence_refs=refs,
        baseline_last_turn_at=float(subject.get("last_turn_at", 0) or 0),
        baseline_last_activity_at=float(subject.get("last_activity_at", 0) or 0),
        attempt_number=attempt_number,
        risk=str(subject.get("risk") or "high"),
        created_at=now, expires_at=now + timedelta(minutes=10),
    )
    eligibility = evaluate_recovery_eligibility(
        subject, journal, proposal.dedupe_key, now)
    if "attempt_budget_exhausted" in eligibility.reason_codes:
        return {"state": "ESCALATION_REQUIRED", "proposal": asdict(proposal),
                "reasons": list(eligibility.reason_codes)}
    if not eligibility.allowed:
        return {"state": "RECOVERY_INELIGIBLE", "proposal": asdict(proposal),
                "reasons": list(eligibility.reason_codes)}

    proposal_claimed = journal.claim_proposal(proposal)
    authority = evaluate_pilot_authority(proposal, policy, now)
    journal.record_authority(authority)
    result = {"proposal": asdict(proposal), "proposal_claimed": proposal_claimed,
              "authority": asdict(authority)}
    if authority.outcome != "AUTHORIZED":
        result["state"] = "RECOVERY_DENIED"
        return result
    if not execute:
        result["state"] = "AUTHORIZED_NOT_DISPATCHED"
        return result
    intent = RecoveryIntent.create(
        proposal, authority,
        str(policy.get("continuation_prompt") or
            "Continue the existing goal from the last verified checkpoint."),
        now)
    if not journal.claim_intent(intent, authority, now):
        result["state"] = "DUPLICATE_SUPPRESSED"
        result["intent"] = asdict(intent)
        return result

    if executor is None:
        from adapters.hermes_recovery_executor import execute_recovery
        executor = execute_recovery
    execution = executor(
        intent, proposal.baseline_last_turn_at,
        proposal.baseline_last_activity_at)
    journal.record_execution(execution)
    result["intent"] = asdict(intent)
    result["execution"] = asdict(execution)

    if post_observer is None:
        after = {
            "session_id": proposal.session_id,
            "last_turn_at": proposal.baseline_last_turn_at,
            "last_activity_at": proposal.baseline_last_activity_at,
            "observed_at": execution.finished_at,
        }
    else:
        after = post_observer(proposal.session_id)
    progress = observe_progress(proposal, after)
    verification = verify_recovery(
        proposal, authority, execution, progress,
        _verification_now(now, progress.observed_at))
    journal.record_verification(verification)
    result["progress"] = asdict(progress)
    result["verification"] = asdict(verification)
    result["state"] = verification.verdict
    if verification.verdict != "VERIFIED_RECOVERED":
        attempts = journal.record_unsuccessful_attempt(proposal.dedupe_key)
        if attempts >= 2:
            result["state"] = "ESCALATION_REQUIRED"
    return result


def _incident_key(subject: dict, refs: tuple[str, ...]) -> str:
    from recovery_control import _stable_id
    causal = {
        "session_id": str(subject.get("session_id") or ""),
        "refs": tuple(sorted(set(refs))),
        "baseline_last_turn_at": float(subject.get("last_turn_at", 0) or 0),
    }
    return _stable_id("recovery", causal)


def _verification_now(now: datetime, observed_at: str) -> datetime:
    if not observed_at:
        return now
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        return max(now, parsed)
    except ValueError:
        return now
