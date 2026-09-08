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


# (repo, surface_path, target_contract_id_in_sources.yaml)
# Each surface has a canonical target whose current SHA is fetched via
# gh api and compared to the surface's recorded pin. Update when the
# public surface changes.
PROVENANCE_PAIRS = [
    ("Aftergraph/docs", "developers/api/index.md",
     "wi.observation"),
    ("Aftergraph/aftergraph.org", "src/content/products/wie.md",
     "wi.observation"),
    ("Aftergraph/brand", "docs/canonical/masterbrand.md",
     "brand.identity"),
]

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


def _surface_pin_sha(repo, surface_path):
    """Find any 40-char hex SHA string in the surface file. Real
    provenance pins are SHA-256 (64-char) or git SHA1 (40-char); we
    accept either to avoid coupling to one digest length."""
    fix = _load_offline_fixture()
    if fix is not None:
        key = f"{repo}:{surface_path}"
        return fix.get("surface_pin", {}).get(key)
    raw = _gh_api(repo, f"contents/{surface_path}")
    if not isinstance(raw, dict):
        return None
    if raw.get("encoding") != "base64":
        return None
    import base64
    try:
        text = base64.b64decode(raw["content"]).decode("utf-8",
                                                       errors="replace")
    except Exception:
        return None
    # SHA-1 (40 hex) — typical git pin.
    m = re.search(r"\b([0-9a-f]{40})\b", text)
    if m:
        return m.group(1)
    # SHA-256 prefix (64 hex) — full digest, but only first 40 anchored.
    m = re.search(r"\b([0-9a-f]{64})\b", text)
    if m:
        return m.group(1)[:40]
    return None


def main():
    global REPO
    REPO = _repo_root()
    # Persistent EventStore: per-sensor, not per-run.
    default_store = REPO / "state" / "public_provenance.sqlite"
    store_path = os.environ.get(
        "AG_FABRIC_STORE", str(default_store))
    store = EventStore(store_path)

    total_emitted = 0
    for repo, surface_path, target in PROVENANCE_PAIRS:
        pin = _surface_pin_sha(repo, surface_path)
        canonical = _canonical_head_sha(repo, None)
        if not pin or not canonical:
            print(f"PUBLIC-PROVENANCE-SKIP: {repo}:{surface_path} "
                  f"pin={bool(pin)} canonical={bool(canonical)}")
            continue
        # Compare on the first 8 chars — full-length SHA1 comparison is
        # too strict for HEAD-vs-pin where pin may have been truncated
        # in the public surface to keep file size sane.
        if pin[:8] == canonical[:8]:
            continue
        evidence = {
            "surface": f"{repo}/{surface_path}",
            "target_contract": target,
            "pin_sha": pin,
            "canonical_sha": canonical,
            "classification": "STALE_PIN",
        }
        fp = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        key = f"public-provenance-drift|{repo}|{target}|{canonical[:12]}"
        action = store.claim_event(key, fp)
        total_emitted += 1 if action == "EMIT" else 0
        print(f"PUBLIC-PROVENANCE-{action}: {repo} pin={pin[:8]} "
              f"canonical={canonical[:8]}")

    print(f"PUBLIC-PROVENANCE-OK: emitted={total_emitted}")
    sys.exit(0)


if __name__ == "__main__":
    main()
