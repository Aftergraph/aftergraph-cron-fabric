"""Independent process helper for exactly-one event claim testing."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from event_store import EventStore


def main():
    db_path, out_dir = sys.argv[1], Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    action = EventStore(db_path).claim_event(
        "ag-test|same-subject|same-condition", "sha:same")
    (out_dir / f"{os.getpid()}.txt").write_text(action, encoding="utf-8")


if __name__ == "__main__":
    main()
