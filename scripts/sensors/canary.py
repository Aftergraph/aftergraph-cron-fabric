"""Fabric state canary (no-agent).

Verifies the local EventStore lifecycle only: atomic EMIT claim, repeated
SILENCE, observed RESOLVED/HEALTHY transition, and re-armability. This script
does NOT prove Hermes/Telegram delivery. End-to-end delivery requires a
separate receipt-observing delivery canary once that surface is available.
"""
import os
import sys
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

_ON_DEMAND = "--on-demand" in sys.argv
KEY = "canary|monthly|synthetic"
EV = {"type": "synthetic_canary", "ref": "canary/state/monthly",
      "observed_at": "run", "repo": None, "sha": None}

require_typed_evidence(EV)
store = EventStore(str(REPO / "state" / "canary.sqlite"))
if _ON_DEMAND:
    # Non-destructive read probe: does not mutate live event state.
    first = store.check(KEY, "canary:on-demand")
    print(f"STATE-CANARY-PROBE: check={first}")
    print("STATE-CANARY-ON-DEMAND: OK (event_store responded)")
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
print("STATE-CANARY-OK: atomic claim -> silence -> resolved -> re-armable")
