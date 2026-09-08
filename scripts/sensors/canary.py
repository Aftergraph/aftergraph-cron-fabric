"""Fabric state canary (no-agent).

Verifies the local EventStore lifecycle only: atomic EMIT claim, repeated
SILENCE, observed RESOLVED/HEALTHY transition, and re-armability. This script
does NOT prove Hermes/Telegram delivery. End-to-end delivery requires a
separate receipt-observing delivery canary once that surface is available.

Production-boundary receipts: every run writes one immutable receipt under
deploy/receipts/ recording emission and resolution STATE facts.
Delivery is explicitly NOT proven here (deliveries: 0, delivery_proven:
false); verify_slo.py check_canary_receipts fails closed on malformed /
inconsistent receipts and on unresolved emissions with no covering
receipt. Receipts never overwrite: an existing file for the same run
second is a hard error.
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
    print("STATE-CANARY-FAIL: cannot locate fabric root (set AG_FABRIC_ROOT)")
    sys.exit(2)


REPO = _repo_root()
sys.path.insert(0, str(REPO / "scripts"))
from event_store import EventStore
from sensor_guard import require_typed_evidence

RECEIPTS_DIR = REPO / "deploy" / "receipts"
RECEIPT_SCHEMA = "canary-receipt/1"


def _write_receipt(payload):
    """Write one immutable per-run receipt. Never overwrites."""
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    # Microsecond stamp: two runs in the same wall-clock second (e.g.
    # self-test immediately followed by --on-demand) must not collide.
    # The exists-check below stays as the immutability backstop.
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + \
        f"{int((time.time() % 1) * 1_000_000):06d}Z"
    path = RECEIPTS_DIR / f"ag-fabric-canary-{stamp}.json"
    if path.exists():
        print(f"CANARY-FAIL: receipt collision (immutable, refusing overwrite): {path}")
        sys.exit(1)
    receipt = {"schema": RECEIPT_SCHEMA, "run_id": stamp, "at": stamp,
               "key": KEY, **payload}
    blob = json.dumps(receipt, indent=2, sort_keys=True)
    receipt["sha256"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"CANARY-RECEIPT: {path.name}")
    return path


_ON_DEMAND = "--on-demand" in sys.argv
KEY = "canary|monthly|synthetic"
EV = {"type": "synthetic_canary", "ref": "canary/state/monthly",
      "observed_at": "run", "repo": None, "sha": None}

require_typed_evidence(EV)
store = EventStore(str(REPO / "state" / "canary.sqlite"))
if _ON_DEMAND:
    # Non-destructive read probe: does not mutate live event state.
    # Writes a probe receipt (receipts dir only) so probes stay auditable.
    first = store.check(KEY, "canary:on-demand")
    print(f"STATE-CANARY-PROBE: check={first}")
    print("STATE-CANARY-ON-DEMAND: OK (event_store responded)")
    _write_receipt({"mode": "probe", "check_result": first,
                    "emitted": 0, "deliveries": 0, "resolved": False,
                    "note": "probe only; live event state untouched"})
    sys.exit(0)

first = store.claim_event(KEY, "canary:probe")
if first != "EMIT":
    print(f"STATE-CANARY-FAIL: expected EMIT, got {first}")
    sys.exit(1)
if store.claim_event(KEY, "canary:probe") != "SILENCE":
    print("STATE-CANARY-FAIL: duplicate claim was not silenced")
    sys.exit(1)
store.resolve(KEY)  # observed synthetic recovery
if store.check(KEY, "canary:probe") != "EMIT":
    print("STATE-CANARY-FAIL: resolved event did not re-arm")
    sys.exit(1)
# State-fact receipt: emission + resolution observed here. Delivery is
# explicitly NOT proven by this canary (see docstring); the receipt says
# so on its face so no reader mistakes it for a delivery proof.
_write_receipt({"mode": "self-test", "emitted": 1, "deliveries": 0,
                "delivery_proven": False,
                "note": "state facts only; delivery needs the "
                        "receipt-observing delivery canary"})
print("STATE-CANARY-OK: atomic claim -> silence -> resolved -> re-armable")
