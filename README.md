# Aftergraph Cron Fabric

Status: Experimental - v0.1.0 design, not yet deployed.
One-line: a small set of Telegram-first scheduled jobs that watch the 22-repo Aftergraph org and only speak when there is something to act on.

## What it does

Today the profile runs 14 cron jobs (verified 2026-09-08 via live listing):
noisy hourly watchdogs (`acc-overnight-watch`), 5-minute pollers
(`avc-ci-local-poll`, `gateway-watchdog`), digests, and several jobs in
`error` state (`weekly-brief`, `skills-vault-sync`) plus a paused
`aftergraph-site-monitor`. This repo replaces watchdog-spam with a fabric:
cheap no-agent sensors gate expensive agent runs, and a severity contract
decides what reaches Telegram.

## Tech stack

Hermes Agent cron (`cronjob_manage`): `script` + `monitor` + `continuity`,
`no_agent` sensors (empty stdout = silence), `deliver: telegram:<chat>`,
one Telegram Ops topic for all cron deliveries.

## Quickstart

```bash
git clone https://github.com/Aftergraph/aftergraph-cron-fabric.git
cd aftergraph-cron-fabric
python3 scripts/validate.py          # validates every jobs/*.yaml
cat CHATGPT-REVIEW-SPEC.md           # the reviewable spec
```

## How to test

`python3 scripts/validate.py` - schema-checks all job definitions
(required keys, valid schedule grammar, severity in contract, no secrets).

## How to deploy

One job at a time via `cronjob_manage action=create` (see each
`jobs/*.yaml`: `prompt` is the self-contained job prompt, `schedule` the
cadence). Start paused where marked, enable after one clean manual `run`.

## Project structure

```text
jobs/                   # one YAML per cron job (the product)
scripts/validate.py     # schema + secrets validation
scripts/sensors/        # no-agent sensor scripts (stdout or silence)
docs/architecture.md    # sensor > state > dedupe > significance > Telegram
CHATGPT-REVIEW-SPEC.md  # reviewable spec (send this to ChatGPT)
CHANGELOG.md SECURITY.md CONTRIBUTING.md CODEOWNERS .env.example
```

## Environment variables

See `.env.example`. Jobs need only: `TELEGRAM_OPS_THREAD_ID`,
`GITHUB_ORG=Aftergraph`. Never commit tokens.

## Links

[Security](SECURITY.md) * [Contributing](CONTRIBUTING.md) * [Changelog](CHANGELOG.md)
