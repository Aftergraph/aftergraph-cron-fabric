# Trigger Fabric v0.2 Recovery Control Design

**Status:** design approved in chat on 2026-09-16; implementation pending written-spec review gate.

## 1. Goal

Extend Trigger Fabric v0.1 from observation-only continuity detection into a governed recovery-control slice that can propose, authorize, dispatch, observe, and verify Hermes mission recovery without writing directly to Hermes state storage.

v0.2 must prove this lifecycle:

```text
STALLED_CONFIRMED
→ RECOVERY_PROPOSED
→ AUTHORITY_PENDING
→ AUTHORIZED | DENIED | REVIEW_REQUIRED
→ RECOVERY_DISPATCHED
→ RECOVERING
→ RECOVERY_VERIFYING
→ RECOVERED | RECOVERY_UNVERIFIED | RECOVERY_FAILED
```

`RECOVERED` is a verified outcome, never an executor claim.
## 2. Architectural boundaries

- Trigger Fabric owns recovery proposal formation, lifecycle state, idempotency, attempt budgets, and recovery evidence references.
- Hermes remains the execution runtime and session owner.
- Trigger Fabric MUST NOT update `~/.hermes/state.db` directly.
- Hermes session continuation must use a supported runtime surface, initially `hermes --resume <session_id> ...`.
- `hermes resume` is explicitly out of scope because it removes the global ESTOP sentinel; it is not a session-resume primitive.
- Policy/EGAC owns authorization semantics. v0.2 may provide a local policy adapter for the pilot, but the adapter must emit an explicit AuthorityReceipt and fail closed.
- WORKS/Runtime integration remains the long-term execution boundary. The v0.2 Hermes adapter is a compatibility execution adapter, not a replacement for that architecture.
- Sentinel or an independent verification adapter owns the final recovery verdict.
- Telegram/Google Chat/War Room are projections only and cannot change recovery truth.

## 3. Recovery eligibility

A recovery proposal may be formed only when all of the following are true:

1. goal status is exactly `active`;
2. two semantically equivalent stalled observations exist;
3. no non-expired session turn lease exists;
4. the session exists and is not ended, archived, or hidden;
5. `last_turn_at > 0`;
6. no current pause/revocation/budget block applies;
7. no recovery for the same causal incident was dispatched inside the dedupe window;
8. recovery attempt budget has not been exhausted;
9. observation freshness is within policy.
## 4. RecoveryProposal contract

Required fields:

```text
proposal_id
subject_ref
session_id
stall_occurrence_refs[]
reason_codes[]
baseline_last_turn_at
baseline_last_activity_at
proposed_action
risk
attempt_number
dedupe_key
created_at
expires_at
```

`proposed_action` for v0.2 is limited to `continue_existing_session`.

The proposal is evidence-backed but carries zero execution authority.

## 5. AuthorityReceipt contract

Required fields:

```text
authority_receipt_id
proposal_id
policy_ref
outcome = AUTHORIZED | DENIED | REVIEW_REQUIRED
constraints
issued_at
expires_at
actor_ref
```
## 6. RecoveryIntent and execution

`RecoveryIntent` contains:

```text
intent_id
proposal_id
authority_receipt_ref
session_id
action = continue_existing_session
continuation_prompt
idempotency_key
deadline
created_at
```

The Hermes adapter must execute with shell-free argv construction and an exact session identifier. It must not interpolate untrusted shell text.

Initial execution shape:

```text
hermes --resume <session_id> --oneshot <continuation_prompt>
```

The precise invocation must be verified against the installed Hermes CLI before implementation and covered by an argv contract test.

An executor success exit code means only `EXECUTION_REPORTED`; it does not mean `RECOVERED`.
## 7. ExecutionReceipt

Required fields:

```text
execution_receipt_id
intent_id
session_id
runtime_ref
started_at
finished_at
exit_code
stdout_ref
stderr_ref
baseline_last_turn_at
baseline_last_activity_at
```

Receipts are immutable evidence records. Raw stdout/stderr may be stored separately and referenced by hash/path.

## 8. Post-dispatch progress observation

After dispatch, Trigger Fabric re-observes the same exact session.

Progress exists only when at least one verifiable post-dispatch signal advances beyond the baseline, such as:

- `last_turn_at_after > baseline_last_turn_at`;
- session activity timestamp advances after dispatch;
- a linked Work/Attempt/Evidence event is produced after dispatch;
- an explicitly configured domain progress signal advances.

Process start, PID existence, command exit zero, or executor stdout alone are insufficient recovery evidence.
## 9. Verification

A verification adapter receives the proposal, authority receipt, execution receipt, and post-dispatch observations.

It may emit:

```text
VERIFIED_RECOVERED
RECOVERY_UNVERIFIED
RECOVERY_FAILED
```

`VERIFIED_RECOVERED` requires post-dispatch progress evidence tied to the same session and causal incident.

`RECOVERY_UNVERIFIED` is used when execution occurred but evidence cannot prove forward progress.

`RECOVERY_FAILED` is used when execution failed or subsequent observations prove the mission remains stalled.

## 10. Attempt budget and escalation

Default v0.2 policy:

- dedupe window: 30 minutes per causal recovery incident;
- maximum unsuccessful attempts: 2;
- third recovery dispatch is forbidden;
- after two unsuccessful attempts the lifecycle becomes `ESCALATION_REQUIRED`;
- a verified recovery resets the incident attempt budget only after the original stall incident is resolved;
- a new causally distinct future stall may receive a new budget.
## 11. Pilot authority policy

For the controlled live pilot, v0.2 uses a dedicated pilot policy rather than unrestricted production recovery.

The pilot policy may return `AUTHORIZED` only when:

- `pilot=true` is explicit on the recovery subject;
- session binding matches the configured pilot session exactly;
- risk is `low`;
- action is `continue_existing_session`;
- no lease, pause, revocation, or budget blocker exists;
- the AuthorityReceipt expires before the next recovery window.

All other subjects return `DENIED` or `REVIEW_REQUIRED`.

Production auto-recovery is outside the v0.2 pilot acceptance gate.

## 12. Persistence

v0.2 stores its own append-only recovery journal under Trigger Fabric state. It does not reuse Hermes internal state tables.

The journal records proposals, authority receipts, intents, execution receipts, observations, verification receipts, attempt counts, lifecycle transitions, timestamps, actor/runtime refs, and causal fingerprints.
## 13. Concurrency and idempotency

- Recovery proposal claiming is atomic.
- One causal incident may have at most one live authorized intent.
- Repeated scheduler ticks must converge on the same dedupe key.
- Duplicate worker execution must be rejected by idempotency key.
- Expired authority receipts cannot be reused.
- A new observation that changes the causal fingerprint invalidates stale proposals.
- Process crash/restart must not reset attempt counts.

## 14. Notifications

Routine scheduler ticks remain silent.

Human projection is warranted only for material state changes:

```text
RECOVERY_PROPOSED requiring review
RECOVERY_DENIED on a material mission
RECOVERY_FAILED
ESCALATION_REQUIRED
VERIFIED_RECOVERED
```

Notification delivery is downstream of canonical state and cannot alter it.
## 15. Testing strategy

v0.2 implementation is test-first.

Required test groups:

1. contract validation for proposal, authority, intent, execution, and verification receipts;
2. lifecycle transition tests including invalid-transition fail-closed behavior;
3. paused/done/cleared/lease-active/revoked/budget-exhausted subjects never dispatch;
4. exact session binding and shell-free Hermes argv construction;
5. atomic duplicate proposal/intent suppression across processes;
6. authority expiry and stale-proposal invalidation;
7. attempt budget persistence across process restart;
8. execution exit zero without progress cannot become recovered;
9. post-dispatch `last_turn_at` advance can satisfy the minimum pilot progress criterion;
10. two failed attempts produce `ESCALATION_REQUIRED` and prohibit a third dispatch;
11. synthetic end-to-end pilot from stall to verified recovery;
12. regression suite for all v0.1 and existing Fabric behavior.

## 16. Live rollout

Phase A: implement and test locally in an isolated worktree.

Phase B: deploy read-only recovery proposal generation beside v0.1 shadow.

Phase C: create one controlled Hermes pilot session and deliberately allow it to become eligible for recovery.
Phase D: issue a pilot AuthorityReceipt and dispatch only that exact pilot session through the supported Hermes session continuation surface.

Phase E: capture ExecutionReceipt and post-dispatch observations.

Phase F: require independent verification before producing `VERIFIED_RECOVERED`.

Phase G: compare v0.2 behavior with the legacy watchdog for false positives, missed stalls, duplicate actions, and notification volume.

The old watchdog remains enabled until the comparison gate is passed.

## 17. Acceptance criteria

v0.2 is accepted only when all are true:

1. no direct writes to Hermes `state.db` occur;
2. inactive or blocked sessions cannot generate executable RecoveryIntent records;
3. recovery execution uses an exact session identifier and shell-free argv;
4. duplicate scheduler ticks produce at most one executable intent per causal incident;
5. no expired AuthorityReceipt can dispatch;
6. executor success alone cannot produce `RECOVERED`;
7. `VERIFIED_RECOVERED` always cites post-dispatch progress evidence;
8. two unsuccessful attempts force escalation and a third attempt is rejected;
9. process restart preserves dedupe and attempt state;
10. the controlled VDS pilot completes the full evidence chain;
11. all existing Fabric tests and v0.1 tests remain green;
12. production recovery remains disabled outside the explicit pilot policy.

## 18. Non-goals

- replacing WORKS/Runtime with Hermes;
- unrestricted autonomous production recovery;
- direct modification of Hermes goal records;
- treating notifications as evidence;
- declaring recovery from process liveness or exit codes;
- redesigning the whole Trigger Fabric in this release.
