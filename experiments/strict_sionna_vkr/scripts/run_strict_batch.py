#!/usr/bin/env python3
"""Batch runner for strict thesis scenarios."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from strict_common import ManagedSionnaServer, list_manifests_for_scenario, load_manifest, package_root, repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a strict scenario family over seeds")
    parser.add_argument("--scenario", required=True, help="Scenario manifest directory name")
    parser.add_argument("--out-root", default="analysis/strict_runs", help="Root output directory")
    parser.add_argument("--seeds", default="11,12,13,14,15,16,17,18,19,20", help="Comma-separated seed list")
    parser.add_argument("--mode", help="Optional single mode basename without .json")
    parser.add_argument("--sumo-gui", type=int, choices=[0, 1], help="Override manifest SUMO GUI setting")
    parser.add_argument("--skip-native-metrics", action="store_true", help="Skip PHY sidecar")
    parser.add_argument("--jobs", type=int, default=8, help="ns-3 build jobs")
    parser.add_argument(
        "--auto-sionna",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically start and stop the local Sionna server for each manifest",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifests = list_manifests_for_scenario(args.scenario)
    if args.mode:
        manifests = [path for path in manifests if path.stem == args.mode]
        if not manifests:
            raise SystemExit(f"No manifest found for mode '{args.mode}' in scenario '{args.scenario}'")

    seeds = [int(item.strip()) for item in args.seeds.split(",") if item.strip()]
    runner = package_root() / "scripts" / "run_strict_scenario.py"
    scenario_root = Path(args.out_root)
    if not scenario_root.is_absolute():
        scenario_root = repo_root() / scenario_root

    for manifest_path in manifests:
        manifest = load_manifest(manifest_path)
        sionna_enabled = bool(manifest["run"]["sionna_enabled"])
        manager = None
        if args.auto_sionna and sionna_enabled:
            log_path = scenario_root / manifest["scenario_id"] / manifest["mode"] / "sionna-server.log"
            manager = ManagedSionnaServer(manifest["sionna_scene"], log_path)
            manager.start()

        try:
            for seed in seeds:
                cmd = [
                    sys.executable,
                    str(runner),
                    "--manifest",
                    str(manifest_path),
                    "--out-root",
                    args.out_root,
                    "--seed",
                    str(seed),
                    "--jobs",
                    str(args.jobs),
                ]
                if args.skip_native_metrics:
                    cmd.append("--skip-native-metrics")
                if args.sumo_gui is not None:
                    cmd.extend(["--sumo-gui", str(args.sumo_gui)])
                subprocess.run(cmd, check=True)
        finally:
            if manager is not None:
                manager.stop()

    summarize_script = package_root() / "scripts" / "summarize_strict_runs.py"
    subprocess.run(
        [sys.executable, str(summarize_script), "--runs-root", str(scenario_root / args.scenario)],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
