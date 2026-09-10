"""Cron observation sensing tests (PRO-002 / PRO-006).

Run: python3 tests/test_cron_observation_sensing.py

Covers the V4 request `cron-observation-sensing`: Cron scheduled
read-only sensing projected to finding candidates that claim no
execution and no admission. The Cron Fabric retains zero execution
authority (seam PROACTIVITY-ORG-V1.md, contract proactivity/0.1).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from cron_observation_sensing import (CronAuthorityError,
                                      project_finding_candidate,
                                      sense_scheduled_reading,
                                      validate_candidate)

passed = 0
failed = []


def check(name, cond):
    global passed
    if not cond:
        failed.append(name)
        print(f"  FAIL: {name}")
        return
    passed += 1
    print(f"  ok: {name}")


def expect_reject(name, candidate, **kwargs):
    try:
        validate_candidate(candidate, **kwargs)
    except (CronAuthorityError, ValueError):
        check(name, True)
        return
    check(name, False)


# Exact acceptance vectors (governance platform-conformance v0.1).
PRO_002_INPUT = {
    "schema": "proactivity/0.1",
    "sensing_id": "sen_22222222222222222222222222222222",
    "path": "cron",
    "native_ref": "cron:reading:000002",
    "candidate_kind": "finding",
    "claims_execution": True,
    "claims_admission": False,
    "admitted_by_tg": False,
    "correlated_paths": ["cron"],
    "asserted_at": "2026-09-08T12:00:01Z",
    "tenant_id": "ten_11111111111111111111111111111111",
}

PRO_006_INPUT = {
    "schema": "proactivity/0.1",
    "sensing_id": "sen_66666666666666666666666666666666",
    "path": "cron",
    "native_ref": "cron:reading:000006",
    "candidate_kind": "finding",
    "claims_execution": False,
    "claims_admission": False,
    "admitted_by_tg": False,
    "correlated_paths": ["cron"],
    "asserted_at": "2026-09-08T12:00:05Z",
    "tenant_id": "ten_11111111111111111111111111111111",
}

PRO_006_NOW = datetime(2026, 9, 8, 12, 5, 0, tzinfo=timezone.utc)


def _fresh_pro006(**overrides):
    cand = dict(PRO_006_INPUT)
    cand.update(overrides)
    return cand


# PRO-006: scheduled read-only sensing finding claiming no execution
# is accepted.
check("PRO-006 accept (claims no execution)",
      validate_candidate(PRO_006_INPUT, now=PRO_006_NOW) is True)

# PRO-002: Cron finding candidate claiming execution is rejected
# (zero Cron execution authority).
expect_reject("PRO-002 reject (claims execution)", PRO_002_INPUT,
              now=datetime(2026, 9, 8, 12, 5, 0, tzinfo=timezone.utc))

# Projection: a scheduled reading becomes a finding candidate that
# claims no execution and no admission.
projected = project_finding_candidate(
    native_ref="cron:reading:000006",
    sensing_id="sen_66666666666666666666666666666666",
    tenant_id="ten_11111111111111111111111111111111",
    asserted_at="2026-09-08T12:00:05Z")
check("projection claims no execution and no admission",
      projected["schema"] == "proactivity/0.1"
      and projected["path"] == "cron"
      and projected["candidate_kind"] == "finding"
      and projected["claims_execution"] is False
      and projected["claims_admission"] is False
      and projected["admitted_by_tg"] is False)
check("projected candidate validates",
      validate_candidate(projected, now=PRO_006_NOW) is True)

# Sensing that admits is rejected: candidates never self-admit.
expect_reject("reject claims_admission=true",
              _fresh_pro006(claims_admission=True), now=PRO_006_NOW)
expect_reject("reject admitted_by_tg=true",
              _fresh_pro006(admitted_by_tg=True), now=PRO_006_NOW)

# Scope-unbound findings are rejected: tenant binding, cron-native
# ref, and cron correlation are all required.
expect_reject("reject missing tenant_id",
              _fresh_pro006(tenant_id=""), now=PRO_006_NOW)
expect_reject("reject malformed tenant_id",
              _fresh_pro006(tenant_id="acme"), now=PRO_006_NOW)
expect_reject("reject empty native_ref",
              _fresh_pro006(native_ref=""), now=PRO_006_NOW)
expect_reject("reject non-cron native_ref",
              _fresh_pro006(native_ref="runtime:reading:1"),
              now=PRO_006_NOW)
expect_reject("reject correlated_paths without cron",
              _fresh_pro006(correlated_paths=["runtime"]),
              now=PRO_006_NOW)
expect_reject("reject non-cron path",
              _fresh_pro006(path="runtime"), now=PRO_006_NOW)

# Stale-schedule replay is rejected: a reading asserted more than the
# schedule window ago cannot be (re)admitted as a fresh finding.
stale_now = (datetime(2026, 9, 8, 12, 0, 5, tzinfo=timezone.utc)
             + timedelta(hours=48))
expect_reject("reject stale-schedule replay", PRO_006_INPUT, now=stale_now)

# Schedule-triggered execution is impossible: a scheduled tick yields
# a finding candidate or silence, never an execution directive.
tick_hit = sense_scheduled_reading(
    {"native_ref": "cron:reading:000006",
     "sensing_id": "sen_66666666666666666666666666666666",
     "tenant_id": "ten_11111111111111111111111111111111",
     "asserted_at": "2026-09-08T12:00:05Z",
     "has_finding": True}, now=PRO_006_NOW)
check("scheduled tick reports finding without execution",
      tick_hit is not None
      and tick_hit["claims_execution"] is False
      and tick_hit["candidate_kind"] == "finding"
      and not any(k in tick_hit
                  for k in ("execute", "dispatch", "run", "admit")))
check("scheduled tick finding validates",
      validate_candidate(tick_hit, now=PRO_006_NOW) is True)
tick_miss = sense_scheduled_reading(
    {"native_ref": "cron:reading:000007",
     "sensing_id": "sen_77777777777777777777777777777777",
     "tenant_id": "ten_11111111111111111111111111111111",
     "asserted_at": "2026-09-08T12:00:06Z",
     "has_finding": False})
check("scheduled tick without finding stays silent", tick_miss is None)

print(f"\nCRON-OBSERVATION-SENSING: {passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
