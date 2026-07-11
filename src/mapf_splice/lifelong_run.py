from __future__ import annotations

import argparse
from pathlib import Path

from mapf_splice.lifelong import (
    LifelongRunConfig,
    run_lifelong_validation,
    write_lifelong_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run bounded deterministic lifelong validation."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run_lifelong_validation(LifelongRunConfig.from_json(args.config))
    write_lifelong_artifacts(result, args.output)
    print(f"termination: {result.summary['termination_reason']}")
    print(f"final tick: {result.summary['final_tick']}")
    print(
        f"tasks: {result.summary['tasks_completed']}/{result.summary['tasks_released']}"
    )
    print(
        "recoveries: "
        f"{result.summary['recoveries_completed']}/"
        f"{result.summary['recoveries_installed']}"
    )
    print(f"output: {args.output}")


if __name__ == "__main__":
    main()
