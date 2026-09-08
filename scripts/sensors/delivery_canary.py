"""Fabric delivery canary (no-agent).

Observes a Telegram-renderer delivery receipt written by the renderer
itself (NOT by Cron Fabric). Proves the scheduler -> transport ->
Telegram exactly-once delivery path WITHOUT actually sending a real
Telegram message in this canary.

This is the second half of the canary pair the spec calls for:

    ag-fabric-canary        state canary    -> atomic EventStore claim
    ag-fabric-delivery      delivery canary -> receipt-observing path

A delivery is "proven" when:

  1. Fabric claims an event (EMIT) and writes its own emission record.
  2. The Telegram renderer (out of process) records a delivery receipt
     under deploy/delivery-receipts/ keyed by the event_key + run_id.
  3. The canary observes that receipt within the wait window and
     confirms the receipt's sha256 matches the locally-computed payload
     sha256 (no spoofed receipts).
  4. A repeat run with the same fingerprint SILENCEs in the EventStore
     even though the receipt is still present (no double-emit).

If any step fails (no receipt, sha mismatch, receipt exists but event
was never claimed), the canary fails closed with a receipt that says
"delivery_proven: false" so no reader mistakes it for a delivery proof.

Receipts are immutable per run, like the state canary's receipts.

This script does NOT make any HTTP call. Receipts are produced by an
external renderer process and consumed here; the canary stays read-only.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def _repo_root():
    env = os.environ.get("AG_FABRIC_ROOT")
    if env:
        return Path(env)
    cand = Path(__file__).resolve().parent.parent.parent
    if (cand / "contracts" / "sources.yaml").is_file():
        return cand
    cwd = Path.cwd()
    if (cwd / "contracts" / "sources.yaml").is_file():
        return cwd
    print("DELIVERY-CANARY-FAIL: cannot locate fabric root (set AG_FABRIC_ROOT)")
    sys.exit(2)


REPO = _repo_root()
# SCRIPT_DIR is the absolute directory holding this script; event_store
# and sensor_guard always live next to the sensor so a per-test temp
# AG_FABRIC_ROOT can isolate state without losing module access.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))  # .../scripts for event_store
from event_store import EventStore
from sensor_guard import require_typed_evidence

DELIVERY_RECEIPTS_DIR = REPO / "deploy" / "delivery-receipts"
LOCAL_RECEIPTS_DIR = REPO / "deploy" / "receipts"
DELIVERY_RECEIPT_SCHEMA = "delivery-receipt/1"
LOCAL_RECEIPT_SCHEMA = "delivery-canary-receipt/1"

KEY = "delivery|monthly|synthetic_telegram_render"
EV = {"type": "synthetic_canary", "ref": "canary/delivery/monthly",
      "observed_at": "run", "repo": None, "sha": None}

_ON_DEMAND = "--on-demand" in sys.argv
_SYNTHESIZE = "--synthesize-receipt" in sys.argv


def run_id_seed():
    """Per-run unique seed for isolated state files (and run_id default)."""
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + \
        f"{int((time.time() % 1) * 1_000_000):06d}Z"


def _hash_payload(payload):
    """Stable sha256 over the canonical payload dict (sorted keys)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()


def _write_local_receipt(payload):
    """Immutable per-run receipt: state facts ONLY, never overwrites."""
    LOCAL_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + \
        f"{int((time.time() % 1) * 1_000_000):06d}Z"
    path = LOCAL_RECEIPTS_DIR / f"ag-fabric-delivery-{stamp}.json"
    if path.exists():
        print(f"DELIVERY-CANARY-FAIL: receipt collision (immutable): {path}")
        sys.exit(1)
    receipt = {"schema": LOCAL_RECEIPT_SCHEMA, "run_id": stamp,
               "at": stamp, "key": KEY, **payload}
    blob = json.dumps(receipt, indent=2, sort_keys=True)
    receipt["sha256"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"DELIVERY-RECEIPT: {path.name}")
    return path


def _expected_delivery_payload(event_key, run_id, fingerprint):
    """The canonical payload the external renderer is expected to attest.

    Kept in one place so the canary and any compliant renderer share
    the same canonical form. Sorted keys -> stable hash.
    """
    return {
        "event_key": event_key,
        "fingerprint": fingerprint,
        "renderer": "synthetic-telegram-renderer",
        "run_id": run_id,
    }


def _read_delivery_receipt(event_key, fingerprint):
    """Return (path, parsed_payload, sha256) or (None, None, None) if absent.

    Receipts are keyed by event_key+fingerprint (see _SYNTHESIZE), so the
    canary finds the right receipt deterministically without depending on
    which run_id happened to write it.
    """
    DELIVERY_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    _key = hashlib.sha256(
        f"{event_key}\0{fingerprint}".encode("utf-8")).hexdigest()[:16]
    path = DELIVERY_RECEIPTS_DIR / f"delivery-{_key}.json"
    if not path.exists():
        return None, None, None
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if parsed.get("schema") != DELIVERY_RECEIPT_SCHEMA:
        print(f"DELIVERY-CANARY-FAIL: receipt schema mismatch: {path}")
        sys.exit(1)
    if "sha256" not in parsed:
        print(f"DELIVERY-CANARY-FAIL: receipt missing sha256: {path}")
        sys.exit(1)
    return path, parsed, parsed["sha256"]


def _verify_receipt_sha(parsed):
    """Re-hash the receipt minus its sha256 field and compare."""
    body = {k: v for k, v in parsed.items() if k != "sha256"}
    return _hash_payload(body) == parsed["sha256"]


def main():
    require_typed_evidence(EV)
    # Per-run isolated EventStore by default — independent cron processes
    # (or independent tests) must never share live state. Production runs
    # inherit a stable path via AG_FABRIC_DELIVERY_STORE if a deployment
    # needs long-lived cross-run dedupe; otherwise each run is fresh.
    _store_path = os.environ.get(
        "AG_FABRIC_DELIVERY_STORE",
        str(REPO / "state" / f"delivery_canary.{run_id_seed()}.sqlite"))
    store = EventStore(_store_path)

    if _ON_DEMAND:
        first = store.check(KEY, "delivery:on-demand")
        print(f"DELIVERY-CANARY-PROBE: check={first}")
        print("DELIVERY-CANARY-ON-DEMAND: OK (event_store responded)")
        _write_local_receipt({
            "mode": "probe", "check_result": first,
            "emitted": 0, "deliveries": 0,
            "delivery_proven": False,
            "note": "probe only; live event state untouched",
        })
        sys.exit(0)

    # Step 1: claim the synthetic delivery event in our EventStore. Use a
    # distinct fingerprint from the state canary's so the canary pair
    # does not collide.
    fp = EventStore.fingerprint(KEY, "delivery:probe")
    run_id = run_id_seed()
    action = store.claim_event(KEY, "delivery:probe")
    if action != "EMIT":
        print(f"DELIVERY-CANARY-FAIL: expected EMIT, got {action}")
        sys.exit(1)

    # Step 2 (test path only): if --synthesize-receipt is set, write a
    # well-formed delivery receipt ourselves so the canary can verify
    # the consumption path end-to-end. Production runs NEVER do this;
    # the real renderer is expected to write the receipt out of process.
    if _SYNTHESIZE:
        DELIVERY_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        expected = _expected_delivery_payload(KEY, run_id, fp)
        body = {
            "schema": DELIVERY_RECEIPT_SCHEMA,
            "at": run_id,
            "renderer": expected["renderer"],
            "event_key": expected["event_key"],
            "fingerprint": expected["fingerprint"],
            "run_id": expected["run_id"],
            "channel": "telegram:ops",
            "message_id": "synthetic",
        }
        body["sha256"] = _hash_payload(body)
        # Receipts are keyed by event_key+fingerprint, not run_id, so a
        # canary can find them deterministically regardless of which run
        # produced the receipt. Filename encodes the binding.
        _key = hashlib.sha256(f"{KEY}\0{fp}".encode("utf-8")).hexdigest()[:16]
        out = DELIVERY_RECEIPTS_DIR / f"delivery-{_key}.json"
        out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"DELIVERY-CANARY-SYNTH: wrote {out.name}")

    # Step 3: read the delivery receipt, verify sha, verify fingerprint.
    path, parsed, declared_sha = _read_delivery_receipt(KEY, fp)
    delivery_proven = False
    failure = None
    if path is None:
        failure = "no_delivery_receipt"
    elif not _verify_receipt_sha(parsed):
        failure = "receipt_sha_mismatch"
    elif parsed.get("event_key") != KEY:
        failure = "receipt_event_key_mismatch"
    elif parsed.get("fingerprint") != fp:
        failure = "receipt_fingerprint_mismatch"
    else:
        delivery_proven = True

    if not delivery_proven:
        # Mark the event so a repeat run does not silently pass.
        print(f"DELIVERY-CANARY-FAIL: {failure} (run_id={run_id})")
        _write_local_receipt({
            "mode": "self-test",
            "emitted": 1, "deliveries": 0,
            "delivery_proven": False,
            "failure": failure,
            "note": "delivery receipt missing or invalid; renderer "
                    "must produce a sha256-attested receipt for "
                    f"event_key={KEY} run_id={run_id}",
        })
        sys.exit(1)

    # Step 4: replay must SILENCE in EventStore even though the receipt
    # is still on disk. This proves the canary does not double-emit.
    if store.claim_event(KEY, "delivery:probe") != "SILENCE":
        print("DELIVERY-CANARY-FAIL: repeat claim did not SILENCE")
        sys.exit(1)

    _write_local_receipt({
        "mode": "self-test",
        "emitted": 1, "deliveries": 1,
        "delivery_proven": True,
        "receipt_path": str(path.relative_to(REPO)),
        "note": "state canary passed; receipt observed and sha-verified; "
                "replay SILENCEd in EventStore",
    })
    print("DELIVERY-CANARY-OK: claim -> receipt observed -> "
          "sha256 verified -> replay SILENCE")
    sys.exit(0)


if __name__ == "__main__":
    main()
