# Changelog

## [Unreleased]

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
- v0.3: 9 schedules / 7 concerns; typed evidence; cross-process
  semaphore; constrained_terminal boundary; canary job; sentinel-release
  replaces firetest-rot; Phase 0 + 3-way shadow acceptance.
