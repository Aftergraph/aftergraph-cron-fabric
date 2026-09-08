"""Merge-queue stall sensor (no_agent, read-only).

Detects PRs that are MERGEABLE + auto-merge enabled + queue-required but
have made no queue progress for a bounded window. This is the failure
class that motivates the job: a PR looks healthy in every UI but never
gets enqueued.

The sensor DOES NOT enqueue, merge, push, approve, or otherwise mutate
state. Observation only. Civilization has already conducted enough
experiments involving monitoring systems that quietly become
administrators.

Output: zero or more EventStore claims under
    merge-queue-stall|repo|pr-number|head-sha
with evidence of the stall condition. The canary pair already
documents the dedupe / replay / re-arm semantics; this sensor is just
another claimant into the same EventStore.

Stall condition (all must hold):
- PR state = OPEN
- mergeable = true
- mergeStateStatus = BLOCKED (the queue is required and stalled)
- autoMergeRequest.enabledBy exists (auto-merge was activated)
- at least one required check is SUCCESS (nothing else is blocking)
- PR has not been touched in > stall_window_minutes (default 90)
- no `enqueued`/`dequeued`/`merge_group` events in the PR's issue timeline

If the conditions do not all hold, the sensor emits nothing for that PR.
"""
import json
import os
import subprocess
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
    print("MERGE-QUEUE-STALL-FAIL: cannot locate fabric root")
    sys.exit(2)


REPO = _repo_root()
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
from event_store import EventStore

# Repos whose main branch is protected by a merge-queue ruleset. The
# canonical list lives in after-graph-governance; we hard-code it here
# because the sensor runs offline (no GH API call for the rule itself).
# Update via PR when governance adds/removes queue-required repos.
QUEUE_REQUIRED_REPOS = [
    "Aftergraph/after-graph-governance",
]

STALL_WINDOW_MINUTES = 90


def _gh_api(repo, endpoint):
    """Best-effort gh api call. Returns dict or None on failure."""
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/{endpoint}"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _pr_timeline_signals(repo, pr_number):
    """Return set of event names that indicate queue progress."""
    try:
        out = subprocess.run(
            ["gh", "api",
             f"repos/{repo}/issues/{pr_number}/timeline",
             "--paginate"],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return set()
        events = set()
        for line in out.stdout.splitlines():
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = obj.get("event", "")
            if ev in ("enqueued", "dequeued", "merge_group",
                      "merge_queue_merged", "auto_merge_enabled"):
                events.add(ev)
        return events
    except subprocess.TimeoutExpired:
        return set()


def _is_stalled(repo, pr):
    """Return (True, evidence_dict) if the PR is stalled per the contract."""
    if pr.get("state") != "OPEN":
        return False, None
    if not pr.get("autoMergeRequest"):
        return False, None
    if pr.get("mergeStateStatus") != "BLOCKED":
        return False, None
    if pr.get("mergeable") != "MERGEABLE":
        return False, None

    # Check that at least one required check is SUCCESS — otherwise the
    # PR is blocked on a real failure, not a queue stall.
    rollup = pr.get("statusCheckRollup") or []
    has_success = any(
        c.get("conclusion") == "SUCCESS" for c in rollup
        if c.get("__typename") == "CheckRun")
    if not has_success:
        return False, None

    # Stall window: updated_at > stall_window ago with no queue progress.
    updated = pr.get("updatedAt")  # ISO 8601
    if not updated:
        return False, None
    age_min = (time.time() - _iso_to_epoch(updated)) / 60.0
    if age_min < STALL_WINDOW_MINUTES:
        return False, None

    signals = _pr_timeline_signals(repo, pr["number"])
    queue_progress = signals & {
        "enqueued", "dequeued", "merge_group", "merge_queue_merged"}
    if queue_progress:
        return False, None

    return True, {
        "repo": repo,
        "pr_number": pr["number"],
        "head_sha": pr["headRefOid"],
        "age_minutes": round(age_min, 1),
        "stall_window_minutes": STALL_WINDOW_MINUTES,
        "queue_signals_seen": sorted(signals),
        "checks_summary": [
            {"name": c.get("name"), "conclusion": c.get("conclusion")}
            for c in rollup if c.get("__typename") == "CheckRun"],
    }


def _iso_to_epoch(s):
    """Parse GitHub's ISO 8601 timestamp to a unix epoch."""
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main():
    store = EventStore(str(
        REPO / "state" / f"merge_queue_stall.{int(time.time())}.sqlite"))

    total_checked = 0
    total_emitted = 0
    for repo in QUEUE_REQUIRED_REPOS:
        prs = _gh_api(repo, "pulls?state=open&per_page=50")
        if not isinstance(prs, list):
            print(f"MERGE-QUEUE-STALL-SKIP: {repo}: gh api unavailable")
            continue
        for pr in prs:
            total_checked += 1
            is_stalled, evidence = _is_stalled(repo, pr)
            if not is_stalled:
                continue
            fp_evidence = json.dumps(evidence, sort_keys=True,
                                    separators=(",", ":"))
            key = (f"merge-queue-stall|{repo}|{pr['number']}|"
                   f"{pr['headRefOid']}")
            action = store.claim_event(key, fp_evidence)
            total_emitted += 1 if action == "EMIT" else 0
            print(f"MERGE-QUEUE-STALL-{action}: {repo}#{pr['number']} "
                  f"age={evidence['age_minutes']}m")

    print(f"MERGE-QUEUE-STALL-OK: checked={total_checked} "
          f"emitted={total_emitted}")
    sys.exit(0)


if __name__ == "__main__":
    main()
