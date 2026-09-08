# Conformance Map - aftergraph-cron-fabric

This document maps every spec section (CHATGPT-REVIEW-SPEC.md) to the
corresponding verification artifact.  A reviewer can check each row
mechanically with one command.

| Spec section | Claim | Verification | Command |
|---|---|---|---|
| 1 | 10 YAML files (8 core + 1 canary + 1 legacy) | test_behavior: catalog pinning | python3 tests/test_behavior.py |
| 1 | jobs/ directory layout matches spec | test_behavior: YAML content validation | python3 tests/test_behavior.py |
| 2 | Event state machine (OPEN/HEALTHY -> EMIT/RESOLVED) | event_store.py + test_behavior: "resolve closes" + "return EMITs again" | python3 tests/test_behavior.py |
| 2 | Typed evidence required on every notify+ | sensor_guard + test_behavior: "typed evidence guard" | python3 tests/test_behavior.py |
| 3 | 8 core schedules | validate.py + test_behavior: catalog pinning | python3 scripts/validate.py jobs |
| 3 | Canary (ag-fabric-canary) | test_behavior: exactly 1 canary YAML | python3 tests/test_behavior.py |
| 3 | Legacy (ag-legacy-noise-gate) | test_behavior: exactly 1 legacy YAML | python3 tests/test_behavior.py |
| 4 | RequireTypedEvidence rejects untupled | test_behavior: "typed evidence guard" + "rejects JSON-not-dict evidence" | python3 tests/test_behavior.py |
| 4 | CrossProcessSemaphore correctness | test_behavior: "3 holders admitted" + "contender refused" | python3 tests/test_behavior.py |
| 5 | Continuity allowed only on deep-audit jobs | test_behavior: "NO_CONTINUITY enforced on non-allowlist" + "allowlist exceptions work" | python3 tests/test_behavior.py |
| 5 | Dedup via fingerprint | test_behavior: "rejects duplicate fingerprint" | python3 tests/test_behavior.py |
| 5 | Retry-After (RFC 9110 delta-seconds + HTTP-date) | test_behavior: 4 retry_after_seconds checks | python3 tests/test_behavior.py |
| 5 | Audit log | event_store.py: log_audit + test_behavior: "record writes receipt" | python3 tests/test_behavior.py |
| 7 | CI runs validate + tests + canary | CI workflow (.github/workflows/ci.yml) | gh api .../check-runs |
| 8 | validate.py validates 10 YAML files | validate.py output | python3 scripts/validate.py jobs |
| 8 | test_behavior.py runs 50 behavioral checks | test_behavior output | python3 tests/test_behavior.py |
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

## Quick conformance check

Run the following to verify all spec claims in one pass:

```
python3 scripts/validate.py jobs       # 10 jobs
python3 tests/test_behavior.py         # 41 behavioral checks
python3 scripts/sensors/canary.py      # canary self-test
```

All three must exit 0.  CI runs these automatically on every push.
