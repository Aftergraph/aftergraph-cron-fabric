![Aftergraph](https://raw.githubusercontent.com/Aftergraph/brand/main/svg/aftergraph-social-banner.svg)

# Aftergraph Cron Fabric

Status: Product baseline v0.4 - 10 schedules / 7 concerns. Phase 1 scheduler/job execution proof is complete; Telegram transport exactly-once delivery is NOT yet claimed.
One-line: a small set of Telegram-first scheduled jobs that watch the Aftergraph org and only speak when there is something to act on.

Brand: `Aftergraph Cron Fabric` under the Aftergraph masterbrand. Identity is owned by [Aftergraph/brand](https://github.com/Aftergraph/brand); this repo copies no logos or tokens. See [docs/brand.md](docs/brand.md).

## What it does

Today the profile runs 13 enabled cron jobs (verified 2026-09-08 11:15
from jobs.json) plus 10 fabric definitions (8 core schedules, 1 canary,
1 legacy-local). Noisy hourly watchdogs and 5-minute pollers have been
retired; this repo provides the replacement fabric.

The v0.4 correctness core makes event claiming atomic across independent
processes and turns `ag-runtime-paritet` / `ag-wi-contract` into two-stage
monitors: a cheap deterministic artifact-change gate wakes an agent only when
relevant state moved; changed SHA alone is never treated as semantic drift.

## Tech stack

Hermes Agent cron: `script` + constrained terminal, `no_agent` sensors
(empty stdout = silence), typed evidence, atomic EventStore claims, one
Telegram Ops topic for cron delivery when production delivery is enabled.

## Quickstart

```bash
git clone https://github.com/Aftergraph/aftergraph-cron-fabric.git
cd aftergraph-cron-fabric
python3 scripts/validate.py          # validates every jobs/*.yaml
python3 tests/test_behavior.py       # 46 behavioral checks at v0.4 merge proof
cat CHATGPT-REVIEW-SPEC.md           # reviewable product contract
cat docs/CONFORMANCE.md              # spec-section -> proof command map
```

## How to test

- `python3 scripts/validate.py` - job interface, schedules and safety boundaries.
- `python3 tests/test_behavior.py` - atomic event dedupe, typed evidence,
  guards, cross-process semaphore and exactly-one event claim.
- `python3 scripts/sensors/canary.py` - local EventStore state-canary.
- `python3 scripts/verify_slo.py ...` - machine-checkable event/evidence/runtime
  SLO metrics when live execution data is supplied.

### Canary boundary

`ag-fabric-canary` is currently a **state canary**. It proves atomic claim ->
repeat silence -> observed recovery -> re-armability in the Fabric-owned
EventStore. It does **not** prove scheduler -> transport -> Telegram
exactly-once delivery. That claim stays open until a receipt-observing delivery
canary can verify the external delivery path.

`ag-fabric-delivery` is the **delivery canary** pair: it observes a
sha256-attested Telegram-renderer delivery receipt under
`deploy/delivery-receipts/`, keyed deterministically by
`event_key + fingerprint`. Together the pair proves:
- atomic claim / silence / resolve / re-arm (state canary)
- external receipt written + sha256 verified + replay SILENCE (delivery canary)

## How to deploy

Phase 0: reconcile live cron, baseline volumes, scope read-only tokens.
Phase 1 scheduler/job execution proof: COMPLETE for canary, runtime-paritet,
wi-contract and sentinel-release. v0.4 semantic sensors remain read-only and
must pass shadow acceptance before production Telegram delivery is promoted.
One job at a time; start paused, enable after clean proof.

## Project structure

```text
jobs/                   # one YAML per cron job
scripts/validate.py     # schema + boundary validation
scripts/event_store.py  # authoritative atomic event lifecycle/dedupe
scripts/sensors/        # cheap deterministic pre-checks + state canary
contracts/sources.yaml  # canonical artifact owners and concrete mirrors
docs/architecture.md    # sensor -> state -> dedupe -> evidence -> Telegram
docs/brand.md           # brand contract
CHATGPT-REVIEW-SPEC.md  # reviewable spec
CHANGELOG.md SECURITY.md CONTRIBUTING.md CODEOWNERS .env.example
```

## Environment variables

See `.env.example`. Jobs need only: `TELEGRAM_OPS_THREAD_ID`,
`GITHUB_ORG=Aftergraph`. Never commit tokens.

## Links

[aftergraph.org](https://aftergraph.org) * [Brand OS](https://github.com/Aftergraph/brand) * [Governance](https://github.com/Aftergraph/after-graph-governance) * [Docs](https://github.com/Aftergraph/docs) * [Security](SECURITY.md) * [Contributing](CONTRIBUTING.md) * [Changelog](CHANGELOG.md)
