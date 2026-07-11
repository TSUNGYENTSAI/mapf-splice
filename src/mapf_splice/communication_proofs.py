from __future__ import annotations

import argparse
import json
from pathlib import Path

from mapf_splice.communication import analyze_communication_proofs
from mapf_splice.lifelong import LifelongRunConfig, run_lifelong_validation
from mapf_splice.replay import load_replay


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find communication-proof windows in production replay."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--replay", type=Path)
    source.add_argument("--config", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.replay is not None:
        replay = load_replay(args.replay)
        default_case_id = args.replay.stem
    else:
        config = LifelongRunConfig.from_json(args.config)
        replay = run_lifelong_validation(config).replay
        default_case_id = args.config.stem
    report = analyze_communication_proofs(
        replay, case_id=args.case_id or default_case_id
    )
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
