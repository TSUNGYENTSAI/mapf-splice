from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_calibration_tool_reports_bounded_runtime_outcomes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "tools/calibrate_hero_scenario.py",
            "--scenario",
            "scenarios/compact-three-robot/scenario.json",
            "--t1",
            "5",
            "--t2",
            "0",
            "--t3",
            "12",
            "--horizons",
            "3,4,5",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "R1@2,R2@2,R3@2@tick-16" in result.stdout
    assert "R1@2,R2@2,R3@2@tick-15" in result.stdout
    assert "R1@2,R2@2,R3@2@tick-14" in result.stdout
    assert result.stdout.count("R1@2,R2@2,R3@2@tick-18") == 3
