# Conformance Map - aftergraph-cron-fabric

This document maps every spec section (CHATGPT-REVIEW-SPEC.md) to the
corresponding verification artifact.  A reviewer can check each row
mechanically with one command.

| Spec section | Claim | Verification | Command |
|---|---|---|---|
| 1 | 16 YAML files (15 jobs/ + 1 jobs/legacy) | test_behavior: catalog pinning | python3 tests/test_behavior.py |
| 1 | jobs/ directory layout matches spec | test_behavior: YAML content validation | python3 tests/test_behavior.py |
| 2 | Event state machine (OPEN/HEALTHY -> EMIT/RESOLVED) | event_store.py + test_behavior: "resolve closes" + "return EMITs again" | python3 tests/test_behavior.py |
| 2 | Typed evidence required on every notify+ | sensor_guard + test_behavior: "typed evidence guard" | python3 tests/test_behavior.py |
| 3 | 14 core schedules | validate.py + test_behavior: catalog pinning | python3 scripts/validate.py jobs |
| 3 | Canary (ag-fabric-canary + ag-fabric-delivery pair) | test_behavior: exactly 2 canary YAMLs | python3 tests/test_behavior.py |
| 3 | Legacy (ag-legacy-noise-gate) | test_behavior: exactly 1 legacy YAML | python3 tests/test_behavior.py |
| 4 | RequireTypedEvidence rejects untupled | test_behavior: "typed evidence guard" + "rejects JSON-not-dict evidence" | python3 tests/test_behavior.py |
| 4 | CrossProcessSemaphore correctness | test_behavior: "3 holders admitted" + "contender refused" | python3 tests/test_behavior.py |
| 5 | Continuity allowed only on deep-audit jobs | test_behavior: "NO_CONTINUITY enforced on non-allowlist" + "allowlist exceptions work" | python3 tests/test_behavior.py |
| 5 | Dedup via fingerprint | test_behavior: "rejects duplicate fingerprint" | python3 tests/test_behavior.py |
| 5 | Retry-After (RFC 9110 delta-seconds + HTTP-date) | test_behavior: 4 retry_after_seconds checks | python3 tests/test_behavior.py |
| 5 | Audit log | event_store.py: log_audit + test_behavior: "record writes receipt" | python3 tests/test_behavior.py |
| 7 | CI runs validate + tests + canary | CI workflow (.github/workflows/ci.yml) | gh api .../check-runs |
| 8 | validate.py validates 16 YAML files | validate.py output | python3 scripts/validate.py jobs |
| 8 | test_behavior.py runs 115 behavioral checks | test_behavior output | python3 tests/test_behavior.py |
| 8 | Canary self-test | canary.py exit 0 | python3 scripts/sensors/canary.py |
| 8 | Canary --on-demand pre-deploy probe | canary --on-demand: EMIT -> OK (non-destructive) | python3 scripts/sensors/canary.py --on-demand |
| 9 | Duplicate rate measurable | verify_slo.py check_duplicate_rate | python3 scripts/verify_slo.py --jobs-dir jobs --events-db state/events.sqlite --canary-db state/canary.sqlite --executions-db EXEC_DB --jobs-json JOBS_JSON |
| 9 | Evidence completeness measurable | verify_slo.py check_evidence_completeness | (same command) |
| 9 | Canary emissions complete + receipts reconciled | verify_slo.py check_canary + check_canary_receipts | python3 scripts/verify_slo.py --jobs-dir jobs --events-db state/events.sqlite --canary-db state/canary.sqlite --executions-db EXEC_DB --jobs-json JOBS_JSON --receipts-dir deploy/receipts --baseline state/baseline.json |
| 9 | Baseline volume vs frozen reference | verify_slo.py check_baseline_volume (missing/invalid baseline = error) | (same command) |
| 9 | Fail-closed sources/mappings/state | verify_slo.py check_sources + unmapped-job error + absent-state error | (same command) |
| 9 | Per-job p50/p95 vs detection_slo | verify_slo.py check_slos + test_behavior: "SLO-VERIFIED when runs meet" + "VIOLATION when runs exceed" | python3 tests/test_behavior.py |
| 9 | Scheduled tick verification | verify_tick.py (next_run_at advanced + non-manual execution) | python3 scripts/verify_tick.py --jobs-json JOBS_JSON --executions-db EXEC_DB |
| 10.1 | SQLite chosen over file-lock | test_behavior: xproc semaphore test (3 holders + 1 contender) | python3 tests/test_behavior.py |
| 10.2 | Canary --on-demand pre-deploy | scripts/sensors/canary.py --on-demand | python3 scripts/sensors/canary.py --on-demand |
| 10.3 | Ack re-arms event (not permanent freeze) | test_behavior: "resolve closes" + "return EMITs again" + event_store.py | python3 tests/test_behavior.py |
| 11 | Delivery receipt contract (sha256-attested, deterministic filename) | docs/delivery-receipts-spec.md + delivery_canary.py self-test + test_behavior: "delivery canary self-test with synthetic receipt" | python3 tests/test_behavior.py |
| 11 | Delivery canary fails closed (no receipt / tampered sha / wrong fingerprint) | test_behavior: 3 fail-closed checks | python3 tests/test_behavior.py |
| 12 | Canary pair as one invariant (state proves local, delivery proves external, neither alone sufficient) | docs/canary-pair-architecture.md + both canary scripts + receipt specs | n/a (architectural doc) |
| 13 | v0.5 P0 fabric concern sensors (merge-queue-stall, org-suite-liveness, public-provenance, research-freeze-watch) | scripts/sensors/{merge_queue_stall,org_suite_liveness,public_provenance,research_freeze_watch}.py + jobs/*.yaml + contracts/queue-policy.yaml + contracts/freeze-manifest.yaml + test_behavior: 7+ checks per sensor | python3 tests/test_behavior.py |
| 13.1 | merge-queue-stall scope from contracts/queue-policy.yaml (no hardcoded repo list) | contracts/queue-policy.yaml + test_behavior: "merge-queue-stall reads from contracts/queue-policy.yaml" + "has no hardcoded repo list" | python3 tests/test_behavior.py |
| 13.2 | public-provenance scope from contracts/sources.yaml | scripts/sensors/public_provenance.py + test_behavior: "public-provenance reads from contracts/sources.yaml" | python3 tests/test_behavior.py |
| 13.3 | research-freeze-watch scope from freeze manifest + amendments | contracts/freeze-manifest.yaml + contracts/freeze-amendments.yaml + test_behavior: "research-freeze-watch reads freeze manifest" + "reads amendments" | python3 tests/test_behavior.py |
| 13.4 | org-suite-liveness CORE_REPOS is a documented module-level constant (not yet policy-driven) | test_behavior: "org-suite-liveness: CORE_REPOS is a module-level constant (documented)" | python3 tests/test_behavior.py |
| 14 | v0.5.1 synthetic proof: all four P0 sensors detect their failure class offline, no live GitHub, no skip counted as proof | tests/test_synthetic_fixtures.py: FIXTURES-OK 17/17 | python3 tests/test_synthetic_fixtures.py |
| 14.1 | Cross-run dedupe: persistent per-sensor EventStore; repeat finding SILENCEs across runs | test_synthetic_fixtures: dedupe scenarios (merge-queue repeat, provenance repeat, freeze repeat) | python3 tests/test_synthetic_fixtures.py |
| 14.2 | Observed recovery: stalled -> RESOLVED (HEALTHY) -> re-arm -> EMIT again | test_synthetic_fixtures: merge-queue resolved_then_rearm scenario | python3 tests/test_synthetic_fixtures.py |
| 14.3 | Daily shadow summary receipt (immutable, one per day, no Telegram delivery) | scripts/v05_shadow_summary.py + jobs/ag-v05-shadow-summary.yaml + deploy/receipts/v05-shadow-summary-*.json (live, gitignored) | python scripts/v05_shadow_summary.py |
| 14.4 | Telegram receipt bridge produces normative delivery receipts on positive renderer proof (chain: claim -> renderer -> receipt -> delivery canary verifies); NOT promoted to production delivery | scripts/telegram_receipt_bridge.py (build_receipt_body + write_delivery_receipt) + docs/delivery-receipts-spec.md + test_behavior: "bridge receipt accepted by delivery canary" + "chain fails closed on rebound bridge receipt" | python3 tests/test_behavior.py |

## Quick conformance check

Run the following to verify all spec claims in one pass:

```
python3 scripts/validate.py jobs       # 16 jobs
python3 tests/test_behavior.py         # 115 behavioral checks
python3 scripts/sensors/canary.py      # canary self-test
```

All three must exit 0.  CI runs these automatically on every push.
