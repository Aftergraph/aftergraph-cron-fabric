# Phase 1 live proof record

Frozen: 2026-09-08 ~02:15 CEST. append-only; new runs add lines, never rewrite old ones.

## Result

- ag-fabric-canary 79c921bb5463: GREEN (deleg_df27617c, 0.34s). CANARY-OK, next 2026-10-01.
- ag-runtime-paritet 97323ff8ddbb: GREEN re-proof (deleg_f55b8d62, 342s; first run deleg_abd0780d 956s, weak). Baseline OPEN.
- ag-wi-contract f33d3564cbb0: GREEN re-proof (deleg_313eb382, 141s; first run deleg_7b649874 944s, weak). Baseline OPEN.
- ag-sentinel-release 50159b84a34f: PENDING. First run deleg_9482cb79 weak (75s, local recon only). Manual re-proof fired ~01:30 never landed (stuck fire flag, pause+resume does not clear it). Rerouted: job left ENABLED, 09:00 scheduled tick is the re-proof vehicle. Re-pause after.

## POST-FREEZE APPENDIX (append-only from 2026-09-08 09:21)

- ag-sentinel-release 50159b84a34f: VERIFIED 2026-09-08 09:16:18 (manual fire
  09:12, execution e8122786 completed, output 2026-09-08_09-16-18.md).
  EMIT with cites: rulepack v1.7.0 blob eed0143791c58fc94bcc576e40e092ada8a1322d;
  sentinel-firetest PRESENT (temporary rule active); sentinel|release recorded
  OPEN in state/events.sqlite (rulepack_sha=eed014...;version=1.7.0;firetest=PRESENT).
  Read-only throughout (gh-read.sh GET-only). Receipt recorded
  deploy/receipts/ag-sentinel-release.json (config_hash 2a76d1058437).
- 09:00 scheduled tick did NOT dispatch (root cause falsified, source-verified:
  cron/jobs.py L2928 completed_occurrence; the 01:25 direct run's
  scheduled_instant=09:00 consumed the slot -> next_run advanced to 09-09).
  Fix: cleared inherited scheduled_instant on the 09:12 run (would have
  suppressed tomorrow's tick) and on canary's direct run (Oct-1 slot).
  Tomorrow 09:00 tick should now fire; verify.
- PHASE 1 LIVE END-TO-END: all 4 jobs now have verified live runs
  (canary + 3 sensors). Standing unchanged: deliver=local, terminal-only,
  job remains ENABLED (next scheduled tick 2026-09-09T09:00+02).

## Fingerprints on disk (state/events.sqlite, verified 02:08)

- wi|contract OPEN fp 80e462a53a58100c evidence boundary=65803732ae866b6eb4f956624cf40e18fbec5a5d;openapi=f5ca4c818e76ca8c7d6cf347c4804d47cf657b37
- runtime|paritet OPEN fp 22040d202e0e2056 evidence registry.ts=36b7f9fdc69d9a2a624ca94adba078de707d74e0;kernel.budget.schema.json=89f52a0405e1c4b4f929ef01217d4196d160f872;delegation-chain.js=f05e65f6ab510a48d6ddf34c8cac1d6902737827;policy.js=43b3eed686bc4f0de1e25da5e6c364e83cf85c6f

## Proven hypothesis

Exact numbered endpoints in the prompt fix agent discipline: wi 944s -> 141s, runtime 956s -> 342s. Repeat runs with unchanged fingerprints stay silent.

## Standing

All Phase 1 jobs deliver=local, terminal-only, paused except sentinel (enabled until 09:00 tick). Gates green at freeze: VALIDATE-OK 10 jobs, BEHAVIOR-OK 22 checks.

## POST-FREEZE APPENDIX 2 (2026-09-08, Codex CONDITIONAL findings closed)

- Correction: the "BEHAVIOR-OK 22 checks" line above is stale. The suite
  grew to 41 checks (SLO paths) and now 50 checks (fail-closed verify_slo
  + canary receipts + baseline volume). Frozen lines are never rewritten;
  this appendix is the current truth. Verify: python3 tests/test_behavior.py
- verify_slo.py now fails closed on unknown sources, unmapped SLO jobs,
  absent state, malformed receipts, uncovered unresolved emissions, and
  missing/invalid baselines. Exit 2 message no longer claims PASS.
- Canary writes immutable per-run receipts (emission/delivery/resolution)
  under deploy/receipts/ (git-ignored machine artifacts).
- "False INCIDENT 0" removed from CHATGPT-REVIEW-SPEC.md section 9: no
  incident tier exists in the fabric (max severity warning/digest).
  Reintroduce only with a real tier + machine check.
- SHIP still requires the 09-09 09:00 scheduled tick + fresh exact-HEAD
  evidence run. Nothing in this appendix declares completion.
