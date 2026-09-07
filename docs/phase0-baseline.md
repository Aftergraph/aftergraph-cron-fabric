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
- ag-wi-contract f33d3564cbb0 (every 120m), proof run deleg_7b649874:
  DONE ok/944s. Same pattern: agent worked (downloaded wi frontend
  client to Temp - good location, but via curl instead of gh-read.sh:
  read-only in spirit, constraint violated in letter), ended
  mid-investigation with no verdict and no fingerprint. Tightened
  2-endpoint prompt already live for re-proof. Re-paused.
  RE-PROOF deleg_313eb382 in flight (tightened prompt).
- ag-sentinel-release 50159b84a34f (daily 9am), proof run deleg_9482cb79:
  DONE ok/75s but WEAK - agent only reconnoitered local files (cat/ls),
  never called gh-read.sh, never checked pack SHAs or firetest. Lesson:
  prompts need numbered steps with exact endpoints, not just rules.
  Re-paused. Prompt tightening required before re-proof.
- All created with deliver=local + workdir=repo root + terminal-only
  toolset, then paused. Paused jobs refuse manual run: proof flow is
  resume -> run -> re-pause on outcome.
- Lesson 01:29: the runtime proof agent wrote a remote repo tree dump
  (state/.rt-tree.txt, 435 lines) into the fabric workdir as scratch.
  Removed + state/ fully ignored. Next prompt revision adds: never
  write files into the workdir except through event_store state.
