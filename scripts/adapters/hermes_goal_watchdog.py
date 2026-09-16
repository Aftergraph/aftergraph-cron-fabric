"""Read-only Hermes goal observation adapter for Trigger Fabric v0.1."""
from __future__ import annotations

from trigger_fabric import TriggerOccurrence


def normalize_goal_observation(data: dict) -> TriggerOccurrence:
    session_id = str(data.get("session_id", "")).strip()
    if not session_id:
        raise ValueError("session_id is required")
    observed_at = str(data.get("observed_at", "")).strip()
    if not observed_at:
        raise ValueError("observed_at is required")
    source_ref = str(data.get("source_ref", "")).strip()
    if not source_ref:
        raise ValueError("source_ref is required")

    status = str(data.get("status", "unknown")).strip() or "unknown"
    if status != "active":
        raise ValueError("continuity v0.1 evaluates active goals only")

    try:
        idle_minutes = max(0, int(data["idle_minutes"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("idle_minutes is required and must be an integer") from exc

    return TriggerOccurrence.from_dict({
        "trigger_id": "mission.continuity.observed",
        "cause_class": "liveness",
        "observed_at": observed_at,
        "subject_ref": f"mission:{session_id}",
        "source_refs": [source_ref],
        "evidence_refs": [f"idle_minutes:{idle_minutes}", f"hermes_status:{status}"],
        "freshness": "fresh",
        "confidence": 1.0,
    })
