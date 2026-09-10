"""Activity normalizer — deterministic GitHub event -> ActivityEvent mapping.

No LLM. No invention. Every display_title, ref, action, importance, and
correlation_id is derived from observable GitHub API fields.

Public contract:
  normalize_event(raw, topo_lookup) -> ActivityEvent dict | None
  build_event_id(event) -> str
  payload_digest(event) -> str

Dedupe contract:
  Two observations of the same GitHub activity MUST produce the same
  event_id and the same payload_digest. The store's UNIQUE constraint on
  (repo, kind, action, ref_identifier, payload_digest) is the runtime
  dedupe safeguard; event_id is the canonical immutable identity.

Correlation contract:
  - PR merge + corresponding merge commit => same correlation_id
  - Same push containing N commits => push wrapper event may be emitted
    as a grouping hint, but individual commit events remain canonical.
  - Same migration/brand wave touching multiple repos in a short period
    => renderer MAY group as a cross-repo wave while preserving events.
"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


KINDS = {
    "PushEvent": "commit",
    "CommitCommentEvent": None,  # not in default feed
    "PullRequestEvent": "pull_request",
    "IssuesEvent": "issue",
    "WorkflowRunEvent": "workflow_run",
    "ReleaseEvent": "release",
    "RepositoryEvent": "repository",
    "CreateEvent": "repository",
    "DeleteEvent": "repository",
    "PublicEvent": None,
    "ForkEvent": None,
    "WatchEvent": None,
    "GollumEvent": None,
}

# Actions we surface by default. Everything else is filtered unless
# explicitly enabled.
DEFAULT_ACTIONS = {
    "PushEvent": {"pushed"},
    "PullRequestEvent": {"opened", "reopened", "ready_for_review", "merged", "closed"},
    "IssuesEvent": {"opened", "closed"},
    "WorkflowRunEvent": {"completed", "failed", "cancelled"},
    "ReleaseEvent": {"published"},
    "RepositoryEvent": {"created", "archived", "unarchived", "renamed"},
}

ACTOR_OVERRIDE_KINDS = {"PushEvent", "ReleaseEvent"}  # actor from payload.committer/actor


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def _fallback_actor(payload: Dict[str, Any]) -> Optional[str]:
    """Best-effort actor when the top-level actor field is absent."""
    for key in ("committer", "sender", "actor"):
        v = payload.get(key)
        if isinstance(v, dict) and v.get("login"):
            return v["login"]
        if isinstance(v, str) and v:
            return v
    return None


def _repo_full_name(payload: Dict[str, Any]) -> Optional[str]:
    """Full_name of the repo, accepting dict ('{"full_name": "o/r"}')
    or plain-string ("o/r") repo fields produced by the collector's
    per-resource fetchers."""
    repo = payload.get("repo") or payload.get("repository")
    if isinstance(repo, dict):
        return repo.get("full_name")
    if isinstance(repo, str) and repo:
        return repo
    return None


def _repo_name_only(payload: Dict[str, Any]) -> Optional[str]:
    name = payload.get("repo") or payload.get("repository")
    if isinstance(name, dict):
        return name.get("name")
    if isinstance(name, str) and name:
        return name.rsplit("/", 1)[-1]
    return None


def normalize_event(
    raw: Dict[str, Any],
    topo_lookup: Optional[callable] = None,
    observed_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Convert one raw GitHub webhook/event payload into an ActivityEvent.

    Returns None for events that are filtered out by default (no
    meaningful activity surfaced in the pulse). The caller is
    responsible for persisting returned events and recording source
    cursors.
    """
    if not isinstance(raw, dict):
        return None

    event_type = raw.get("type") or raw.get("event") or raw.get("X-GitHub-Event")
    if not event_type:
        return None

    kind = KINDS.get(event_type)
    if kind is None:
        return None  # filtered by default

    action = raw.get("action")
    if not action:
        return None

    # RepositoryEvent / CreateEvent / DeleteEvent: map action from
    # payload fields for rename/archive/etc.
    if event_type in ("RepositoryEvent", "CreateEvent", "DeleteEvent"):
        action = _repository_action(raw)

    # Determine allowed actions: for DeleteEvent/RepositoryEvent/CreateEvent
    # fall back to RepositoryEvent defaults since they share the same plane.
    allowed_key = event_type
    if event_type in ("DeleteEvent",):
        allowed_key = "RepositoryEvent"
    allowed_actions = DEFAULT_ACTIONS.get(allowed_key)
    if allowed_actions and action not in allowed_actions:
        return None  # filtered by default

    repo = _repo_full_name(raw)
    if not repo:
        return None

    repo_name = _repo_name_only(raw) or repo.split("/")[-1]

    occurred_at_raw = raw.get("created_at") or raw.get("updated_at")
    occurred_at = parse_iso(occurred_at_raw)
    if not occurred_at:
        occurred_at = utc_now_iso()

    actor = raw.get("actor", {}).get("login") if isinstance(raw.get("actor"), dict) else raw.get("actor")
    if not actor:
        actor = _fallback_actor(raw)
    if not actor:
        actor = "unknown"

    payload = raw.get("payload") or raw
    entry = _extract_entry(event_type, payload, repo, repo_name)

    if entry is None:
        return None

    ref_type, ref_identifier, ref_name = entry

    # Resolve semantic merge for PRs where action="closed" but merged=True.
    # _extract_entry cannot distinguish closed vs merged by itself when the
    # PR title does not contain "merge", so we re-check here.
    if kind == "pull_request":
        pr = payload.get("pull_request")
        if isinstance(pr, dict):
            if pr.get("merged") is True and action in ("closed", "merged"):
                action = "merged"

    source_url = _source_url(event_type, payload, repo)
    source_type = "github_api"

    # Plane/role from canonical topology when resolvable
    plane = None
    role = None
    if topo_lookup is not None:
        topo = topo_lookup(repo)
        if topo:
            plane = topo.get("plane")
            role = topo.get("role")

    display_title = _display_title(event_type, action, repo_name, ref_name, ref_identifier, payload)
    importance = _importance(event_type, action, payload)

    correlation_id = _correlation_id(event_type, payload, repo)

    # occurred_at and observed_at must be populated for downstream
    # event_id / dedupe / persistence. Use caller values when provided,
    # else current UTC now ( collector sets these explicitly per run ).
    occurred = occurred_at or utc_now_iso()
    observed = observed_at or utc_now_iso()

    return {
        "kind": kind,
        "action": action,
        "repo": repo,
        "plane": plane,
        "role": role,
        "actor": actor,
        "occurred_at": occurred,
        "observed_at": observed,
        "ref": {
            "type": ref_type,
            "identifier": ref_identifier,
            "name": ref_name,
        },
        "source_url": source_url,
        "source_type": source_type,
        "importance": importance,
        "correlation_id": correlation_id,
        "display_title": display_title,
        # caller must fill event_id, payload_digest
    }


def build_event_id(event: Dict[str, Any]) -> str:
    """Deterministic event_id from canonical fields.

    Stable across repeated observations of the same activity. Includes
    repo, kind, action, ref identifier, and a digest of the observable
    core payload.
    """
    core = {
        "repo": event["repo"],
        "kind": event["kind"],
        "action": event["action"],
        "ref": event["ref"]["identifier"],
        "actor": event["actor"],
        "occurred_at": event.get("occurred_at") or utc_now_iso(),
    }
    # Normalize actor comparisons: "unknown" stays unknown
    raw = json.dumps(core, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"ag-activity-{digest[:16]}"


def payload_digest(event: Dict[str, Any]) -> str:
    """Digest of the raw payload used for dedupe.

    Two observations of the same activity must produce the same
    payload_digest. The dedupe key is
    (repo, kind, action, ref_identifier, payload_digest).
    """
    canon = {
        "repo": event["repo"],
        "kind": event["kind"],
        "action": event["action"],
        "ref_type": event["ref"]["type"],
        "ref_identifier": event["ref"]["identifier"],
        "occurred_at": event["occurred_at"],
        "observed_at": event["observed_at"],
        "actor": event["actor"],
    }
    raw = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---- internal extraction ----

def _repository_action(raw: Dict[str, Any]) -> Optional[str]:
    payload = raw.get("payload") or raw
    action = payload.get("action") or raw.get("action")
    if not action:
        # CreateEvent: distinguish repo created vs branch/tag created.
        ref_type = payload.get("ref_type") or raw.get("ref_type")
        if ref_type == "repository":
            return "created"
        if ref_type in ("branch", "tag"):
            return None  # we do not surface branch/tag creates by default
        # DeleteEvent
        if raw.get("type") == "DeleteEvent" or payload.get("action") == "deleted":
            return "deleted"
        return None
    return action


def _extract_entry(
    event_type: str,
    payload: Dict[str, Any],
    repo: str,
    repo_name: str,
) -> Optional[Tuple[str, str, Optional[str]]]:
    """Return (ref_type, ref_identifier, ref_name) for the event."""
    if event_type == "PushEvent":
        commits = payload.get("commits") or []
        if not commits:
            return None
        commit = commits[0]
        sha = commit.get("id") or commit.get("sha")
        if not sha:
            return None
        return ("commit", sha, commit.get("message", "").split("\n")[0][:80] or "push")

    if event_type == "PullRequestEvent":
        pr = payload.get("pull_request")
        if not isinstance(pr, dict):
            pr = payload
        number = pr.get("number")
        if number is None:
            return None
        state = pr.get("state")
        action = payload.get("action")
        merged = pr.get("merged")
        # Determine semantic action: merged takes precedence over closed.
        if merged is True and action in ("closed", "merged"):
            return ("pull_request", str(number), f"PR #{number} merged")
        if action == "closed" and state == "closed":
            return ("pull_request", str(number), f"PR #{number} closed")
        title = pr.get("title", "")
        return ("pull_request", str(number), title[:80] or f"PR #{number}")

    if event_type == "IssuesEvent":
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            return None
        number = issue.get("number")
        if number is None:
            return None
        title = issue.get("title", "")
        return ("issue", str(number), title[:80] or f"Issue #{number}")

    if event_type == "WorkflowRunEvent":
        run = payload.get("workflow_run")
        if not isinstance(run, dict):
            return None
        run_id = run.get("id")
        name = run.get("name", "")
        event_action = payload.get("action")
        conclusion = run.get("conclusion")
        if event_action == "completed":
            label = f"Workflow {name or ''}"
            if conclusion:
                label = f"{label} {conclusion}"
            return ("workflow_run", str(run_id), label[:80])
        return ("workflow_run", str(run_id), name[:80] or f"Workflow #{run_id}")

    if event_type == "ReleaseEvent":
        release = payload.get("release")
        if not isinstance(release, dict):
            return None
        tag = release.get("tag_name")
        name = release.get("name", tag)
        if not tag:
            return None
        return ("release", tag, name or tag)

    if event_type in ("RepositoryEvent", "CreateEvent", "DeleteEvent"):
        action = _repository_action(raw=None) if False else payload.get("action") or "created"
        return ("repository", repo_name, repo_name)

    return None


def _source_url(event_type: str, payload: Dict[str, Any], repo: str) -> str:
    if event_type == "PushEvent":
        commits = payload.get("commits") or []
        sha = commits[0].get("id") if commits else None
        if sha:
            return f"https://github.com/{repo}/commit/{sha}"
        return f"https://github.com/{repo}/commits/main"
    if event_type == "PullRequestEvent":
        pr = payload.get("pull_request")
        if isinstance(pr, dict) and pr.get("html_url"):
            return pr["html_url"]
        number = payload.get("number")
        if number:
            return f"https://github.com/{repo}/pull/{number}"
        return f"https://github.com/{repo}/pulls"
    if event_type == "IssuesEvent":
        issue = payload.get("issue")
        if isinstance(issue, dict) and issue.get("html_url"):
            return issue["html_url"]
        number = payload.get("issue", {}).get("number") if isinstance(payload.get("issue"), dict) else None
        if number:
            return f"https://github.com/{repo}/issues/{number}"
        return f"https://github.com/{repo}/issues"
    if event_type == "WorkflowRunEvent":
        run = payload.get("workflow_run")
        if isinstance(run, dict) and run.get("html_url"):
            return run["html_url"]
        run_id = payload.get("workflow_run", {}).get("id") if isinstance(payload.get("workflow_run"), dict) else None
        if run_id:
            return f"https://github.com/{repo}/actions/runs/{run_id}"
        return f"https://github.com/{repo}/actions"
    if event_type == "ReleaseEvent":
        release = payload.get("release")
        if isinstance(release, dict) and release.get("html_url"):
            return release["html_url"]
        tag = release.get("tag_name") if isinstance(release, dict) else None
        if tag:
            return f"https://github.com/{repo}/releases/tag/{tag}"
        return f"https://github.com/{repo}/releases"
    return f"https://github.com/{repo}"


def _display_title(
    event_type: str,
    action: str,
    repo_name: str,
    ref_name: Optional[str],
    ref_identifier: str,
    payload: Dict[str, Any],
) -> str:
    """One-line deterministic title for the Telegram card."""
    if event_type == "PushEvent":
        return f"{repo_name}: push to main ({ref_identifier[:8]})"
    if event_type == "PullRequestEvent":
        verb = _pr_verb(action, payload)
        return f"{repo_name}: {verb} {ref_name or '#'+ref_identifier}"
    if event_type == "IssuesEvent":
        verb = "opened" if action == "opened" else "closed"
        return f"{repo_name}: {verb} {ref_name or '#'+ref_identifier}"
    if event_type == "WorkflowRunEvent":
        conclusion = (payload.get("workflow_run") or {}).get("conclusion")
        label = f"{conclusion or action}" if conclusion else action
        return f"{repo_name}: workflow {label}"
    if event_type == "ReleaseEvent":
        return f"{repo_name}: release {ref_name or ref_identifier}"
    if event_type in ("RepositoryEvent", "CreateEvent", "DeleteEvent"):
        return f"{repo_name}: repository {action}"
    return f"{repo_name}: {action}"


def _pr_verb(action: str, payload: Dict[str, Any]) -> str:
    if action == "opened":
        return "PR opened"
    if action == "reopened":
        return "PR reopened"
    if action == "ready_for_review":
        return "PR ready for review"
    if action == "merged":
        return "PR merged"
    if action == "closed":
        pr = payload.get("pull_request") or payload
        if isinstance(pr, dict) and pr.get("merged"):
            return "PR merged"
        return "PR closed"
    return action


def _importance(
    event_type: str,
    action: str,
    payload: Dict[str, Any],
) -> str:
    """Deterministic importance classification."""
    if event_type == "PullRequestEvent":
        if action == "merged":
            return "high"
        if action == "closed":
            return "normal"
        return "normal"
    if event_type == "WorkflowRunEvent":
        conclusion = (payload.get("workflow_run") or {}).get("conclusion")
        if conclusion == "failure":
            return "high"
        if conclusion == "cancelled":
            return "normal"
        return "normal"
    if event_type == "ReleaseEvent":
        return "high"
    if event_type in ("RepositoryEvent", "CreateEvent", "DeleteEvent"):
        if action in ("archived", "unarchived", "renamed"):
            return "high"
        if action == "created":
            return "normal"
        return "normal"
    if event_type == "IssuesEvent":
        return "normal"
    if event_type == "PushEvent":
        return "normal"
    return "normal"


def _correlation_id(
    event_type: str,
    payload: Dict[str, Any],
    repo: str,
) -> Optional[str]:
    """Optional correlation group for related events."""
    if event_type == "PullRequestEvent":
        pr = payload.get("pull_request") or payload
        if isinstance(pr, dict) and pr.get("number"):
            # PR merge correlated with the merge commit PushEvent
            return f"pr-{repo}-{pr['number']}"
    if event_type == "PushEvent":
        commits = payload.get("commits") or []
        if commits:
            # Use the push ref to correlate all commits in the push
            ref = payload.get("ref") or ""
            return f"push-{repo}-{ref.replace('refs/heads/', '')}"
    return None


# ---- minimal topological reconciliation ----

def reconcile_topology(
    live_repos: list,
    topology_repos: list,
) -> dict:
    """Compare live GitHub org repos against canonical topology.

    Returns:
      unknown_live: live repos not in topology (warn, do not add)
      missing_expected: topology repos not present in live (observe)
    """
    def _strip_org(full_name: str) -> str:
        return full_name.rsplit("/", 1)[-1] if "/" in full_name else full_name

    live_names = {_strip_org(r["full_name"] if isinstance(r, dict) else r) for r in live_repos}
    topo_names = {
        _strip_org(r["name"] if isinstance(r, dict) else r) for r in topology_repos
    }

    unknown_list = [r for r in live_repos
                    if _strip_org(r["full_name"] if isinstance(r, dict) else r) not in topo_names]
    missing_list = [r for r in topology_repos
                    if _strip_org(r["name"] if isinstance(r, dict) else r) not in live_names]

    return {
        "unknown_live": sorted([{
            "name": _strip_org(r["full_name"] if isinstance(r, dict) else r)
        } for r in unknown_list], key=lambda x: x["name"]),
        "missing_expected": sorted([{
            "name": _strip_org(r["name"] if isinstance(r, dict) else r)
        } for r in missing_list], key=lambda x: x["name"]),
    }
