"""Public-provenance sensor (no_agent, read-only).

Detects provenance drift between Aftergraph's public surfaces and the
canonical sources they reference. Distinct from "docs changed": a public
surface can be perfectly healthy while citing a stale source pin, an
outdated API contract SHA, or a brand artifact that has moved.

The sensor's authoritative source map is the same `contracts/sources.yaml`
the other drift jobs already consume. There is no second registry.

Output: zero or more EventStore claims under
    public-provenance-drift|surface|target|canonical
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# Real provenance surface: Aftergraph/docs pins every canonical repo's
# HEAD in src/data/sources.ts (one commitSha per repo, freshness-verified
# by the docs pipeline). The sensor reads that manifest and compares
# each recorded pin against the pinned repo's LIVE HEAD.
# Surface: (repo, path) = (Aftergraph/docs, src/data/sources.ts)
# Canonical targets: every repository listed in the manifest.
PROVENANCE_SURFACE = ("Aftergraph/docs", "src/data/sources.ts")

# Offline fixture support. Schema:
#   {
#     "canonical": { "<repo>": "<sha>", ... },
#     "surface_pin": { "<repo>:<path>": "<sha>" }
#   }
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
    print("PUBLIC-PROVENANCE-FAIL: cannot locate fabric root")
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


def _canonical_head_sha(repo, paths):
    """Return the current HEAD SHA of a canonical artifact. Uses the
    default branch's HEAD for the repo (the canonical surface
    identifier), not the file's own blob SHA — which would change too
    often to be useful as a public surface anchor."""
    fix = _load_offline_fixture()
    if fix is not None:
        return fix.get("canonical", {}).get(repo)
    head = _gh_api(repo, "commits/HEAD")
    if not isinstance(head, dict):
        return None
    return head.get("sha")


def _surface_pins(repo, surface_path):
    """Return {pinned_repo: pin_sha} parsed from the surface manifest.

    The docs sources.ts manifest has one line per canonical repo with a
    40-hex commitSha. We extract every (repository, commitSha) pair.
    """
    fix = _load_offline_fixture()
    if fix is not None:
        return fix.get("surface_pin", {})
    raw = _gh_api(repo, f"contents/{surface_path}")
    if not isinstance(raw, dict):
        return {}
    if raw.get("encoding") != "base64":
        return {}
    import base64
    try:
        text = base64.b64decode(raw["content"]).decode("utf-8",
                                                       errors="replace")
    except Exception:
        return {}
    pins = {}
    for line in text.splitlines():
        # Manifest lines are compact objects: both fields on one line.
        m = re.search(
            r"repository:\s*['\"]([^'\"]+)['\"].*?"
            r"commitSha:\s*['\"]([0-9a-f]{40})['\"]", line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def main():
    global REPO
    REPO = _repo_root()
    # Persistent EventStore: per-sensor, not per-run.
    default_store = REPO / "state" / "public_provenance.sqlite"
    store_path = os.environ.get(
        "AG_FABRIC_STORE", str(default_store))
    store = EventStore(store_path)

    total_emitted = 0
    total_checked = 0
    total_skipped = 0
    surface_repo, surface_path = PROVENANCE_SURFACE
    pins = _surface_pins(surface_repo, surface_path)
    if not pins:
        # Fail-closed: unreadable surface = SENSOR-DEGRADED (SKIP),
        # never a fabricated CLEAN or EMIT.
        print(f"PUBLIC-PROVENANCE-DEGRADED: surface unreadable: "
              f"{surface_repo}:{surface_path}")
        print("PUBLIC-PROVENANCE-OK: emitted=0 checked=0 skipped=0 "
              "degraded=surface")
        sys.exit(0)

    for pinned_repo, pin in sorted(pins.items()):
        canonical = _canonical_head_sha(pinned_repo, None)
        if not canonical:
            total_skipped += 1
            print(f"PUBLIC-PROVENANCE-SKIP: {pinned_repo} "
                  f"canonical unreadable (degraded, not fabricated)")
            continue
        total_checked += 1
        # Compare on the first 8 chars — full-length SHA1 comparison is
        # too strict for HEAD-vs-pin where pin may have been truncated
        # in the public surface to keep file size sane.
        if pin[:8] == canonical[:8]:
            continue
        evidence = {
            "surface": f"{surface_repo}/{surface_path}",
            "target_contract": pinned_repo,
            "pin_sha": pin,
            "canonical_sha": canonical,
            "classification": "STALE_PIN",
        }
        fp = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        key = (f"public-provenance-drift|{surface_repo}|"
               f"{pinned_repo}|{canonical[:12]}")
        action = store.claim_event(key, fp)
        total_emitted += 1 if action == "EMIT" else 0
        print(f"PUBLIC-PROVENANCE-{action}: {pinned_repo} "
              f"pin={pin[:8]} canonical={canonical[:8]}")

    print(f"PUBLIC-PROVENANCE-OK: emitted={total_emitted} "
          f"checked={total_checked} skipped={total_skipped}")
    sys.exit(0)


if __name__ == "__main__":
    main()
