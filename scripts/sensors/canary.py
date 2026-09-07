"""Monthly synthetic canary (no-agent). Injects one synthetic event,
verifies exactly one emission + one RESOLVED lifecycle. Nonzero exit
or unexpected counts = monitor failure (visible)."""
import sys
from pathlib import Path

# Anchored to the repo, never to the scheduler's cwd: a deployed sensor
# must run identically no matter where the tick fires it from.
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from event_store import EventStore
from sensor_guard import require_typed_evidence

store = EventStore(str(REPO / "state" / "canary.sqlite"))
KEY = "canary|monthly|synthetic"
EV = {"type": "synthetic_canary", "ref": "canary/monthly",
      "observed_at": "run", "repo": None, "sha": None}

require_typed_evidence(EV)
first = store.check(KEY, "canary:probe")
if first != "EMIT":
    print(f"CANARY-FAIL: expected EMIT, got {first}")
    sys.exit(1)
store.record(KEY, "canary:probe")
assert store.check(KEY, "canary:probe") == "SILENCE"
store.resolve(KEY)
assert store.check(KEY, "canary:probe") == "EMIT"
store.resolve(KEY)
print("CANARY-OK: inject -> one emission -> resolved -> re-armable")
