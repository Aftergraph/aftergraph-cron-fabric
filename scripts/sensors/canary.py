"""Monthly synthetic canary (no-agent). Injects one synthetic event,
verifies exactly one emission + one RESOLVED lifecycle. Nonzero exit
or unexpected counts = monitor failure (visible).

Production-boundary receipts (Codex CONDITIONAL finding #1): every run
writes one immutable receipt under deploy/receipts/ recording emission,
delivery handoff, and resolution. verify_slo.py check_canary_receipts
fails closed on malformed/inconsistent receipts and on unresolved
emissions with no covering receipt. Receipts never overwrite: an
existing file for the same run second is a hard error.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# Anchored to the repo, never to the scheduler's cwd: a deployed sensor
# must run identically no matter where the tick fires it from. Deployed
# copies live outside the tree, so resolve in order: explicit env,
# in-tree layout proof, scheduler workdir proof, else fail loudly
# (never guess a state dir).
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
    print("CANARY-FAIL: cannot locate fabric root (set AG_FABRIC_ROOT)")
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
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
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
EV = {"type": "synthetic_canary", "ref": "canary/monthly",
      "observed_at": "run", "repo": None, "sha": None}

require_typed_evidence(EV)
store = EventStore(str(REPO / "state" / "canary.sqlite"))
if _ON_DEMAND:
    # Non-destructive probe: EMIT -> SILENCE -> RESOLVED -> re-armable.
    # Does not mutate live event state (test only). Writes a probe receipt
    # (receipts dir only) so probes are auditable without touching state.
    first = store.check(KEY, "canary:on-demand")
    print(f"CANARY-PROBE: check={first}")
    if first == "EMIT":
        print("CANARY-ON-DEMAND: OK (pipeline is live, event_store responding)")
    else:
        print("CANARY-ON-DEMAND: SILENCE (event already OPEN - pipeline responded)")
    _write_receipt({"mode": "probe", "check_result": first,
                    "emitted": 0, "deliveries": 0, "resolved": False,
                    "note": "probe only; live event state untouched"})
    sys.exit(0)

first = store.check(KEY, "canary:probe")
if first != "EMIT":
    print(f"CANARY-FAIL: expected EMIT, got {first}")
    sys.exit(1)
store.record(KEY, "canary:probe")
assert store.check(KEY, "canary:probe") == "SILENCE"
store.resolve(KEY)
assert store.check(KEY, "canary:probe") == "EMIT"
store.resolve(KEY)
# Delivery handoff: the monthly job (deliver: telegram-ops-topic) carries
# the recorded emission to Telegram; machine proof of that hop lives in
# the scheduler executions.db for scheduled runs. The receipt records the
# handoff claim; verify_slo reconciles receipts against canary.sqlite.
_write_receipt({"mode": "self-test", "emitted": 1, "deliveries": 1,
                "delivery_target": "dispatch (telegram-ops-topic via monthly job)",
                "resolved": True})
print("CANARY-OK: inject -> one emission -> resolved -> re-armable")
