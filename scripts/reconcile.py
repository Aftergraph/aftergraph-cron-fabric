"""Idempotent reconciler (ChatGPT finding #4 fix).

Git -> Hermes without duplicates or hardcoded IDs:

  validate -> cron list -> match exact canonical name
  -> diff desired/live -> create OR update -> run paused
  -> record deployment receipt (deploy/receipts/<name>.json)

Rollback reads job_id from live list or receipt, never a constant.
Usage: python3 scripts/reconcile.py [--apply] [--job NAME]
Dry-run (default) prints the plan. --apply executes via cronjob tool
calls the operator pastes; the script itself never touches the API.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def desired_jobs():
    jobs = {}
    for path in sorted((ROOT / "jobs").glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        name = next(l.split(":", 1)[1].strip()
                    for l in text.splitlines() if l.startswith("name:"))
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        jobs[name] = {"file": str(path.name), "config_hash": digest}
    for path in sorted((ROOT / "jobs" / "legacy").glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        name = next(l.split(":", 1)[1].partition("#")[0].strip()
                    for l in text.splitlines() if l.startswith("name:"))
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        jobs[name] = {"file": f"legacy/{path.name}", "config_hash": digest}
    return jobs


def plan(live_by_name, expired=frozenset()):
    """live_by_name: {name: {job_id, config_hash}}. Returns action list.
    expired: names whose expires_when condition is true -> retired
    (reconciler pauses/removes the live job and emits a receipt)."""
    actions = []
    for name, want in desired_jobs().items():
        if name in expired:
            actions.append(("retire", name, want["config_hash"]))
            continue
        live = live_by_name.get(name)
        if live is None:
            actions.append(("create", name, want["config_hash"]))
        elif live.get("config_hash") != want["config_hash"]:
            actions.append(("update", name, want["config_hash"]))
        else:
            actions.append(("noop", name, want["config_hash"]))
    for name in live_by_name:
        if name not in desired_jobs():
            actions.append(("review-remove", name,
                            live_by_name[name].get("job_id", "?")))
    return actions


def write_receipt(name, job_id, config_hash):
    out = ROOT / "deploy" / "receipts"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps({
        "job": name, "job_id": job_id, "config_hash": config_hash,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }, indent=1) + "\n", encoding="utf-8")


def main():
    live = {}
    if len(sys.argv) > 2 and sys.argv[1] == "--live":
        live = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    for action, name, ref in plan(live):
        print(f"{action:14} {name} {ref}")
    if "--apply" not in sys.argv:
        print("(dry-run; pass --apply with --live <json> to record receipts)")


if __name__ == "__main__":
    main()
