#!/usr/bin/env python3
"""Native radio calibration sweeps for strict manifests."""

from __future__ import annotations

import argparse
import csv
import os
import shlex
import subprocess
import sys
from pathlib import Path

from strict_common import (
    ManagedSionnaServer,
    build_metrics_args,
    load_manifest,
    normalize_cli_list_value_args,
    package_root,
    repo_root,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep native radio knobs for a strict manifest")
    parser.add_argument("--manifest", required=True, help="Base manifest JSON path")
    parser.add_argument("--out-root", default="analysis/strict_calibration", help="Calibration output root")
    parser.add_argument("--tx-powers", default="8,11,14,17,20,23", help="Comma-separated tx powers")
    parser.add_argument("--rsrp-thresholds", default="-132,-129,-126,-123,-120,-118", help="Comma-separated RSRP thresholds")
    parser.add_argument("--jobs", type=int, default=8, help="ns-3 build jobs")
    parser.add_argument(
        "--auto-sionna",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically start and stop the local Sionna server for the sweep",
    )
    return parser.parse_args(normalize_cli_list_value_args(sys.argv[1:], {"--rsrp-thresholds"}))


def read_average_prr(prr_csv: Path) -> float:
    total = 0.0
    count = 0
    if not prr_csv.exists():
        return float("nan")
    with prr_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                total += float(row["prr"])
                count += 1
            except Exception:
                continue
    return (total / count) if count else float("nan")


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    tx_powers = [int(item.strip()) for item in args.tx_powers.split(",") if item.strip()]
    thresholds = [int(item.strip()) for item in args.rsrp_thresholds.split(",") if item.strip()]

    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = repo_root() / out_root
    calib_root = out_root / manifest["scenario_id"] / manifest["mode"]
    calib_root.mkdir(parents=True, exist_ok=True)

    sidecar_runner = package_root() / "scripts" / "run_native_metrics.sh"
    summary_rows: list[dict[str, object]] = []
    manager = None
    if args.auto_sionna and bool(manifest["run"]["sionna_enabled"]):
        manager = ManagedSionnaServer(
            manifest["sionna_scene"],
            calib_root / "sionna-server.log",
        )
        manager.start()

    try:
        for tx_power in tx_powers:
            for threshold in thresholds:
                combo_dir = calib_root / f"tx-{tx_power}_rsrp-{threshold}"
                combo_dir.mkdir(parents=True, exist_ok=True)
                combo_manifest = json_clone(manifest)
                combo_manifest["radio"]["txPower"] = tx_power
                combo_manifest["radio"]["slThresPsschRsrp"] = threshold
                combo_manifest["run"]["sim_time_s"] = min(float(combo_manifest["run"]["sim_time_s"]), 20.0)
                metrics_prefix = combo_dir / "native_nr"
                metrics_args = shlex.join(build_metrics_args(combo_manifest, metrics_prefix))
                env = os.environ.copy()
                env.update({"OUT_DIR": str(combo_dir), "METRICS_ARGS": metrics_args, "JOBS": str(args.jobs)})
                subprocess.run([str(sidecar_runner)], check=True, env=env)
                summary_rows.append(
                    {
                        "tx_power_dbm": tx_power,
                        "slThresPsschRsrp_dbm": threshold,
                        "avg_prr": read_average_prr(combo_dir / "native_nr-prr.csv"),
                        "run_dir": str(combo_dir),
                    }
                )
    finally:
        if manager is not None:
            manager.stop()

    out_csv = calib_root / "calibration_summary.csv"
    with out_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(out_csv)
    return 0


def json_clone(value):
    import json

    return json.loads(json.dumps(value))


if __name__ == "__main__":
    raise SystemExit(main())
