"""Governed recovery-control primitives for Trigger Fabric v0.2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_id(prefix: str, payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(blob.encode()).hexdigest()[:24]}"


@dataclass(frozen=True)
class RecoveryProposal:
    proposal_id: str
    subject_ref: str
    session_id: str
    stall_occurrence_refs: tuple[str, ...]
    baseline_last_turn_at: float
    baseline_last_activity_at: float
    proposed_action: str
    risk: str
    attempt_number: int
    dedupe_key: str
    created_at: str
    expires_at: str
    @classmethod
    def create(cls, session_id: str, subject_ref: str,
               stall_occurrence_refs: tuple[str, ...],
               baseline_last_turn_at: float, baseline_last_activity_at: float,
               attempt_number: int, risk: str, created_at: datetime,
               expires_at: datetime) -> "RecoveryProposal":
        refs = tuple(sorted(set(stall_occurrence_refs)))
        if len(refs) < 2:
            raise ValueError("two stalled observations required")
        causal = {"session_id": session_id, "refs": refs,
                  "baseline_last_turn_at": baseline_last_turn_at}
        dedupe_key = _stable_id("recovery", causal)
        payload = {**causal, "attempt_number": attempt_number,
                   "created_at": _iso(created_at)}
        return cls(_stable_id("proposal", payload), subject_ref, session_id,
                   refs, float(baseline_last_turn_at),
                   float(baseline_last_activity_at), "continue_existing_session",
                   risk, int(attempt_number), dedupe_key, _iso(created_at),
                   _iso(expires_at))


@dataclass(frozen=True)
class AuthorityReceipt:
    authority_receipt_id: str
    proposal_id: str
    policy_ref: str
    outcome: str
    constraints: tuple[str, ...]
    issued_at: str
    expires_at: str
    actor_ref: str


@dataclass(frozen=True)
class RecoveryIntent:
    intent_id: str
    proposal_id: str
    authority_receipt_ref: str
    session_id: str
    action: str
    continuation_prompt: str
    idempotency_key: str
    deadline: str
    created_at: str

    @classmethod
    def create(cls, proposal: RecoveryProposal, authority: AuthorityReceipt,
               continuation_prompt: str, now: datetime) -> "RecoveryIntent":
        if authority.proposal_id != proposal.proposal_id:
            raise ValueError("authority/proposal mismatch")
        idem = _stable_id("intent", {
            "dedupe_key": proposal.dedupe_key,
            "attempt_number": proposal.attempt_number,
            "session_id": proposal.session_id,
            "action": proposal.proposed_action,
        })
        return cls(idem, proposal.proposal_id, authority.authority_receipt_id,
                   proposal.session_id, "continue_existing_session",
                   continuation_prompt, idem, proposal.expires_at, _iso(now))


@dataclass(frozen=True)
class ExecutionReceipt:
    execution_receipt_id: str
    intent_id: str
    session_id: str
    runtime_ref: str
    started_at: str
    finished_at: str
    exit_code: int
    stdout_ref: str
    stderr_ref: str
    baseline_last_turn_at: float
    baseline_last_activity_at: float


@dataclass(frozen=True)
class VerificationReceipt:
    verification_receipt_id: str
    proposal_id: str
    session_id: str
    verdict: str
    evidence_refs: tuple[str, ...]
    verified_at: str

@dataclass(frozen=True)
class EligibilityResult:
    allowed: bool
    reason_codes: tuple[str, ...]


class RecoveryJournal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30.0)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS proposals (
          proposal_id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS intents (
          idempotency_key TEXT PRIMARY KEY, intent_id TEXT NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
          dedupe_key TEXT PRIMARY KEY, unsuccessful INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS evidence (
          evidence_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def claim_proposal(self, proposal: RecoveryProposal) -> bool:
        payload = json.dumps(proposal.__dict__, sort_keys=True)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT INTO proposals VALUES (?,?,?,?)",
                            (proposal.proposal_id, proposal.dedupe_key,
                             payload, proposal.created_at))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def unsuccessful_attempts(self, dedupe_key: str) -> int:
        row = self.db.execute("SELECT unsuccessful FROM attempts WHERE dedupe_key=?",
                              (dedupe_key,)).fetchone()
        return int(row[0]) if row else 0

    def record_unsuccessful_attempt(self, dedupe_key: str) -> int:
        self.db.execute("""INSERT INTO attempts(dedupe_key, unsuccessful)
            VALUES (?,1) ON CONFLICT(dedupe_key) DO UPDATE SET
            unsuccessful=unsuccessful+1""", (dedupe_key,))
        self.db.commit()
        return self.unsuccessful_attempts(dedupe_key)

    def attempt_budget_exhausted(self, dedupe_key: str) -> bool:
        return self.unsuccessful_attempts(dedupe_key) >= 2

    def claim_intent(self, intent: RecoveryIntent, authority: AuthorityReceipt,
                     now: datetime) -> bool:
        if authority.outcome != "AUTHORIZED":
            return False
        if _parse(authority.expires_at) <= now.astimezone(timezone.utc):
            return False
        if authority.authority_receipt_id != intent.authority_receipt_ref:
            return False
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT INTO intents VALUES (?,?,?,?)",
                            (intent.idempotency_key, intent.intent_id,
                             json.dumps(intent.__dict__, sort_keys=True),
                             intent.created_at))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def _record_evidence(self, evidence_id: str, kind: str,
                         payload: dict, created_at: str) -> bool:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "INSERT INTO evidence VALUES (?,?,?,?)",
                (evidence_id, kind, json.dumps(payload, sort_keys=True), created_at))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def record_authority(self, receipt: AuthorityReceipt) -> bool:
        return self._record_evidence(
            receipt.authority_receipt_id, "authority",
            receipt.__dict__, receipt.issued_at)

    def record_execution(self, receipt: ExecutionReceipt) -> bool:
        return self._record_evidence(
            receipt.execution_receipt_id, "execution",
            receipt.__dict__, receipt.finished_at)

    def record_verification(self, receipt: VerificationReceipt) -> bool:
        return self._record_evidence(
            receipt.verification_receipt_id, "verification",
            receipt.__dict__, receipt.verified_at)

    def evidence_kinds(self) -> list[str]:
        return [row[0] for row in self.db.execute(
            "SELECT kind FROM evidence ORDER BY rowid").fetchall()]
def evaluate_recovery_eligibility(subject: dict, journal: RecoveryJournal,
                                  dedupe_key: str, now: datetime) -> EligibilityResult:
    reasons = []
    if subject.get("status") != "active": reasons.append("status_not_active")
    if not subject.get("session_exists", False): reasons.append("session_missing")
    if subject.get("ended"): reasons.append("session_ended")
    if subject.get("archived"): reasons.append("session_archived")
    if subject.get("hidden"): reasons.append("session_hidden")
    if float(subject.get("last_turn_at", 0) or 0) <= 0: reasons.append("never_started")
    if subject.get("lease_active"): reasons.append("lease_active")
    if subject.get("paused"): reasons.append("paused")
    if subject.get("revoked"): reasons.append("revoked")
    if subject.get("budget_blocked"): reasons.append("budget_blocked")
    if not subject.get("observations_equivalent", False): reasons.append("stall_not_confirmed")
    if not subject.get("observations_fresh", False): reasons.append("stale_observation")
    if journal.attempt_budget_exhausted(dedupe_key): reasons.append("attempt_budget_exhausted")
    return EligibilityResult(not reasons, tuple(reasons))


def evaluate_pilot_authority(proposal: RecoveryProposal, policy: dict,
                             now: datetime) -> AuthorityReceipt:
    outcome = "AUTHORIZED"
    constraints = ["pilot_only", "exact_session", "continue_existing_session"]
    if not policy.get("pilot", False):
        outcome = "DENIED"
    elif policy.get("session_id") != proposal.session_id:
        outcome = "DENIED"
    elif proposal.risk != "low":
        outcome = "REVIEW_REQUIRED"
    elif proposal.proposed_action != "continue_existing_session":
        outcome = "DENIED"
    elif _parse(proposal.expires_at) <= now.astimezone(timezone.utc):
        outcome = "DENIED"
    issued = _iso(now)
    expires = _iso(min(_parse(proposal.expires_at), now.astimezone(timezone.utc) + timedelta(minutes=10)))
    payload = {"proposal_id": proposal.proposal_id, "outcome": outcome,
               "issued_at": issued, "expires_at": expires}
    return AuthorityReceipt(
        _stable_id("authority", payload), proposal.proposal_id,
        "aftergraph.trigger-fabric.pilot-recovery/0.2", outcome,
        tuple(constraints), issued, expires, "trigger-fabric:pilot-policy")


@dataclass(frozen=True)
class ProgressObservation:
    session_id: str
    observed_at: str
    evidence_refs: tuple[str, ...]
    has_progress: bool
    session_matches: bool


def observe_progress(proposal: RecoveryProposal, after: dict) -> ProgressObservation:
    session_id = str(after.get("session_id") or "")
    observed_at = str(after.get("observed_at") or "")
    refs = []
    if session_id == proposal.session_id:
        last_turn = float(after.get("last_turn_at", 0) or 0)
        last_activity = float(after.get("last_activity_at", 0) or 0)
        if last_turn > proposal.baseline_last_turn_at:
            refs.append(
                f"last_turn_at:{proposal.baseline_last_turn_at}->{last_turn}")
        if last_activity > proposal.baseline_last_activity_at:
            refs.append(
                f"last_activity_at:{proposal.baseline_last_activity_at}->{last_activity}")
    return ProgressObservation(
        session_id=session_id,
        observed_at=observed_at,
        evidence_refs=tuple(refs),
        has_progress=bool(refs),
        session_matches=session_id == proposal.session_id,
    )


def verify_recovery(proposal: RecoveryProposal,
                    authority: AuthorityReceipt,
                    execution: ExecutionReceipt,
                    progress: ProgressObservation,
                    now: datetime) -> VerificationReceipt:
    verdict = "RECOVERY_UNVERIFIED"
    refs = list(progress.evidence_refs)
    if execution.exit_code != 0:
        verdict = "RECOVERY_FAILED"
        refs.append(f"execution_exit_code:{execution.exit_code}")
    elif authority.outcome != "AUTHORIZED":
        refs.append(f"authority:{authority.outcome}")
    elif authority.proposal_id != proposal.proposal_id:
        refs.append("authority_proposal_mismatch")
    elif execution.session_id != proposal.session_id:
        refs.append("execution_session_mismatch")
    elif not progress.session_matches:
        refs.append("progress_session_mismatch")
    elif not progress.observed_at:
        refs.append("progress_timestamp_missing")
    else:
        try:
            observed_at = _parse(progress.observed_at)
            if observed_at > now.astimezone(timezone.utc) + timedelta(minutes=1):
                refs.append("progress_timestamp_future")
            elif observed_at < _parse(execution.finished_at):
                refs.append("progress_precedes_execution")
            elif any(ref.startswith("last_turn_at:") for ref in progress.evidence_refs):
                verdict = "VERIFIED_RECOVERED"
        except ValueError:
            refs.append("progress_timestamp_invalid")
    payload = {
        "proposal_id": proposal.proposal_id,
        "session_id": proposal.session_id,
        "verdict": verdict,
        "verified_at": _iso(now),
        "evidence_refs": sorted(refs),
    }
    return VerificationReceipt(
        verification_receipt_id=_stable_id("verification", payload),
        proposal_id=proposal.proposal_id,
        session_id=proposal.session_id,
        verdict=verdict,
        evidence_refs=tuple(sorted(refs)),
        verified_at=_iso(now),
    )
