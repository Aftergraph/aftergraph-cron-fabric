# Canary Pair Architecture - state + delivery as one invariant

## The problem with one canary

A canary is a sensor that proves an invariant by running a minimal,
controlled probe. If the probe succeeds, the invariant holds. If the
probe fails closed, the invariant is broken.

Cron Fabric has two failure modes that look similar but live in
different places:

1. **The local claim can be lost, double-counted, or stuck open.**
   This is a property of the EventStore. A scheduler restart, a
   crashed process mid-transaction, or a misconfigured ack can break
   it. No Telegram call is required to break it.
2. **The external delivery can be lost, duplicated, or fabricated.**
   This is a property of the renderer path - whatever process turns
   a Fabric claim into a Telegram card. A dropped webhook, a retried
   HTTP, or a renderer that lies about success can break it. The
   EventStore cannot detect this on its own.

A single canary can prove one mode and miss the other. The state
canary (`ag-fabric-canary`) runs inside Fabric; it sees the EventStore
but cannot see Telegram. The delivery canary (`ag-fabric-delivery`)
sees both, but only by trusting the renderer to write a receipt.
Neither alone is sufficient.

## The pair

| Canary             | Scope                | Invariant                                                                                           | Where it proves it                                |
|--------------------|----------------------|------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| `ag-fabric-canary` | Local EventStore     | claim is atomic across processes, dedupe works, resolve/re-arm round-trips, ack does not silence recovery | `state/canary.sqlite` + receipt under `deploy/receipts/` |
| `ag-fabric-delivery` | External delivery path | claim is followed by a sha256-attested Telegram-renderer receipt, replay SILENCEs, no fabricated receipts | `deploy/delivery-receipts/`                       |

The pair is read together: a Fabric operator wants to see both
canaries green before claiming scheduler -> transport -> Telegram
exactly-once. Either canary failing closes the claim.

## State canary in one paragraph

`ag-fabric-canary` is `mode: no_agent`, runs monthly on the 1st at
9am, and uses `scripts/sensors/canary.py`. It does not talk to
GitHub, Telegram, or any external system. It runs through the four
EventStore transitions (EMIT, repeat SILENCE, resolve HEALTHY,
re-arm EMIT) and writes an immutable per-run receipt under
`deploy/receipts/`. The receipt explicitly states
`deliveries: 0, delivery_proven: false` so no reader mistakes it for a
delivery proof.

This canary fails closed when:

- `claim_event()` returns SILENCE where EMIT is expected
- `claim_event()` returns EMIT on replay (dedupe broken)
- `resolve()` does not transition the event to HEALTHY
- A receipt collision occurs (immutable-write violated)

It does NOT fail closed when:

- The Telegram renderer never delivered anything
- The Telegram renderer delivered something Fabric did not claim

Those are the delivery canary's job.

## Delivery canary in one paragraph

`ag-fabric-delivery` is `mode: no_agent`, runs monthly on the 1st at
9am (offset from the state canary so they do not collide), and uses
`scripts/sensors/delivery_canary.py`. It does not talk to GitHub
either. It reads a sha256-attested receipt from
`deploy/delivery-receipts/delivery-{key16}.json` (where `key16` is
derived from event_key + fingerprint). It verifies the receipt's
sha256 self-attestation, the event_key/fingerprint match the
EventStore claim, and that a replay SILENCEs.

This canary fails closed when:

- The receipt is missing (`no_delivery_receipt`)
- The receipt's sha256 does not re-hash correctly (`receipt_sha_mismatch`)
- The receipt's event_key or fingerprint does not match the EventStore claim
- A second claim returns EMIT instead of SILENCE (delivery dedupe broken)

It does NOT fail closed when:

- The EventStore itself is broken (state canary's job)
- The renderer is unreachable (it just reports `no_delivery_receipt`)

## How a renderer fits

The renderer is the missing piece. Cron Fabric never sends a real
Telegram message itself; that is the renderer's job. The delivery
receipts spec (`docs/delivery-receipts-spec.md`) is the contract
between the renderer and the delivery canary:

- the renderer writes a sha256-attested receipt under
  `deploy/delivery-receipts/` after each positive Telegram 200;
- the delivery canary reads that receipt on its next run;
- the canary's verdict (PASS or fail-closed mode) is the only
  machine-checkable proof that the renderer followed the spec.

A renderer that does not write receipts cannot be verified by
Cron Fabric. The state canary will still be green; the delivery
canary will report `no_delivery_receipt` until the renderer
implements the spec.

## What the pair does NOT prove

- **End-to-end latency.** Neither canary measures how long a claim
  took to become a delivered Telegram card. That is a runtime SLO
  concern (`scripts/verify_slo.py`) which lives in a separate
  concern, not a canary.
- **Telegram API behaviour.** The pair proves Fabric wrote a receipt;
  it does not prove Telegram received it. A renderer that fakes
  successful HTTP responses will pass the canary.
- **Operator UX.** The canaries prove invariants, not that operators
  notice them. The Telegram Ops topic + `telegram-live-status` card
  pipeline is a separate concern.
- **Replay after long downtime.** A renderer that has been offline
  for a week must catch up by replaying every missed claim. The
  canaries do not test the catch-up path; the renderer is responsible
  for proving its own catch-up semantics.

## Operating the pair

For the first rollout, expect:

1. State canary green; delivery canary red (`no_delivery_receipt`)
   until a renderer is wired up. This is the normal pre-production
   state. Document it; do not treat it as a Fabric failure.
2. State canary green; delivery canary green once the renderer
   implements the spec and produces real receipts. This is the goal.
3. State canary red at any point indicates a local EventStore
   regression. Stop and investigate before changing anything in the
   renderer path.
4. Both green does not prove production exactly-once. Run a
   shadow rollout: have the renderer write receipts for a week
   without sending real cards, then compare receipt count to claim
   count. Drift there is a real signal.

## Pair-level invariant summary

The pair proves:

```text
For every event_key claimed by Fabric with fingerprint f:
  - the EventStore contains exactly one OPEN row at the time of claim
    (state canary)
  - a sha256-attested receipt exists under deploy/delivery-receipts/
    whose event_key matches and whose fingerprint equals f
    (delivery canary)
  - a second claim_event() call for the same (event_key, f) returns
    SILENCE (state canary + delivery canary)
```

What is still unproven:

```text
- that the Telegram card visible to an operator matches the receipt
- that the receipt's message_id corresponds to a card still visible
  in the Telegram topic
- that the renderer's HTTP 200 from Telegram was honest
```

These are renderer-side concerns. Cron Fabric's pair gives operators
the strongest check Cron Fabric itself can give; the rest is the
renderer's contract.

## See also

- `docs/architecture.md` - the wider sensor -> state -> dedupe ->
  evidence -> Telegram pipeline (this doc zooms into the canary layer)
- `docs/delivery-receipts-spec.md` - the renderer contract
- `scripts/sensors/canary.py` and `scripts/sensors/delivery_canary.py`
  - the implementation; both are short enough to read end-to-end
- `CHATGPT-REVIEW-SPEC.md` section 0 - the historical note that the
  state canary alone was not enough and why the pair was introduced
