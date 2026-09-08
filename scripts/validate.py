"""Validate jobs/*.yaml: required keys, schedule grammar, severity contract."""
import re
import sys
from pathlib import Path

REQUIRED = {"name", "schedule", "deliver", "severity",
            "allowed_dispositions", "read_only", "rollback"}
# prompt is REQUIRED for mode=agent, FORBIDDEN for mode=no_agent (#8).
# mode + job_type declare the execution boundary (#1): read_only means
# NO external target-system mutation; own writes to state/,
# deploy/receipts/ and local audit output are explicitly allowed.
# constrained_terminal = terminal ONLY via scripts/gh-read.sh (GET) and
# scripts/event_store.py; validator rejects bare [terminal] otherwise.
# continuity = reasoning context for deep audits ONLY (#5). Watch/rotate
# jobs must not carry it: they re-sense on every tick instead of
# deduping against their own last report.
NO_CONTINUITY = {"ag-claim-watch", "ag-vault-watch", "ag-sentinel-release",
                 "ag-legacy-noise-gate"}
SEVERITIES = {"info", "warning", "critical"}
DISPOSITIONS = {"store", "digest", "notify", "decision", "incident"}
SECRETS = re.compile(r"BEGIN PRIVATE KEY|ghp_|gho_|github_pat_|xoxb-|xoxp-|xoxa-|sk-live|AKIA[0-9A-Z]{16}|api_key\s*[:=]\s*\S|token\s*[:=]\s*\S",
                     re.IGNORECASE)


def parse_simple_yaml(path):
    data, key, buf = {}, None, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if re.match(r"^[A-Za-z_]+:", line):
            if key:
                data[key] = "\n".join(buf).strip()
            key, _, val = line.partition(":")
            key, buf = key.strip(), [val.strip()]
        elif key is not None:
            buf.append(line)
    if key:
        data[key] = "\n".join(buf).strip()
    return data


def job_errors(job, d):
    errors = []
    missing = REQUIRED - set(d)
    if missing:
        errors.append(f"{job.name}: missing {sorted(missing)}")
    if d.get("severity", "").strip("| ") not in SEVERITIES:
        errors.append(f"{job.name}: bad severity")
    for disp in re.findall(r"[a-z]+", d.get("allowed_dispositions", "")):
        if disp not in DISPOSITIONS:
            errors.append(f"{job.name}: bad disposition {disp}")
    if d.get("read_only", "").strip("| ") != "true":
        errors.append(f"{job.name}: read_only MUST be true")
    mode = d.get("mode", "").strip("| ")
    if mode == "agent" and "prompt" not in d:
        errors.append(f"{job.name}: agent mode requires prompt")
    if mode == "no_agent" and "prompt" in d:
        errors.append(f"{job.name}: no_agent forbids prompt (dead config)")
    if mode == "no_agent" and "script" not in d:
        errors.append(f"{job.name}: no_agent requires script")
    if "continuity" in d and job.stem in NO_CONTINUITY:
        errors.append(f"{job.stem}: continuity forbidden (watch job, "
                      f"spec #5 - deep audits only)")
    tools = d.get("enabled_toolsets", "")
    if ("terminal" in tools and d.get("job_type", "").strip("| ")
            != "constrained_terminal"):
        errors.append(f"{job.name}: bare [terminal] rejected without "
                      f"job_type: constrained_terminal")
    prompt = d.get("prompt", "")
    if "terminal" in tools and "gh-read.sh" not in prompt:
        errors.append(f"{job.name}: constrained_terminal must route "
                      f"through scripts/gh-read.sh")
    if not d.get("schedule", "").strip("| "):
        errors.append(f"{job.name}: empty schedule")
    text = Path(job).read_text(encoding="utf-8")
    if SECRETS.search(text):
        errors.append(f"{job.name}: secret-shaped value found")
    return errors


def main():
    jobs = sorted(Path("jobs").glob("*.yaml")) + sorted(
        Path("jobs/legacy").glob("*.yaml"))
    assert jobs, "no jobs/*.yaml found"
    errors = []
    for job in jobs:
        errors += job_errors(job, parse_simple_yaml(job))
    if errors:
        print("\n".join(errors))
        return 1
    print(f"VALIDATE-OK: {len(jobs)} jobs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
