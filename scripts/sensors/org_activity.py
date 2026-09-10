"""Org activity collector — scheduled read-only observation of Aftergraph org activity.

Collects GitHub activity across live repositories, persists immutable
ActivityEvent rows, and records source/delivery/digest cursors. All
GitHub access goes through scripts/gh-read.sh (GET-only enforcement).

Schedule: every 15 minutes (jobs/ag-org-pulse.yaml).
Mode: no_agent / script_only.

This script does NOT emit Telegram. Rendering is handled by
org_activity_renderer.py and dispatched via the existing
telegram_receipt_bridge.py / hermes statuscard path.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from activity_store import ActivityStore
from activity_normalizer import (
    build_event_id,
    normalize_event,
    payload_digest,
    reconcile_topology,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    env = os.environ.get("AG_FABRIC_ROOT")
    if env:
        return Path(env)
    cand = Path(__file__).resolve().parent.parent
    if (cand / "contracts" / "sources.yaml").is_file():
        return cand
    cwd = Path.cwd()
    if (cwd / "contracts" / "sources.yaml").is_file():
        return cwd
    print("ORG-PULSE-FAIL: cannot locate fabric root")
    sys.exit(2)


REPO = _repo_root()
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = REPO / "state"

# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

TOPOLOGY_PATH = (
    Path.home()
    / "after-graph-governance"
    / "docs"
    / "platform-topology"
    / "2.0.json"
)


def load_topology():
    """Load canonical topology from local governance checkout.

    GitHub is OBSERVED live state. Governance topology is CANONICAL
    organizational classification. Unknown live repos are recorded as
    observation events; they are never added to canonical topology.
    """
    if not TOPOLOGY_PATH.is_file():
        return []
    try:
        data = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
        return data.get("repositories") or []
    except (json.JSONDecodeError, OSError):
        return []


def topo_lookup(repo_full_name: str):
    """Return canonical plane/role for repo if in topology.

    Topology names are bare repo names ("aftergraph-cron-fabric");
    API returns full_name ("Aftergraph/aftergraph-cron-fabric").
    Compare on the trailing name segment, consistent with
    reconcile_topology().
    """
    topo = load_topology()
    key = repo_full_name.rsplit("/", 1)[-1]
    for r in topo:
        topo_name = r.get("name", "").rsplit("/", 1)[-1]
        if topo_name == key:
            return {"plane": r.get("plane"), "role": r.get("role")}
    return None


# ---------------------------------------------------------------------------
# GitHub access (GET-only, through gh-read.sh)
# ---------------------------------------------------------------------------

# Relative to cwd: the subprocess always runs with cwd=str(REPO), and
# bash (git-bash on Windows) refuses absolute MSYS paths like
# C:/.../gh-read.sh as a command. A relative path resolves correctly
# as long as cwd == REPO, which gh_api() guarantees.
GH_READ = "scripts/gh-read.sh"
GITHUB_ORG = os.environ.get("GITHUB_ORG", "Aftergraph")

# On Windows, subprocess -> bash argv translation mangles args that
# contain '&' or jq expressions (the bash child re-parses the flattened
# command line and treats them as standalone commands). So we NEVER pass
# --jq / query strings as separate argv entries; we pass one full
# "bash -c" command string with single-quoted shell tokens instead.
# This keeps all network calls GET-only through gh-read.sh (which still
# rejects -X/-F), and Python parses the raw JSON response.


def gh_api(endpoint_or_args, timeout: int = 60):
    """GET-only GitHub API call through gh-read.sh.

    Accepts either a bare endpoint (str like 'orgs/Aftergraph/repos')
    or an endpoint with query params, e.g.
    'orgs/Aftergraph/repos?per_page=100&page=1&type=all'.
    Returns parsed JSON (list/dict) or None on failure. Never mutates.
    """
    endpoint = endpoint_or_args if isinstance(endpoint_or_args, str) \
        else " ".join(endpoint_or_args)
    cmd = f"{GH_READ} '{endpoint}'"
    # bash -c is required: a plain argv ['bash', GH_READ, endpoint]
    # reconstructs a Windows command line where '&' splits the endpoint
    # into separate bash commands.
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(REPO),
        )
    except subprocess.TimeoutExpired:
        print(f"ORG-PULSE-WARN: gh api timeout: {endpoint[:80]}")
        return None

    if proc.returncode != 0:
        print(f"ORG-PULSE-WARN: gh api failed rc={proc.returncode}: {proc.stderr[:200]}")
        return None

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"ORG-PULSE-WARN: gh api non-JSON response: {proc.stdout[:200]}")
        return None


def list_org_repos():
    """Live GitHub repos of the Aftergraph org (paginated, read-only).

    GitHub is OBSERVED live state. Returns list of repo dicts with
    the API's raw fields (full_name, name, visibility, archived,
    default_branch, pushed_at, private, ...).
    """
    repos = []
    page = 1
    while True:
        data = gh_api(
            f"orgs/{GITHUB_ORG}/repos?per_page=100&page={page}&type=all"
        )
        if not isinstance(data, list) or not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)  # avoid API bursts
    return repos


# ---------------------------------------------------------------------------
# Per-repo fetchers (primary evidence: direct repo/resource endpoints)
# ---------------------------------------------------------------------------

def _fetch_repo_events(repo_full_name: str) -> list:
    """Repo lifecycle event feed (created, archived, renamed, deleted)."""
    data = gh_api(f"repos/{repo_full_name}/events?per_page=50")
    if not isinstance(data, list):
        return []
    out = []
    for ev in data:
        if not isinstance(ev, dict):
            continue
        et = ev.get("type")
        if et not in ("RepositoryEvent", "CreateEvent", "DeleteEvent"):
            continue
        actor = ev.get("actor") or {}
        repo = ev.get("repo") or {}
        out.append({
            "type": et,
            "action": ev.get("action"),
            "created_at": ev.get("created_at"),
            "actor": actor.get("login") if isinstance(actor, dict) else actor,
            "repo": repo,
            "payload": ev.get("payload") or {},
        })
    return out


def _repo_default_branch(repo_full_name: str) -> str:
    info = gh_api(f"repos/{repo_full_name}")
    if isinstance(info, dict) and info.get("default_branch"):
        return info["default_branch"]
    return "main"


def _fetch_repo_commits(repo_full_name: str, since: Optional[str] = None) -> list:
    """Recent commits on the default branch, wrapped as PushEvent rows."""
    default_branch = _repo_default_branch(repo_full_name)
    url = f"repos/{repo_full_name}/commits?per_page=50"
    if since:
        url += f"&since={since}"
    data = gh_api(url)
    if not isinstance(data, list):
        return []
    wrapped = []
    for c in data:
        if not isinstance(c, dict) or not c.get("sha"):
            continue
        commit = c.get("commit") or {}
        author_meta = commit.get("author") or {}
        author = c.get("author") or {}
        author_date = author_meta.get("date")
        author_login = author.get("login") or commit.get("author", {}).get("name") or "unknown"
        wrapped.append({
            "type": "PushEvent",
            "action": "pushed",
            "created_at": author_date,
            "actor": author_login,
            "repo": repo_full_name,
            "payload": {
                "commits": [
                    {
                        "id": c.get("sha"),
                        "sha": c.get("sha"),
                        "message": commit.get("message", ""),
                        "timestamp": author_date,
                    }
                ],
                "ref": f"refs/heads/{default_branch}",
                "distinct": True,
                "repository": {
                    "full_name": repo_full_name,
                    "name": repo_full_name.split("/")[-1],
                },
            },
        })
    return wrapped


def _fetch_repo_pull_requests(repo_full_name: str) -> list:
    """Recent PRs surfaced as PullRequestEvent-like payloads."""
    data = gh_api(f"repos/{repo_full_name}/pulls?state=all&per_page=50")
    if not isinstance(data, list):
        return []
    wrapped = []
    for pr in data:
        if not isinstance(pr, dict) or pr.get("number") is None:
            continue
        user = pr.get("user") or {}
        login = user.get("login") or "github"
        wrapped.append({
            "type": "PullRequestEvent",
            "action": "opened" if pr.get("state") == "open" else "closed",
            "created_at": pr.get("updated_at"),
            "actor": login,
            "repo": repo_full_name,
            "payload": {
                "pull_request": {
                    "number": pr["number"],
                    "state": pr.get("state"),
                    "merged": pr.get("merged"),
                    "title": pr.get("title", ""),
                    "html_url": pr.get("html_url"),
                },
                "repository": {
                    "full_name": repo_full_name,
                    "name": repo_full_name.split("/")[-1],
                },
            },
        })
    return wrapped


def _fetch_repo_workflow_runs(repo_full_name: str) -> list:
    """Completed workflow runs with conclusions."""
    data = gh_api(f"repos/{repo_full_name}/actions/runs?per_page=50")
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        return []
    wrapped = []
    for run in data["workflow_runs"]:
        if not isinstance(run, dict) or run.get("id") is None:
            continue
        if run.get("status") != "completed":
            continue
        wrapped.append({
            "type": "WorkflowRunEvent",
            "action": "completed",
            "created_at": run.get("created_at"),
            "actor": "github",
            "repo": repo_full_name,
            "payload": {
                "workflow_run": {
                    "id": run["id"],
                    "name": run.get("name", ""),
                    "conclusion": run.get("conclusion"),
                    "html_url": run.get("html_url"),
                },
                "repository": {
                    "full_name": repo_full_name,
                    "name": repo_full_name.split("/")[-1],
                },
            },
        })
    return wrapped


def _fetch_repo_releases(repo_full_name: str) -> list:
    """Non-draft releases."""
    data = gh_api(f"repos/{repo_full_name}/releases?per_page=50")
    if not isinstance(data, list):
        return []
    wrapped = []
    for rel in data:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        wrapped.append({
            "type": "ReleaseEvent",
            "action": "published",
            "created_at": rel.get("created_at"),
            "actor": "github",
            "repo": repo_full_name,
            "payload": {
                "release": {
                    "tag_name": rel.get("tag_name"),
                    "name": rel.get("name"),
                    "html_url": rel.get("html_url"),
                },
                "repository": {
                    "full_name": repo_full_name,
                    "name": repo_full_name.split("/")[-1],
                },
            },
        })
    return wrapped


def _fetch_repo_issues(repo_full_name: str) -> list:
    """Issues (non-PR) surfaced as IssuesEvent-like payloads."""
    data = gh_api(f"repos/{repo_full_name}/issues?state=all&per_page=50")
    if not isinstance(data, list):
        return []
    wrapped = []
    for iss in data:
        if not isinstance(iss, dict) or iss.get("number") is None:
            continue
        if iss.get("pull_request"):
            continue  # PRs are collected separately; never double-surface
        user = iss.get("user") or {}
        login = user.get("login") or "github"
        wrapped.append({
            "type": "IssuesEvent",
            "action": "opened" if iss.get("state") == "open" else "closed",
            "created_at": iss.get("created_at"),
            "actor": login,
            "repo": repo_full_name,
            "payload": {
                "issue": {
                    "number": iss["number"],
                    "state": iss.get("state"),
                    "title": iss.get("title", ""),
                    "html_url": iss.get("html_url"),
                },
                "repository": {
                    "full_name": repo_full_name,
                    "name": repo_full_name.split("/")[-1],
                },
            },
        })
    return wrapped


def fetch_repo_activity(repo_full_name: str, since: Optional[str] = None):
    """Fetch recent activity for one repo.

    Primary evidence: direct repo/resource endpoints (commits, PRs,
    issues, workflow runs, releases, repo events). The org Events feed
    is NOT authoritative real-time state.

    Returns list of normalized ActivityEvent dicts (not yet persisted),
    with event_id / occurred_at / observed_at / payload_digest filled.
    """
    events = []
    observed_at = utc_now_iso()
    raw_batches = [
        _fetch_repo_events(repo_full_name),
        _fetch_repo_commits(repo_full_name, since),
        _fetch_repo_pull_requests(repo_full_name),
        _fetch_repo_workflow_runs(repo_full_name),
        _fetch_repo_releases(repo_full_name),
        _fetch_repo_issues(repo_full_name),
    ]
    for raw in raw_batches:
        for item in raw:
            ev = normalize_event(item, topo_lookup)
            if ev is None:
                continue
            ev["event_id"] = build_event_id(ev)
            ev["occurred_at"] = ev.get("occurred_at") or observed_at
            ev["observed_at"] = observed_at
            ev["payload_digest"] = payload_digest(ev)
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def main():
    store = ActivityStore()
    observed_at = utc_now_iso()

    print("ORG-PULSE-COLLECT: discovering live org repos")
    live_repos = list_org_repos()
    if not live_repos:
        print("ORG-PULSE-WARN: could not discover org repos")
        sys.exit(1)

    live_repos = [r for r in live_repos
                  if isinstance(r, dict) and r.get("full_name")
                  and not r["full_name"].endswith("/.github")]

    topology = load_topology()
    recon = reconcile_topology(live_repos, topology)
    for repo in recon["unknown_live"]:
        repo_name = repo.get("name") if isinstance(repo, dict) else repo
        print(f"ORG-PULSE-WARN: unknown live repo (not in canonical topology): {repo_name}")
        obs = {
            "event_id": f"ag-observation-unknown-{repo_name.replace('/', '-')}",
            "occurred_at": observed_at,
            "observed_at": observed_at,
            "repo": repo_name,
            "kind": "repository",
            "action": "observed_unknown",
            "actor": "org-pulse-collector",
            "ref": {"type": "repository", "identifier": repo_name, "name": repo_name},
            "source_url": f"https://github.com/{repo_name}",
            "source_type": "github_api",
            "payload_digest": f"unknown-live-{repo_name}",
            "display_title": f"Unknown live repo: {repo_name}",
            "importance": "high",
            "canonical": 1,
        }
        store.insert_event(obs)
    for repo in recon["missing_expected"]:
        repo_name = repo.get("name") if isinstance(repo, dict) else repo
        print(f"ORG-PULSE-WARN: expected repo missing in live org: {repo_name}")

    for repo_info in live_repos:
        store.upsert_repo_metadata(
            repo_info["full_name"],
            archived=repo_info.get("archived", False),
            default_branch=repo_info.get("default_branch"),
        )

    print(f"ORG-PULSE-COLLECT: collecting activity for {len(live_repos)} repos")
    total_persisted = 0
    total_duplicates = 0
    total_events = 0
    for idx, repo_info in enumerate(live_repos):
        repo = repo_info["full_name"]
        print(f"ORG-PULSE-COLLECT: [{idx+1}/{len(live_repos)}] {repo}")
        try:
            events = fetch_repo_activity(repo)
        except Exception as exc:
            print(f"ORG-PULSE-WARN: activity fetch failed for {repo}: {exc}")
            continue
        for ev in events:
            total_events += 1
            if store.insert_event(ev):
                total_persisted += 1
            else:
                total_duplicates += 1
        # Rate-limit courtesy: small delay between repos, never a burst.
        if idx < len(live_repos) - 1:
            time.sleep(0.4)

    store.set_source_cursor("github_org_repos", observed_at)
    store.set_source_cursor("github_repo_activity", observed_at)

    print(
        f"ORG-PULSE-COLLECT-DONE: events={total_events} "
        f"persisted={total_persisted} duplicates={total_duplicates} "
        f"repos={len(live_repos)}"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()