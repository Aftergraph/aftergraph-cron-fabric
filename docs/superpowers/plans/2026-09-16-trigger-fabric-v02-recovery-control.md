# Trigger Fabric v0.2 Recovery Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the governed v0.2 recovery-control slice from verified stall through pilot-authorized Hermes continuation and independent recovery verification.

**Architecture:** Extend the existing v0.1 Trigger Fabric with small focused modules for recovery contracts/state, pilot authority, Hermes execution, progress observation, and verification. Persist all recovery state in Trigger Fabric-owned SQLite/JSON evidence; Hermes state remains read-only and execution uses exact-session CLI argv.

**Tech Stack:** Python 3, stdlib dataclasses/json/sqlite3/subprocess/hashlib/pathlib, existing repo test harness.

**Spec:** `docs/superpowers/specs/2026-09-16-trigger-fabric-v02-recovery-control-design.md`

## Global Constraints

- Never write directly to `~/.hermes/state.db`.
- Only `status=active` goals are recovery candidates.
- Two equivalent stalled observations are required before proposal.
- No active turn lease, pause, revocation, budget block, expired evidence, or exhausted attempt budget may dispatch.
- Pilot policy is the only v0.2 path to AUTHORIZED.
- Hermes execution argv must be a list using exact `session_id`; no shell interpolation.
- Exit code 0 is execution evidence only, never recovery proof.
- `VERIFIED_RECOVERED` requires post-dispatch progress evidence.
- Deduplicate recovery incidents for 30 minutes.
- Maximum unsuccessful attempts per incident is 2; third dispatch is forbidden.
- Production recovery remains disabled outside explicit pilot policy.

---### Task 1: Recovery contracts and durable journal

**Files:**
- Create: `scripts/recovery_control.py`
- Test: `tests/test_recovery_control.py`

**Interfaces:**
- Produces dataclasses `RecoveryProposal`, `AuthorityReceipt`, `RecoveryIntent`, `ExecutionReceipt`, `VerificationReceipt`.
- Produces `RecoveryJournal(path)` with atomic `claim_proposal`, `record_authority`, `claim_intent`, `record_execution`, `record_verification`, and attempt-count queries.

- [ ] Write failing tests proving stable IDs/dedupe, append-only evidence, persistence across reopen, and max-two unsuccessful attempts.
- [ ] Run `python tests/test_recovery_control.py`; expect failure because module is absent.
- [ ] Implement minimal dataclasses plus SQLite journal using `BEGIN IMMEDIATE` for proposal/intent claims.
- [ ] Re-run test and require all Task-1 checks green.
- [ ] Commit `feat(trigger-fabric): add durable recovery journal`.

### Task 2: Eligibility and pilot authority

**Files:**
- Modify: `scripts/recovery_control.py`
- Test: `tests/test_recovery_control.py`

**Interfaces:**
- Produces `evaluate_recovery_eligibility(subject, observations, journal, now)`.
- Produces `evaluate_pilot_authority(proposal, policy, now) -> AuthorityReceipt`.

- [ ] Add failing tests for paused/done/cleared, lease-active, revoked, stale evidence, dedupe window, exhausted budget, mismatched pilot session, high risk, and expired authority.
- [ ] Verify RED with `python tests/test_recovery_control.py`.
- [ ] Implement fail-closed eligibility and explicit `AUTHORIZED|DENIED|REVIEW_REQUIRED` pilot receipts.
- [ ] Verify GREEN and ensure unauthorized cases never create executable intents.
- [ ] Commit `feat(trigger-fabric): gate recovery with pilot authority`.### Task 3: Hermes recovery executor

**Files:**
- Create: `scripts/adapters/hermes_recovery_executor.py`
- Test: `tests/test_hermes_recovery_executor.py`

**Interfaces:**
- Produces `build_resume_argv(session_id, continuation_prompt) -> list[str]`.
- Produces `execute_recovery(intent, runner=subprocess.run) -> ExecutionReceipt`.

- [ ] Write failing tests asserting exact argv `["hermes", "--resume", session_id, "--oneshot", continuation_prompt]`, rejecting empty/invalid session IDs, no `shell=True`, and preserving stdout/stderr refs.
- [ ] Verify RED with `python tests/test_hermes_recovery_executor.py`.
- [ ] Implement shell-free executor with bounded timeout and immutable execution evidence hashes.
- [ ] Verify GREEN; exit-code 0 must still report only `EXECUTION_REPORTED`.
- [ ] Commit `feat(trigger-fabric): add hermes recovery executor`.

### Task 4: Post-dispatch verification

**Files:**
- Modify: `scripts/recovery_control.py`
- Test: `tests/test_recovery_control.py`

**Interfaces:**
- Produces `observe_progress(baseline, after, execution) -> list[evidence]`.
- Produces `verify_recovery(proposal, authority, execution, progress, now) -> VerificationReceipt`.

- [ ] Add failing tests: exit 0/no progress => `RECOVERY_UNVERIFIED`; advanced `last_turn_at` on same session => `VERIFIED_RECOVERED`; wrong session/stale observation => not recovered; nonzero executor failure => `RECOVERY_FAILED`; two unsuccessful attempts => `ESCALATION_REQUIRED`.
- [ ] Verify RED.
- [ ] Implement minimum pilot verifier with causal/session binding and freshness checks.
- [ ] Verify GREEN and prove `VERIFIED_RECOVERED` always cites post-dispatch evidence.
- [ ] Commit `feat(trigger-fabric): verify recovery from progress evidence`.

### Task 5: Controlled pipeline and regression gate

**Files:**
- Create: `scripts/recovery_pilot.py`
- Create: `deploy/trigger-fabric-v02-pilot.example.json`
- Test: `tests/test_recovery_pilot.py`

**Interfaces:**
- Produces one orchestration entry point that observes a supplied exact session, proposes, authorizes, dispatches only when `--execute` and pilot policy permit it, re-observes, verifies, and writes evidence receipts.

- [ ] Write failing synthetic E2E test from two stall observations through `VERIFIED_RECOVERED`, plus denial and escalation paths.
- [ ] Verify RED.
- [ ] Implement dry-run default and explicit execution gate; never mutate Hermes state storage.
- [ ] Run v0.2 tests plus `python scripts/validate.py`, `python tests/test_behavior.py`, `python tests/test_trigger_fabric.py`, `python tests/test_hermes_goal_watchdog_adapter.py`, and `python scripts/sensors/canary.py`.
- [ ] Commit `feat(trigger-fabric): add governed v02 recovery pilot`.

### Task 6: VDS shadow deployment and controlled live pilot

**Files/Runtime:**
- Deploy from exact tested branch HEAD to an isolated VDS worktree.
- Keep existing v0.1 job `cdcfb718630e` and legacy watchdog enabled during comparison.

- [ ] Run v0.2 in proposal-only mode against current Hermes goals and confirm zero dispatch for ineligible subjects.
- [ ] Create a controlled low-risk pilot session with explicit `pilot=true`; capture exact session ID and pre-dispatch timestamps.
- [ ] Allow two equivalent stall observations, issue pilot AuthorityReceipt, then execute exactly one RecoveryIntent through Hermes CLI.
- [ ] Capture ExecutionReceipt, re-observe exact session, and require progress evidence before verification.
- [ ] Run duplicate-tick and second-failure scenarios to prove idempotency and escalation budget.
- [ ] Record live evidence document with exact repo HEAD, job/run IDs, receipt IDs, and comparison to legacy watchdog.
- [ ] Do not disable legacy watchdog or enable general production recovery in v0.2.