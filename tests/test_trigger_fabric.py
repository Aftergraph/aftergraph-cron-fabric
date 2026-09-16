import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from trigger_fabric import (
    TriggerOccurrence,
    evaluate_continuity,
    write_shadow_receipt,
)


def occurrence(observed_at="2026-09-16T17:00:00Z", idle_minutes=0):
    return {
        "trigger_id": "mission.continuity.observed",
        "cause_class": "liveness",
        "observed_at": observed_at,
        "subject_ref": "mission:test-1",
        "source_refs": ["hermes:state-meta:goal:test-1"],
        "evidence_refs": [f"idle_minutes:{idle_minutes}"],
        "freshness": "fresh",
        "confidence": 1.0,
    }


def test_occurrence_fingerprint_ignores_timestamp():
    a = TriggerOccurrence.from_dict(occurrence("2026-09-16T17:00:00Z", 20))
    b = TriggerOccurrence.from_dict(occurrence("2026-09-16T17:05:00Z", 20))
    assert a.fingerprint() == b.fingerprint()


def test_occurrence_rejects_missing_subject():
    data = occurrence()
    del data["subject_ref"]
    try:
        TriggerOccurrence.from_dict(data)
    except ValueError as exc:
        assert "subject_ref" in str(exc)
    else:
        raise AssertionError("missing subject_ref accepted")


def test_continuity_active_and_stall_transitions():
    active = TriggerOccurrence.from_dict(occurrence(idle_minutes=5))
    suspected = TriggerOccurrence.from_dict(occurrence(idle_minutes=16))
    stalled = TriggerOccurrence.from_dict(occurrence(idle_minutes=31))
    assert evaluate_continuity(active, "ACTIVE").state == "ACTIVE"
    assert evaluate_continuity(suspected, "ACTIVE").state == "SUSPECTED_STALL"
    assert evaluate_continuity(stalled, "SUSPECTED_STALL").state == "STALLED_CONFIRMED"
    assert evaluate_continuity(stalled, "STALLED_CONFIRMED").state == "RECOVERY_PROPOSED"


def test_observation_never_claims_recovered():
    stalled = TriggerOccurrence.from_dict(occurrence(idle_minutes=31))
    decision = evaluate_continuity(stalled, "RECOVERY_REQUESTED")
    assert decision.state == "RECOVERY_UNVERIFIED"
    assert decision.state != "RECOVERED"


def test_invalid_transition_fails_closed():
    active = TriggerOccurrence.from_dict(occurrence(idle_minutes=1))
    try:
        evaluate_continuity(active, "RECOVERED")
    except ValueError as exc:
        assert "unsupported prior state" in str(exc)
    else:
        raise AssertionError("unsupported state accepted")


def test_shadow_receipt_is_deduped_and_shadow_only():
    with tempfile.TemporaryDirectory() as tmp:
        occ = TriggerOccurrence.from_dict(occurrence(idle_minutes=31))
        decision = evaluate_continuity(occ, "STALLED_CONFIRMED")
        first = write_shadow_receipt(Path(tmp), occ, decision)
        second = write_shadow_receipt(Path(tmp), occ, decision)
        assert first["receipt_id"] == second["receipt_id"]
        assert first["shadow_only"] is True
        assert len(list(Path(tmp).glob("*.json"))) == 1
        on_disk = json.loads(next(Path(tmp).glob("*.json")).read_text())
        assert on_disk["decision_state"] == "RECOVERY_PROPOSED"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok: {test.__name__}")
    print(f"TRIGGER-FABRIC-OK: {len(tests)} checks")
