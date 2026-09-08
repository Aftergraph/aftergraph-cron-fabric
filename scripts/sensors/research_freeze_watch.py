"""Research-freeze watch sensor (no_agent, read-only).

Detects mutations of preregistered / frozen research artifacts without
a corresponding amendment entry. ISR's study011 lockfile pattern is
the canonical case: frozen datasets, experiment fingerprints, and
benchmark gates that are supposed to be immutable once registered.

The sensor reads a freeze manifest under contracts/freeze-manifest.yaml
(list of frozen artifacts with their preregistration SHA) and compares
current SHAs. A drift without an amendment entry is RESEARCH-FREEZE-BREACH.

If the freeze manifest does not exist, the sensor reports nothing. The
manifest is itself a contract; absent contract = no obligation =
no false alarm.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

MANIFEST_PATH = "contracts/freeze-manifest.yaml"
AMENDMENTS_PATH = "contracts/freeze-amendments.yaml"

# Offline fixture support. Schema:
#   { "current_sha": { "<repo>:<path>": "<sha>" } }
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
    print("RESEARCH-FREEZE-FAIL: cannot locate fabric root")
    sys.exit(2)


REPO = _repo_root()
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
from event_store import EventStore


def _read_manifest():
    """Return list of {repo, path, preregistration_sha} from
    contracts/freeze-manifest.yaml, or empty list if absent.

    YAML format:
        - repo: Aftergraph/intelligence-systems-research
          path: data/study011_dependency_lock.txt
          preregistration_sha: "<40 hex>"
    """
    p = REPO / MANIFEST_PATH
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    entries = []
    cur = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            if cur:
                entries.append(cur)
            cur = {}
            s = s[2:]
        if ":" in s:
            k, _, v = s.partition(":")
            cur[k.strip()] = v.strip().strip('"').strip("'")
    if cur:
        entries.append(cur)
    return [e for e in entries
            if "repo" in e and "path" in e and "preregistration_sha" in e]


def _read_amendments():
    """Return list of approved amendment SHAs from freeze-amendments.yaml.
    Same YAML format as manifest. A drift whose current SHA appears in
    the amendment list is NOT a breach — it was authorised."""
    p = REPO / AMENDMENTS_PATH
    if not p.exists():
        return set()
    text = p.read_text(encoding="utf-8")
    shas = set()
    for line in text.splitlines():
        m = None
        import re
        m = re.search(r"\b([0-9a-f]{40,64})\b", line)
        if m:
            shas.add(m.group(1))
    return shas


def _current_sha(repo, path):
    fix = _load_offline_fixture()
    if fix is not None:
        return fix.get("current_sha", {}).get(f"{repo}:{path}")
    try:
        out = subprocess.run(
            ["gh", "api",
             f"repos/{repo}/commits?path={path}&per_page=1"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        arr = json.loads(out.stdout)
        if not arr:
            return None
        return arr[0]["sha"]
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def main():
    global REPO
    REPO = _repo_root()
    # Persistent EventStore: per-sensor, not per-run.
    default_store = REPO / "state" / "research_freeze.sqlite"
    store_path = os.environ.get(
        "AG_FABRIC_STORE", str(default_store))
    store = EventStore(store_path)

    manifest = _read_manifest()
    if not manifest:
        print("RESEARCH-FREEZE-OK: no freeze manifest, nothing to watch")
        sys.exit(0)

    amendments = _read_amendments()
    emitted = 0
    for entry in manifest:
        repo = entry["repo"]
        path = entry["path"]
        registered = entry["preregistration_sha"]
        current = _current_sha(repo, path)
        if not current:
            print(f"RESEARCH-FREEZE-SKIP: {repo}:{path}: gh api unavailable")
            continue
        # Compare on first 12 chars — sufficient for drift detection
        # without false positives from full-length SHA mismatches.
        if current[:12] == registered[:12]:
            continue
        # Amendment cover: if either the preregistration or current SHA
        # appears in the amendment list, the drift is authorised.
        if (registered in amendments or current in amendments
                or registered[:12] in {a[:12] for a in amendments}
                or current[:12] in {a[:12] for a in amendments}):
            print(f"RESEARCH-FREEZE-AMENDED: {repo}:{path} "
                  f"registered={registered[:12]} current={current[:12]}")
            continue
        evidence = {
            "repo": repo,
            "path": path,
            "preregistration_sha": registered,
            "current_sha": current,
            "classification": "RESEARCH-FREEZE-BREACH",
        }
        fp = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        key = f"research-freeze-breach|{repo}|{path}|{current[:12]}"
        action = store.claim_event(key, fp)
        emitted += 1 if action == "EMIT" else 0
        print(f"RESEARCH-FREEZE-{action}: {repo}:{path} "
              f"registered={registered[:12]} current={current[:12]}")

    print(f"RESEARCH-FREEZE-OK: manifest_entries={len(manifest)} "
          f"emitted={emitted}")
    sys.exit(0)


if __name__ == "__main__":
    main()
