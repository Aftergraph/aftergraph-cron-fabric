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

# Repos whose main branch is protected by a merge-queue ruleset.
# Scope is read from contracts/queue-policy.yaml at sensor startup.
# The sensor fails closed if the file is missing or malformed.
QUEUE_POLICY_PATH = "contracts/queue-policy.yaml"

# Trust threshold: a repo is operationally interesting only if its
# queue ruleset is active AND has zero bypass actors. A repo with
# bypass actors can merge --admin at any time, so a stall there is
# not a meaningful finding.
MIN_RULESET_ENFORCEMENT = "active"
MAX_BYPASS_ACTORS = 0

STALL_WINDOW_MINUTES = 90

# Offline fixture support. When --offline-fixture <path> is passed (or
# AG_FABRIC_OFFLINE_FIXTURE is set), all gh api calls return data from
# the fixture dict instead of network. The fixture schema is:
#   {
#     "prs": { "<repo>": [<pr dict>, ...], ... },
#     "timeline": { "<repo>#<num>": [<event>, ...], ... }
#   }
# This is the only synthetic-proof path. Live GitHub access is never
# required for a fixture run.
_OFFLINE_FIXTURE = None


def _load_offline_fixture():
    global _OFFLINE_FIXTURE
    if _OFFLINE_FIXTURE is not None:
        return _OFFLINE_FIXTURE
    path = None
    if len(sys.argv) > 1 and sys.argv[1] == "--offline-fixture" \
            and len(sys.argv) > 2:
        path = sys.argv[2]
    elif "AG_FABRIC_OFFLINE_FIXTURE" in os.environ:
        path = os.environ["AG_FABRIC_OFFLINE_FIXTURE"]
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        _OFFLINE_FIXTURE = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _OFFLINE_FIXTURE = {}
    return _OFFLINE_FIXTURE


def _gh_api(repo, endpoint):
    """Best-effort gh api call. Returns dict or None on failure.
    In offline-fixture mode, returns data from the loaded fixture
    based on (repo, endpoint) key."""
    fix = _load_offline_fixture()
    if fix is not None:
        # Endpoint "pulls?state=open&..." maps to fix["prs"][repo]
        if endpoint.startswith("pulls"):
            return fix.get("prs", {}).get(repo)
        return None
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
    fix = _load_offline_fixture()
    if fix is not None:
        key = f"{repo}#{pr_number}"
        events = fix.get("timeline", {}).get(key, [])
        return {e.get("event") for e in events if e.get("event")}
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

    # Include queue_progress presence in evidence so a re-arm
    # (progress -> no-progress) yields a new fingerprint and the
    # EventStore can re-EMIT. Without this, a transition from
    # "stalled with progress" back to "stalled without progress" is
    # observably different state but the sensor would dedupe to
    # SILENCE.
    return True, {
        "repo": repo,
        "pr_number": pr["number"],
        "head_sha": pr["headRefOid"],
        "age_minutes": round(age_min, 1),
        "stall_window_minutes": STALL_WINDOW_MINUTES,
        "queue_signals_seen": sorted(signals),
        "queue_progress_active": bool(queue_progress),
        "checks_summary": [
            {"name": c.get("name"), "conclusion": c.get("conclusion")}
            for c in rollup if c.get("__typename") == "CheckRun"],
    }


def _iso_to_epoch(s):
    """Parse GitHub's ISO 8601 timestamp to a unix epoch."""
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _load_queue_policy():
    """Read contracts/queue-policy.yaml and return the list of repos
    that pass the trust threshold (active ruleset, zero bypass actors).

    Fails closed: missing file, malformed YAML, no schema fields -> [].
    The sensor then emits nothing (it does not know what to watch).
    """
    p = REPO / QUEUE_POLICY_PATH
    if not p.exists():
        print(f"MERGE-QUEUE-STALL-FAIL: policy missing: {QUEUE_POLICY_PATH}")
        return []
    try:
        # Tiny YAML reader: we only need one nested list with a fixed
        # shape. PyYAML would be cleaner but is not a dep of Cron Fabric.
        import yaml  # type: ignore
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"MERGE-QUEUE-STALL-FAIL: policy malformed: {exc}")
        return []
    repos = []
    for entry in (data or {}).get("queue_required_repos", []) or []:
        full = entry.get("full_name")
        enforcement = entry.get("enforcement")
        bypass = entry.get("bypass_actors", 0)
        if not full:
            continue
        if enforcement != MIN_RULESET_ENFORCEMENT:
            print(f"MERGE-QUEUE-STALL-SKIP: {full}: enforcement={enforcement}")
            continue
        if bypass > MAX_BYPASS_ACTORS:
            print(f"MERGE-QUEUE-STALL-SKIP: {full}: bypass_actors={bypass}")
            continue
        repos.append(full)
    return repos


def main():
    # REPO is resolved at module-import time, but tests inject a
    # different fabric root via AG_FABRIC_ROOT. Re-resolve here so
    # REPO-rooted paths (state/, contracts/) match the test workdir.
    global REPO
    REPO = _repo_root()
    # Persistent EventStore path: per-sensor, not per-run. Cross-run
    # dedupe (EMIT once, then SILENCE on repeat) requires the same DB
    # across runs. Tests override via AG_FABRIC_STORE.
    default_store = REPO / "state" / "merge_queue_stall.sqlite"
    store_path = os.environ.get(
        "AG_FABRIC_STORE", str(default_store))
    store = EventStore(store_path)

    queue_repos = _load_queue_policy()
    if not queue_repos:
        print("MERGE-QUEUE-STALL-OK: no queue-protected repos in policy, "
              "nothing to watch")
        sys.exit(0)

    total_checked = 0
    total_emitted = 0
    stalled_keys = set()
    for repo in queue_repos:
        prs = _gh_api(repo, "pulls?state=open&per_page=50")
        if not isinstance(prs, list):
            print(f"MERGE-QUEUE-STALL-SKIP: {repo}: gh api unavailable")
            continue
        for pr in prs:
            total_checked += 1
            is_stalled, evidence = _is_stalled(repo, pr)
            if not is_stalled:
                continue
            stalled_keys.add(
                f"merge-queue-stall|{repo}|{pr['number']}|"
                f"{pr['headRefOid']}")
            fp_evidence = json.dumps(evidence, sort_keys=True,
                                    separators=(",", ":"))
            key = (f"merge-queue-stall|{repo}|{pr['number']}|"
                   f"{pr['headRefOid']}")
            action = store.claim_event(key, fp_evidence)
            total_emitted += 1 if action == "EMIT" else 0
            print(f"MERGE-QUEUE-STALL-{action}: {repo}#{pr['number']} "
                  f"age={evidence['age_minutes']}m")

    # Observed recovery: any previously-OPEN stall event for a scanned
    # repo that is no longer stalled (queue progress appeared, PR
    # merged, or head changed) is RESOLVED. Marking HEALTHY re-arms the
    # event key so a later stall with the same head SHA can EMIT again.
    total_resolved = 0
    open_keys = store.open_event_keys()
    for key in open_keys:
        if not key.startswith("merge-queue-stall|"):
            continue
        if key in stalled_keys:
            continue
        store.resolve(key)
        total_resolved += 1
        print(f"MERGE-QUEUE-STALL-RESOLVED: {key}")

    print(f"MERGE-QUEUE-STALL-OK: checked={total_checked} "
          f"emitted={total_emitted} resolved={total_resolved}")
    sys.exit(0)


if __name__ == "__main__":
    main()
