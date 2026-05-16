#!/usr/bin/env python3
"""Run the strict thesis calibration and batch campaign across all scenario families."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from strict_common import normalize_cli_list_value_args, package_root, repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run calibration + multi-seed strict thesis campaign")
    parser.add_argument("--out-root", default="analysis/thesis_campaign_runs", help="Batch output root")
    parser.add_argument("--calibration-root", default="analysis/thesis_campaign_calibration", help="Calibration output root")
    parser.add_argument("--seeds", default="11,12,13,14,15", help="Comma-separated seeds for batch runs")
    parser.add_argument("--tx-powers", default="11,17,23", help="Comma-separated tx powers for calibration")
    parser.add_argument("--rsrp-thresholds", default="-132,-126,-120", help="Comma-separated RSRP thresholds for calibration")
    parser.add_argument("--jobs", type=int, default=8, help="ns-3 build jobs")
    parser.add_argument("--skip-calibration", action="store_true", help="Skip radio calibration sweeps")
    parser.add_argument("--skip-batch", action="store_true", help="Skip multi-seed batch runs")
    return parser.parse_args(normalize_cli_list_value_args(sys.argv[1:], {"--rsrp-thresholds"}))


def main() -> int:
    args = parse_args()
    python = sys.executable
    batch_runner = package_root() / "scripts" / "run_strict_batch.py"
    calib_runner = package_root() / "scripts" / "run_radio_calibration.py"

    calibration_manifests = [
        package_root() / "manifests" / "strict_lane_obstacle" / "good_link.json",
        package_root() / "manifests" / "strict_intersection" / "radar_good.json",
        package_root() / "manifests" / "strict_onramp_merge" / "good_link.json",
    ]
    scenarios = [
        "strict_lane_obstacle",
        "strict_intersection",
        "strict_onramp_merge",
    ]

    if not args.skip_calibration:
        for manifest in calibration_manifests:
            subprocess.run(
                [
                    python,
                    str(calib_runner),
                    "--manifest",
                    str(manifest),
                    "--out-root",
                    args.calibration_root,
                    f"--tx-powers={args.tx_powers}",
                    f"--rsrp-thresholds={args.rsrp_thresholds}",
                    "--jobs",
                    str(args.jobs),
                ],
                check=True,
            )

    if not args.skip_batch:
        for scenario in scenarios:
            subprocess.run(
                [
                    python,
                    str(batch_runner),
                    "--scenario",
                    scenario,
                    "--out-root",
                    args.out_root,
                    "--seeds",
                    args.seeds,
                    "--jobs",
                    str(args.jobs),
                ],
                check=True,
            )

    print(Path(args.calibration_root).resolve())
    print(Path(args.out_root).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
