"""Validate jobs/*.yaml and the topology role coverage policy."""
import json
import re
import sys
from pathlib import Path

REQUIRED = {"name", "schedule", "deliver", "severity",
            "allowed_dispositions", "read_only", "rollback", "mode"}
NO_CONTINUITY = {"ag-claim-watch", "ag-vault-watch", "ag-sentinel-release",
                 "ag-legacy-noise-gate"}
SEVERITIES = {"info", "warning", "critical"}
DISPOSITIONS = {"store", "digest", "notify", "decision", "incident"}
MODES = {"agent", "no_agent"}
SECRETS = re.compile(r"BEGIN PRIVATE KEY|ghp_|gho_|github_pat_|xoxb-|xoxp-|xoxa-|sk-live|AKIA[0-9A-Z]{16}|api_key\s*[:=]\s*\S|token\s*[:=]\s*\S",
                     re.IGNORECASE)
SCHEDULES = [
    re.compile(r"^every\s+\d+[mh]$", re.IGNORECASE),
    re.compile(r"^every\s+day\s+at\s+\d{1,2}(?:am|pm)$", re.IGNORECASE),
    re.compile(r"^every\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+\d{1,2}(?:am|pm)$", re.IGNORECASE),
    re.compile(r"^every\s+month\s+on\s+the\s+\d{1,2}(?:st|nd|rd|th)\s+at\s+\d{1,2}(?:am|pm)$", re.IGNORECASE),
]


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


def valid_schedule(value):
    value = value.strip("| ")
    return any(pattern.fullmatch(value) for pattern in SCHEDULES)


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
    if mode not in MODES:
        errors.append(f"{job.name}: mode must be one of {sorted(MODES)}")
    if mode == "agent" and "prompt" not in d:
        errors.append(f"{job.name}: agent mode requires prompt")
    if mode == "no_agent" and "prompt" in d:
        errors.append(f"{job.name}: no_agent forbids prompt (dead config)")
    if mode == "no_agent" and "script" not in d:
        errors.append(f"{job.name}: no_agent requires script")

    if "script" in d:
        script = d["script"].strip("| ").splitlines()[0].strip()
        if script and not Path(script).is_file():
            errors.append(f"{job.name}: script not found: {script}")

    if "continuity" in d and job.stem in NO_CONTINUITY:
        errors.append(f"{job.stem}: continuity forbidden (watch job, spec #5 - deep audits only)")

    tools = d.get("enabled_toolsets", "")
    if ("terminal" in tools and d.get("job_type", "").strip("| ")
            != "constrained_terminal"):
        errors.append(f"{job.name}: bare [terminal] rejected without job_type: constrained_terminal")
    prompt = d.get("prompt", "")
    if "terminal" in tools and "gh-read.sh" not in prompt:
        errors.append(f"{job.name}: constrained_terminal must route through scripts/gh-read.sh")

    schedule = d.get("schedule", "")
    if not schedule.strip("| "):
        errors.append(f"{job.name}: empty schedule")
    elif not valid_schedule(schedule):
        errors.append(f"{job.name}: unsupported schedule grammar: {schedule.strip()}")

    text = Path(job).read_text(encoding="utf-8")
    if SECRETS.search(text):
        errors.append(f"{job.name}: secret-shaped value found")
    return errors


def coverage_errors(path=Path("contracts/coverage-policy.json")):
    """Validate role policy shape without duplicating Governance repo identity.

    Completeness against the live topology is intentionally a runtime
    ag-governance-drift responsibility. CI only proves the policy itself is
    unambiguous and points at the canonical topology owner.
    """
    errors = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"coverage policy unreadable: {exc}"], 0

    if data.get("schema_version") != "cron-coverage-policy/1.0":
        errors.append("coverage policy: bad schema_version")
    topology = data.get("topology_contract", {})
    if topology.get("repo") != "Aftergraph/after-graph-governance":
        errors.append("coverage policy: topology owner must be Aftergraph/after-graph-governance")
    if topology.get("path") != "docs/platform-topology/1.0.json":
        errors.append("coverage policy: unexpected topology path")

    concerns = data.get("concerns")
    if not isinstance(concerns, dict) or not concerns:
        errors.append("coverage policy: concerns missing")
        return errors, 0

    role_owner = {}
    for concern, cfg in concerns.items():
        roles = cfg.get("roles") if isinstance(cfg, dict) else None
        if not isinstance(roles, list) or not roles:
            errors.append(f"coverage policy: {concern} has no roles")
            continue
        if not cfg.get("disposition"):
            errors.append(f"coverage policy: {concern} missing disposition")
        for role in roles:
            if not isinstance(role, str) or not role:
                errors.append(f"coverage policy: invalid role in {concern}")
                continue
            if role in role_owner:
                errors.append(
                    f"coverage policy: role {role} mapped twice: {role_owner[role]}, {concern}")
            else:
                role_owner[role] = concern

    for special in data.get("special_rules", {}):
        if special not in role_owner:
            errors.append(f"coverage policy: special role {special} is not mapped to a concern")
    return errors, len(role_owner)


def main():
    jobs = sorted(Path("jobs").glob("*.yaml")) + sorted(
        Path("jobs/legacy").glob("*.yaml"))
    assert jobs, "no jobs/*.yaml found"
    errors = []
    seen = {}
    for job in jobs:
        data = parse_simple_yaml(job)
        errors += job_errors(job, data)
        name = data.get("name", "").strip("| ")
        if name:
            if name in seen:
                errors.append(f"{job.name}: duplicate canonical job name also in {seen[name]}")
            else:
                seen[name] = str(job)

    cov_errors, role_count = coverage_errors()
    errors += cov_errors
    if errors:
        print("\n".join(errors))
        return 1
    print(f"VALIDATE-OK: {len(jobs)} jobs; COVERAGE-POLICY-OK: {role_count} roles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
