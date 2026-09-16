# Trigger Fabric v0.1 Shadow Pilot Evidence

Date: 2026-09-16
Environment: Hermes VDS `vmi3517816`
Implementation SHA tested: `5e8787436830ec0a2a3d49b39035e1fc2c8b2603`
Mode: shadow/read-only with respect to Hermes

## Safety boundary

The pilot did not modify `/root/.hermes/state.db`, `/root/.hermes/cron/jobs.json`, gateway configuration, agent queues, Telegram delivery, or runtime authority state.

Trigger Fabric receipts were written only inside the isolated worktree under ignored `state/` paths.

## Live Hermes observation

The existing watchdog's pure `observe()` path was loaded without invoking its `main()` path. It classified six durable goal records as:

- `done`: 2
- `paused`: 2
- `never_started`: 1
- `stale_record`: 1
- eligible `not_stale` or `stalled`: 0

v0.1 therefore emitted zero continuity/recovery decisions for live goals. This is the intended fail-closed result: terminal, paused, never-started and stale-record goals do not enter recovery evaluation.

## Controlled stalled-case proof

A synthetic active goal with `idle_minutes=31` was evaluated twice in the isolated worktree:

1. Prior state `ACTIVE` -> `SUSPECTED_STALL`.
2. Prior state `SUSPECTED_STALL` -> `STALLED_CONFIRMED`.

Both receipts used the same occurrence fingerprint, proving timestamp-independent causal identity while preserving distinct decision states. Every receipt carried `shadow_only: true`.

No path in v0.1 can emit `RECOVERED`. Recovery requires a later implementation of authority, dispatch evidence, post-dispatch progress evidence and verification.

## Verification

Required verification for promotion beyond shadow mode:

```text
python3 scripts/validate.py
python3 tests/test_behavior.py
python3 tests/test_trigger_fabric.py
python3 tests/test_hermes_goal_watchdog_adapter.py
python3 scripts/sensors/canary.py
```

v0.1 remains a compatibility pilot. The existing Hermes scheduler is still authoritative and no live cron definition has been replaced.
