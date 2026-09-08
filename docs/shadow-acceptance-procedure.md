# Shadow Acceptance Procedure (v0.5.1 → SHADOW_ACCEPTED)

Mechanical gate. No prose verdicts. The 7-day window cannot be
shortened, prorated, or backfilled. Earliest possible acceptance:
2026-09-15 (day 1 = 2026-09-08 partial, first full day 09-09).

## Inputs (all must exist)

1. Seven daily summaries `deploy/receipts/v05-shadow-summary-YYYY-MM-DD.json`,
   schema v05-shadow-summary/2, one per day 09-09 … 09-15, each
   `aggregate.verdict == "COMPLETE"`.
2. Per-run receipts `deploy/receipts/ag-v05-shadow-<job>-*.json`
   covering the window (sha-verified by the summaries).
3. `cron/executions.db` rows for the 5 shadow job_ids.
4. Per-sensor EventStores under `state/` (final state readable).
5. Frozen budget `state/baseline.json` (`max_daily_emissions: 2`).

## Checks (all must hold, in order)

C1. Daily verdicts: 7 consecutive COMPLETE, zero INCOMPLETE,
    zero PARTIAL inside the window. PARTIAL days do not count
    and do not break the streak - they extend it.
    Command: `python -c` loop over the 7 files asserting verdict.

C2. Duplicate EMIT = 0. Three legs (EventStore keeps one row per
    key, so no single table proves this alone):
    (a) code path: `EventStore.claim_event` returns SILENCE on
        identical key+fingerprint (cite `scripts/event_store.py`
        + fixture `test_synthetic_fixtures.py` re-arm cases);
    (b) receipts: no two same-day EMIT receipts for one job share
        identical stdout EMIT lines (compare `stdout_tail`);
    (c) final stores consistent: every OPEN key has a matching
        live condition or a same-window EMIT receipt.

C3. Crashes = 0: no `classification == "CRASH"` in any receipt,
    no non-zero returncode in any summary sensor block.

C4. Mutations = 0: all 5 jobs `read_only: true` + `mode: no_agent`
    (re-run the D2 audit at accept time); sensors only call
    `gh api` GET (guarded by `scripts/gh-read.sh`); receipts all
    show `agent_invoked: false`, `mutation_attempts: 0`.

C5. Tick loss = 0 unexplained: every summary shows
    `missing_ticks == 0` per sensor. Any missing tick must have a
    dated cause (scheduler outage, API outage) or the window
    resets.

C6. Noise within budget: `projected_telegram_messages_per_day`
    summed over 7 days <= 14 (2/day from `state/baseline.json`).
    Healthy state must read as silence.

C7. Synthetic detection 4/4: re-run `test_synthetic_fixtures.py`
    on the acceptance HEAD - 17/17 green, fully offline.

C8. Fail-closed evidence current: `test_behavior.py` fail-closed
    checks green on the acceptance HEAD; any SENSOR-DEGRADED in
    the window must map to a real evidence outage, never to an
    inference.

C9. No ordinary HEAD churn alerts: no EMIT in the window may be
    attributable to routine pushes/merges on watched repos
    (each EMIT, if any, cites its evidence).

## Verdict recording

On pass, write `docs/v05-shadow-acceptance.md` on the exact
acceptance HEAD: date range, the 7 summary hashes, EventStore
final states, fixture/behavior results, and the single line
`SHADOW_ACCEPTED`. That file - not this procedure - is the
gate output Phase D may consume.

On any fail: name the failed check, classify cause, fix, reset
the 7-day window to start at the next full day, resume.
