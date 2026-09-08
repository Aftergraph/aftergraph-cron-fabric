# CHATGPT REVIEW SPEC - Aftergraph Cron Fabric v0.4

> Reviewer: reply with (1) verdict SHIP / CONDITIONAL / BLOCKED,
> (2) numbered findings with file/section + concrete fix,
> (3) what you would still delete. ASCII-only document.

## 0. What changed from v0.3

- Event emission ownership is now atomic. `scripts/event_store.py`
  exposes `claim_event()` using SQLite `BEGIN IMMEDIATE`; the claim and
  EMIT/UPDATE/SILENCE decision happen in one transaction. The prior
  `check() -> external emit -> record()` sequence is no longer the
  canonical emission path.
- Cross-process proof added: 10 independent processes claim the same
  event/fingerprint; exactly 1 returns EMIT and 9 return SILENCE.
- ACK is no longer conflated with recovery. `acknowledge()` leaves an
  event OPEN; only sensor-observed recovery calls `resolve()` -> HEALTHY.
- `ag-runtime-paritet` and `ag-wi-contract` are two-stage monitors:
  deterministic scripts emit stable artifact fingerprints; Hermes
  `monitor` wakes the agent only on change. Changed SHA is a cost gate,
  never a finding by itself.
- WI now watches both canonical backend artifacts and concrete frontend
  consumer surfaces (`src/api/contracts.ts`, `src/api/client.ts`). Agent
  analysis must prove semantic incompatibility before notifying.
- `ag-governance-drift` checks live GitHub repository topology against
  `organization.topology` before lower-level source-map drift.
- `contracts/sources.yaml` adds `organization.topology` and concrete WI
  mirror paths.
- Validator now requires execution mode, validates supported schedule
  grammar, verifies referenced scripts exist, and rejects duplicate
  canonical job names.
- Canary claim corrected: `ag-fabric-canary` is a local **state canary**.
  It proves EventStore atomic claim/dedupe/recovery/re-armability. It does
  NOT claim scheduler -> transport -> Telegram exactly-once delivery.

## 1. Decision

Keep 10 schedules / 7 concerns: 8 core schedules, 1 state canary,
1 legacy-local job. Do not add one job per repository.

Phase 1 concern set remains runtime parity, WI contract compatibility and
Sentinel release integrity. v0.4 changes their correctness semantics, not
schedule count or write authority.

## 2. Current verified platform ground truth

Current GitHub org reality observed 2026-09-08 contains 24 repositories.
The canonical topology owner is `Aftergraph/after-graph-governance`.
A topology reconciliation is tracked separately; Cron Fabric must consume
that canonical topology once reconciled rather than hard-code a second org
registry.

Special classifications:
- `autonomous-venture-company`: legacy/transition concern.
- `sentinel-firetest`: temporary live-fire fixture.
- `veranza`: private INTERNAL HOLD/incubation; topology observation must
  not upgrade product/public maturity.

## 3. Goals / non-goals

Goals:
1. At most one claimed alert per event fingerprint across independent
   Fabric processes.
2. Notify only on semantically actionable findings with typed evidence.
3. Read-only external behavior by default and in v0.4.
4. Cheap deterministic sensing before expensive agent reasoning.
5. Every current repository is mapped to a concern or explicitly excluded.

Non-goals:
- autonomous merge/deploy/secret rotation;
- one schedule per repository;
- AI-news digests;
- claiming Telegram exactly-once delivery before delivery receipts exist.

## 4. Architecture

```text
deterministic sensor / pre-check
        |
        v
Hermes monitor change gate
  unchanged -> skip agent
  changed   -> wake investigator
        |
        v
semantic comparison against declared source ownership
        |
        +-- no incompatibility -> silence
        |
        v
EventStore.claim_event()  [atomic authoritative dedupe]
        |
        v
typed evidence + disposition gate
        |
        v
Telegram Ops delivery (when production delivery is enabled)
```

Ownership:
- EventStore = authoritative event lifecycle and dedupe.
- Hermes monitor = cost/wake gate only.
- continuity = reasoning context for deep audits only.
- GitHub/Governance repositories = source truth; Fabric is an observer.

## 5. Job catalog

Core schedules:
1. `ag-runtime-paritet` - 2h, deterministic artifact gate -> semantic investigator.
2. `ag-wi-contract` - 2h, backend+frontend gate -> compatibility investigator.
3. `ag-governance-drift` - 6h, topology first, then registered contract drift.
4. `ag-claim-watch` - 6h cheap research change watch.
5. `ag-research-evidence` - weekly deep evidence audit.
6. `ag-vault-watch` - 6h cheap skills/model/docs change watch.
7. `ag-vault-freshness` - weekly deep freshness audit.
8. `ag-sentinel-release` - daily release integrity + temporary firetest rule.

Infrastructure:
9. `ag-fabric-canary` - monthly local state canary, no-agent.

Legacy-local:
10. `jobs/legacy/ag-legacy-noise-gate` - AVC noise filtering.

## 6. Interfaces

Required per job:
- `name`
- `schedule`
- `deliver`
- `severity`
- `allowed_dispositions`
- `read_only: true`
- `mode`
- `rollback`

Conditional:
- agent mode requires `prompt`;
- no_agent requires `script` and forbids prompt;
- terminal jobs require `job_type: constrained_terminal` and route GitHub
  reads through `scripts/gh-read.sh`.

Agent jobs may also use `script` + `monitor` as a deterministic wake gate.
A script fingerprint change is not permission to emit a finding.

## 7. Safety

- `scripts/gh-read.sh` rejects write methods before network access.
- no Fabric job may merge, push, deploy, approve or rotate secrets.
- local writes are limited to Fabric-owned state/receipts/audit surfaces.
- GitHub concurrency is bounded cross-process with SQLite leases.
- sensor 429/5xx/timeout is sensor degradation, not target-repo incident.
- no public/product maturity is inferred from repository existence.

## 8. Testing and evidence

CI runs:

```bash
python3 scripts/validate.py
python3 tests/test_behavior.py
python3 scripts/sensors/canary.py
```

Verified on PR #3 merge proof:
- `VALIDATE-OK: 10 jobs`
- `BEHAVIOR-OK: 46 checks`
- cross-process semaphore: 3 admitted / 1 refused
- same-event cross-process claim: exactly 1 EMIT / 9 SILENCE
- `STATE-CANARY-OK`

`scripts/verify_tick.py` proves scheduled tick consumption.
`scripts/verify_slo.py` checks metrics available from persistent Fabric and
Hermes execution state. It must not claim external Telegram delivery proof.

## 9. Rollout and success gates

Before Phase 2 expansion:
- canonical topology reflects current org reality;
- atomic event claim remains green under cross-process tests;
- runtime/WI notifications are semantic findings, not SHA-change alerts;
- state-canary and future delivery-canary claims stay separate;
- every current repo maps to a concern or explicit exclusion;
- no new write authority is introduced.

Machine-checkable locally:
- duplicate event claim regression tests;
- evidence completeness;
- state-canary health;
- p50/p95 job execution SLOs where enough live samples exist.

Requires external delivery/baseline evidence, therefore NOT inferred from
EventStore alone:
- Telegram delivery exactly-once;
- false INCIDENT rate;
- total notification-volume reduction versus frozen baseline.

## 10. Remaining open gates

1. Merge/reconcile the current 24-repo canonical topology in Governance,
   then regenerate exact-head org-state using the existing generator.
2. Shadow-run the v0.4 semantic runtime/WI investigators against direct
   source observations before production Telegram promotion.
3. Add a delivery receipt surface before introducing a true end-to-end
   Telegram delivery canary. Do not fake this with local state.
4. Keep `sentinel-firetest` temporary and remove its rule after the fixture
   is actually deleted.
5. Keep `veranza` topology-only while it remains INTERNAL HOLD.
