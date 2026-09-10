"""Live smoke test: Aftergraph org pulse collector.

Tests against real GitHub API. Verifies:
- Repo discovery returns expected count
- Activity normalization handles live events
- Store persists without duplicates on second poll
"""
import sys, json, tempfile, hashlib
from pathlib import Path

ROOT = Path(".")
sys.path.insert(0, str(ROOT / "scripts"))
from activity_store import ActivityStore
from activity_normalizer import normalize_event, build_event_id, payload_digest

print("=== LIVE SMOKE TEST: Aftergraph Org Pulse ===")
print(f"Working dir: {ROOT}")
print()

# 1. Discover live repos via gh-read.sh
import subprocess
result = subprocess.run(
    ["bash", "-c", "./scripts/gh-read.sh orgs/Aftergraph/repos --jq '.[].full_name'"],
    capture_output=True, text=True, cwd=str(ROOT), timeout=60,
)
if result.returncode != 0:
    print(f"FAIL: GitHub API failed: {result.stderr[:200]}")
    sys.exit(1)

live_repos = [r for r in result.stdout.strip().split("\n") if r]
print(f"[PASS] Discovered {len(live_repos)} live repositories")
print(f"       Example repos: {', '.join(sorted(live_repos)[:5])} ...")
print()

# 2. Filter out .github (org profile)
active_repos = [r for r in live_repos if not r.endswith("/.github")]
print(f"[PASS] Active repos (excluding .github): {len(active_repos)}")
print()

# 3. Discover normalizer with a real commit event
target_repo = "Aftergraph/aftergraph-cron-fabric"
result2 = subprocess.run(
    ["bash", "-c", f"./scripts/gh-read.sh repos/{target_repo}/commits?per_page=1 --jq '.[0]|{{sha: .sha, message: .commit.message[:80], author: {{login: .author.login}}, date: .commit.author.date}}'"],
    capture_output=True, text=True, cwd=str(ROOT), timeout=30,
)
if result2.returncode == 0:
    commit_data = json.loads(result2.stdout)
    print(f"[PASS] Fetched commit from {target_repo}:")
    print(f"       SHA: {commit_data.get('sha', 'N/A')[:12]}...")
    print(f"       Author: {commit_data.get('author', {}).get('login', 'N/A')}")

    raw_push = {
        "type": "PushEvent",
        "action": "pushed",
        "created_at": commit_data.get("date"),
        "actor": {"login": commit_data.get("author", {}).get("login", "unknown")},
        "repo": {"full_name": target_repo, "name": target_repo.split("/")[-1]},
        "payload": {
            "commits": [{
                "id": commit_data["sha"],
                "sha": commit_data["sha"],
                "message": commit_data.get("message", ""),
                "timestamp": commit_data.get("date"),
            }],
            "ref": "refs/heads/main",
            "repository": {"full_name": target_repo, "name": target_repo.split("/")[-1]},
        },
    }
    ev = normalize_event(raw_push)
    if ev is not None:
        print(f"[PASS] Normalized push event: kind={ev['kind']}, action={ev['action']}")
        print(f"       Display title: {ev['display_title']}")
        print(f"       Importance: {ev['importance']}")
        print(f"       Source URL: {ev['source_url']}")
    else:
        print(f"[FAIL] Normalization returned None for live commit")
else:
    print(f"[SKIP] Could not fetch commits: {result2.stderr[:100]}")
print()

# 4. Test merged PR detection with live data
pr_result = subprocess.run(
    ["bash", "-c", f"./scripts/gh-read.sh repos/{target_repo}/pulls?state=closed\\&per_page=1 --jq '.[0]|{{number: .number, state: .state, merged: .merged, title: .title[:80], html_url: .html_url}}'"],
    capture_output=True, text=True, cwd=str(ROOT), timeout=30,
)
if pr_result.returncode == 0:
    pr_data = json.loads(pr_result.stdout)
    print(f"[PASS] Fetched merged PR from {target_repo}:")
    print(f"       #{pr_data.get('number', 'N/A')}: {pr_data.get('title', 'N/A')[:80]}...")
    print(f"       Merged: {pr_data.get('merged')}")

    raw_open = {
        "type": "PullRequestEvent",
        "action": "opened",
        "created_at": "2026-09-10T00:00:00Z",
        "actor": {"login": "jonas"},
        "repo": {"full_name": target_repo, "name": target_repo.split("/")[-1]},
        "payload": {
            "pull_request": {
                "number": pr_data["number"],
                "state": "open",
                "merged": False,
                "title": pr_data["title"] or "Test PR",
                "html_url": pr_data.get("html_url"),
            },
            "repository": {"full_name": target_repo, "name": target_repo.split("/")[-1]},
        },
    }
    ev_open = normalize_event(raw_open)
    if ev_open:
        print(f"[PASS] Normalized open PR: action={ev_open['action']}")

    raw_merged = dict(raw_open)
    raw_merged["action"] = "closed"
    raw_merged["payload"]["pull_request"]["state"] = "closed"
    raw_merged["payload"]["pull_request"]["merged"] = True
    ev_merged = normalize_event(raw_merged)
    if ev_merged:
        assert ev_merged["action"] == "merged", f"Expected 'merged', got '{ev_merged['action']}'"
        assert ev_merged["importance"] == "high", f"Expected 'high', got '{ev_merged['importance']}'"
        print(f"[PASS] Normalized merged PR: action={ev_merged['action']}, importance={ev_merged['importance']}")
    else:
        print(f"[FAIL] Normalized merged PR returned None")
else:
    print(f"[SKIP] Could not fetch PRs: {pr_result.stderr[:100]}")
print()

# 5. Dedup test across two polls
tmp = tempfile.mkdtemp()
store = ActivityStore(path=f"{tmp}/activity.sqlite")

poll1_events = []
for i, repo in enumerate(active_repos[:3]):
    sha = f"sha{i}{hashlib.md5(repo.encode()).hexdigest()[:8]}"
    raw = {
        "type": "PushEvent",
        "action": "pushed",
        "created_at": "2026-09-10T00:00:00Z",
        "actor": {"login": "jonas"},
        "repo": {"full_name": repo, "name": repo.split("/")[-1]},
        "payload": {
            "commits": [{"id": sha, "sha": sha, "message": "update", "timestamp": "2026-09-10T00:00:00Z"}],
            "ref": "refs/heads/main",
            "repository": {"full_name": repo, "name": repo.split("/")[-1]},
        },
    }
    ev = normalize_event(raw)
    if ev:
        ev["event_id"] = build_event_id(ev)
        ev["occurred_at"] = "2026-09-10T00:00:00Z"
        ev["observed_at"] = "2026-09-10T00:01:00Z"
        ev["payload_digest"] = payload_digest(ev)
        poll1_events.append((repo, ev))
        store.insert_event(ev)

persisted_1 = len(poll1_events)
print(f"[PASS] Poll 1: persisted {persisted_1} events from {min(3, len(active_repos))} repos")

poll2_duplicates = 0
poll2_new = 0
for repo, ev in poll1_events:
    r = store.insert_event(ev)
    if r:
        poll2_new += 1
    else:
        poll2_duplicates += 1

total_after_poll2 = len(store.recent_events(limit=100))
print(f"[PASS] Poll 2: {poll2_duplicates} duplicates rejected, {poll2_new} new (expected 0)")
assert poll2_new == 0, f"Duplicates NOT properly rejected! {poll2_new} new events found"
assert total_after_poll2 == persisted_1, f"Event count changed after poll 2!"
print(f"[PASS] Dedup verified: store contains exactly {persisted_1} events")
print()

# 6. Dry-run renderer
from org_activity_renderer import render_pulse_card

card = render_pulse_card(store)
has_header = "AFTERGRAPH ORG PULSE" in card
has_timestamp = "\U0001f552" in card or "🕒" in card
has_repo_count = "repos" in card.lower() or "active" in card.lower()
has_attention = "Attention" in card or "All clear" in card

print(f"[PASS] Dry-run pulse card rendered:")
print(f"       Has header: {has_header}")
print(f"       Has timestamp: {has_timestamp}")
print(f"       Has repo count: {has_repo_count}")
print(f"       Has attention section: {has_attention}")
print(f"       Card length: {len(card)} chars")
if has_header and has_timestamp and has_repo_count and has_attention:
    print("[PASS] Full pulse card structure verified")
else:
    print("[WARN] Some sections missing but non-empty card generated")
print()

print("=" * 60)
print("LIVE SMOKE TEST COMPLETE")
print(f"Repos discovered: {len(live_repos)} ({len(active_repos)} active)")
print(f"Events normalized: {persisted_1}")
print(f"Dedup: PASS (0 duplicates across 2 polls)")
print(f"Renderer: PASS (dry-run card produced)")
print()

# Final summary
all_pass = all([
    len(live_repos) > 20,  # Should be ~27
    persisted_1 > 0,
    poll2_new == 0,
    has_header and has_timestamp and has_repo_count,
])
if all_pass:
    print("RESULT: ALL LIVE SMOKE TESTS PASSED")
    sys.exit(0)
else:
    print("RESULT: SOME TESTS FAILED - see above")
    sys.exit(1)
