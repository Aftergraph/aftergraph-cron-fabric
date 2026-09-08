"""Cheap deterministic pre-check for ag-wi-contract.

Tracks both the canonical wi-backend contract/API and the concrete wi-frontend
consumer surface. Hermes monitor mode suppresses the agent when this stable
fingerprint is unchanged. A changed fingerprint wakes semantic comparison; it
is not itself a mismatch finding.
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
    raise SystemExit("WI-SENSOR-FAIL: cannot locate fabric root")


ROOT = repo_root()
GH = ROOT / "scripts" / "gh-read.sh"
ARTIFACTS = [
    ("Aftergraph/wi-backend", "contracts/work-intelligence-boundary/1.0.json"),
    ("Aftergraph/wi-backend", "openapi.json"),
    ("Aftergraph/wi-frontend", "src/api/contracts.ts"),
    ("Aftergraph/wi-frontend", "src/api/client.ts"),
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
        raise SystemExit(f"WI-SENSOR-FAIL: invalid blob sha for {repo}/{path}")
    return sha


payload = {
    "sensor": "wi-contract-artifacts/v1",
    "artifacts": [
        {"repo": repo, "path": path, "sha": blob_sha(repo, path)}
        for repo, path in ARTIFACTS
    ],
}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
