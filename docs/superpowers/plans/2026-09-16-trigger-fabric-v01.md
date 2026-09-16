# Trigger Fabric v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shadow-mode Trigger Fabric vertical slice that converts mission liveness observations into governed continuity decisions and auditable shadow receipts without changing live Hermes behavior.

**Architecture:** v0.1 adds deterministic TriggerOccurrence and Mission Continuity primitives inside aftergraph-cron-fabric. Existing cron remains authoritative for scheduling while a shadow adapter evaluates equivalent observations, deduplicates them, produces decision-state transitions, and records receipts. No runtime dispatch, authority write, Telegram delivery, or live job replacement is permitted in v0.1.

**Tech Stack:** Python 3 standard library, YAML/JSON contracts, existing SQLite EventStore patterns, repository-native tests.

**Spec:** `docs/superpowers/specs/2026-09-16-trigger-fabric-vnext-design.md`

## Global Constraints

- Shadow mode only: no live execution dispatch, no GitHub writes, no Telegram sends.
- Trigger, decision, authority, execution, evidence, verification and outcome remain distinct records.
- `RECOVERED` cannot be emitted without post-dispatch progress evidence plus verification; v0.1 therefore cannot emit `RECOVERED`.
- Preserve existing 17-job behavior and read-only safety guarantees.
- Exact-SHA evidence only; stale receipts are not current evidence.
- Fail closed on malformed observations or unsupported state transitions.

---

### Task 1: TriggerOccurrence contract and validator

**Files:**
- Create: `contracts/trigger-occurrence.schema.json`
- Create: `scripts/trigger_fabric.py`
- Create: `tests/test_trigger_fabric.py`

**Interfaces:**
- Produces: `TriggerOccurrence.from_dict(data)`, `TriggerOccurrence.fingerprint()` and `validate_trigger_occurrence(data)`.
- Fingerprint inputs: trigger id, subject ref, cause class, normalized evidence refs; timestamps must not create duplicate identities.

- [ ] Write failing tests for valid occurrence parsing, malformed occurrence rejection, and timestamp-independent fingerprints.
- [ ] Run `python tests/test_trigger_fabric.py`; verify the tests fail because the implementation is absent.
- [ ] Implement the minimal immutable TriggerOccurrence model and validation logic.
- [ ] Re-run `python tests/test_trigger_fabric.py`; verify green.
- [ ] Run `python scripts/validate.py` and `python tests/test_behavior.py`.
- [ ] Commit as `feat(trigger-fabric): add occurrence contract`.

### Task 2: Mission continuity state machine

**Files:**
- Modify: `scripts/trigger_fabric.py`
- Modify: `tests/test_trigger_fabric.py`

**Interfaces:**
- Consumes: validated `TriggerOccurrence`.
- Produces: `ContinuityDecision` with one of `ACTIVE`, `SUSPECTED_STALL`, `STALLED_CONFIRMED`, `RECOVERY_PROPOSED`, `RECOVERY_REQUESTED`, `RECOVERY_UNVERIFIED`, `ESCALATION_REQUIRED`.

- [ ] Write failing tests for active, suspected stall, confirmed stall, recovery proposal, and fail-closed invalid transition behavior.
- [ ] Run the focused tests and verify expected failures.
- [ ] Implement deterministic transition rules with explicit prior-state input.
- [ ] Verify focused tests pass.
- [ ] Add a regression test proving observation alone can never produce `RECOVERED`.
- [ ] Run the full repository test baseline.
- [ ] Commit as `feat(continuity): add governed mission state machine`.

### Task 3: Shadow receipt and deterministic dedupe

**Files:**
- Modify: `scripts/trigger_fabric.py`
- Create: `scripts/trigger_shadow.py`
- Modify: `tests/test_trigger_fabric.py`

**Interfaces:**
- Consumes: `TriggerOccurrence` and `ContinuityDecision`.
- Produces: immutable JSON receipt with `receipt_id`, `occurrence_fingerprint`, `decision_state`, `subject_ref`, `observed_at`, `source_refs`, `evidence_refs`, `shadow_only: true`.
- Persists only under `state/trigger-fabric/`; duplicate fingerprints must not create a second decision receipt.

- [ ] Write failing tests for receipt shape, deterministic dedupe, malformed receipt rejection and shadow-only invariant.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement atomic local receipt persistence using create-if-absent semantics.
- [ ] Verify focused tests pass and duplicate input returns the original receipt identity.
- [ ] Run full validation, behavior suite and state canary.
- [ ] Commit as `feat(trigger-fabric): add shadow decision receipts`.

### Task 4: Hermes goal-watchdog shadow adapter

**Files:**
- Create: `scripts/adapters/hermes_goal_watchdog.py`
- Create: `tests/test_hermes_goal_watchdog_adapter.py`
- Create: `deploy/trigger-fabric-v01-shadow.example.json`

**Interfaces:**
- Consumes: normalized goal observations exported from Hermes state.
- Produces: TriggerOccurrence records with trigger id `mission.continuity.observed` and calls the v0.1 continuity evaluator.
- Must never write Hermes `state.db`, enqueue an agent, send Telegram, or claim recovery.

- [ ] Write failing tests for ACTIVE, STALLED and duplicate goal observations plus forbidden side effects.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement a pure normalization adapter and CLI accepting JSON input/file.
- [ ] Verify focused tests pass.
- [ ] Run the adapter against a synthetic stalled goal and inspect the generated shadow receipt.
- [ ] Run all repository verification commands again.
- [ ] Commit as `feat(hermes): add continuity shadow adapter`.

### Task 5: VDS shadow pilot evidence

**Files:**
- Create at runtime only: `state/trigger-fabric/` receipts; do not commit them.
- Modify: `docs/trigger-fabric-v01-pilot.md`

**Interfaces:**
- Consumes: read-only snapshots from the live Hermes goal store.
- Produces: comparison evidence between current watchdog classification and v0.1 continuity classification.

- [ ] Copy only the minimum read-only goal observation fields needed from VDS into adapter input; never copy secrets or unrelated state.
- [ ] Run v0.1 shadow evaluation without changing `/root/.hermes/cron/jobs.json`.
- [ ] Record counts for observations, dedupes, state classifications, recovery proposals and any semantic disagreements.
- [ ] Document exact commit SHA, commands and limitations in `docs/trigger-fabric-v01-pilot.md`.
- [ ] Re-run validation, behavior suite, v0.1 focused tests and canary.
- [ ] Commit as `docs(trigger-fabric): record v01 shadow pilot evidence`.
