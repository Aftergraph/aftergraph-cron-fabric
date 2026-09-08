# Phase 1 live proof record

Frozen: 2026-09-08 ~02:15 CEST. append-only; new runs add lines, never rewrite old ones.

## Result

- ag-fabric-canary 79c921bb5463: GREEN (deleg_df27617c, 0.34s). CANARY-OK, next 2026-10-01.
- ag-runtime-paritet 97323ff8ddbb: GREEN re-proof (deleg_f55b8d62, 342s; first run deleg_abd0780d 956s, weak). Baseline OPEN.
- ag-wi-contract f33d3564cbb0: GREEN re-proof (deleg_313eb382, 141s; first run deleg_7b649874 944s, weak). Baseline OPEN.
- ag-sentinel-release 50159b84a34f: PENDING. First run deleg_9482cb79 weak (75s, local recon only). Manual re-proof fired ~01:30 never landed (stuck fire flag, pause+resume does not clear it). Rerouted: job left ENABLED, 09:00 scheduled tick is the re-proof vehicle. Re-pause after.

## Fingerprints on disk (state/events.sqlite, verified 02:08)

- wi|contract OPEN fp 80e462a53a58100c evidence boundary=65803732ae866b6eb4f956624cf40e18fbec5a5d;openapi=f5ca4c818e76ca8c7d6cf347c4804d47cf657b37
- runtime|paritet OPEN fp 22040d202e0e2056 evidence registry.ts=36b7f9fdc69d9a2a624ca94adba078de707d74e0;kernel.budget.schema.json=89f52a0405e1c4b4f929ef01217d4196d160f872;delegation-chain.js=f05e65f6ab510a48d6ddf34c8cac1d6902737827;policy.js=43b3eed686bc4f0de1e25da5e6c364e83cf85c6f

## Proven hypothesis

Exact numbered endpoints in the prompt fix agent discipline: wi 944s -> 141s, runtime 956s -> 342s. Repeat runs with unchanged fingerprints stay silent.

## Standing

All Phase 1 jobs deliver=local, terminal-only, paused except sentinel (enabled until 09:00 tick). Gates green at freeze: VALIDATE-OK 10 jobs, BEHAVIOR-OK 22 checks.
