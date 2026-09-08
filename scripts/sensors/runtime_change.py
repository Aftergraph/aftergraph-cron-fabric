"""Cheap deterministic pre-check for ag-runtime-paritet.

Prints one stable JSON fingerprint of the registered runtime-related artifacts.
Hermes monitor mode compares stdout with the prior tick; identical output skips
the agent. A changed fingerprint wakes the semantic investigator but is NOT by
itself a parity finding.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def repo_root():
    env = os.environ.get("AG_FABRIC_ROOT")
    if env:
        return Path(env)
    cand = Path(__file__).resolve().parent.parent.parent
    if (cand / "scripts" / "gh-read.sh").is_file():
        return cand
    cwd = Path.cwd()
    if (cwd / "scripts" / "gh-read.sh").is_file():
        return cwd
    raise SystemExit("RUNTIME-SENSOR-FAIL: cannot locate fabric root")


ROOT = repo_root()
GH = ROOT / "scripts" / "gh-read.sh"
ARTIFACTS = [
    ("Aftergraph/runtime", "packages/invariants/src/registry.ts"),
    ("Aftergraph/works-execution", "contracts/schemas/kernel.budget.schema.json"),
    ("Aftergraph/trust-gateway", "src/gateway/delegation-chain.js"),
    ("Aftergraph/trust-gateway", "src/gateway/policy.js"),
]


def blob_sha(repo, path):
    endpoint = f"repos/{repo}/contents/{path}"
    proc = subprocess.run(
        ["bash", str(GH), endpoint, "--jq", ".sha"],
        cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        raise SystemExit(proc.returncode)
    sha = proc.stdout.strip()
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise SystemExit(f"RUNTIME-SENSOR-FAIL: invalid blob sha for {repo}/{path}")
    return sha


payload = {
    "sensor": "runtime-contract-artifacts/v1",
    "artifacts": [
        {"repo": repo, "path": path, "sha": blob_sha(repo, path)}
        for repo, path in ARTIFACTS
    ],
}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
