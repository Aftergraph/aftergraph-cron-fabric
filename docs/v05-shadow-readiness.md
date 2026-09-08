# Cron Fabric v0.5.1 SHADOW READINESS REPORT

**Generated:** 2026-09-08
**Exact HEAD:** `bfa144d35cde19c4dddd9a71f329c3b4d99e3a1d` + v0.5.1 changes (see git log below)
**Verdict:** `READY_FOR_SHADOW` (synthetic proof 4/4 COMPLETE)

## Lifecycle states (mechanical, do not conflate)

```
NOT_READY -> READY_FOR_SHADOW -> SHADOW_RUNNING -> SHADOW_ACCEPTED -> TELEGRAM_READY
```

This report records the transition `NOT_READY -> READY_FOR_SHADOW`.
The next transition (`SHADOW_RUNNING`) requires a scheduled 7-day run;
the following (`SHADOW_ACCEPTED`) requires the acceptance criteria in
section D to be measured, not asserted.

## Current state vs v0.5

| Area | v0.5 (bfa144d) | v0.5.1 (this branch) |
|------|----------------|----------------------|
| Synthetic proof | 1/4 fixtures, one reported `skip_or_no_op` | **4/4 sensors, 16 fixture scenarios, all PASS, no skip counted** |
| EventStore per sensor | fresh per run (dedupe broken across runs) | **persistent per-sensor store** |
| Recovery semantics | none (stalled→resolved→stalled silently re-dedupe) | **observed-recovery RESOLVED → re-arm → EMIT** |
| merge-queue-stall repo scope | contracts/queue-policy.yaml | unchanged (policy-driven) |
| Test count | 110 behavior | 110 behavior + 16 fixture scenarios |
| Readiness HEAD reference | stale (referred to 5c626e2) | current HEAD |

## Job inventory (15 schedules, 7 concerns, 24 roles)

Unchanged from v0.5. See git history and `contracts/coverage-policy.json`.

## Synthetic proof results (4/4 sensors, all offline, no live GitHub)

Run: `python tests/test_synthetic_fixtures.py` → `FIXTURES-OK: 16/16`

### 1. ag-merge-queue-stall (4 scenarios)
- stalled PR (MERGEABLE+BLOCKED+auto-merge+SUCCESS checks+age>90m+no queue progress) → exactly one EMIT ✓
- repeat same fixture → SILENCE (dedupe via persistent EventStore) ✓
- queue progress (enqueued) appears → RESOLVED; progress removed → re-arm → EMIT again ✓
- PR younger than stall window → no EMIT ✓

### 2. ag-org-suite-liveness (5 scenarios)
- all expected runs present → VERIFIED ✓
- one missing within tolerance → PENDING ✓
- >20% missing → DEGRADED ✓
- zero reachable runs → MISSED ✓
- all runs in_progress (API-unavailable analog) → clean exit, no crash, no fabricated finding ✓

### 3. ag-public-provenance (3 scenarios)
- pin matches canonical → CLEAN ✓
- stale pin → exactly one STALE_PIN EMIT, repeat → SILENCE (dedupe) ✓
- repaired pin → CLEAN ✓

### 4. ag-research-freeze-watch (4 scenarios)
- empty manifest → CLEAN ✓
- mutated without amendment → BREACH EMIT, repeat → SILENCE ✓
- mutation covered by valid amendment → CLEAN (AMENDED) ✓
- missing evidence (no current SHA) → SKIP, never fabricated BREACH, never CLEAN by inference ✓

**No `skip_or_no_op` result counts as synthetic proof in this report.**
Every scenario above exercises the sensor's real classification logic
through injected fixtures with live GitHub access fully disabled.

## Mechanical bugs found and fixed during fixture work

1. **Cross-run dedupe was broken.** Each sensor created a fresh
   timestamped EventStore per run, so the same finding re-EMITted on
   every run. Fixed: persistent per-sensor store under `state/`,
   overridable via `AG_FABRIC_STORE` for tests.
2. **`org_suite_liveness` could reference `missing_pct` before
   assignment** in the MISSED branch (NameError path). Fixed with an
   explicit `missing_pct = 1.0`.
3. **Stall recovery semantics.** A PR that stalled, saw queue progress,
   and stalled again was silently deduped to SILENCE. Fixed: the
   sensor now marks observed recovery as RESOLVED (HEALTHY), which
   re-arms the event key so a later stall with the same head SHA
   EMITs again.
4. **`public_provenance` had a latent `TimeoutExceeded` typo**
   (wrong exception name) — dead code path would raise uncaught. Fixed.
5. **REPO was resolved at import time**, breaking fixture-injection
   root overrides. Now re-resolved at main() entry.

## Acceptance criteria status (section D of the v0.5.1 contract)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | zero duplicate EMIT for identical event/fingerprint | ✓ (persistent EventStore + `BEGIN IMMEDIATE` claim; proven by fixture dedupe scenarios) |
| 2 | zero unexplained sensor crashes | ✓ (all 16 fixture scenarios rc=0; live shadow receipts pending) |
| 3 | zero mutation/write attempts | ✓ (behavior tests verify read-only for all four sensors) |
| 4 | no alert from ordinary HEAD churn | ✓ (semantic gates; live confirmation pending) |
| 5 | missing evidence → SENSOR-DEGRADED/SKIP, never fabricated | ✓ (proven by fixtures: org-suite API-failure, freeze missing-SHA) |
| 6 | each synthetic fixture detected | ✓ **4/4 sensors, 16/16 scenarios** |
| 7 | zero agent invocation | ✓ (`no_agent`, no prompt, verified in behavior tests) |
| 8 | projected Telegram volume vs frozen baseline | measured during SHADOW_RUNNING, not before |

## What this verdict does NOT claim

- NOT `SHADOW_RUNNING` — no scheduled 7-day receipts exist yet.
- NOT `SHADOW_ACCEPTED` — acceptance metrics are not measured.
- NOT `TELEGRAM_READY` — no renderer-produced delivery receipt has
  been observed and verified.

## Remaining work to reach SHADOW_RUNNING

1. Enable the four v0.5 sensor schedules in the Hermes cron profile
   with Telegram delivery disabled (shadow mode).
2. Daily summary receipts under `deploy/receipts/` with runs, EMIT,
   UPDATE, SILENCE, RESOLVED, SENSOR-DEGRADED, crashes, duration
   p50/p95, duplicate count, projected messages/day.

## Verdict

```
V0.5.1_FIXTURES_GREEN_READY_FOR_7D_SHADOW
```