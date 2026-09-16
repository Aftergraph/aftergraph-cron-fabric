"""CLI for Trigger Fabric v0.1 shadow evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapters"))

from hermes_goal_watchdog import normalize_goal_observation
from trigger_fabric import evaluate_continuity, write_shadow_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", default="state/trigger-fabric")
    parser.add_argument("--prior-state", default="ACTIVE")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    occurrence = normalize_goal_observation(data)
    decision = evaluate_continuity(occurrence, args.prior_state)
    receipt = write_shadow_receipt(Path(args.output_dir), occurrence, decision)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
