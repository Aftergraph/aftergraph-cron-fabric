# CHATGPT REVIEW SPEC - Aftergraph Cron Fabric v0.3

> Reviewer (ChatGPT): v0.2 verdict CONDITIONAL addressed below. Reply
> with: (1) verdict SHIP / CONDITIONAL / BLOCKED, (2) numbered findings
> with file/section + concrete fix, (3) what you would still delete.
> ASCII-only document.

## 0. v0.2 findings -> fixes (all verified by tests, not prose)

- #1 terminal bypasses wrapper -> read_only now DEFINED as "no external
  target-system mutation"; own writes to state/, deploy/receipts/ and
  local audit output explicitly allowed. New fields: mode (agent |
  no_agent) + job_type (constrained_terminal). Validator REJECTS bare
  [terminal] without job_type: constrained_terminal AND rejects any
  such job whose prompt does not route through scripts/gh-read.sh.
  Verified: 4 jobs failed validation until fixed (true positive run).
- #2 process-local semaphore -> scripts/sensor_guard.py
  CrossProcessSemaphore (SQLite leases, BEGIN IMMEDIATE, ttl 300s).
  Contract: GH_CONCURRENCY_LIMIT MUST hold across all fabric processes.
  Test: 3 holders + 1 contender in INDEPENDENT processes -> 3 admitted,
  1 refused (would admit 4 if process-local). In suite: 19/19 -> 22/22.
- #3 narrow SHA evidence -> require_typed_evidence: {type, ref,
  observed_at, repo?, sha?}; types: commit | workflow_run | contract |
  http_observation | repository_state | cron_run | synthetic_canary.
  Commit findings still require exact sha. Tested (3 new checks).
- #4 counts -> honest: 10 schedules / 7 concerns (8 core schedules,
  1 canary, 1 legacy-local). No creative accounting.
- #5 dedupe ownership -> event_store = authoritative event lifecycle +
  delivery dedupe. monitor/wake = cost gate only. continuity = reasoning
  context only, kept ONLY on deep audits (removed from claim-watch,
  vault-watch, sentinel-release, legacy-noise-gate). Enforced by
  validator (NO_CONTINUITY allowlist) + 3 new tests (watch rejected,
  deep-audit allowed, allowlist pinned).
- #6 expires lifecycle -> reconcile.py plan() takes expired set:
  desired_state retired -> reconciler pauses/removes live job + emits
  receipt. Adopted your simpler model: ag-sentinel-release is permanent
  (pack-skew) with a temporary firetest-must-disappear RULE that closes
  while the job continues. ag-firetest-rot.yaml DELETED.
- #7 oracle -> shadow acceptance is 3-way: new fabric vs
  acc-overnight-watch (comparator, not oracle) vs frozen direct source
  observations. Oracle = fixtures + GitHub/CI/runtime evidence.
- #8 prompt conditional -> validator: mode=agent REQUIRES prompt;
  mode=no_agent FORBIDS prompt + REQUIRES script. ag-fabric-canary is
  the first no_agent job (script: scripts/sensors/canary.py).
- #9 latency thresholds -> every job carries detection_slo
  {p50_max, p95_max} (2h jobs: 2h/4h; 6h: 6h/12h; daily: 24h/48h;
  weekly: 7d/10d).
- Deletes adopted: severity_ceiling gone (was v0.2); no buttons in core
  (DECISION = Telegram + continuable session + textual action with
  decision_id, e.g. DEC-20260908-0042 with inspect/defer/ack replies);
  no whole-job expiry.
- Open Q1 (cut order): no cuts; merge order if needed: vault pair,
  then claim pair; governance stays.
- Open Q2 (DECISION format): thread follow-up with decision_id, per
  your recommendation.
- Open Q3 (canary injector): new monthly no_agent job ag-fabric-canary,
  external to tested jobs; inject -> one emission -> auto-resolve ->
  exactly-one-Telegram + one RESOLVED; always
  evidence.type=synthetic_canary. Implemented + passing.
- Open Q4 (release-watch home): jobs/ag-sentinel-release.yaml in fabric;
  contracts/sources.yaml points at canonical artifacts.

## 1. Decision

10 schedules / 7 concerns (8 core + 1 canary + 1 legacy). Phase 1:
ag-runtime-paritet, ag-wi-contract, ag-sentinel-release (temporary rule
active) - paused + manual run + 7-day shadow vs comparator.

## 2-3. Ground truth, goals (unchanged from v0.2; read_only now enforced
per above, not promised).

## 4. Architecture (updated)

sensor (no-agent script or constrained_terminal agent, gh-read.sh
GET-only, CrossProcessSemaphore(3)) -> event_store (authoritative,
own SQLite) -> typed-evidence gate -> disposition
(store/digest/notify/decision/incident) -> Telegram Ops topic.
DECISION carries decision_id; replies are thread follow-ups
(inspect/defer/ack DEC-ID).

## 5. Job catalog (final)

Core (8 schedules): runtime-paritet, wi-contract, governance-drift,
claim-watch + research-evidence (pair), vault-watch + vault-freshness
(pair), sentinel-release (version-change watch + 1 temporary rule). Infra (1):
ag-fabric-canary (monthly, no_agent). Legacy-local (1):
jobs/legacy/ag-legacy-noise-gate.

## 6. Interfaces (per jobs/*.yaml)

Required: name, schedule, deliver, severity (info|warning|critical),
allowed_dispositions, read_only (= true, MUST), mode, rollback.
Conditional: prompt (agent only), script (no_agent only).
Optional: job_type (= constrained_terminal when terminal is listed),
detection_slo, continuity (deep audits only), expires rule input.
Forbidden: severity_ceiling, bare [terminal], secrets.

## 7. Safety (enforced, tested)

gh-read.sh exit 3 pre-network (test); validator rejects undeclared
terminal (4 true-positive failures fixed); CrossProcessSemaphore tested
across processes; tokens read-only scoped at rollout (Phase 0 item).

## 8. Testing

validate.py (10 jobs) + test_behavior.py (31 checks) + canary.py
self-test. CI runs all three. Output schema: typed evidence on every
notify+ (require_typed_evidence). scripts/verify_tick.py proves a
scheduled (not manual) tick consumed its slot: next_run_at advanced,
completed scheduler-source execution with matching scheduled_instant,
output file present (exit 0 verified / 1 failed / 2 pending).
retry_after_seconds() parses RFC 9110 Retry-After (delta-seconds and
HTTP-date) for 429 handling; unparseable/expired -> None + backoff.

## 9. Rollout + success (updated)

Phase 0: reconcile 14 live jobs, baseline, Ops topic, read-only token
scoping. Phase 1: 3 jobs paused/manual/shadow (3-way acceptance).
Success: duplicate rate 0; false INCIDENT 0; canary 100% (emission +
RESOLVED); evidence completeness 100%; per-job p50/p95 SLOs met;
volume down vs frozen baseline.

## 10. Open questions

1. Is CrossProcessSemaphore-over-SQLite acceptable, or do you want
   file-lock primitive instead for NFS-style hosts?
2. Should canary also run on-demand pre-deploy (in addition to monthly)?
3. DECISION ack semantics: does ack freeze the event (no re-emit on
   same fingerprint) - currently yes via resolve; confirm.
