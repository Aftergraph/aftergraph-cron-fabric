# Org Activity Pulse (aftergraph-cron-fabric)

Telegram-first activity surface showing recent GitHub activity across
every live repository in the Aftergraph organization.

## What it does

One coherent activity surface showing, on Telegram from a phone:

- what changed
- in which repository
- when it changed
- commit / PR / issue / action / release identity
- significant GitHub state transitions
- how many repositories are currently active
- whether anything needs attention

Feed includes at minimum:
- commit on canonical/default branch
- PR opened / reopened / ready-for-review / merged / closed
- issue opened / closed
- workflow completed / failed / cancelled
- release published
- repository created / archived / unarchived / renamed
- default-branch changes when observable
- branch create/delete when observable

Does NOT show every comment, reaction, bot refresh, or meaningless
updated_at change by default.

## Architecture

jobs/ag-org-pulse.yaml         — 15-minute collector schedule
scripts/sensors/org_activity.py — read-only collector (no_agent)
scripts/activity_store.py       — durable SQLite ActivityStore
scripts/activity_normalizer.py  — deterministic GitHub -> event mapping
scripts/org_activity_renderer.py — Telegram surfaces (pulse/digest/alert)
contracts/activity-event.schema.json — normative event schema
tests/test_org_activity.py      — offline fixture tests

### Existing infrastructure reused

- scripts/gh-read.sh (GET-only enforcement)
- telegram_receipt_bridge.py (delivery receipt chain)
- hermes statuscard (canonical renderer)
- No new bot. No second Telegram delivery architecture.
- EventStore (prose problem-state) remains separate from ActivityStore.

### Three surfaces

1. ag-org-pulse — persistent edit-in-place status card, refreshed each
   collector cycle (15 min). Shows timestamp, repo count, active repos,
   event counts, latest rows, attention section, evidence freshness.

2. ag-org-digest — at most one new digest per hour. Emitted only when
   new meaningful activity exists since prior digest.

3. ag-org-alert — one-shot deduped exception cards for:
   - failed default-branch CI
   - unknown live repository
   - high-risk repository lifecycle change
   - activity collector failure
   - prolonged source blindness

### Repository discovery

Never hard-code the Aftergraph repository list. Discover current live
organization state through authenticated read-only GitHub API calls.
Reconcile against canonical topology from Aftergraph/after-graph-governance.
- Unknown live repo: record activity event + warning. Do not add to
  canonical topology.
- Missing expected repo: record observation. Do not mutate GitHub or
  governance.

### ActivityStore

Immutable SQLite store separate from prose logs. Concepts:
- immutable activity event, deterministic event_id
- occurred_at, observed_at, repo, canonical plane/role when resolvable
- event kind, action, actor, ref (SHA / PR / issue / run / release)
- source URL, source type, payload digest, display title, importance
- correlation id (PR merge + merge commit, cross-repo wave)

Also persists:
- source cursors (ETags / last successful observation)
- delivery/digest cursor
- last observed repository metadata snapshot

Repeated observation MUST NOT create duplicate ActivityEvent.
Restart MUST NOT replay already delivered activity.

### Dedupe + correlation

Semantic presentation dedupe:
- PR merge + corresponding merge commit => one rendered activity row
- Push containing N commits => commits remain canonical events; renderer
  may collapse push wrapper
- Same migration/brand wave touching multiple repos in short period =>
  renderer MAY group as cross-repo wave while preserving events

Never delete underlying evidence merely because display rows collapsed.

### Polling

Collector schedule: every 15 minutes.
Authenticated conditional GET where possible. Persist ETag/cursor state.
Handle pagination. Handle GitHub rate limits and Retry-After correctly.
No broad concurrent API bursts.

GitHub organization Events API is NOT authoritative real-time state.
Direct repository/resource endpoints are primary evidence.
Organization events may be supplementary.

Audit Log integration only if already-authorized read-only org token
provides required permission. Do NOT broaden token authority solely for
this feature.

### Telegram UX

Reuse canonical Hermes telegram-live-status renderer. Do not build
direct Telegram API logic in the sensor. Delivery only successful after
renderer positive evidence. Existing delivery receipt chain.

### No-agent hot path

Collector, normalizer, dedupe, correlation, renderer are deterministic
script-only / no_agent code. No LLM decides whether a GitHub event
occurred. No LLM invents titles, SHAs, states, or repo activity.

### Local git extension (future)

Schema supports future second observer emitting source=local-git for:
- unpushed commits
- branch ahead/behind
- dirty worktree
- new local branches

Production v1 is remote GitHub org activity. Never conflate local git
observation with GitHub remote truth.

### Failure semantics

- GitHub temporarily unavailable: retain previous card + mark source STALE
- One repo forbidden/unreachable: show PARTIAL, preserve other repos
- Auth failure: FAIL CLOSED, show source blindness alert
- Malformed API response: do not advance cursor
- Telegram send failure: do not mark digest delivered
- Process crash between event persistence and rendering: restart resumes
  safely without event duplication
- Same event observed by multiple GitHub surfaces: one canonical
  ActivityEvent / correlated display group

## Deployment

Work on isolated branch/worktree. Do not interfere with running sessions.
Run existing project conformance commands first. Implement through TDD.
Create PR. Review exact diff. Merge only with green gates. Reconcile live
Hermes cron definitions after merge. Start collector in shadow/dry-run.
Observe at least two cycles. Verify dedupe. Then enable Telegram Pulse.
Prove delivery with receipt evidence.

## Tests

TDD. Executable tests covering all new behavior with fixtures. No live
GitHub required. Separate read-only live smoke test against Aftergraph.

See tests/test_org_activity.py for the full suite.

## Acceptance evidence required

- all existing Cron Fabric validation still passes
- all existing behavior tests still pass
- all new activity tests pass
- GET-only guard still passes
- adversarial tests
- live Aftergraph repo discovery succeeds
- live observation detects current multi-repo activity
- no duplicate events across two consecutive live polls
- dry-run renderer produces complete Telegram card
- real Telegram canary proves one delivered Pulse update
- delivery receipt verifies second identical renderer attempt does not
  create duplicate logical activity
- cron schedule installed
- schedule observed executing
- rollback/pause command documented and tested

Do not claim Telegram exactly-once unless existing external delivery
receipt / canary contract proves it.
