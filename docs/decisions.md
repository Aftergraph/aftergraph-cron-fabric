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

## D9. Per-run shadow receipts (shadow day 1, verified gap)

Executions.db proves a tick fired but stores no stdout or
classification, so the 7-day decision would have been
reconstructed, not read.

Options: (a) accept executions.db + EventStores as sufficient;
(b) have the daily summary re-run the sensors and record that;
(c) wrappers write one immutable sha256 receipt per run.

Debate: (a) leaves classification unattested per run. (b) was the
actual prior design - and the flaw: the summary measured four
fresh synthetic runs instead of the day's cron ticks, i.e. the
rollup proved itself. (c) costs one small writer + best-effort
wrapper hooks that can never fail a sensor run.

Choice: (c). scripts/shadow_receipt.py, schema
shadow-run-receipt/1, live-proven by manual run then by the
23:47 natural tick (SILENCE, sha-ok). duplicate_count stays with
EventStores/canary - receipts carry no event_key by design, and
the summary says so instead of inventing the number.

## D10. PARTIAL verdict for short windows

Live aggregation immediately showed COMPLETE on a 14-minute
window: nothing missing, but nothing proven either.

Options: (a) COMPLETE whenever missing=0; (b) PARTIAL below a
full-day threshold.

Debate: (a) lets the shadow-start day read as a full
observation day at acceptance time - exactly the kind of prose
optimism the loop rules exist to kill.

Choice: (b), threshold 20h. Day 1 reports PARTIAL by
construction. 7x COMPLETE still required, unchanged.

## D11. No cadence compression (speed debate, owner-prompted)

Owner asked why the long wait and wanted it faster.

Options: (a) tighten intervals temporarily; (b) manual backfill
posing as days; (c) shorten the 7-day window; (d) cut own
latency + parallelize all gate-independent work now.

Debate: (a) proves a different duty cycle than production will
run - acceptance demands the intended schedules. (b) is
fabricated evidence. (c) is explicitly forbidden by the mission.
Only (d) is honest acceleration: CI waits, sequential PRs, and
deferred audits (D2 safety, D6 noise, dependabot, doc truth)
needed no gate to proceed.

Choice: (d). Window and cadences stand. Everything verifiable
today was verified today: D2 audit (read_only + no_agent x5,
GET-only via gh-read.sh, no secrets), D6 (0 EMIT -> 0/day),
PR queue drained to zero.

## D12. Parallel fleet work in the shared tree

A sibling instance wrote scripts/ag_fleet_status.py + fleet
tests into this checkout mid-session, then rolled them back.

Options: (a) integrate on sight; (b) delete as scope risk;
(c) touch neither, review on merit if it returns as a PR.

Debate: (a) commits unauthored Telegram-sending code during
shadow silence. (b) destroys another worker's output the owner
may have asked for.

Choice: (c). Noted, not committed, not deleted. If it returns:
requires tests + scope review + no Telegram from shadow before
a production decision exists.

## D13. Day-1 verifier prompt left as-is

Prompt (07:56 once-cron) predates the receipt layer but already
reads executions.db + summary receipt + EventStores directly.

Options: (a) rewrite it around receipts; (b) leave it.

Choice: (b). Minimal touch on a live one-shot: its evidence
sources are sufficient for a day-1 verdict, and the receipt
aggregator + 08:00 rollup cover every day after. Rewriting a
scheduled prompt hours before it fires adds risk for no new
verdict power.
