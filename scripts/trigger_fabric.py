"""Trigger Fabric v0.1 shadow primitives.

No live dispatch, authority write, or notification side effects live here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable


REQUIRED_FIELDS = (
    "trigger_id", "cause_class", "observed_at", "subject_ref",
    "source_refs", "evidence_refs", "freshness", "confidence",
)

ALLOWED_STATES = {
    "ACTIVE", "SUSPECTED_STALL", "STALLED_CONFIRMED",
    "RECOVERY_PROPOSED", "RECOVERY_REQUESTED",
    "RECOVERY_UNVERIFIED", "ESCALATION_REQUIRED",
}


def _normalized_refs(values: Iterable[str]) -> tuple[str, ...]:
    refs = tuple(sorted(set(str(v).strip() for v in values if str(v).strip())))
    if not refs:
        raise ValueError("reference list must not be empty")
    return refs

@dataclass(frozen=True)
class TriggerOccurrence:
    trigger_id: str
    cause_class: str
    observed_at: str
    subject_ref: str
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    freshness: str
    confidence: float

    @classmethod
    def from_dict(cls, data: dict) -> "TriggerOccurrence":
        missing = [field for field in REQUIRED_FIELDS if field not in data]
        if missing:
            raise ValueError(f"missing required field(s): {', '.join(missing)}")
        if not str(data["subject_ref"]).strip():
            raise ValueError("subject_ref must not be empty")
        confidence = float(data["confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        freshness = str(data["freshness"])
        if freshness not in {"fresh", "stale", "unknown"}:
            raise ValueError("freshness must be fresh, stale, or unknown")
        return cls(
            trigger_id=str(data["trigger_id"]).strip(),
            cause_class=str(data["cause_class"]).strip(),
            observed_at=str(data["observed_at"]).strip(),
            subject_ref=str(data["subject_ref"]).strip(),
            source_refs=_normalized_refs(data["source_refs"]),
            evidence_refs=_normalized_refs(data["evidence_refs"]),
            freshness=freshness,
            confidence=confidence,
        )

    def fingerprint(self) -> str:
        payload = {
            "trigger_id": self.trigger_id,
            "cause_class": self.cause_class,
            "subject_ref": self.subject_ref,
            "source_refs": self.source_refs,
            "evidence_refs": self.evidence_refs,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ContinuityDecision:
    state: str
    reason: str


def _idle_minutes(occurrence: TriggerOccurrence) -> int:
    for ref in occurrence.evidence_refs:
        if ref.startswith("idle_minutes:"):
            try:
                return max(0, int(ref.split(":", 1)[1]))
            except ValueError as exc:
                raise ValueError("idle_minutes evidence must be an integer") from exc
    raise ValueError("missing idle_minutes evidence")


def evaluate_continuity(
    occurrence: TriggerOccurrence,
    prior_state: str,
) -> ContinuityDecision:
    if prior_state not in ALLOWED_STATES:
        raise ValueError(f"unsupported prior state: {prior_state}")
    idle = _idle_minutes(occurrence)
    if prior_state == "RECOVERY_REQUESTED":
        return ContinuityDecision("RECOVERY_UNVERIFIED", "dispatch has no verified post-recovery progress")
    if idle <= 15:
        return ContinuityDecision("ACTIVE", f"idle={idle}m within liveness threshold")
    if idle <= 30:
        return ContinuityDecision("SUSPECTED_STALL", f"idle={idle}m exceeds suspicion threshold")
    if prior_state == "ACTIVE":
        return ContinuityDecision("SUSPECTED_STALL", f"idle={idle}m requires confirmation")
    if prior_state == "SUSPECTED_STALL":
        return ContinuityDecision("STALLED_CONFIRMED", f"idle={idle}m confirmed across observations")
    if prior_state == "STALLED_CONFIRMED":
        return ContinuityDecision("RECOVERY_PROPOSED", "confirmed stall requires governed recovery decision")
    if prior_state == "RECOVERY_PROPOSED":
        return ContinuityDecision("RECOVERY_PROPOSED", "proposal remains pending authority")
    if prior_state == "RECOVERY_UNVERIFIED":
        return ContinuityDecision("ESCALATION_REQUIRED", "recovery lacks verified progress")
    if prior_state == "ESCALATION_REQUIRED":
        return ContinuityDecision("ESCALATION_REQUIRED", "manual or higher-authority intervention required")
    raise ValueError(f"unsupported transition from {prior_state}")


def write_shadow_receipt(
    directory: Path,
    occurrence: TriggerOccurrence,
    decision: ContinuityDecision,
) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    fp = occurrence.fingerprint()
    receipt_id = f"shadow-{fp}-{decision.state.lower()}"
    path = directory / f"{receipt_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    receipt = {
        "schema": "aftergraph.trigger-fabric.shadow-receipt/0.1",
        "receipt_id": receipt_id,
        "occurrence_fingerprint": fp,
        "decision_state": decision.state,
        "decision_reason": decision.reason,
        "subject_ref": occurrence.subject_ref,
        "observed_at": occurrence.observed_at,
        "source_refs": list(occurrence.source_refs),
        "evidence_refs": list(occurrence.evidence_refs),
        "shadow_only": True,
    }
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
