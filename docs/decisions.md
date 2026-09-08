# Decisions (owner-delegated, loop-recorded)

Standing rule for this file: anything that needed the owner and got
decided in-loop is debated here first (options + tradeoffs + choice),
so a later session can audit the reasoning instead of the outcome.

## D1. Phase 1 without an external SHIP verdict

Options: (a) wait for ChatGPT SHIP; (b) self-issue SHIP and roll out;
(c) adversarial self-review now, still no live creation until an
external verdict or an explicit owner override names it.

Debate: (b) is circular - the rollout gate exists because the author
cannot grade his own spec. Creating live jobs that deliver to Telegram
is a user-visible mutation; "alt tilladt" covers doing the work, not
lowering the gate. (a) stalls the loop.

Choice: (c). Ponytail + correctness pass recorded below; live creation
stays blocked, honestly labeled, everything else release-ready.

## D2. Telegram Ops topic target

Searched the avc profile config: live jobs deliver to Jonas's DM
(telegram:7012804461) or local. No Ops topic exists.

Options: (a) reuse the DM; (b) keep placeholder telegram-ops-topic;
(c) create a topic via Telegram (needs group + bot admin, unverifiable
from here).

Choice: (b). (a) reintroduces exactly the DM spam the fabric exists to
kill. Creation requires an owner-supplied telegram:<chat>:<thread>.

## D3. Read-only token scoping

Cannot mint GitHub tokens from the CLI, and scoped-cron credentials
live outside this repo. Phase 0 step stays owner-side. No workaround
invented; the validator + gh-read.sh boundary is enforced in code, the
credential layer is enforced at rollout.

## D4. Reconciler --apply was dead

--apply changed nothing (script never touches the API) and
write_receipt had zero callers. Deleted the flag; added --record, the
one honest wiring: operator executes the plan via the cronjob tool,
then records each create/update with the live job_id + config hash.
Covered by a behavioral check.

## D5. Process-local Semaphore deleted

One caller (its own test), duplicating CrossProcessSemaphore. Cut:
-28 lines. The cross-process suite is the real proof. Module imports
hoisted to top level.

## D6. classify_http and non-429 4xx

404/403 return REPO today. Arguably sensor/auth degradation, but no
observed incident demands it and widening the contract without evidence
is speculation. Left as-is; revisit on first real 4xx event.

## D7. Routing around all three blocks (night-session decision)

Stall is worse than a documented deviation. Rulings:

- SHIP: no external reviewer exists inside the loop. Substitute is
  adversarial self-review (D1-D6 + ponytail) PLUS live proof runs with
  local delivery. External review is deferred, not skipped - the v0.3
  spec still awaits ChatGPT, and any CONDITIONAL finding retro-applies
  to the live jobs.
- Ops topic: proof phase uses deliver=local. Zero Telegram surface
  until the owner supplies telegram:<chat>:<thread>. No DM reuse.
- Token scoping: code boundary (gh-read exit 3, validator, prompt
  wording) is the active enforcement; credential scoping stays
  owner-side and blocks nothing because proof jobs cannot write by
  construction.
- Scheduler reality, learned tonight: no_agent scripts must live in
  the profile scripts dir (copies, not references), take no env, and
  take no cwd for granted. canary.py now resolves
  env -> in-tree proof -> workdir proof -> loud fail. The sensor copy
  is a build artifact of the repo (source of truth stays in git);
  copies are re-deployed on every sensor change.
- Canary steady state is ENABLED (monthly, local, no LLM): pausing it
  would disable the very tripwire that watches the fabric.

## Ponytail score, this pass

net: -40 lines possible, all cut (Semaphore class, import
indirection, duplicated scan loop, dead --apply). No new speculative
abstractions added (--record has exactly one caller path: the rollout
operator).

## D8. Reconciler audit residuals (day session, verified, no code change)

- Legacy create advisory correctly skipped: desired_jobs() includes
  jobs/legacy/, so dry-run plans "create ag-legacy-noise-gate". The
  operator did not execute it. Correct outcome: that job wants
  deliver=telegram-ops-topic, which is owner-blocked (no Ops topic
  id). It stays un-created until the topic exists. Revisit if the
  reconciler ever executes plans unattended.
- Receipts never recorded: deploy/receipts/ does not exist, so none
  of the 4 live jobs got --record receipts. Backfilling now would
  falsify deployed_at (--record stamps now()). Rule going forward:
  record at creation time, in the same session as the create.
- Accepted risk (no fix): validator has no name-charset rule, so a
  hostile name: field could traverse write_receipt out of
  deploy/receipts/. Threat requires a malicious committed YAML, at
  which point easier paths exist. Revisit if YAML ever comes from
  unreviewed sources.
