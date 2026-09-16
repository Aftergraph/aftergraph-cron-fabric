# Trigger Fabric vNext — Design Specification

**Status:** Proposed architecture for review  
**Date:** 2026-09-16  
**Scope:** Replace Cron Fabric's scheduler-first control flow with a governed Trigger Fabric integrated with Aftergraph operational reality, ontology, authority, execution, evidence, verification, and learning.

## 1. Problem

The current Cron Fabric is optimized around `sensor -> EventStore -> Telegram`. That model is now behind the platform architecture.

Live Hermes jobs also bypass the newer control plane: several directly inspect repositories, infer actionability, and notify Telegram without first creating ontology-backed decisions, authority receipts, execution intents, or independently verified outcomes.

`goal-watchdog.py` additionally records `recovery_injected` when it only emits stdout/audit text; no runtime resume is proven. This is a semantic correctness bug.

## 2. Design goal

Cron becomes one trigger source among several. Trigger Fabric observes that something *may require attention*; it never owns business truth, policy authority, or arbitrary execution.

Canonical flow:

```text
Time / Event / Condition / Drift / Evidence Expiry / Dependency / Human Intent
  -> TriggerOccurrence
  -> ObservationEnvelope
  -> Operational Reality / Reality Diff
  -> Mission + WorkGraph context
  -> DecisionProposal
  -> Policy + EGAC authority decision
  -> WORKS / Runtime dispatch
  -> Evidence receipts
  -> Sentinel verification
  -> VerifiedOutcome
  -> Learning / research / world-model update
```
## 3. Non-negotiable boundaries

1. Governance owns topology, component semantics, contracts, and declared authority boundaries.
2. Trigger Fabric owns trigger registration, due evaluation, occurrence dedupe, trigger lifecycle, and trigger evidence.
3. War Room owns analysis/control-plane projection; it is not the execution authority.
4. EGAC/policy owns whether autonomous action is allowed, requires review, or must halt.
5. WORKS owns work admission/orchestration; Runtime owns execution/session mechanics.
6. Sentinel owns independent verification and exact-evidence verdicts.
7. Telegram/Google Chat/Studio are delivery or interaction surfaces, never canonical state.
8. Hermes is an agent/runtime participant, never the source of organizational truth.
9. A repository is not assumed to equal a system. Triggers can target ontology entities and components.
10. No state may be called recovered, completed, verified, or healthy without evidence for the corresponding transition.

## 4. Trigger taxonomy

Trigger Fabric MUST support a common envelope for:

- `temporal`: cron/calendar/deadline schedules.
- `interval`: periodic sampling when no event source exists.
- `event`: GitHub, runtime, deployment, message, webhook, or domain event.
- `condition`: predicate becomes true over observed state.
- `drift`: expected vs observed reality diverges materially.
- `dependency`: upstream prerequisite or graph edge changes state.
- `evidence_expiry`: evidence becomes stale under freshness policy.
- `slo`: duration, latency, freshness, or error budget threshold crossed.
- `continuity`: mission/session/work item loses progress or heartbeat.
- `recovery`: previously degraded condition is observed healthy again.
- `manual`: explicit authorized human/system trigger.

Temporal polling is a transport mechanism for detecting conditions, not a different semantic class of decision.
## 5. Core contracts

### `TriggerDefinition`

```text
id, version, kind, subject_ref, predicate/schedule, source_refs[],
freshness_policy, dedupe_policy, severity_policy, authority_policy_ref,
continuation_policy_ref, enabled, owner_ref
```

### `TriggerOccurrence`

```text
occurrence_id, trigger_id, trigger_version, subject_ref, cause,
observed_at, source_refs[], observation_refs[], fingerprint,
dedupe_key, freshness, provenance, sensor_health, correlation_id
```

### `DecisionProposal`

```text
decision_id, occurrence_refs[], subject_ref, mission_ref?, work_ref?,
proposed_action, alternatives[], rationale_refs[], risk, uncertainty,
required_authority, evidence_requirements[], expires_at
```

### `AuthorityReceipt`

```text
decision_id, policy_ref, egac_result, authority_level, granted_scope,
constraints[], issued_at, expires_at, evidence_refs[]
```

### `ExecutionIntent`

```text
intent_id, decision_id, authority_receipt_ref, target_runtime,
operation, immutable_scope, idempotency_key, expected_outcome_contract
```
### `ExecutionReceipt`

```text
intent_id, runtime_ref, attempt_ref, started_at, finished_at?, status,
result_refs[], logs_refs[], side_effect_refs[], provenance
```

### `VerificationReceipt`

```text
verification_id, subject_ref, exact_evidence_refs[], verifier_ref,
verdict, reason_codes[], observed_at, freshness, supersedes_ref?
```

### `VerifiedOutcome`

```text
outcome_id, mission_ref?, work_ref?, decision_ref, execution_ref,
verification_ref, status, achieved_at, evidence_graph_ref
```

## 6. Trigger state machine

```text
REGISTERED
  -> ARMED
  -> OBSERVED
  -> DEDUPED | SUPPRESSED | PROPOSED
  -> AUTHORITY_PENDING
  -> AUTHORIZED | REVIEW_REQUIRED | DENIED | EXPIRED
  -> DISPATCHED
  -> EXECUTING
  -> EXECUTION_REPORTED
  -> VERIFICATION_PENDING
  -> VERIFIED | FAILED | INCONCLUSIVE | STALE
  -> RESOLVED
```

A trigger can re-arm only after its re-arm predicate is satisfied. `ACKNOWLEDGED` is presentation state and MUST NOT imply `RESOLVED`.
## 7. Core evaluation algorithm

Each scheduler tick/event performs a bounded deterministic pipeline before any model wakes:

```text
1. load_due_or_event_trigger()
2. resolve_subject_against_operational_reality()
3. collect_minimum_observation_set()
4. validate_source_freshness_and_sensor_health()
5. evaluate_predicate()
6. compute_semantic_fingerprint()
7. atomically claim occurrence/dedupe key()
8. correlate_with_open mission/work/decision/outcome state()
9. classify: SILENCE | OBSERVE | PROPOSE | ESCALATE_SENSOR_FAILURE
10. if PROPOSE: create DecisionProposal, not an execution
11. authority engine evaluates proposal
12. only authorized intents enter WORKS/Runtime
13. ingest execution evidence
14. request independent verification
15. project VerifiedOutcome and update trigger re-arm state
```

Hard rule: source failure, rate limiting, authentication failure, timeout, parse failure, or stale observation MUST classify sensor health; it MUST NOT be converted into a target-system incident without separate evidence.

## 8. Semantic dedupe and correlation

The existing atomic SQLite claim primitive is retained, but the dedupe identity changes from alert-centric to occurrence-centric.

Canonical dedupe key:

```text
sha256(trigger_id + trigger_version + subject_ref + semantic_fingerprint + epoch_bucket?)
```

`epoch_bucket` is allowed only when recurrence itself is semantically meaningful. A periodic scheduler MUST NOT create a new occurrence merely because another tick happened.

Correlation binds multiple observations to one active decision/incident/mission when they share causal subject and compatible reason codes. This prevents `GitHub drift`, `runtime degradation`, and `evidence stale` from generating three competing workflows for the same root condition.
## 9. Mission Continuity Controller

The current `goal-watchdog` is replaced conceptually by a continuity controller operating on Mission/Work/Attempt/Session/Action evidence rather than one `goal:<session>` record.

Continuity classifications:

```text
HEALTHY             progress evidence within expected window
WAITING             explicit dependency/authority wait
PAUSED              intentional pause; never auto-resume
LEASE_ACTIVE        active execution lease; do not collide
STALLED             expected progress missing, no valid wait/pause/lease
RECOVERY_PROPOSED   a recovery action exists but has no authority receipt
RECOVERY_DISPATCHED authorized recovery was sent to runtime
RECOVERING          runtime receipt exists, progress not yet observed
RECOVERED           new progress evidence observed and independently reconciled
RECOVERY_FAILED     dispatch/execution failed
STALE_RECORD        state refers to dead/missing canonical object
```

`RECOVERED` requires evidence after the recovery action. A log message, notification, or scheduler success is insufficient.

Recovery selection is ordered and fail-closed:

```text
1. refresh observation
2. check pause/revocation/budget/authority
3. check competing lease and mutable-scope collision
4. identify last verified checkpoint
5. choose least-destructive continuation action
6. create DecisionProposal
7. obtain authority
8. dispatch with idempotency key
9. observe progress
10. verify resulting state
```

Automatic recovery MUST be forbidden for revoked authority, expired budget, destructive scope without policy grant, ambiguous mutable ownership, or missing canonical checkpoint.
## 10. Adaptive scheduling algorithm

Schedules are policy inputs, not fixed truth. Every trigger has `min_interval`, `max_interval`, `base_interval`, and optional event subscriptions.

Effective cadence is derived from bounded risk signals:

```text
urgency = max(severity_weight, deadline_pressure, slo_pressure)
change  = normalized_recent_change_rate(subject)
health  = sensor_reliability_penalty
cost    = observation_cost + provider_pressure + concurrency_pressure

cadence_score = clamp((urgency + change) * health / max(cost, epsilon), 0, 1)
interval = interpolate(max_interval, min_interval, cadence_score)
```

Rules:
- event-driven sources suppress redundant polling while event freshness is healthy;
- repeated unchanged observations back off toward `max_interval`;
- a material change temporarily increases cadence within configured bounds;
- rate limit / provider degradation backs off globally per provider;
- critical evidence expiry may increase cadence but never bypass authority;
- jitter is deterministic per trigger ID to avoid synchronized fleet bursts.

## 11. Priority and wake-up algorithm

Expensive reasoning is permitted only after deterministic qualification.

```text
priority = impact * urgency * confidence * freshness * dependency_centrality
           * (1 - duplicate_probability)
```

The score orders work; it does not grant authority. Low confidence with high impact is routed to observation/review, not silently discarded. Agent wake-up requires either a semantic delta, an unresolved high-priority occurrence, or an explicit policy-mandated review.

## 12. Circuit and component awareness

Trigger subjects resolve through Governance P-1 operational reality and component/circuit contracts. Supported targets include organization, system, domain, component, service, deployment, runtime, agent, mission, work item, attempt, interaction thread, decision, evidence, verification, outcome, and research study.

Repo-based selectors remain compatibility adapters only. New definitions MUST prefer stable ontology/component references over repository names where available.
## 13. Hermes migration

Live Hermes cron remains a scheduler/runtime adapter during migration, not canonical configuration.

Required conversions:

- `[bot:atlas] mission sweep` -> `mission.reconciliation.due`; reads canonical WorkGraph/operational reality and emits proposals/deltas.
- `[bot:judge] verification sweep` -> `verification.required` + `verification.freshness`; Judge consumes explicit verification work instead of scanning one legacy repo hourly.
- `[bot:avc] executive morning brief` -> Aftergraph executive projection over verified org state; AVC identity becomes legacy compatibility only.
- `goal-watchdog` -> Mission Continuity Controller with real dispatch and post-dispatch evidence.
- `billing-uptime-monitor` -> service/SLO trigger definition with sensor-health separation and domain ownership.
- Google Chat briefing -> presentation projection built from the same canonical occurrences/outcomes as Telegram/Studio.
- harness scorecard -> evidence/research health trigger rather than an isolated reporting cron.

Hermes `jobs.json` becomes generated/deployed runtime material. Hand-edited live jobs are transitional and MUST carry provenance back to a versioned TriggerDefinition.

## 14. Notification algorithm

Notifications are projections of state transitions, never the event lifecycle itself.

Default suppression rules:
- silence unchanged observations;
- silence successful routine execution unless policy requests receipts;
- coalesce causally related occurrences;
- notify on new required human decision, material degradation, failed recovery, verification failure, or verified resolution after a previously notified incident;
- heartbeat summaries are derived views and MUST NOT mutate trigger state.

Every notification references stable IDs for subject, decision/incident, latest evidence, and current state so an operator can inspect why it exists.
## 15. Persistence and evidence

Trigger Fabric keeps an append-only occurrence/evidence journal plus indexed current projections. SQLite remains acceptable for a single-node runtime, but schemas MUST make later externalization possible without semantic changes.

Required durable records:

- trigger definitions and versions;
- occurrences and semantic fingerprints;
- source observations and freshness metadata;
- correlation links;
- proposal/authority/execution/verification references;
- lifecycle transitions with actor + timestamp + cause;
- delivery receipts as presentation evidence only;
- scheduler leases and idempotency claims.

No model-generated narrative is authoritative evidence by itself.

## 16. Reliability and concurrency

- Atomic claim remains mandatory across processes.
- Scheduler leases use fencing tokens or monotonically increasing generations; expired workers cannot finalize newer claims.
- All execution dispatches carry idempotency keys.
- Provider concurrency and rate-limit budgets are shared across jobs, not independently guessed per schedule.
- Clock skew is explicitly measured; deadline logic uses UTC instants and records scheduler-observed skew.
- Catch-up after downtime is policy-driven: `skip`, `latest_only`, `bounded_replay`, or `all_required`; default is `latest_only` for observations.
- Poison occurrences enter quarantine after bounded retries; retry storms cannot block unrelated triggers.
- Trigger evaluation and notification delivery have separate retry state.

## 17. Security and authority

Sensor credentials are read-only whenever possible. Trigger evaluation cannot mint broader credentials than its source adapter requires.

A trigger definition cannot itself grant execution authority. Authority references are resolved at decision time against current policy, revocation state, budget, mutable-scope ownership, and evidence freshness.

Fail closed on unknown policy, stale revocation state, malformed authority receipt, ambiguous target identity, or evidence whose exact subject/revision cannot be established.
## 18. Testing strategy

Required proof layers:

- contract/schema tests for every core record;
- property tests for dedupe, monotonic lifecycle transitions, re-arm, and authority non-bypass;
- concurrency tests proving exactly-one occurrence claim and fenced completion;
- synthetic trigger fixtures for every taxonomy class;
- fault injection for timeout, 429, stale source, malformed payload, clock skew, duplicate webhook, worker crash, and restart;
- continuity tests proving PAUSED/REVOKED/LEASE_ACTIVE never auto-resume;
- recovery tests proving `RECOVERED` is impossible without post-dispatch progress evidence;
- integration tests for Governance reality resolution, EGAC decisions, WORKS admission, Runtime dispatch, Sentinel verification, and War Room projection;
- migration tests showing legacy Hermes jobs produce equivalent or stricter outcomes without duplicate notifications.

## 19. Rollout

Phase A: introduce vNext contracts and compatibility adapters with zero execution authority.

Phase B: shadow current live Hermes jobs and compare occurrence/decision projections against existing outputs.

Phase C: move notifications to canonical projections while legacy jobs remain read-only fallbacks.

Phase D: enable governed dispatch for continuity recovery behind explicit authority policy and canaries.

Phase E: retire direct AVC-centric cron definitions and generate Hermes runtime schedules from versioned TriggerDefinitions.

Phase F: make War Room/Studio the primary inspection surfaces; Telegram and Google Chat remain concise attention channels.

Rollback is per-trigger and per-adapter. A rollback disables vNext dispatch but preserves observation/evidence journals; it never rewrites history.
## 20. Acceptance criteria

vNext is acceptable only when all are true:

1. No production trigger directly performs privileged work without a current AuthorityReceipt.
2. `RECOVERED` cannot be emitted without post-dispatch progress evidence.
3. Duplicate ticks/webhooks across independent workers create one canonical occurrence.
4. Sensor degradation is distinguishable from target-system degradation in contracts and UI projections.
5. Every live Hermes cron job has a versioned TriggerDefinition or an explicit retirement record.
6. No live job uses AVC as canonical organizational authority; legacy naming is compatibility metadata only.
7. Governance operational reality resolves trigger subjects without a hard-coded secondary org registry.
8. WORKS/Runtime receive idempotent execution intents; Sentinel produces independent verification receipts.
9. Notification volume is change/decision driven rather than tick driven.
10. Restart, scheduler overlap, provider throttling, and stale evidence are covered by executable tests.
11. Exact subject/revision provenance is preserved from observation through VerifiedOutcome.
12. War Room can explain `why did this trigger`, `why was this action allowed`, `what executed`, and `what independently verified it` from durable references.

## 21. Explicit non-goals

- Trigger Fabric is not a general agent framework.
- It does not replace Governance, War Room, WORKS, Runtime, Sentinel, or Trust Gateway/EGAC.
- It does not infer authority from agent identity.
- It does not guarantee real-time execution where upstream sources only support polling.
- It does not create one schedule per repository.
- It does not use notification delivery as proof of system recovery.

## 22. Migration principle

Preserve proven mechanisms, replace stale semantics. Atomic claims, deterministic pre-checks, read-only sensors, delivery receipts, and bounded concurrency remain valuable. The obsolete part is making scheduler jobs and Telegram alerts the center of the system.

The vNext center is the governed lifecycle from observed reality to independently verified outcome.
