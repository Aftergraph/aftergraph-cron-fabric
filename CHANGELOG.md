# Changelog

## [Unreleased]

### Added 2026-09-08
- scripts/verify_tick.py: mechanical proof that a scheduled (not manual)
  tick actually fired and consumed its slot - checks jobs.json
  next_run_at advanced, a completed scheduler-source execution with
  matching scheduled_instant in executions.db, and an output file.
  Exit 0 verified / 1 failed / 2 pending. Kills the last Phase-1
  ambiguity (suppressed tick looks like a fired one from jobs.json
  alone). 2 behavior checks.
- retry_after_seconds() in sensor_guard.py: RFC 9110 Retry-After parsing
  (delta-seconds + HTTP-date). 4 new behavior checks.
- Job catalog pinned in tests: 8 core + 1 canary + 1 legacy = 10 jobs,
  4 new behavior checks (catches count prose-drift like the v0.2 9-vs-10
  bug). Suite now 35.
- scripts/verify_slo.py: makes every spec #9 success criterion
  machine-checkable (duplicate rate + evidence completeness from
  events.sqlite, canary emission->RESOLVED from canary.sqlite, per-job
  p50/p95 execution duration vs declared detection_slo in jobs/*.yaml).
  Exit 0 all-pass / 1 violation / 2 insufficient data. 3 new behavior
  checks. Suite now 38.

### Verified 2026-09-08
- Sentinel release sensor re-proven live: manual fire 09:12 -> completed
  09:16:18 (execution e8122786), first full EMIT with cites (rulepack
  v1.7.0 blob eed01437, firetest PRESENT, typed event recorded in
  state/events.sqlite). Read-only throughout; receipt recorded
  deploy/receipts/ag-sentinel-release.json (config hash 2a76d1058437).
- Root cause falsified, source-verified: a direct run inherits the next
  scheduled instant as its scheduled_instant, and jobs.py L2928
  (completed_occurrence) skips a non-manual tick whose slot shows a
  completed execution -> one direct fire consumes the upcoming tick.
  Fix: clear inherited scheduled_instant after direct fires so the next
  scheduled tick still fires.
- Phase 1 live end-to-end COMPLETE: all 4 monitored jobs now have
  verified live runs (canary, runtime-paritet, wi-contract,
  sentinel-release).
- CI completeness: canary self-test added to ci.yml (spec section 8
  "CI runs all three" now true - was validate + behavior only); verified
  green in job steps on 614da01.

### Added
- Initial 7-job fabric + review spec.
- Aftergraph brand setup: docs/brand.md, README hero/links, brand.identity
  source entry; GitHub About wired to aftergraph.org.
- Brand naming compliance: avc noise gate renamed to
  jobs/legacy/ag-legacy-noise-gate (owner_profile stays runtime truth).

### Fixed
- Ponytail pass: deleted process-local Semaphore (xproc suite is the
  proof), dead reconciler --apply; added wired --record receipt path;
  hoisted duplicated desired_jobs scans. Net -40 lines.
- Honesty fix: no pack pin exists in aftergraph.org site sources, so
  sentinel-release permanent rule is a version-change watch (live
  RULE_PACK_VERSION 1.7.0 at check time), not cross-repo pin matching.
  Prompts rewritten as numbered steps with exact gh-read endpoints
  after first proof showed agents reconnoiter instead of sense.

## [0.1.0] - 2026-09-08

### Added
- Product scaffold, validator, CI.

## [0.3.0] - 2026-09-08
### Changed
- v0.3: 10 schedules / 7 concerns; typed evidence; cross-process
  semaphore; constrained_terminal boundary; canary job; sentinel-release
  replaces firetest-rot; Phase 0 + 3-way shadow acceptance.
