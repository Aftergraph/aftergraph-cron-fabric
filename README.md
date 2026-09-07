![Aftergraph](https://raw.githubusercontent.com/Aftergraph/brand/main/svg/aftergraph-social-banner.svg)

# Aftergraph Cron Fabric

Status: Product baseline v0.3 - 10 schedules / 7 concerns, not yet deployed.
One-line: a small set of Telegram-first scheduled jobs that watch the Aftergraph org and only speak when there is something to act on.

Brand: `Aftergraph Cron Fabric` under the Aftergraph masterbrand. Identity is owned by [Aftergraph/brand](https://github.com/Aftergraph/brand); this repo copies no logos or tokens. See [docs/brand.md](docs/brand.md).

## What it does

Today the profile runs 14 cron jobs (verified 2026-09-08 via live listing):
noisy hourly watchdogs (`acc-overnight-watch`), 5-minute pollers
(`avc-ci-local-poll`, `gateway-watchdog`), digests, and several jobs in
`error` state (`weekly-brief`, `skills-vault-sync`) plus a paused
`aftergraph-site-monitor`. This repo replaces watchdog-spam with a fabric:
cheap no-agent sensors gate expensive agent runs, and a disposition contract
decides what reaches Telegram.

## Tech stack

Hermes Agent cron: `script` + constrained terminal, `no_agent` sensors
(empty stdout = silence), typed evidence, one Telegram Ops topic for all
cron deliveries.

## Quickstart

```bash
git clone https://github.com/Aftergraph/aftergraph-cron-fabric.git
cd aftergraph-cron-fabric
python3 scripts/validate.py          # validates every jobs/*.yaml
python3 tests/test_behavior.py       # 22 behavioral checks
cat CHATGPT-REVIEW-SPEC.md           # the reviewable spec
```

## How to test

- `python3 scripts/validate.py` - schema-checks all job definitions.
- `python3 tests/test_behavior.py` - event dedupe, typed evidence, guards,
  cross-process semaphore.
- `python3 scripts/sensors/canary.py` - monthly synthetic canary self-test.

## How to deploy

Phase 0: reconcile live cron, baseline volumes, scope read-only tokens.
Phase 1: three jobs paused + manual run + 7-day shadow (see review spec).
One job at a time; start paused, enable after one clean manual run.

## Project structure

```text
jobs/                   # one YAML per cron job (the product)
scripts/validate.py     # schema + boundary validation
scripts/sensors/        # no-agent sensor scripts (stdout or silence)
contracts/sources.yaml  # canonical artifact owners incl. brand identity
docs/architecture.md    # sensor -> state -> dedupe -> evidence -> Telegram
docs/brand.md           # brand contract (references, naming, surfaces)
CHATGPT-REVIEW-SPEC.md  # reviewable spec (send this to ChatGPT)
CHANGELOG.md SECURITY.md CONTRIBUTING.md CODEOWNERS .env.example
```

## Environment variables

See `.env.example`. Jobs need only: `TELEGRAM_OPS_THREAD_ID`,
`GITHUB_ORG=Aftergraph`. Never commit tokens.

## Links

[aftergraph.org](https://aftergraph.org) * [Brand OS](https://github.com/Aftergraph/brand) * [Governance](https://github.com/Aftergraph/after-graph-governance) * [Docs](https://github.com/Aftergraph/docs) * [Security](SECURITY.md) * [Contributing](CONTRIBUTING.md) * [Changelog](CHANGELOG.md)
