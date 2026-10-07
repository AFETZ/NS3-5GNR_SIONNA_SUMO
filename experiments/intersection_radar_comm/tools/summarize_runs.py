#!/usr/bin/env python3
"""Summarize radar/link intersection case outcomes."""

from __future__ import annotations

import argparse
import csv
import math
import xml.etree.ElementTree as ET
from pathlib import Path


def _to_float(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "") else math.nan
    except Exception:
        return math.nan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize intersection radar/link scenario runs")
    parser.add_argument("--runs-root", required=True, help="Root directory that contains per-mode run dirs")
    parser.add_argument("--modes", nargs="+", required=True, help="Mode directories to summarize")
    parser.add_argument("--out-dir", required=True, help="Directory for summary CSV")
    return parser.parse_args()


def read_first_event(ctrl_csv: Path, event_type: str) -> float:
    if not ctrl_csv.exists():
        return math.nan
    best = math.nan
    with ctrl_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("event_type") or "").strip() != event_type:
                continue
            current = _to_float(row.get("time_s"))
            if not math.isfinite(current):
                continue
            if not math.isfinite(best) or current < best:
                best = current
    return best


def read_tx_cam_total(artifacts: Path, tx_vehicle_id: str) -> int:
    msg_csv = artifacts / f"eva-{tx_vehicle_id}-MSG.csv"
    if not msg_csv.exists():
        return 0
    total = 0
    with msg_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("msg_type") or "").strip() != "CAM":
                continue
            if (row.get("tx_t_s") or "").strip() == "":
                continue
            total += 1
    return total


def read_rx_cam_ok(msg_csv: Path, tx_id: str) -> int:
    if not msg_csv.exists():
        return 0
    rx_ok = 0
    with msg_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("tx_id") or "").strip() != tx_id:
                continue
            if (row.get("msg_type") or "").strip() != "CAM":
                continue
            if (row.get("rx_ok") or "").strip() == "1":
                rx_ok += 1
    return rx_ok


def read_profile_equiv_dbm(profile_csv: Path) -> float:
    if not profile_csv.exists():
        return math.nan
    with profile_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            return _to_float(row.get("equiv_tx_power_dbm"))
    return math.nan


def read_collision(collision_xml: Path, veh_a: str, veh_b: str) -> tuple[int, float]:
    if not collision_xml.exists():
        raise FileNotFoundError(f"Collision outcome unverified: missing {collision_xml}")
    try:
        root = ET.parse(collision_xml).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Collision outcome unverified: malformed {collision_xml}") from exc
    if root.tag != "collisions":
        raise ValueError(f"Unexpected collision XML root {root.tag!r}: {collision_xml}")
    found = 0
    first_t = math.nan
    for coll in root.findall("collision"):
        collider = (coll.attrib.get("collider") or "").strip()
        victim = (coll.attrib.get("victim") or "").strip()
        if {collider, victim} != {veh_a, veh_b}:
            continue
        current = _to_float(coll.attrib.get("time"))
        found = 1
        if math.isfinite(current) and (not math.isfinite(first_t) or current < first_t):
            first_t = current
    return found, first_t


def read_collision_risk_summary(summary_csv: Path) -> dict[str, str]:
    result = {
        "min_gap_m": "",
        "min_ttc_s": "",
        "risky_gap_events": "",
        "risky_ttc_events": "",
    }
    if not summary_csv.exists():
        return result
    with summary_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            result["min_gap_m"] = row.get("min_gap_m", "")
            result["min_ttc_s"] = row.get("min_ttc_s", "")
            result["risky_gap_events"] = row.get("risky_gap_events", "")
            result["risky_ttc_events"] = row.get("risky_ttc_events", "")
            break
    return result


def main() -> int:
    args = parse_args()
    runs_root = Path(args.runs_root).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "intersection_radar_comm_mode_summary.csv"

    with out_csv.open("w", newline="") as handle:
        fieldnames = [
            "mode",
            "run_dir",
            "veh3_equiv_tx_power_dbm",
            "cam_total_from_veh2",
            "cam_received_by_veh3_from_veh2",
            "observed_prr_veh3_from_veh2",
            "first_cam_reaction_s",
            "first_sensor_reaction_s",
            "collision_veh3_with_veh2",
            "first_collision_time_s",
            "min_gap_m",
            "min_ttc_s",
            "risky_gap_events",
            "risky_ttc_events",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for mode in args.modes:
            run_dir = runs_root / mode
            artifacts = run_dir / "artifacts"

            total = read_tx_cam_total(artifacts, "veh2")
            ok = read_rx_cam_ok(artifacts / "eva-veh3-MSG.csv", "2")
            observed = (ok / total) if total > 0 else math.nan
            collision_flag, collision_time = read_collision(artifacts / "eva-collision.xml", "veh2", "veh3")
            risk_summary = read_collision_risk_summary(artifacts / "collision_risk" / "collision_risk_summary.csv")

            writer.writerow(
                {
                    "mode": mode,
                    "run_dir": str(run_dir),
                    "veh3_equiv_tx_power_dbm": "" if not math.isfinite(read_profile_equiv_dbm(artifacts / "eva-veh3-PROFILE.csv")) else f"{read_profile_equiv_dbm(artifacts / 'eva-veh3-PROFILE.csv'):.6f}",
                    "cam_total_from_veh2": total,
                    "cam_received_by_veh3_from_veh2": ok,
                    "observed_prr_veh3_from_veh2": "" if not math.isfinite(observed) else f"{observed:.6f}",
                    "first_cam_reaction_s": "" if not math.isfinite(read_first_event(artifacts / "eva-veh3-CTRL.csv", "cam_reaction")) else f"{read_first_event(artifacts / 'eva-veh3-CTRL.csv', 'cam_reaction'):.6f}",
                    "first_sensor_reaction_s": "" if not math.isfinite(read_first_event(artifacts / "eva-veh3-CTRL.csv", "sensor_reaction")) else f"{read_first_event(artifacts / 'eva-veh3-CTRL.csv', 'sensor_reaction'):.6f}",
                    "collision_veh3_with_veh2": collision_flag,
                    "first_collision_time_s": "" if not math.isfinite(collision_time) else f"{collision_time:.6f}",
                    "min_gap_m": risk_summary["min_gap_m"],
                    "min_ttc_s": risk_summary["min_ttc_s"],
                    "risky_gap_events": risk_summary["risky_gap_events"],
                    "risky_ttc_events": risk_summary["risky_ttc_events"],
                }
            )

    print(out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
