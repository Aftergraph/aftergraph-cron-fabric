# Delivery Receipts Spec (aftergraph-cron-fabric)

## Purpose

`ag-fabric-canary` proves atomic claim / silence / resolve / re-arm in the
Fabric-owned EventStore. It does NOT claim scheduler -> transport ->
Telegram exactly-once delivery. `ag-fabric-delivery` is the
receipt-observing pair: it reads a sha256-attested Telegram-renderer
delivery receipt and proves the claim was followed by an external
delivery.

This document is the normative spec for the **delivery receipt**: what
an external Telegram renderer MUST write under
`deploy/delivery-receipts/` so that `ag-fabric-delivery` can verify it.
Renderers are out of process for Cron Fabric; this repo never sends a
real Telegram message itself.

The receipt format below is the canonical payload that
`_expected_delivery_payload()` in `scripts/sensors/delivery_canary.py`
already documents. There is one source of truth: the canary code. If
they diverge, the canary is wrong.

## Receipt file

```text
deploy/delivery-receipts/delivery-{key16}.json
```

where `key16` is the first 16 hex chars of
`sha256("{event_key}\0{fingerprint}")`. Both fields are produced by
Fabric (event_key from the job; fingerprint from
`EventStore.fingerprint()`).

Filename is deterministic and shared between Fabric claim and any
renderer that observes the same claim, so a later canary run can find a
receipt that an earlier run produced without depending on
clock-derived run_ids.

## Receipt payload (canonical, normative)

```json
{
  "schema": "delivery-receipt/1",
  "at": "20260908T160726866567Z",
  "renderer": "synthetic-telegram-renderer",
  "event_key": "delivery|monthly|synthetic_telegram_render",
  "fingerprint": "f47ac10b58cc4372a5670e02b2c3d479",
  "run_id": "20260908T160726866567Z",
  "channel": "telegram:ops",
  "message_id": "1234567890",
  "sha256": "<self-attested sha256, see below>"
}
```

### Field contract

| Field         | Type   | Required | Notes |
|---------------|--------|----------|-------|
| `schema`      | string | yes      | MUST be exactly `"delivery-receipt/1"`. Other values fail closed. |
| `at`          | string | yes      | ISO-8601 compact UTC timestamp the receipt was written. Free-form, used for ordering/audit only. |
| `renderer`    | string | yes      | Identity of the renderer that produced the receipt. Free-form but MUST be stable for the lifetime of one renderer (so receipts from the same renderer are recognisable). |
| `event_key`   | string | yes      | MUST equal the Fabric `event_key` this receipt attests to. The canary compares this exactly to the EventStore key. |
| `fingerprint` | string | yes      | MUST equal `EventStore.fingerprint(event_key, evidence)` from Fabric. 16 hex chars; sha256-prefix. |
| `run_id`      | string | yes      | The Fabric run identifier that produced the claim this receipt attests to. Free-form but MUST be the same `run_id` Fabric used. |
| `channel`     | string | yes      | Free-form delivery target identifier. SHOULD be `telegram:<topic>` for Telegram topics, but the canary does not validate shape beyond non-empty. |
| `message_id`  | string | yes      | The Telegram-assigned `message_id` (or renderer-assigned equivalent) that lets an operator correlate the receipt to the visible card. |
| `sha256`      | string | yes      | Self-attested sha256 of all other fields in canonical JSON form (sorted keys, no whitespace). See below. |

### Self-attested sha256

The `sha256` field attests to the integrity of the other eight fields.
It MUST equal:

```python
import hashlib, json
canonical = json.dumps(
    {k: v for k, v in receipt.items() if k != "sha256"},
    sort_keys=True, separators=(",", ":"),
)
sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Any other ordering, whitespace, or key insertion breaks the attestation.
The canary re-hashes the same way and refuses the receipt on mismatch.

The receipt is fail-closed: a missing field, wrong schema, wrong
event_key/fingerprint, or sha256 mismatch causes the canary to write a
`delivery_proven: false` local receipt and exit 1.

## Where the receipt must be written

Path: `<AG_FABRIC_ROOT>/deploy/delivery-receipts/delivery-{key16}.json`

`AG_FABRIC_ROOT` defaults to the Cron Fabric repo root. Production
deployments may override via the env var; the renderer reads the same
variable.

The directory MUST be created with mode 0700 on POSIX hosts; on Windows
the ACL should be restricted to the renderer process. Receipts are
**per-event-key** (overwriting is allowed and expected for re-claims
with new evidence — see "Replay" below).

## When the receipt must be written

A receipt MUST be written when, and only when, the Telegram renderer
successfully delivered a card for a Fabric claim. The renderer MUST
NOT speculatively write a receipt for a claim it cannot observe, and
MUST NOT delay writing the receipt until after the canary has run.

The canary runs on its own schedule. The renderer has no way to know
which canary run will verify its work. Therefore:

- receipts are written as soon as the renderer has positive proof of
  delivery (HTTP 200 from Telegram with a `message_id`);
- receipt writes are idempotent (overwriting the same `{key16}.json`
  with the same payload is a no-op; overwriting with a different
  payload is a renderer bug and the canary will refuse it).

## Replay

If Fabric re-claims the same `event_key` with a new `fingerprint`
(material evidence change), the filename changes (`key16` is a function
of both fields). The renderer MUST NOT keep stale receipts alive —
its claim that "I delivered the old fingerprint" no longer applies.
Delete the old receipt, write a new one with the new fingerprint, and
keep the canary honest.

If Fabric re-claims with the same fingerprint, the receipt filename
is unchanged and the canary expects to see SILENCE in the EventStore
(not a fresh EMIT). The receipt is unchanged; no action required by
the renderer.

## What the renderer MUST NOT do

- Write a receipt before the Telegram delivery is positively
  confirmed. Pre-emptive receipts cause the canary to believe delivery
  succeeded when in fact no message was ever sent.
- Write a receipt with a fabricated `sha256`. The canary re-hashes
  every other field and refuses mismatches. There is no path past this
  check that does not actually deliver.
- Skip `event_key` / `fingerprint` / `run_id` to "save space". These
  are the canary's only binding back to Fabric. Without them the
  receipt is unattached and the canary treats it as a missing receipt.
- Write the receipt under a different path. The canary looks up by
  `key16`, not by scanning the directory.

## Verification command

```bash
python3 scripts/sensors/delivery_canary.py --synthesize-receipt
```

This self-test writes a well-formed receipt (canonical payload +
correct sha256), then re-runs the canary in observation mode. Exit 0
+ `DELIVERY-CANARY-OK` means the canary accepted its own receipt, which
is necessary but not sufficient — a renderer is only correct if it can
fool this same canary with a receipt Fabric did not help write.

For a true external test, run the canary without `--synthesize-receipt`
against a directory where the renderer has just written a real
receipt; the canary will either accept (renderer is correct) or refuse
with a failure mode (`no_delivery_receipt`, `receipt_sha_mismatch`,
`receipt_event_key_mismatch`, or `receipt_fingerprint_mismatch`).

## Conformance checklist (for renderer implementers)

- [ ] Receipt written only after positive Telegram 200 with `message_id`.
- [ ] All nine fields present; `schema` is exactly `delivery-receipt/1`.
- [ ] `event_key` and `fingerprint` copied verbatim from the Fabric claim.
- [ ] `sha256` is the self-attested hash of the other eight fields in
      canonical JSON (sorted keys, no whitespace).
- [ ] Path is exactly `deploy/delivery-receipts/delivery-{key16}.json`.
- [ ] Old receipts are deleted when the renderer observes a Fabric
      re-claim with a new fingerprint.
- [ ] Renderer fails closed: if any check fails, no receipt is written.
- [ ] Renderer does NOT need Cron Fabric at runtime; only the env var
      `AG_FABRIC_ROOT` and access to the `deploy/delivery-receipts/`
      directory.
