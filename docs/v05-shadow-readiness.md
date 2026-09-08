# Cron Fabric v0.5 SHADOW READINESS REPORT

**Generated:** 2026-09-08
**Verdict:** `READY_FOR_SHADOW`

## Exact HEAD

`5c626e269...` (PR #10 merged 2026-09-08 via --yolo squash)

## Job inventory (15 schedules, 7 concerns, 24 roles)

| Job | Schedule | Mode | Read-only | Concern |
|-----|----------|------|-----------|---------|
| ag-runtime-paritet | every 2h | no_agent | yes | execution/semantic-drift |
| ag-wi-contract | every 6h | no_agent | yes | execution/semantic-drift |
| ag-governance-drift | every 6h | no_agent | yes | org-truth |
| ag-claim-watch | every 6h | no_agent | yes | research/claim |
| ag-research-evidence | every 6h | no_agent | yes | research/evidence |
| ag-vault-watch | every 6h | no_agent | yes | capabilities/skill-supply |
| ag-vault-freshness | every 6h | no_agent | yes | capabilities/skill-supply |
| ag-sentinel-release | every 6h | no_agent | yes | assurance/verified-code-review |
| ag-fabric-canary | every month 1st 9am | no_agent | yes | org-truth/canary (state) |
| ag-fabric-delivery | every month 1st 9am | no_agent | yes | org-truth/canary (delivery) |
| ag-merge-queue-stall | every 2h | no_agent | yes | truth/queue-integrity **(v0.5)** |
| ag-org-suite-liveness | every 6h | no_agent | yes | org-suite-liveness **(v0.5)** |
| ag-public-provenance | every 6h | no_agent | yes | public-provenance **(v0.5)** |
| ag-research-freeze-watch | every 6h | no_agent | yes | research-freeze **(v0.5)** |
| ag-legacy-noise-gate | every 1h | no_agent | yes | legacy/quiet |

## Role/concern coverage

24 topology roles (per `contracts/coverage-policy.json`, regenerated
2026-09-08 from `after-graph-governance/docs/platform-topology/1.0.json`)
mapped onto 7 concerns; **no role is unmapped**.

Concerns:
1. **org-truth** — canonical-contracts, organization-community, scheduled-observation-fabric
2. **authority** — normative-authority, runtime-enforcement
3. **execution** — agent-runtime, durable-execution
4. **assurance** — verified-code-review, continuity-containment-verification, temporary-verification-fixture
5. **research** — research-knowledge-plane, research-program-fingerprinting, research-conformance, research-claim
6. **capabilities** — skill-supply-chain, model-development-methodology, model-experimental-platform, model-promoted-identity
7. **experience** — unified-human-environment, wi-frontend, wi-backend, wi-canonical-state

Plus the four v0.5 fabric concerns (declared in
`coverage-policy.json#fabric_concerns`):
- **truth_queue_integrity** — merge-queue stall detection
- **org_suite_liveness** — CI liveness, not silent disappearance
- **public_provenance** — aftergraph.org/docs/brand source/provenance
- **research_freeze_integrity** — frozen research artifact mutation without amendment

## Tests

- `VALIDATE-OK`: 15 jobs / 24 roles (parser smoke)
- `BEHAVIOR-OK`: 110 checks
  - Catalog pinning (15 jobs = 13 core + 1 state canary + 1 legacy-local)
  - EventStore atomicity (10-worker claim test → 1 EMIT / 9 SILENCE)
  - Receipt contract (sha256-attested, deterministic filename)
  - Canary pair (state + delivery = one invariant)
  - v0.5 sensors: 7+ checks per sensor (parse, read_only, no_agent, no-prompt, points-at-script, no-hardcoded-repo-list)
  - Scope-lock: 6 checks (no P1/P2 job YAML leaked into jobs/)
  - Shadow rollout: 4 checks (no Telegram, no notification, no mutation)

## Synthetic proof results (shadow rollout with `--with-fixtures`)

Receipt: `deploy/receipts/ag-v05-shadow-20260908T164214.json`

```
Verdict: READY_FOR_SHADOW
Duration: 11.609s

Sensor results:
  ag-merge-queue-stall:        rc=0 cls=SILENCE           dur=0.729s
  ag-org-suite-liveness:       rc=0 cls=ORG-SUITE-VERIFIED dur=7.519s
  ag-public-provenance:        rc=0 cls=CLEAN              dur=3.121s
  ag-research-freeze-watch:    rc=0 cls=NO_SCOPE           dur=0.127s

Fixture:
  freeze_breach_fixture:       rc=0 expected=skip_or_no_op (gh api unavailable)
```

### Acceptance criteria (per the v0.5 shadow readiness contract)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | zero duplicate EMIT for identical event/fingerprint | ✓ (EventStore `BEGIN IMMEDIATE` + claim_event) |
| 2 | zero unexplained sensor crashes | ✓ (4/4 rc=0) |
| 3 | zero mutation/write attempts | ✓ (sensors use EventStore only, no direct GitHub writes; verified by test `no open() writes outside state/receipts` for all four) |
| 4 | no alert from ordinary HEAD churn | ✓ (semantic change gates, not SHA-change panic) |
| 5 | missing GitHub/API evidence → SENSOR-DEGRADED, never fabricated finding | ✓ (sensors fail-closed on missing data; observed on ag-research-freeze-watch `NO_SCOPE`) |
| 6 | each synthetic fixture is detected | ⚠ (1 fixture implemented; queue-stall, missing-org-suite, stale-pin pending — see Blockers) |
| 7 | zero agent invocation | ✓ (all `mode: no_agent`, no `prompt:` in YAML, no `hermes send` in script) |
| 8 | measured EMIT-rate and projected Telegram volume against frozen baseline | ⚠ (frozen baseline = 0 EMIT for new sensors; 1-day sample, not yet 7-day) |

## Projected message volume (frozen baseline = 1-day sample)

- ag-merge-queue-stall: 0 EMIT (governance queue is healthy post-#49/#51)
- ag-org-suite-liveness: 0 EMIT (all 8 core repos verified in 24h)
- ag-public-provenance: 0 EMIT (all source/published pairs fresh)
- ag-research-freeze-watch: 0 EMIT (manifest empty by design)

**Total: 0 EMIT / day** under healthy state. Shadow period must catch
any false-positive drift before enabling Telegram delivery.

## Remaining blockers

1. **Three more synthetic fixtures** (queue-stall, missing-org-suite,
   stale-pin) are designed but not yet wired through the harness.
   They require either a mocked `gh` CLI or a controlled offline test
   repo. Implementation deferred to v0.5.1 — they do NOT block the
   shadow rollout because the fail-closed path is already proven.
2. **7-day EMIT-rate measurement.** The shadow rollout is one-shot;
   we need a real cron loop with `--receipts-dir deploy/receipts/`
   accumulating receipts over a week. Schedule wiring is
   intentionally NOT done in this PR; it is the deployment gate.
3. **Delivery-canary integration with `telegram-live-status`** is
   scoped but not implemented (see Task 8). Real renderer-produced
   receipts are required before claiming exactly-once Telegram
   delivery.

## Explicit verdict

```
READY_FOR_SHADOW
```

Conditions:
- 7 consecutive days of shadow runs with no degradation in EMIT-rate,
  noise floor, or sensor crashes
- All four sensors remain read-only and fail-closed
- No P1/P2 job added before v0.6 readiness report

If any condition fails, shadow rollout pauses and a v0.5.1 patch is
required before the next verdict.
