#!/usr/bin/env python3
"""Run a single strict thesis scenario manifest."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from strict_common import (
    build_behavior_args,
    build_metrics_args,
    load_manifest,
    manifest_run_dir,
    package_root,
    repo_root,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one strict scenario manifest")
    parser.add_argument("--manifest", required=True, help="Manifest JSON path")
    parser.add_argument("--out-root", default="analysis/strict_runs", help="Root output directory")
    parser.add_argument("--seed", type=int, help="Override both RNG run and SUMO seed")
    parser.add_argument("--sumo-gui", type=int, choices=[0, 1], help="Override manifest SUMO GUI setting")
    parser.add_argument("--skip-native-metrics", action="store_true", help="Skip PHY sidecar run")
    parser.add_argument("--jobs", type=int, default=8, help="ns-3 build jobs for the sidecar")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    if args.seed is not None:
        manifest["run"]["rng_run"] = args.seed
        manifest["run"]["sumo_seed"] = args.seed
    if args.sumo_gui is not None:
        manifest["run"]["sumo_gui"] = args.sumo_gui

    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = repo_root() / out_root
    run_dir = manifest_run_dir(out_root, manifest, seed=args.seed)
    behavior_dir = run_dir / "behavior"
    native_dir = run_dir / "native_nr"
    behavior_dir.mkdir(parents=True, exist_ok=True)
    native_dir.mkdir(parents=True, exist_ok=True)

    manifest_copy = dict(manifest)
    manifest_copy["resolved"] = {
        "run_dir": str(run_dir),
        "behavior_dir": str(behavior_dir),
        "native_nr_dir": str(native_dir),
    }
    with (run_dir / "run_manifest.json").open("w") as handle:
        json.dump(manifest_copy, handle, indent=2, sort_keys=True)

    seed = int(manifest["run"]["rng_run"])
    behavior_args = shlex.join(build_behavior_args(manifest))
    behavior_env = os.environ.copy()
    behavior_env.update(
        {
            "OUT_DIR": str(behavior_dir),
            "RUN_ARGS": behavior_args,
            "SUMO_PORT": str(35000 + seed),
            "PLOT": str(manifest["run"]["plot"]),
            "EVENT_TIMELINE": str(manifest["run"]["event_timeline"]),
            "ENABLE_COLLISION_OUTPUT": str(manifest["run"]["collision_output"]),
            "COLLISION_ACTION": str(manifest["run"]["collision_action"]),
            "COLLISION_CHECK_JUNCTIONS": str(manifest["run"]["collision_check_junctions"]),
            "EXPORT_RESULTS": str(manifest["run"]["export_results"]),
            "PHY_ANALYSIS": "0" if args.skip_native_metrics else str(manifest["run"]["phy_analysis"]),
        }
    )
    scenario_runner = repo_root() / "experiments" / "operational" / "v2v-emergencyVehicleAlert-nrv2x" / "run.sh"
    subprocess.run([str(scenario_runner)], check=True, env=behavior_env)

    if not args.skip_native_metrics:
        metrics_prefix = native_dir / "native_nr"
        metrics_args = shlex.join(build_metrics_args(manifest, metrics_prefix))
        metrics_env = os.environ.copy()
        metrics_env.update(
            {
                "OUT_DIR": str(native_dir),
                "METRICS_ARGS": metrics_args,
                "JOBS": str(args.jobs),
            }
        )
        sidecar_runner = package_root() / "scripts" / "run_native_metrics.sh"
        subprocess.run([str(sidecar_runner)], check=True, env=metrics_env)

    summarize_script = package_root() / "scripts" / "summarize_strict_runs.py"
    subprocess.run(
        [sys.executable, str(summarize_script), "--run-dir", str(run_dir)],
        check=True,
    )
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
