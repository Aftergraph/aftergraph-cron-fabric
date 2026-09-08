"""Per-run shadow receipt writer (Phase B evidence).

Cron's execution record proves a tick fired, but it stores no
stdout/classification. This helper gives every shadow sensor run an
immutable per-daylight receipt so the 7-day SHADOW_ACCEPTED decision
is mechanical instead of reconstructed from executions.db:

  deploy/receipts/ag-v05-shadow-<job>-<stamp>.json   (gitignored, live only)

Receipts are facts only: job, run_id, start/end, duration,
returncode, classification (EMIT/SILENCE/DEGRADED/CRASH),
emit-line count, stdout tail, agent_invoked=false,
mutation_attempts=0. sha256-attested with the same canonical form
as the delivery canary (sorted keys, compact separators).

Callers (Hermes no_agent wrappers) must treat receipt writing as
best-effort: never let it fail the sensor run. Wrap in try/except.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "shadow-run-receipt/1"
FILENAME_PREFIX = "ag-v05-shadow-"


def _stamp(dt=None):
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%S") + \
        f"{dt.microsecond:06d}Z"


def classify_output(returncode, stdout):
    """EMIT/SILENCE/DEGRADED/CRASH from exit code + sensor stdout lines."""
    if returncode != 0:
        return "CRASH"
    lines = (stdout or "").splitlines()
    if any("-EMIT" in ln for ln in lines):
        return "EMIT"
    if any("SENSOR-DEGRADED" in ln or "-DEGRADED" in ln
           or "-SKIP" in ln or "-FAIL" in ln for ln in lines):
        return "DEGRADED"
    return "SILENCE"


def count_emit_lines(stdout):
    return sum(1 for ln in (stdout or "").splitlines() if "-EMIT" in ln)


def write_run_receipt(repo_root, job, started_at, ended_at,
                      returncode, stdout, stderr=""):
    """Write one immutable receipt. Returns the receipt path.

    started_at/ended_at: timezone-aware datetimes (UTC).
    stdout/stderr: captured sensor output (str).
    Raises FileExistsError on run_id collision (fail-closed, immutable).
    """
    repo = Path(repo_root)
    run_id = _stamp(ended_at)
    duration_s = round((ended_at - started_at).total_seconds(), 3)
    classification = classify_output(returncode, stdout)
    tail = (stdout or "").splitlines()[-20:]

    receipt = {
        "schema": SCHEMA,
        "job": job,
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_s": duration_s,
        "returncode": returncode,
        "classification": classification,
        "emit_lines": count_emit_lines(stdout),
        "stdout_tail": tail,
        "agent_invoked": False,
        "mutation_attempts": 0,
    }
    blob = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    receipt["sha256"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()

    outdir = repo / "deploy" / "receipts"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{FILENAME_PREFIX}{job}-{run_id}.json"
    if path.exists():
        raise FileExistsError(f"receipt collision (immutable): {path}")
    path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return path


def verify_receipt(path):
    """Re-hash a receipt file. Returns True iff sha256 matches."""
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    expect = parsed.pop("sha256", None)
    blob = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest() == expect
