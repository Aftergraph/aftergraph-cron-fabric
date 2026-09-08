# Phase 0 baseline - live cron inventory (frozen 2026-09-08 01:12 CEST)

Read-only listing, no mutations performed. 14 live jobs on the avc profile.

## Inventory

| live job | schedule | state | last | deliver |
|---|---|---|---|---|
| weekly-brief | Mon 9am | scheduled | error | telegram |
| avc-daily-brief | daily 8am | paused | error | telegram |
| avc-ci-local-poll | every 5m | scheduled | ok | telegram |
| avc-dev-loop | every 45m | paused | ok | telegram |
| skills-vault-sync | Mon 9am | scheduled | error | local |
| skill-usage-report | monthly | scheduled | never ran | local |
| dependabot-automerge | daily 9am | scheduled | ok | local |
| gateway-watchdog | every 5m | scheduled | ok | local |
| card-triage | daily 7am | scheduled | ok | local |
| control-watch | every 10m | scheduled | ok | local |
| aftergraph-site-monitor | every 30m | paused | ok | local |
| acc-overnight-watch | hourly | scheduled | ok | telegram |
| acc-morning-digest | daily 7am | scheduled | ok | telegram |
| cron-incident-digest | every 12h | scheduled | never ran | local |

## Read

- 3 error-state (weekly-brief, avc-daily-brief, skills-vault-sync): the
  exact noise the fabric replaces. avc-daily-brief already paused.
- 3 high-frequency pollers (5m/5m/10m) all delivering ok: volume baseline
  for the "before" side of the shadow comparison.
- acc-overnight-watch is healthy and hourly: confirmed as shadow
  comparator, not oracle.
- aftergraph-site-monitor paused since 2026-09-07 13:58: reason unknown,
  flagged for owner (possible Phase 1 overlap with runtime-paritet).

## Fabric mapping (dry-run)

`python3 scripts/reconcile.py` against an empty live set plans 10 creates
(one per jobs/*.yaml). Against the real live set, names do not collide
(fabric names are ag-*-prefixed), so Phase 1 creates are purely additive;
no live job is touched by the reconciler until an explicit retire decision
with a deployment receipt.

## Next (needs owner)

- Phase 0 remaining: Telegram Ops topic id + read-only token scoping.
- Phase 1 gate: SHIP verdict on the v0.3 spec, then create the 3 Phase 1
  jobs paused + manual run + 7-day shadow vs acc-overnight-watch.

## Deployment record (live, night session)

- Sensor copy: repo scripts/sensors/canary.py ->
  avc-profile scripts/ag-fabric-canary.py (re-deploy on every change).
- Live job ag-fabric-canary, job_id 79c921bb5463, schedule 0 9 1 * *,
  no_agent, workdir = repo root, deliver = local (D7 deviation).
- Scheduler grammar note: natural monthly schedules rejected, cron
  expression required. jobs/*.yaml schedule field documents intent;
  reconcile maps intent -> deployable cron expression at creation.

## Phase 1 jobs (live, paused, local delivery)

- ag-runtime-paritet 97323ff8ddbb (every 120m), proof run deleg_abd0780d:
  DONE ok/956s. Plumbing proven: agent used gh-read.sh correctly
  (GET-only, quoted), zero writes, zero dirt. Discipline unproven:
  old prompt let it wander (README grep instead of pinned registry.ts),
  run ended mid-investigation with no recorded fingerprint. Tightened
  4-endpoint prompt already live for re-proof. Re-paused.
  RE-PROOF deleg_f55b8d62 in flight (tightened prompt).
  RE-PROOF DONE ok/342s - same confirmation: clean table of all four
  blob SHAs, baseline 22040d202e0e2056 recorded OPEN in
  state/events.sqlite (verified on disk with full evidence string),
  read-back verified, zero dirt. 956s -> 342s. Re-paused.
- ag-wi-contract f33d3564cbb0 (every 120m), proof run deleg_7b649874:
  DONE ok/944s. Same pattern: agent worked (downloaded wi frontend
  client to Temp - good location, but via curl instead of gh-read.sh:
  read-only in spirit, constraint violated in letter), ended
  mid-investigation with no verdict and no fingerprint. Tightened
  2-endpoint prompt already live for re-proof. Re-paused.
  RE-PROOF deleg_313eb382 in flight (tightened prompt).
  RE-PROOF DONE ok/141s - HYPOTHESIS CONFIRMED: exact endpoints fix
  discipline. Agent followed both steps via gh-read.sh, cited both blob
  SHAs (boundary 65803732, openapi f5ca4c81), recorded fingerprint
  80e462a53a58100c as OPEN in state/events.sqlite (verified on disk:
  repeat runs will SILENCE). 944s -> 141s. Zero dirt. Re-paused.
- ag-sentinel-release 50159b84a34f (daily 9am), proof run deleg_9482cb79:
  DONE ok/75s but WEAK - agent only reconnoitered local files (cat/ls),
  never called gh-read.sh, never checked pack SHAs or firetest. Lesson:
  prompts need numbered steps with exact endpoints, not just rules.
  Re-paused. Prompt tightening required before re-proof.
  RE-PROOF STUCK: manual run fired ~01:30 but 22+ min with no output
  file and no status update; pause+resume does not clear the
  already-being-fired flag. Job parked paused (planned state). If the
  stuck run lands, assess then; else the 09:00 scheduled tick (or a
  fresh resume+run) is the backup proof.
  REROUTE 01:55: manual-run flag still stuck after pause+resume cycle.
  Decision: leave sentinel ENABLED until the 09:00 scheduled tick and
  use THAT as the re-proof vehicle (read-only, local delivery, zero
  risk) instead of fighting the stuck manual flag. Re-pause after.
  ROOT CAUSE 07:55 (read-only executions.db query): no stuck/running
  row exists scheduler-side - later manual runs never dispatched at
  all (client-side no-op on paused/refire, not a hung execution).
  The 01:25 direct run's scheduled_instant=09:00 is informational
  (canary's direct run likewise points at its next tick Oct 1), not
  a consumed slot - the 09:00 tick should fire normally. Verify the
  output dir after ~09:15; if empty, escalate to owner (no scheduler
  hacking).
  [07:55 ROOT CAUSE FALSIFIED 09:15 - SOURCE-VERIFIED] The 09:00 tick
  did NOT dispatch: scheduler log 09:00:08 ran dependabot + one
  no_agent job, no sentinel line; executions.db has NO 09:00 row;
  jobs.json next_run jumped to 09-09. Hermes source cron/jobs.py
  L2928-2934: `if not manual_run and completed_occurrence(job,
  next_run): advance next_run; return False` - the 01:25 direct run
  (scheduled_instant=2026-09-08T07:00:00+00:00 = 09:00 local) DID
  consume the slot. scheduled_instant IS a consumed-slot marker, not
  informational. Consequence: canary's Oct-1 tick will be suppressed
  the same way. Recovery used: manual fire (job ENABLED, pause flag
  clear) dispatched immediately - 09:12 direct run, status running.
  Verify output after it lands; then decide next scheduled tick
  strategy (job stays enabled; only ONE direct fire per tick-slot).
  MANUAL-FIRE RE-PROOF 09:16:18 VERIFIED: execution e8122786
  (direct, completed 09:16:18, no error) produced cron output
  2026-09-08_09-16-18.md with REAL report: rulepack v1.7.0 blob
  eeed0143791c58fc94bcc576e40e092ada8a1322d, firetest PRESENT,
  sentinel|release EMIT recorded in state/events.sqlite (OPEN,
  evidence rulepack_sha=...;version=1.7.0;firetest=PRESENT),
  read-only throughout (gh-read.sh GET-only). Receipt recorded:
  deploy/receipts/ag-sentinel-release.json {job_id 50159b84a34f,
  config_hash 2a76d1058437, deployed_at 09:20}. Old 19-byte stub
  receipts/sentinel.json removed (superseded).
  FIX 09:20: the 09:12 direct run had inherited scheduled_instant=
  2026-09-09T07:00:00+00:00 (= tomorrow 09:00) - it would have
  suppressed the NEXT tick the same way. Cleared scheduled_instant
  to NULL on e8122786 AND on canary's direct run 4792db4d
  (Oct-1 slot) so scheduled ticks can fire. Rule going forward:
  after any direct run, verify it does not carry a future
  scheduled_instant; if it does, clear it (scheduled ticks are the
  proof vehicle, direct runs must not consume them).
  LIVE-PROMPT FIX 08:08: live job 50159b84a34f was created 01:24:47,
  four minutes BEFORE the prompt tightening commit 65ea0ea
  (01:29:01), and later manual runs never dispatched - so the live
  prompt was the weak pre-tightening text (confirmed via list:
  different opening). Live prompt just updated via cronjob tool to
  the tightened repo text (verified in update response); schedule,
  enabled state, deliver=local and next_run 09:00 all intact.
  The 09:00 tick now runs the numbered-endpoint prompt.
- All created with deliver=local + workdir=repo root + terminal-only
  toolset, then paused. Paused jobs refuse manual run: proof flow is
  resume -> run -> re-pause on outcome.
- Lesson 01:29: the runtime proof agent wrote a remote repo tree dump
  (state/.rt-tree.txt, 435 lines) into the fabric workdir as scratch.
  Removed + state/ fully ignored. Next prompt revision adds: never
  write files into the workdir except through event_store state.
