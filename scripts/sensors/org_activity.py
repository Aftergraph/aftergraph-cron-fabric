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
    if (cand / "contracts" / "sources.yaml").is_file():
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

    The collector reconciles live GitHub state against this canonical
    classification. Unknown live repos are recorded as warnings; they are
    NOT added to the canonical topology.
    """
    if not TOPOLOGY_PATH.is_file():
        return []
    try:
        data = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
        return data.get("repositories") or []
    except (json.JSONDecodeError, OSError):
        return []


def topo_lookup(repo_full_name: str):
    """Return canonical plane/role for repo if in topology."""
    topo = load_topology()
    for r in topo:
        if r.get("name") == repo_full_name:
            return {"plane": r.get("plane"), "role": r.get("role")}
    return None


# ---------------------------------------------------------------------------
# GitHub access (GET-only, through gh-read.sh)
# ---------------------------------------------------------------------------

GH_READ = str(REPO / "scripts" / "gh-read.sh")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "Aftergraph")


def gh_api(args: list, timeout: int = 60) -> Optional[dict]:
    """Authenticated GET-only GitHub API call via gh-read.sh.

    Returns parsed JSON or None on failure. Never mutates state.
    """
    cmd = ["bash", GH_READ] + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(REPO),
        )
    except subprocess.TimeoutExpired:
        print(f"ORG-PULSE-WARN: gh api timeout: {' '.join(args)[:80]}")
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
    """Discover current live organization repositories.

    Authenticated read-only call. Returns list of repo dicts from the
    GitHub API with pagination.
    """
    repos = []
    page = 1
    while True:
        data = gh_api(
            [
                "orgs",
                GITHUB_ORG,
                "repos",
                "--paginate",
                "--limit",
                "100",
                f"--page",
                str(page),
                "--jq",
                "'.[] | {full_name: .full_name, name: .name, visibility: .visibility, archived: .archived, default_branch: .default_branch, pushed_at: .pushed_at}'",
            ]
        )
        if not isinstance(data, list):
            break
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)  # avoid bursts
    return repos


def fetch_repo_activity(repo_full_name: str, since: Optional[str] = None):
    """Fetch recent activity for a single repository.

    Uses per-resource endpoints as primary evidence. Organization events
    API is NOT used as authoritative real-time state.

    Returns list of normalized ActivityEvent dicts (not yet persisted).
    """
    events = []
    observed_at = utc_now_iso()

    # 1. Repository events (creates, deletes, renames, archives) — primary
    repo_events = _fetch_repo_events(repo_full_name)
    for raw in repo_events:
        ev = normalize_event(raw, topo_lookup)
        if ev is not None:
            ev["event_id"] = build_event_id(ev)
            ev["occurred_at"] = ev.get("occurred_at") or utc_now_iso()
            ev["observed_at"] = observed_at
            ev["payload_digest"] = payload_digest(ev)
            events.append(ev)

    # 2. Recent pushes / commits on default branch — primary
    commits = _fetch_repo_commits(repo_full_name, since)
    for raw in commits:
        ev = normalize_event(raw, topo_lookup)
        if ev is not None:
            ev["event_id"] = build_event_id(ev)
            ev["occurred_at"] = ev.get("occurred_at") or utc_now_iso()
            ev["observed_at"] = observed_at
            ev["payload_digest"] = payload_digest(ev)
            events.append(ev)

    # 3. Pull requests — primary
    prs = _fetch_repo_pull_requests(repo_full_name)
    for raw in prs:
        ev = normalize_event(raw, topo_lookup)
        if ev is not None:
            ev["event_id"] = build_event_id(ev)
            ev["occurred_at"] = ev.get("occurred_at") or utc_now_iso()
            ev["observed_at"] = observed_at
            ev["payload_digest"] = payload_digest(ev)
            events.append(ev)

    # 4. Workflow runs — primary
    workflows = _fetch_repo_workflow_runs(repo_full_name)
    for raw in workflows:
        ev = normalize_event(raw, topo_lookup)
        if ev is not None:
            ev["event_id"] = build_event_id(ev)
            ev["occurred_at"] = ev.get("occurred_at") or utc_now_iso()
            ev["observed_at"] = observed_at
            ev["payload_digest"] = payload_digest(ev)
            events.append(ev)

    # 5. Releases — primary
    releases = _fetch_repo_releases(repo_full_name)
    for raw in releases:
        ev = normalize_event(raw, topo_lookup)
        if ev is not None:
            ev["event_id"] = build_event_id(ev)
            ev["occurred_at"] = ev.get("occurred_at") or utc_now_iso()
            ev["observed_at"] = observed_at
            ev["payload_digest"] = payload_digest(ev)
            events.append(ev)

    # 6. Issues — primary (opened/closed)
    issues = _fetch_repo_issues(repo_full_name)
    for raw in issues:
        ev = normalize_event(raw, topo_lookup)
        if ev is not None:
            ev["event_id"] = build_event_id(ev)
            ev["occurred_at"] = ev.get("occurred_at") or utc_now_iso()
            ev["observed_at"] = observed_at
            ev["payload_digest"] = payload_digest(ev)
            events.append(ev)

    return events


def _fetch_repo_events(repo_full_name: str) -> list:
    """RepositoryEvent feed for repo lifecycle changes."""
    data = gh_api(
        [
            "repos",
            repo_full_name,
            "events",
            "--limit",
            "50",
            "--jq",
            "'.[] | select(.type == \"RepositoryEvent\" or .type == \"CreateEvent\" or .type == \"DeleteEvent\") | {type: .type, action: .action, created_at: .created_at, actor: .actor.login, repo: .repo.full_name, payload: .}'",
        ]
    )
    if isinstance(data, list):
        return data
    return []


def _fetch_repo_commits(repo_full_name: str, since: Optional[str] = None) -> list:
    """Recent commits on default branch."""
    # Determine default branch via per-repo API
    default_branch = "main"
    info = gh_api(["repos", repo_full_name, "--jq", "'.default_branch'"])
    if isinstance(info, str) and info:
        default_branch = info

    args = [
        "repos",
        repo_full_name,
        "commits",
        "--limit",
        "50",
        "--jq",
        f".[] | {{type: 'PushEvent', action: 'pushed', created_at: .commit.author.date, actor: .author.login, repo: '{repo_full_name}', payload: {{commits: [{{id: .sha, message: .commit.message, timestamp: .commit.author.date}}], ref: 'refs/heads/{default_branch}', repo: '{repo_full_name}'}}}}",
    ]
    if since:
        # Only commits after `since`
        pass  # GitHub commits endpoint supports ?since; add below
    data = gh_api(args)
    if isinstance(data, list):
        # Wrap as PushEvent-like payloads for the normalizer
        wrapped = []
        for c in data:
            if isinstance(c, dict) and c.get("sha"):
                wrapped.append(
                    {
                        "type": "PushEvent",
                        "action": "pushed",
                        "created_at": c.get("commit", {}).get("author", {}).get("date"),
                        "actor": c.get("author", {}).get("login"),
                        "repo": repo_full_name,
                        "payload": {
                            "commits": [
                                {
                                    "id": c.get("sha"),
                                    "sha": c.get("sha"),
                                    "message": c.get("commit", {}).get("message", ""),
                                    "timestamp": c.get("commit", {}).get("author", {}).get("date"),
                                }
                            ],
                            "ref": f"refs/heads/{default_branch}",
                            "repository": {"full_name": repo_full_name, "name": repo_full_name.split("/")[-1]},
                        },
                    }
                )
        return wrapped
    return []


def _fetch_repo_pull_requests(repo_full_name: str) -> list:
    """Recent PRs surfaced as PullRequestEvent-like payloads."""
    # List recent PRs with activity; pull requests endpoint
    data = gh_api(
        [
            "repos",
            repo_full_name,
            "pulls",
            "--limit",
            "50",
            "--state",
            "all",
            "--jq",
            "'.[] | {number: .number, state: .state, merged: .merged, title: .title, html_url: .html_url, updated_at: .updated_at}'",
        ]
    )
    if not isinstance(data, list):
        return []
    wrapped = []
    for pr in data:
        # Emit a synthetic PullRequestEvent only if it has recent updates
        # We treat any PR returned as an observation point; normalizer
        # filters by action. Here we emit "opened" for any that exists.
        wrapped.append(
            {
                "type": "PullRequestEvent",
                "action": "opened",
                "created_at": pr.get("updated_at"),
                "actor": "github",
                "repo": repo_full_name,
                "payload": {
                    "pull_request": {
                        "number": pr["number"],
                        "state": pr["state"],
                        "merged": pr.get("merged", False),
                        "title": pr.get("title", ""),
                        "html_url": pr.get("html_url"),
                    },
                    "repository": {"full_name": repo_full_name, "name": repo_full_name.split("/")[-1]},
                },
            }
        )
    return wrapped


def _fetch_repo_workflow_runs(repo_full_name: str) -> list:
    """Recent workflow runs with conclusions."""
    data = gh_api(
        [
            "repos",
            repo_full_name,
            "actions",
            "runs",
            "--limit",
            "50",
            "--jq",
            "'.workflow_runs[] | select(.status == \"completed\") | {id: .id, name: .name, conclusion: .conclusion, html_url: .html_url, created_at: .created_at, status: .status}'",
        ]
    )
    if not isinstance(data, list):
        return []
    wrapped = []
    for run in data:
        wrapped.append(
            {
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
                    "repository": {"full_name": repo_full_name, "name": repo_full_name.split("/")[-1]},
                },
            }
        )
    return wrapped


def _fetch_repo_releases(repo_full_name: str) -> list:
    """Recent releases."""
    data = gh_api(
        [
            "repos",
            repo_full_name,
            "releases",
            "--limit",
            "50",
            "--jq",
            "'.[] | {tag_name: .tag_name, name: .name, html_url: .html_url, created_at: .created_at, draft: .draft}'",
        ]
    )
    if not isinstance(data, list):
        return []
    wrapped = []
    for rel in data:
        if rel.get("draft"):
            continue
        wrapped.append(
            {
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
                    "repository": {"full_name": repo_full_name, "name": repo_full_name.split("/")[-1]},
                },
            }
        )
    return wrapped


def _fetch_repo_issues(repo_full_name: str) -> list:
    """Recent issues (opened/closed)."""
    data = gh_api(
        [
            "repos",
            repo_full_name,
            "issues",
            "--limit",
            "50",
            "--state",
            "all",
            "--jq",
            "'.[] | select(.pull_request | not) | {number: .number, state: .state, title: .title, html_url: .html_url, created_at: .created_at, updated_at: .updated_at}'",
        ]
    )
    if not isinstance(data, list):
        return []
    wrapped = []
    for iss in data:
        wrapped.append(
            {
                "type": "IssuesEvent",
                "action": "opened",
                "created_at": iss.get("created_at"),
                "actor": "github",
                "repo": repo_full_name,
                "payload": {
                    "issue": {
                        "number": iss["number"],
                        "state": iss["state"],
                        "title": iss.get("title", ""),
                        "html_url": iss.get("html_url"),
                    },
                    "repository": {"full_name": repo_full_name, "name": repo_full_name.split("/")[-1]},
                },
            }
        )
    return wrapped


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def main():
    store = ActivityStore()
    observed_at = utc_now_iso()

    # 1. Discover live organization repositories
    print("ORG-PULSE-COLLECT: discovering live org repos")
    live_repos = list_org_repos()
    if not live_repos:
        print("ORG-PULSE-WARN: could not discover org repos")
        sys.exit(1)

    # Filter out .github (org profile)
    live_repos = [r for r in live_repos if not r["full_name"].endswith("/.github")]

    # 2. Reconcile against canonical topology
    topology = load_topology()
    recon = reconcile_topology(live_repos, topology)
    if recon["unknown_live"]:
        for repo in recon["unknown_live"]:
            print(f"ORG-PULSE-WARN: unknown live repo (not in canonical topology): {repo}")
            # Record an observation event for the unknown repo
            obs = {
                "event_id": f"ag-observation-unknown-{repo.replace('/', '-')}",
                "occurred_at": observed_at,
                "observed_at": observed_at,
                "repo": repo,
                "kind": "repository",
                "action": "observed_unknown",
                "actor": "org-pulse-collector",
                "ref": {"type": "repository", "identifier": repo, "name": repo},
                "source_url": f"https://github.com/{repo}",
                "source_type": "github_api",
                "payload_digest": "unknown-live-repo",
                "display_title": f"Unknown live repo: {repo}",
                "importance": "high",
                "canonical": 1,
            }
            store.insert_event(obs)
    if recon["missing_expected"]:
        for repo in recon["missing_expected"]:
            print(f"ORG-PULSE-WARN: expected repo missing in live org: {repo}")

    # 3. Record repo metadata snapshot
    for repo_info in live_repos:
        store.upsert_repo_metadata(
            repo_info["full_name"],
            archived=repo_info.get("archived", False),
            default_branch=repo_info.get("default_branch"),
        )

    # 4. Collect activity for each repo (respecting rate limits)
    total_events = 0
    total_persisted = 0
    total_duplicates = 0

    print(f"ORG-PULSE-COLLECT: collecting activity for {len(live_repos)} repos")
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

        # Rate-limit courtesy: small delay between repos
        if idx < len(live_repos) - 1:
            time.sleep(0.5)

    # 5. Update source cursors
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
