"""Cross-process semaphore worker (spawned by test_behavior.py, 4x)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from sensor_guard import CrossProcessSemaphore, SensorDegraded

db_path, hold_secs, out_path = sys.argv[1], float(sys.argv[2]), sys.argv[3]
try:
    with CrossProcessSemaphore(db_path, limit=3, ttl=60.0):
        time.sleep(hold_secs)
    result = "acquired"
except SensorDegraded:
    result = "refused"
with open(out_path, "a", encoding="utf-8") as fh:
    fh.write(result + "\n")
