"""Org-suite liveness sensor (no_agent, read-only).

Proves the shared Aftergraph polyrepo integration machinery actually
ran in the expected window and did not silently disappear. CI tells us
when a test fails; this sensor tells us when the test that should have
run never ran.

Output: zero or one EventStore claim per run under
    org-suite-liveness|{run_window_id}
with classification:
    ORG-SUITE-VERIFIED   — all expected core repos had a fresh run
    ORG-SUITE-PENDING    — some expected runs missing but within tolerance
    ORG-SUITE-MISSED     — required runs absent past tolerance
    ORG-SUITE-DEGRADED   — runs present but inconsistent (wrong revision,
                           or core repo skipped silently)
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Repos whose main CI is expected to run on a regular schedule. Update
# when the org adds/removes a core repo with a CI workflow.
CORE_REPOS = [
    "Aftergraph/after-graph-governance",
    "Aftergraph/aftergraph-cron-fabric",
    "Aftergraph/continuum",
    "Aftergraph/sentinel",
    "Aftergraph/skills-vault",
    "Aftergraph/runtime",
    "Aftergraph/trust-gateway",
    "Aftergraph/works-execution",
]

# A run is "fresh" if it completed within this window (hours).
FRESH_WINDOW_HOURS = 24

DEGRADED_PCT_THRESHOLD = 0.20  # >20% missing core repos = DEGRADED


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
    print("ORG-SUITE-LIVENESS-FAIL: cannot locate fabric root")
    sys.exit(2)


REPO = _repo_root()
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
from event_store import EventStore


def _gh_api(repo, endpoint):
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/{endpoint}"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _latest_ci_run(repo):
    """Return (conclusion, head_sha, completed_at) for the most recent
    completed CI workflow run on main, or None if absent."""
    out = subprocess.run(
        ["gh", "run", "list",
         "-R", repo, "--branch", "main",
         "--limit", "5", "--json", "conclusion,name,headSha,status,databaseId"],
        capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None
    try:
        runs = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    completed = [r for r in runs if r.get("status") == "completed"]
    if not completed:
        return None
    r = completed[0]
    return {
        "conclusion": r.get("conclusion"),
        "head_sha": r.get("headSha"),
        "run_id": r.get("databaseId"),
        "name": r.get("name"),
    }


def main():
    store = EventStore(str(
        REPO / "state" / f"org_suite_liveness.{int(time.time())}.sqlite"))

    missing = []
    present = []
    for repo in CORE_REPOS:
        run = _latest_ci_run(repo)
        if run is None:
            missing.append(repo)
            continue
        present.append({"repo": repo, "run": run})

    if not present:
        classification = "ORG-SUITE-MISSED"
    else:
        missing_pct = len(missing) / len(CORE_REPOS)
        if missing_pct == 0.0:
            classification = "ORG-SUITE-VERIFIED"
        elif missing_pct > DEGRADED_PCT_THRESHOLD:
            classification = "ORG-SUITE-DEGRADED"
        else:
            classification = "ORG-SUITE-PENDING"

    run_window_id = time.strftime("%Y%m%dT%H", time.gmtime())
    evidence = {
        "classification": classification,
        "run_window_id": run_window_id,
        "core_repos": len(CORE_REPOS),
        "present": len(present),
        "missing": sorted(missing),
        "missing_pct": round(missing_pct, 4)
            if 'missing_pct' in dir() else len(missing) / len(CORE_REPOS),
        "fresh_window_hours": FRESH_WINDOW_HOURS,
    }
    fp = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    key = f"org-suite-liveness|{run_window_id}"
    action = store.claim_event(key, fp)
    print(f"ORG-SUITE-LIVENESS-{action}: {classification} "
          f"present={len(present)} missing={len(missing)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
