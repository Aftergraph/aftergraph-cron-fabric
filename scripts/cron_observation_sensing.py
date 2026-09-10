"""Cron observation sensing (proactivity/0.1, cron path).

Scheduled read-only organization sensing projected to finding
candidates. The Cron Fabric retains zero execution authority: sensing
reports findings, never executes work and never admits candidates
(seam PROACTIVITY-ORG-V1.md).

Pure data mapping: no I/O, no subprocess, no network, no EventStore
writes. A scheduled tick yields a finding candidate or silence (None),
never an execution directive.
"""
import re
from datetime import datetime, timezone

SCHEMA = "proactivity/0.1"
PATH = "cron"
CANDIDATE_KIND = "finding"
NATIVE_REF_PREFIX = "cron:"
MAX_NATIVE_REF_LEN = 512
MAX_ASSERTED_AT_LEN = 64
# A reading asserted longer ago than this is a stale-schedule replay,
# not a fresh finding.
MAX_SCHEDULE_AGE_SECONDS = 24 * 3600

SENSING_ID_RE = re.compile(r"^sen_[a-f0-9]{32}$")
TENANT_ID_RE = re.compile(r"^ten_[a-f0-9]{32}$")
VALID_PATHS = {"wie", "runtime", "cron"}


class CronAuthorityError(ValueError):
    """A candidate violates Cron zero-execution-authority invariants."""


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise CronAuthorityError("unparseable asserted_at")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _check_scope(native_ref, sensing_id, tenant_id, correlated_paths):
    if not isinstance(native_ref, str) or not native_ref \
            or len(native_ref) > MAX_NATIVE_REF_LEN:
        raise CronAuthorityError("native_ref must be 1..512 chars")
    if not native_ref.startswith(NATIVE_REF_PREFIX):
        raise CronAuthorityError("cron sensing requires a cron: native_ref")
    if not isinstance(sensing_id, str) \
            or not SENSING_ID_RE.fullmatch(sensing_id):
        raise CronAuthorityError("bad sensing_id")
    if not isinstance(tenant_id, str) \
            or not TENANT_ID_RE.fullmatch(tenant_id):
        raise CronAuthorityError("findings must be tenant-bound")
    if not isinstance(correlated_paths, list) \
            or not 1 <= len(correlated_paths) <= 3 \
            or any(p not in VALID_PATHS for p in correlated_paths):
        raise CronAuthorityError("bad correlated_paths")
    if PATH not in correlated_paths:
        raise CronAuthorityError("cron sensing must correlate the cron path")


def project_finding_candidate(*, native_ref, sensing_id, tenant_id,
                              asserted_at=None, correlated_paths=("cron",)):
    """Project one scheduled read-only reading to a finding candidate.

    The projection claims no execution and no admission. Scope is
    validated fail-closed; freshness is enforced by validate_candidate.
    """
    if asserted_at is None:
        asserted_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    if not isinstance(asserted_at, str) or not asserted_at \
            or len(asserted_at) > MAX_ASSERTED_AT_LEN:
        raise CronAuthorityError("asserted_at must be 1..64 chars")
    _parse_time(asserted_at)
    paths = list(correlated_paths)
    _check_scope(native_ref, sensing_id, tenant_id, paths)
    return {
        "schema": SCHEMA,
        "sensing_id": sensing_id,
        "path": PATH,
        "native_ref": native_ref,
        "candidate_kind": CANDIDATE_KIND,
        "claims_execution": False,
        "claims_admission": False,
        "admitted_by_tg": False,
        "correlated_paths": paths,
        "asserted_at": asserted_at,
        "tenant_id": tenant_id,
    }


def validate_candidate(candidate, *, now=None,
                       max_age_seconds=MAX_SCHEDULE_AGE_SECONDS):
    """Accept (True) or reject (raise) a cron finding candidate.

    Rejects: execution claims (PRO-002, zero Cron execution
    authority), admission claims, scope-unbound findings, non-cron
    paths, malformed contract shape, and stale-schedule replays.
    """
    if not isinstance(candidate, dict):
        raise CronAuthorityError("candidate must be a dict")
    if candidate.get("schema") != SCHEMA:
        raise CronAuthorityError("bad schema")
    if candidate.get("path") != PATH:
        raise CronAuthorityError("cron sensing owns only the cron path")
    if candidate.get("candidate_kind") != CANDIDATE_KIND:
        raise CronAuthorityError("cron sensing projects finding candidates")
    if candidate.get("claims_execution") is not False:
        raise CronAuthorityError(
            "cron retains zero execution authority (PRO-002)")
    if candidate.get("claims_admission") is not False:
        raise CronAuthorityError("candidates never self-admit")
    if candidate.get("admitted_by_tg") is not False:
        raise CronAuthorityError("cron findings are never pre-admitted")
    _check_scope(candidate.get("native_ref"), candidate.get("sensing_id"),
                 candidate.get("tenant_id"),
                 candidate.get("correlated_paths"))
    asserted_at = candidate.get("asserted_at")
    if not isinstance(asserted_at, str) or not asserted_at \
            or len(asserted_at) > MAX_ASSERTED_AT_LEN:
        raise CronAuthorityError("bad asserted_at")
    age = (now if now is not None
           else datetime.now(timezone.utc)) - _parse_time(asserted_at)
    if age.total_seconds() < 0 or age.total_seconds() > max_age_seconds:
        raise CronAuthorityError("stale-schedule replay")
    return True


def sense_scheduled_reading(reading, **kwargs):
    """Handle one scheduled tick: finding candidate or silence (None).

    Never an execution directive. Readings without a finding stay
    silent, mirroring the fabric's empty-stdout-means-silence rule.
    """
    if not isinstance(reading, dict) or not reading.get("has_finding"):
        return None
    candidate = project_finding_candidate(
        native_ref=reading["native_ref"],
        sensing_id=reading["sensing_id"],
        tenant_id=reading["tenant_id"],
        asserted_at=reading.get("asserted_at"),
        correlated_paths=reading.get("correlated_paths", ("cron",)))
    validate_candidate(candidate, **kwargs)
    return candidate
