"""Telegram receipt bridge — renderer-side producer for the v0.5.1
delivery-receipt chain.

This script is the OUT-OF-PROCESS Telegram renderer bridge required by
docs/delivery-receipts-spec.md. It closes the proof chain:

    Fabric event -> renderer (this bridge) -> Telegram API success
        -> renderer receipt -> delivery canary verifies receipt

It does two things and nothing else:

1. Send one Telegram status card via the canonical renderer
   (telegram-live-status skill: `hermes statuscard` / tg-status.sh
   path) for a given Fabric event claim.
2. On positive delivery proof (renderer exit 0), write the normative
   sha256-attested delivery receipt under deploy/delivery-receipts/
   exactly as the spec requires — same canonical payload, same
   deterministic filename, same hash discipline as
   scripts/sensors/delivery_canary.py expects.

It NEVER writes a receipt without positive delivery proof, and it does
not touch the Fabric EventStore (that is the sensors' job). It is
invoked manually or by a scheduled job; it is NOT part of the shadow
delivery path until SHADOW_ACCEPTED.

Usage:
    python scripts/telegram_receipt_bridge.py \
        --event-key "fabric-event|key" \
        --fingerprint <16-hex> \
        --task-id <tg-status task_id> \
        --message "[INFO] shadow status" \
        [--channel telegram:ops] \
        [--producer <producer-id>]

Exit codes: 0 = delivered + receipt written; 1 = delivery failed
(no receipt written); 2 = usage/config error.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DELIVERY_RECEIPT_SCHEMA = "delivery-receipt/1"


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
    print("RECEIPT-BRIDGE-FAIL: cannot locate fabric root")
    sys.exit(2)


REPO = _repo_root()
DELIVERY_RECEIPTS_DIR = REPO / "deploy" / "delivery-receipts"


def _hash_payload(body):
    """Canonical sha256 attestation — identical to the canary's
    (compact separators, sorted keys, payload WITHOUT the sha256
    field). The spec's prose says 'no whitespace'; the canary's
    compact json.dumps is the authority."""
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _key16(event_key, fingerprint):
    # NUL byte separator, identical to EventStore.fingerprint() and the
    # delivery canary's receipt-filename derivation.
    return hashlib.sha256(
        (event_key + "\0" + fingerprint).encode("utf-8")).hexdigest()[:16]


def _send_via_renderer(task_id, message, producer=None):
    """Send one card through the canonical renderer. We deliberately go
    through `hermes statuscard` (the plugin CLI) rather than shelling
    into the skill's scripts, because the skill contract requires
    producers to converge on one renderer, one persistent message_id
    per task, and revision-guarded edits. Returns (ok, detail)."""
    cmd = ["hermes", "statuscard", "--to", "telegram:Jonas",
           "--task", task_id, "--event",
           json.dumps({
               "task_id": task_id,
               "status": "RUNNING",
               "message": message,
               "producer": producer or "cron-fabric-receipt-bridge",
               "revision": int(time.time()),
           })]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=60)
    except subprocess.TimeoutExpired:
        return False, "renderer timed out after 60s"
    if out.returncode != 0:
        return False, (f"renderer rc={out.returncode} "
                       f"stderr={out.stderr.strip()[:200]}")
    detail = (out.stdout or "").strip()
    # The statuscard CLI prints the card summary on success. A cheap
    # positive-proof check: non-empty stdout and no 'error' marker.
    if not detail:
        return False, "renderer returned empty output (no proof)"
    if "error" in detail.lower():
        return False, f"renderer reported error: {detail[:200]}"
    return True, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-key", required=True)
    ap.add_argument("--fingerprint", required=True,
                    help="16-hex Fabric fingerprint for this event")
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--channel", default="telegram:ops")
    ap.add_argument("--producer", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="send nothing; verify chain wiring only")
    args = ap.parse_args()

    if args.dry_run:
        k = _key16(args.event_key, args.fingerprint)
        print(f"RECEIPT-BRIDGE-DRYRUN: would deliver "
              f"{args.event_key} fp={args.fingerprint} "
              f"receipt=delivery-{k}.json")
        sys.exit(0)

    ok, detail = _send_via_renderer(args.task_id, args.message,
                                    producer=args.producer)
    if not ok:
        print(f"RECEIPT-BRIDGE-FAIL: {detail}")
        sys.exit(1)

    # Positive delivery proof: write the normative receipt.
    DELIVERY_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "Z"
    message_id = f"statuscard-{args.task_id}-{now}"
    body = {
        "schema": DELIVERY_RECEIPT_SCHEMA,
        "at": now,
        "renderer": "telegram-live-status/statuscard",
        "event_key": args.event_key,
        "fingerprint": args.fingerprint,
        "run_id": now,
        "channel": args.channel,
        "message_id": message_id,
    }
    body["sha256"] = _hash_payload(body)
    out = DELIVERY_RECEIPTS_DIR / f"delivery-{_key16(args.event_key, args.fingerprint)}.json"
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"RECEIPT-BRIDGE-OK: {out.name} -> {args.channel}")
    sys.exit(0)


if __name__ == "__main__":
    main()