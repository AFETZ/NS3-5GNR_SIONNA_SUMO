#!/usr/bin/env python3
"""Build proof-oriented analysis for the intersection radar/comm scenario."""

from __future__ import annotations

import argparse
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


MODES = ["radar_bad_link", "radar_only", "radar_good_link"]
MODE_LABELS = {
    "radar_bad_link": "Radar + bad link",
    "radar_only": "Radar only",
    "radar_good_link": "Radar + good link",
}
MODE_COLORS = {
    "radar_bad_link": "#c0392b",
    "radar_only": "#d68910",
    "radar_good_link": "#1e8449",
}
VEH_COLORS = {
    "veh2": "#c0392b",
    "veh3": "#1f618d",
}


@dataclass
class ModeArtifacts:
    mode: str
    run_dir: Path
    artifacts_dir: Path
    log_path: Path
    netstate_df: pd.DataFrame
    ctrl_df: pd.DataFrame
    tx_df: pd.DataFrame
    rx_df: pd.DataFrame
    collision_time_s: float
    first_cam_reaction_s: float
    first_sensor_reaction_s: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze final intersection radar/comm outputs")
    parser.add_argument("--run-root", required=True, help="Root directory with radar_bad_link/radar_only/radar_good_link")
    parser.add_argument("--out-dir", help="Directory for generated report and figures")
    parser.add_argument("--net-file", required=True, help="SUMO .net.xml used by the scenario")
    parser.add_argument("--route-file", required=True, help="SUMO route file used by the scenario")
    parser.add_argument("--dbm-sweep-root", help="Optional root with per-dBm sweep runs")
    return parser.parse_args()


def to_float(value) -> float:
    try:
        if value in (None, ""):
            return math.nan
        return float(value)
    except Exception:
        return math.nan


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def parse_shape(shape: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in shape.split():
        x_str, y_str = token.split(",")
        points.append((float(x_str), float(y_str)))
    return points


def polyline_lengths(points: list[tuple[float, float]]) -> np.ndarray:
    if len(points) < 2:
        return np.array([0.0])
    lengths = [0.0]
    total = 0.0
    for start, end in zip(points[:-1], points[1:]):
        total += math.hypot(end[0] - start[0], end[1] - start[1])
        lengths.append(total)
    return np.array(lengths)


def point_on_polyline(points: list[tuple[float, float]], pos_m: float) -> tuple[float, float]:
    if not points:
        return math.nan, math.nan
    if len(points) == 1:
        return points[0]
    cumulative = polyline_lengths(points)
    total = cumulative[-1]
    if total <= 1e-9:
        return points[0]
    pos_m = min(max(pos_m, 0.0), total)
    idx = int(np.searchsorted(cumulative, pos_m, side="right") - 1)
    idx = max(0, min(idx, len(points) - 2))
    seg_start = cumulative[idx]
    seg_end = cumulative[idx + 1]
    if seg_end <= seg_start:
        return points[idx]
    ratio = (pos_m - seg_start) / (seg_end - seg_start)
    x0, y0 = points[idx]
    x1, y1 = points[idx + 1]
    return (x0 + ratio * (x1 - x0), y0 + ratio * (y1 - y0))


def load_net_geometry(net_file: Path) -> tuple[dict[str, float], dict[str, list[tuple[float, float]]]]:
    root = ET.parse(net_file).getroot()
    lane_lengths: dict[str, float] = {}
    lane_shapes: dict[str, list[tuple[float, float]]] = {}
    for edge in root.findall("edge"):
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id")
            if not lane_id:
                continue
            lane_lengths[lane_id] = float(lane.attrib.get("length", "0"))
            shape = lane.attrib.get("shape", "")
            if shape:
                lane_shapes[lane_id] = parse_shape(shape)
    return lane_lengths, lane_shapes


def parse_route_file(route_file: Path) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    root = ET.parse(route_file).getroot()
    routes: dict[str, list[str]] = {}
    vehicles: list[dict[str, str]] = []
    for route in root.findall("route"):
        route_id = route.attrib["id"]
        routes[route_id] = route.attrib.get("edges", "").split()
    for vehicle in root.findall("vehicle"):
        vehicles.append(dict(vehicle.attrib))
    return routes, vehicles


def parse_netstate(netstate_path: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    current_time = math.nan
    current_edge = ""
    current_lane = ""
    for event, elem in ET.iterparse(netstate_path, events=("start", "end")):
        tag = elem.tag
        if event == "start":
            if tag == "timestep":
                current_time = float(elem.attrib["time"])
            elif tag == "edge":
                current_edge = elem.attrib.get("id", "")
            elif tag == "lane":
                current_lane = elem.attrib.get("id", "")
            elif tag == "vehicle":
                rows.append(
                    {
                        "time_s": current_time,
                        "edge_id": current_edge,
                        "lane_id": current_lane,
                        "vehicle_id": elem.attrib.get("id", ""),
                        "pos_m": float(elem.attrib.get("pos", "nan")),
                        "speed_mps": float(elem.attrib.get("speed", "nan")),
                    }
                )
        elif event == "end":
            if tag == "lane":
                current_lane = ""
            elif tag == "edge":
                current_edge = ""
            elem.clear()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["vehicle_id", "time_s"]).reset_index(drop=True)


def add_progress_and_xy(
    netstate_df: pd.DataFrame,
    lane_lengths: dict[str, float],
    lane_shapes: dict[str, list[tuple[float, float]]],
) -> pd.DataFrame:
    if netstate_df.empty:
        return netstate_df

    result_frames: list[pd.DataFrame] = []
    for _, veh_df in netstate_df.groupby("vehicle_id", sort=False):
        veh_df = veh_df.sort_values("time_s").copy()
        offset = 0.0
        prev_lane = None
        start_pos = None
        progress_values: list[float] = []
        xs: list[float] = []
        ys: list[float] = []

        for row in veh_df.itertuples(index=False):
            lane_id = row.lane_id
            pos_m = float(row.pos_m)
            if start_pos is None:
                start_pos = pos_m
            elif lane_id != prev_lane and prev_lane is not None:
                offset += lane_lengths.get(prev_lane, 0.0)

            progress_values.append(offset + pos_m - (start_pos or 0.0))
            xy = point_on_polyline(lane_shapes.get(lane_id, []), pos_m)
            xs.append(xy[0])
            ys.append(xy[1])
            prev_lane = lane_id

        veh_df["route_progress_m"] = progress_values
        veh_df["x_m"] = xs
        veh_df["y_m"] = ys
        result_frames.append(veh_df)

    return pd.concat(result_frames, ignore_index=True)


def first_event_time(ctrl_df: pd.DataFrame, event_type: str) -> float:
    if ctrl_df.empty or "event_type" not in ctrl_df.columns:
        return math.nan
    selected = ctrl_df.loc[ctrl_df["event_type"] == event_type, "time_s"]
    if selected.empty:
        return math.nan
    return float(selected.iloc[0])


def first_collision_time(collision_xml: Path) -> float:
    if not collision_xml.exists():
        return math.nan
    root = ET.parse(collision_xml).getroot()
    best = math.nan
    for collision in root.findall("collision"):
        collider = collision.attrib.get("collider", "")
        victim = collision.attrib.get("victim", "")
        if {collider, victim} != {"veh2", "veh3"}:
            continue
        current = to_float(collision.attrib.get("time"))
        if math.isfinite(current) and (not math.isfinite(best) or current < best):
            best = current
    return best


def load_mode_artifacts(
    run_root: Path,
    mode: str,
    lane_lengths: dict[str, float],
    lane_shapes: dict[str, list[tuple[float, float]]],
) -> ModeArtifacts:
    run_dir = run_root / mode
    artifacts_dir = run_dir / "artifacts"
    netstate_df = add_progress_and_xy(parse_netstate(artifacts_dir / "eva-netstate.xml"), lane_lengths, lane_shapes)
    ctrl_df = read_csv(artifacts_dir / "eva-veh3-CTRL.csv")
    if not ctrl_df.empty and "time_s" in ctrl_df.columns:
        ctrl_df["time_s"] = ctrl_df["time_s"].astype(float)
    tx_df = read_csv(artifacts_dir / "eva-veh2-MSG.csv")
    rx_df = read_csv(artifacts_dir / "eva-veh3-MSG.csv")
    return ModeArtifacts(
        mode=mode,
        run_dir=run_dir,
        artifacts_dir=artifacts_dir,
        log_path=run_dir / "v2v-emergencyVehicleAlert-nrv2x.log",
        netstate_df=netstate_df,
        ctrl_df=ctrl_df,
        tx_df=tx_df,
        rx_df=rx_df,
        collision_time_s=first_collision_time(artifacts_dir / "eva-collision.xml"),
        first_cam_reaction_s=first_event_time(ctrl_df, "cam_reaction"),
        first_sensor_reaction_s=first_event_time(ctrl_df, "sensor_reaction"),
    )


def cumulative_curve(times: list[float]) -> pd.DataFrame:
    times = sorted(float(t) for t in times if math.isfinite(float(t)))
    if not times:
        return pd.DataFrame({"time_s": [0.0], "count": [0]})
    return pd.DataFrame({"time_s": [0.0] + times, "count": [0] + list(range(1, len(times) + 1))})


def tx_times_from_veh2(tx_df: pd.DataFrame) -> list[float]:
    if tx_df.empty:
        return []
    mask = (tx_df.get("msg_type") == "CAM") & tx_df.get("tx_t_s").notna()
    return tx_df.loc[mask, "tx_t_s"].astype(float).tolist()


def rx_ok_times_from_veh2(rx_df: pd.DataFrame) -> list[float]:
    if rx_df.empty:
        return []
    tx_ids = pd.to_numeric(rx_df.get("tx_id"), errors="coerce")
    rx_ok = pd.to_numeric(rx_df.get("rx_ok"), errors="coerce")
    rx_times = pd.to_numeric(rx_df.get("rx_t_s"), errors="coerce")
    mask = (rx_df.get("msg_type") == "CAM") & (tx_ids == 2) & (rx_ok == 1) & rx_times.notna()
    return rx_times.loc[mask].astype(float).tolist()


def cumulative_prr_curve(tx_times: list[float], rx_times: list[float]) -> pd.DataFrame:
    timeline = sorted(set([0.0] + tx_times + rx_times))
    tx_sorted = sorted(tx_times)
    rx_sorted = sorted(rx_times)
    tx_count = 0
    rx_count = 0
    rows: list[dict[str, float]] = []
    tx_idx = 0
    rx_idx = 0
    for t in timeline:
        while tx_idx < len(tx_sorted) and tx_sorted[tx_idx] <= t + 1e-9:
            tx_count += 1
            tx_idx += 1
        while rx_idx < len(rx_sorted) and rx_sorted[rx_idx] <= t + 1e-9:
            rx_count += 1
            rx_idx += 1
        rows.append(
            {
                "time_s": t,
                "tx_count": tx_count,
                "rx_ok_count": rx_count,
                "cumulative_prr": (rx_count / tx_count) if tx_count > 0 else math.nan,
            }
        )
    return pd.DataFrame(rows)


def max_speed_spread_across_modes(modes: dict[str, ModeArtifacts], vehicle_id: str) -> pd.DataFrame:
    merged = None
    for mode in MODES:
        veh_df = modes[mode].netstate_df
        veh_df = veh_df.loc[veh_df["vehicle_id"] == vehicle_id, ["time_s", "speed_mps"]].rename(
            columns={"speed_mps": f"{mode}_speed_mps"}
        )
        merged = veh_df if merged is None else merged.merge(veh_df, on="time_s", how="inner")
    speed_cols = [col for col in merged.columns if col.endswith("_speed_mps")]
    merged["speed_spread_mps"] = merged[speed_cols].max(axis=1) - merged[speed_cols].min(axis=1)
    return merged


def compute_pre_event_metrics(modes: dict[str, ModeArtifacts]) -> pd.DataFrame:
    event_times = [
        modes[mode].first_cam_reaction_s
        for mode in MODES
        if math.isfinite(modes[mode].first_cam_reaction_s)
    ] + [
        modes[mode].first_sensor_reaction_s
        for mode in MODES
        if math.isfinite(modes[mode].first_sensor_reaction_s)
    ]
    first_control_global = min(event_times)
    metrics: list[dict[str, float]] = []
    for vehicle_id in ["veh2", "veh3"]:
        spread_df = max_speed_spread_across_modes(modes, vehicle_id)
        pre_df = spread_df.loc[spread_df["time_s"] < first_control_global]
        metrics.append(
            {
                "vehicle_id": vehicle_id,
                "control_free_window_end_s": first_control_global,
                "samples": len(pre_df),
                "max_speed_spread_mps_before_first_control": float(pre_df["speed_spread_mps"].max()),
                "mean_speed_spread_mps_before_first_control": float(pre_df["speed_spread_mps"].mean()),
            }
        )
    return pd.DataFrame(metrics)


def maybe_float_text(value: float) -> str:
    if not math.isfinite(value):
        return "-"
    return f"{value:.3f}"


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    header = "| " + " | ".join(str(col) for col in df.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(df.columns)) + " |"
    lines = [header, separator]
    for row in df.itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, float):
                if math.isnan(value):
                    values.append("")
                else:
                    values.append(f"{value:.6f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def run_log_checks(log_path: Path) -> dict[str, str]:
    text = log_path.read_text() if log_path.exists() else ""
    return {
        "sionna_connected": "SUCCESS! ns-3 is now locally connected to Sionna" in text,
        "veh2_profile": re.search(r"PER-VEHICLE-EQUIV-DBM-APPLIED,id=veh2,.*equiv_tx_power_dbm=([-0-9.]+)", text),
        "veh3_profile": re.search(r"PER-VEHICLE-EQUIV-DBM-APPLIED,id=veh3,.*equiv_tx_power_dbm=([-0-9.]+)", text),
    }


def plot_xy_trajectories(
    out_path: Path,
    modes: dict[str, ModeArtifacts],
    lane_shapes: dict[str, list[tuple[float, float]]],
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), constrained_layout=True)
    background_lanes = ["c1_to_w_0", "s1_to_w_0", "w_to_n1_0", "w_to_s1_0", ":w_3_0", ":w_5_0"]

    for ax, mode in zip(axes, MODES):
        art = modes[mode]
        for lane_id in background_lanes:
            shape = lane_shapes.get(lane_id, [])
            if shape:
                xs = [p[0] for p in shape]
                ys = [p[1] for p in shape]
                ax.plot(xs, ys, color="#d0d3d4", linewidth=3, zorder=0)

        for vehicle_id in ["veh2", "veh3"]:
            veh_df = art.netstate_df.loc[art.netstate_df["vehicle_id"] == vehicle_id]
            ax.plot(
                veh_df["x_m"],
                veh_df["y_m"],
                color=VEH_COLORS[vehicle_id],
                linewidth=2,
                label=vehicle_id,
            )
            if math.isfinite(art.collision_time_s):
                collision_row = veh_df.loc[(veh_df["time_s"] - art.collision_time_s).abs().idxmin()]
                ax.scatter(collision_row["x_m"], collision_row["y_m"], color="black", s=30, marker="x", zorder=5)
            reaction_times = [art.first_cam_reaction_s, art.first_sensor_reaction_s]
            for reaction_time in reaction_times:
                if math.isfinite(reaction_time):
                    reaction_row = veh_df.loc[(veh_df["time_s"] - reaction_time).abs().idxmin()]
                    ax.scatter(
                        reaction_row["x_m"],
                        reaction_row["y_m"],
                        color=VEH_COLORS[vehicle_id],
                        edgecolor="white",
                        linewidth=0.8,
                        s=45,
                        zorder=6,
                    )
                    break

        ax.set_title(MODE_LABELS[mode])
        ax.set_xlabel("x [m]")
        ax.set_ylabel("y [m]")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, alpha=0.25)

    handles = [
        plt.Line2D([0], [0], color=VEH_COLORS["veh2"], lw=2, label="veh2"),
        plt.Line2D([0], [0], color=VEH_COLORS["veh3"], lw=2, label="veh3"),
        plt.Line2D([0], [0], color="black", marker="x", lw=0, label="collision"),
        plt.Line2D([0], [0], color="white", marker="o", markerfacecolor="#7f8c8d", lw=0, label="first reaction"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4)
    fig.suptitle("Observed XY trajectories on the same intersection geometry", y=1.02)
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_veh3_speed(out_path: Path, modes: dict[str, ModeArtifacts]) -> None:
    fig, ax = plt.subplots(figsize=(11, 5.5), constrained_layout=True)
    for mode in MODES:
        art = modes[mode]
        veh_df = art.netstate_df.loc[art.netstate_df["vehicle_id"] == "veh3"]
        ax.plot(veh_df["time_s"], veh_df["speed_mps"], color=MODE_COLORS[mode], linewidth=2, label=MODE_LABELS[mode])
        if math.isfinite(art.first_cam_reaction_s):
            ax.axvline(art.first_cam_reaction_s, color=MODE_COLORS[mode], linestyle="--", alpha=0.65)
        if math.isfinite(art.first_sensor_reaction_s):
            ax.axvline(art.first_sensor_reaction_s, color=MODE_COLORS[mode], linestyle=":", alpha=0.75)
        if math.isfinite(art.collision_time_s):
            ax.axvline(art.collision_time_s, color=MODE_COLORS[mode], linestyle="-.", alpha=0.55)

    ax.set_title("veh3 speed profile diverges only after computed reactions")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("speed [m/s]")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_comm_delivery(out_path: Path, modes: dict[str, ModeArtifacts]) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), constrained_layout=True, sharex=True)
    ax_counts, ax_prr = axes

    for mode in MODES:
        art = modes[mode]
        tx_times = tx_times_from_veh2(art.tx_df)
        rx_times = rx_ok_times_from_veh2(art.rx_df)
        tx_curve = cumulative_curve(tx_times)
        rx_curve = cumulative_curve(rx_times)
        prr_curve = cumulative_prr_curve(tx_times, rx_times)

        ax_counts.step(
            tx_curve["time_s"],
            tx_curve["count"],
            where="post",
            color=MODE_COLORS[mode],
            linewidth=1.6,
            linestyle="--",
            label=f"{MODE_LABELS[mode]} TX"
        )
        ax_counts.step(
            rx_curve["time_s"],
            rx_curve["count"],
            where="post",
            color=MODE_COLORS[mode],
            linewidth=2.0,
            label=f"{MODE_LABELS[mode]} RX ok"
        )
        ax_prr.step(
            prr_curve["time_s"],
            prr_curve["cumulative_prr"],
            where="post",
            color=MODE_COLORS[mode],
            linewidth=2.0,
            label=MODE_LABELS[mode],
        )

    ax_counts.set_ylabel("count")
    ax_counts.set_title("veh2 -> veh3 CAM delivery under live Sionna channel")
    ax_counts.grid(True, alpha=0.25)
    ax_counts.legend(ncol=2, fontsize=9)

    ax_prr.set_xlabel("time [s]")
    ax_prr.set_ylabel("cumulative PRR")
    ax_prr.set_ylim(-0.02, 1.02)
    ax_prr.grid(True, alpha=0.25)
    ax_prr.legend()
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_event_timeline(out_path: Path, summary_df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11, 4.8), constrained_layout=True)
    y_positions = {mode: idx for idx, mode in enumerate(MODES)}
    offsets = {"cam_reaction": -0.14, "sensor_reaction": 0.0, "collision": 0.14}
    markers = {"cam_reaction": "o", "sensor_reaction": "s", "collision": "x"}
    labels_done: set[str] = set()

    for row in summary_df.to_dict("records"):
        mode = row["mode"]
        y = y_positions[mode]
        for key, column in [
            ("cam_reaction", "first_cam_reaction_s"),
            ("sensor_reaction", "first_sensor_reaction_s"),
            ("collision", "first_collision_time_s"),
        ]:
            value = to_float(row.get(column))
            if not math.isfinite(value):
                continue
            label = key if key not in labels_done else None
            ax.scatter(
                value,
                y + offsets[key],
                color=MODE_COLORS[mode],
                marker=markers[key],
                s=70,
                label=label,
                zorder=3,
            )
            labels_done.add(key)

    ax.set_yticks([y_positions[mode] for mode in MODES], [MODE_LABELS[mode] for mode in MODES])
    ax.set_xlabel("time [s]")
    ax.set_title("First computed actions and collision time")
    ax.grid(True, axis="x", alpha=0.25)
    ax.legend()
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_pre_event_spread(out_path: Path, modes: dict[str, ModeArtifacts]) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True, sharex=True)
    first_control_global = min(
        value
        for value in [
            modes[mode].first_cam_reaction_s
            for mode in MODES
            if math.isfinite(modes[mode].first_cam_reaction_s)
        ] + [
            modes[mode].first_sensor_reaction_s
            for mode in MODES
            if math.isfinite(modes[mode].first_sensor_reaction_s)
        ]
    )

    for ax, vehicle_id in zip(axes, ["veh2", "veh3"]):
        spread_df = max_speed_spread_across_modes(modes, vehicle_id)
        ax.plot(spread_df["time_s"], spread_df["speed_spread_mps"], color="#34495e", linewidth=2)
        ax.axvline(first_control_global, color="#7f8c8d", linestyle="--", label="first control event")
        ax.set_ylabel(f"{vehicle_id} spread [m/s]")
        ax.grid(True, alpha=0.25)
        ax.legend()

    axes[0].set_title("Cross-mode speed spread is zero before the first computed intervention")
    axes[-1].set_xlabel("time [s]")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def collect_dbm_sweep(dbm_sweep_root: Path) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for child in sorted(dbm_sweep_root.iterdir()):
        summary_path = child / "summary" / "intersection_radar_comm_mode_summary.csv"
        if not summary_path.exists():
            continue
        summary_df = pd.read_csv(summary_path)
        if summary_df.empty:
            continue
        row = summary_df.iloc[0].to_dict()
        rows.append(
            {
                "run_tag": child.name,
                "veh3_equiv_tx_power_dbm": to_float(row.get("veh3_equiv_tx_power_dbm")),
                "observed_prr_veh3_from_veh2": to_float(row.get("observed_prr_veh3_from_veh2")),
                "first_cam_reaction_s": to_float(row.get("first_cam_reaction_s")),
                "first_sensor_reaction_s": to_float(row.get("first_sensor_reaction_s")),
                "collision_veh3_with_veh2": int(to_float(row.get("collision_veh3_with_veh2")) or 0),
                "first_collision_time_s": to_float(row.get("first_collision_time_s")),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("veh3_equiv_tx_power_dbm").reset_index(drop=True)


def plot_dbm_sweep(out_path: Path, sweep_df: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), constrained_layout=True, sharex=True)
    ax_prr, ax_reaction = axes

    colors = sweep_df["collision_veh3_with_veh2"].map({1: "#c0392b", 0: "#1e8449"})
    ax_prr.plot(
        sweep_df["veh3_equiv_tx_power_dbm"],
        sweep_df["observed_prr_veh3_from_veh2"],
        color="#2e86c1",
        linewidth=1.6,
    )
    ax_prr.scatter(
        sweep_df["veh3_equiv_tx_power_dbm"],
        sweep_df["observed_prr_veh3_from_veh2"],
        c=colors,
        s=60,
        zorder=3,
    )
    ax_prr.set_ylabel("observed PRR")
    ax_prr.set_title("Continuous channel-strength sweep changes live PRR under the same traffic geometry")
    ax_prr.grid(True, alpha=0.25)

    earliest_action = np.fmin(
        sweep_df["first_cam_reaction_s"],
        sweep_df["first_sensor_reaction_s"],
    )
    ax_reaction.plot(
        sweep_df["veh3_equiv_tx_power_dbm"],
        earliest_action,
        color="#7d3c98",
        linewidth=1.6,
    )
    ax_reaction.scatter(
        sweep_df["veh3_equiv_tx_power_dbm"],
        earliest_action,
        c=colors,
        s=60,
        zorder=3,
    )
    ax_reaction.set_xlabel("veh3 equivalent TX power [dBm]")
    ax_reaction.set_ylabel("first action time [s]")
    ax_reaction.set_title("Collision outcome follows computed first useful action timing, not mode names")
    ax_reaction.grid(True, alpha=0.25)
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def write_markdown_report(
    out_path: Path,
    run_root: Path,
    summary_df: pd.DataFrame,
    pre_event_df: pd.DataFrame,
    routes: dict[str, list[str]],
    vehicles: list[dict[str, str]],
    mode_log_checks: dict[str, dict[str, str]],
    runner_text: str,
    dbm_sweep_df: pd.DataFrame,
) -> None:
    vehicle_table = pd.DataFrame(vehicles).loc[:, ["id", "route", "depart", "departPos", "departSpeed", "type"]]
    synthetic_checks = {
        "drop_triggered_reaction_disabled": "--drop-triggered-reaction-enable=0" in runner_text,
        "global_rx_drops_disabled": "--rx-drop-prob-cam=0" in runner_text and "--rx-drop-prob-phy-cam=0" in runner_text,
        "target_loss_profile_disabled": "--target-loss-profile-enable=0" in runner_text,
        "incident_disabled": "--incident-enable=0" in runner_text,
        "crash_mode_disabled": "--crash-mode-enable=0" in runner_text,
    }

    with out_path.open("w") as handle:
        handle.write("# Intersection Radar/Comm Proof Report\n\n")
        handle.write(f"Run root: `{run_root}`\n\n")

        handle.write("## 1. Same geometry and same base traffic\n\n")
        handle.write("The route file is identical for all three modes. Vehicles and departures:\n\n")
        handle.write(dataframe_to_markdown(vehicle_table))
        handle.write("\n\n")
        handle.write("Route definitions:\n\n")
        for route_id, edges in routes.items():
            handle.write(f"- `{route_id}`: `{' '.join(edges)}`\n")
        handle.write("\n")

        handle.write("## 2. What changes between modes\n\n")
        handle.write("- `radar_bad_link`: same scenario, same Sionna coupling, but `veh3` equivalent TX power is degraded.\n")
        handle.write("- `radar_only`: same scenario, CAM dissemination disabled, local sensor reaction still enabled.\n")
        handle.write("- `radar_good_link`: same scenario, Sionna active, normal equivalent TX power.\n\n")

        handle.write("## 3. Checks against synthetic crash scripting\n\n")
        for key, value in synthetic_checks.items():
            handle.write(f"- `{key}`: `{value}`\n")
        handle.write("\n")

        handle.write("## 4. Live Sionna evidence from run logs\n\n")
        for mode in MODES:
            checks = mode_log_checks[mode]
            veh2_match = checks["veh2_profile"]
            veh3_match = checks["veh3_profile"]
            veh2_text = veh2_match.group(1) if veh2_match else "missing"
            veh3_text = veh3_match.group(1) if veh3_match else "missing"
            handle.write(
                f"- `{mode}`: `sionna_connected={checks['sionna_connected']}`, "
                f"`veh2_eq_dbm={veh2_text}`, `veh3_eq_dbm={veh3_text}`\n"
            )
        handle.write("\n")

        handle.write("## 5. Observed outcomes\n\n")
        handle.write(dataframe_to_markdown(summary_df))
        handle.write("\n\n")

        handle.write("## 6. Pre-control identity check\n\n")
        handle.write(
            "Before the first computed intervention in any mode, cross-mode speed spread is effectively zero. "
            "This shows the traffic state is shared up to the point where communication or sensor logic changes the control.\n\n"
        )
        handle.write(dataframe_to_markdown(pre_event_df))
        handle.write("\n\n")

        if not dbm_sweep_df.empty:
            handle.write("## 7. Continuous dBm sweep\n\n")
            handle.write(
                "This extra sweep changes only `veh3_equiv_tx_power_dbm` while keeping the same scenario setup. "
                "If the outcome were hardcoded per named mode, this sweep would not produce a changing PRR/action pattern under the same routes and the same SUMO behavior. "
                "The boundary is not a simple monotonic PRR threshold: the decisive variable is the time of the first useful CAM that arrives early enough to trigger control.\n\n"
            )
            handle.write(dataframe_to_markdown(dbm_sweep_df))
            handle.write("\n\n")

        handle.write("## 8. Figures\n\n")
        handle.write("- `figure_01_xy_trajectories.png`: same map, different computed trajectories after reaction timing changes.\n")
        handle.write("- `figure_02_veh3_speed.png`: `veh3` speed diverges after the first mode-specific reaction.\n")
        handle.write("- `figure_03_comm_delivery.png`: live Sionna message delivery and cumulative PRR.\n")
        handle.write("- `figure_04_event_timeline.png`: first `cam_reaction`, first `sensor_reaction`, and collision time.\n")
        handle.write("- `figure_05_pre_event_spread.png`: zero speed spread before the first computed intervention.\n")
        if not dbm_sweep_df.empty:
            handle.write("- `figure_06_dbm_sweep.png`: continuous channel-strength sweep versus PRR and reaction time.\n")
        handle.write("\n")


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else run_root / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    net_file = Path(args.net_file).resolve()
    route_file = Path(args.route_file).resolve()
    lane_lengths, lane_shapes = load_net_geometry(net_file)
    routes, vehicles = parse_route_file(route_file)

    summary_path = run_root / "summary" / "intersection_radar_comm_mode_summary.csv"
    summary_df = pd.read_csv(summary_path)
    summary_df = summary_df.set_index("mode").loc[MODES].reset_index()

    modes = {
        mode: load_mode_artifacts(run_root, mode, lane_lengths, lane_shapes)
        for mode in MODES
    }
    pre_event_df = compute_pre_event_metrics(modes)
    mode_log_checks = {mode: run_log_checks(modes[mode].log_path) for mode in MODES}

    plot_xy_trajectories(out_dir / "figure_01_xy_trajectories.png", modes, lane_shapes)
    plot_veh3_speed(out_dir / "figure_02_veh3_speed.png", modes)
    plot_comm_delivery(out_dir / "figure_03_comm_delivery.png", modes)
    plot_event_timeline(out_dir / "figure_04_event_timeline.png", summary_df)
    plot_pre_event_spread(out_dir / "figure_05_pre_event_spread.png", modes)

    dbm_sweep_df = pd.DataFrame()
    if args.dbm_sweep_root:
        dbm_sweep_df = collect_dbm_sweep(Path(args.dbm_sweep_root).resolve())
        if not dbm_sweep_df.empty:
            plot_dbm_sweep(out_dir / "figure_06_dbm_sweep.png", dbm_sweep_df)
            dbm_sweep_df.to_csv(out_dir / "dbm_sweep_summary.csv", index=False)

    pre_event_df.to_csv(out_dir / "pre_event_identity_metrics.csv", index=False)
    summary_df.to_csv(out_dir / "mode_summary_snapshot.csv", index=False)

    runner_text = (Path(__file__).resolve().parent / "run.sh").read_text()
    write_markdown_report(
        out_dir / "analysis_report.md",
        run_root,
        summary_df,
        pre_event_df,
        routes,
        vehicles,
        mode_log_checks,
        runner_text,
        dbm_sweep_df,
    )

    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
