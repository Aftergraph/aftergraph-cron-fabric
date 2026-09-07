"""Idempotent reconciler (ChatGPT finding #4 fix).

Git -> Hermes without duplicates or hardcoded IDs:

  validate -> cron list -> match exact canonical name
  -> diff desired/live -> operator executes create/update paused
  -> operator records receipt: reconcile.py --record NAME JOB_ID
     (writes deploy/receipts/<name>.json with the live config hash)

Rollback reads job_id from live list or receipt, never a constant.
Usage: python3 scripts/reconcile.py [--live live.json]
         python3 scripts/reconcile.py --record NAME JOB_ID
Dry-run (default) prints the plan. The script never touches the API.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def desired_jobs():
    jobs = {}
    for base, label in ((ROOT / "jobs", ""),
                        (ROOT / "jobs" / "legacy", "legacy/")):
        for path in sorted(base.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            name = next(l.split(":", 1)[1].partition("#")[0].strip()
                        for l in text.splitlines() if l.startswith("name:"))
            digest = hashlib.sha256(text.encode()).hexdigest()[:12]
            jobs[name] = {"file": f"{label}{path.name}",
                          "config_hash": digest}
    return jobs


def plan(live_by_name, expired=frozenset()):
    """live_by_name: {name: {job_id, config_hash}}. Returns action list.
    expired: names whose expires_when condition is true -> retired
    (reconciler pauses/removes the live job and emits a receipt)."""
    want_all = desired_jobs()
    actions = []
    for name, want in want_all.items():
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
        if name not in want_all:
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
    args = sys.argv[1:]
    if args[:1] == ["--record"]:
        if len(args) != 3 or args[1] not in desired_jobs():
            print("usage: reconcile.py --record NAME JOB_ID")
            sys.exit(1)
        write_receipt(args[1], args[2],
                      desired_jobs()[args[1]]["config_hash"])
        print(f"receipt: deploy/receipts/{args[1]}.json {args[2]}")
        return
    live = {}
    if len(args) > 1 and args[0] == "--live":
        live = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    for action, name, ref in plan(live):
        print(f"{action:14} {name} {ref}")
    print("(dry-run; execute the plan via the cronjob tool, then "
          "--record each create/update)")


if __name__ == "__main__":
    main()
