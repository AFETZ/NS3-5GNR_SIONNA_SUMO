#!/usr/bin/env python3
"""Audit a 100 s emergency CAM run at application level.

Eligibility is sampled from the *nearest prior* 0.05 s SUMO netstate record.
This deliberately fixed (rather than interpolated in time) rule avoids using
future mobility state when judging a transmission opportunity.  Lane positions
are converted to Cartesian coordinates by arclength interpolation on the
``shape`` polylines in ``net.xml``.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from bisect import bisect_right
from pathlib import Path
import xml.etree.ElementTree as ET

HORIZON_S = 100.0
SNAPSHOT_S = 0.05
RANGE_M = 150.0
MAX_CAM_DELIVERY_S = 1.0
SENDER = "veh2"
RECEIVERS = tuple(f"veh{i}" for i in range(1, 21) if i != 2)
EPS = 1e-7


def _number(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Invalid {label}: {value!r}")
    return result


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"tx_t_s", "rx_t_s", "rx_ok", "msg_type", "tx_id", "rx_id", "cam_gdt_ms"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"Missing required MSG columns in {path}")
        return list(reader)


def _validate_msg_clocks(path: Path) -> list[dict[str, str]]:
    rows = _read_csv(path)
    previous = {"tx_t_s": -math.inf, "rx_t_s": -math.inf}
    for line, row in enumerate(rows, 2):
        for column in previous:
            if row[column] == "":
                continue
            value = _number(row[column], f"{column} at {path}:{line}")
            if not 0 <= value <= HORIZON_S or value < previous[column] - EPS:
                raise ValueError(f"Invalid {column} clock at {path}:{line}")
            previous[column] = value
    return rows


def _polyline_point(points: list[tuple[float, float]], position: float, lane_length: float) -> tuple[float, float]:
    if not 0 <= position <= lane_length + EPS:
        raise ValueError(f"Lane position {position} outside lane length {lane_length}")
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        raise ValueError("Degenerate lane shape")
    remaining = min(position / lane_length * total, total) if lane_length else 0.0
    for start, end, length in zip(points, points[1:], lengths):
        if remaining <= length + EPS:
            share = 0.0 if length == 0 else min(1.0, remaining / length)
            return start[0] + share * (end[0] - start[0]), start[1] + share * (end[1] - start[1])
        remaining -= length
    return points[-1]


def load_lanes(net_xml: Path) -> dict[str, tuple[list[tuple[float, float]], float]]:
    lanes = {}
    for lane in ET.parse(net_xml).getroot().iter("lane"):
        if not lane.get("shape") or lane.get("id") is None:
            continue
        points = [tuple(map(float, point.split(",")[:2])) for point in lane.attrib["shape"].split()]
        if len(points) < 2:
            continue
        lanes[lane.attrib["id"]] = (points, _number(lane.get("length"), "lane length"))
    if not lanes:
        raise ValueError(f"No usable lanes in {net_xml}")
    return lanes


def load_snapshots(netstate_xml: Path, lanes: dict) -> tuple[list[float], dict[float, dict[str, tuple[float, float]]]]:
    times: list[float] = []
    snapshots: dict[float, dict[str, tuple[float, float]]] = {}
    previous = -math.inf
    for _, timestep in ET.iterparse(netstate_xml, events=("end",)):
        if timestep.tag != "timestep":
            continue
        time_s = _number(timestep.get("time"), "netstate time")
        if time_s < previous - EPS or time_s < -EPS or time_s > HORIZON_S + EPS:
            raise ValueError(f"Invalid netstate clock at {time_s}")
        if abs(time_s / SNAPSHOT_S - round(time_s / SNAPSHOT_S)) > 1e-5:
            raise ValueError(f"Netstate time {time_s} is not on the {SNAPSHOT_S}s grid")
        if time_s in snapshots:
            raise ValueError(f"Duplicate netstate time {time_s}")
        state: dict[str, tuple[float, float]] = {}
        for lane in timestep.iter("lane"):
            lane_id = lane.get("id")
            if lane_id not in lanes:
                raise ValueError(f"Netstate references unknown lane {lane_id!r}")
            points, lane_length = lanes[lane_id]
            for vehicle in lane.findall("vehicle"):
                vehicle_id = vehicle.get("id")
                if not vehicle_id or vehicle_id in state:
                    raise ValueError(f"Invalid or duplicate vehicle in netstate at {time_s}")
                state[vehicle_id] = _polyline_point(points, _number(vehicle.get("pos"), "lane position"), lane_length)
        times.append(time_s)
        snapshots[time_s] = state
        previous = time_s
        timestep.clear()
    if not times or abs(times[-1] - HORIZON_S) > 0.15:
        raise ValueError("Netstate does not cover the 100 s horizon")
    return times, snapshots


def _snapshot_at(times: list[float], snapshots: dict, tx_s: float) -> dict[str, tuple[float, float]]:
    index = bisect_right(times, tx_s + EPS) - 1
    if index < 0 or tx_s - times[index] > SNAPSHOT_S + EPS:
        raise ValueError(f"No nearest-prior {SNAPSHOT_S}s netstate snapshot for TX at {tx_s}")
    return snapshots[times[index]]


def _validate_route(route_xml: Path) -> None:
    root = ET.parse(route_xml).getroot()
    vehicle = next((node for node in root.findall("vehicle") if node.get("id") == SENDER), None)
    if vehicle is None or vehicle.get("type") != "Car0":
        raise ValueError("Expected sender veh2 with route type Car0")


def _transmissions(sender_file: Path) -> list[dict]:
    result = []
    previous = -math.inf
    for line, row in enumerate(_validate_msg_clocks(sender_file), 2):
        if row["msg_type"] != "CAM" or row["tx_id"] != "2" or row["tx_t_s"] == "":
            continue
        tx_s = _number(row["tx_t_s"], f"TX time at {sender_file}:{line}")
        if not 0 <= tx_s <= HORIZON_S or tx_s < previous - EPS:
            raise ValueError(f"Invalid TX clock at {sender_file}:{line}")
        gdt = row["cam_gdt_ms"]
        if not gdt.isdigit() or not 0 <= int(gdt) < 65536:
            raise ValueError(f"Invalid CAM generationDeltaTime at {sender_file}:{line}")
        result.append({"tx_s": tx_s, "gdt": int(gdt), "line": line})
        previous = tx_s
    if not result:
        raise ValueError(f"No sender CAM transmissions in {sender_file}")
    seen_gdt: dict[int, float] = {}
    for tx in result:
        earlier = seen_gdt.get(tx["gdt"])
        if earlier is not None and tx["tx_s"] - earlier < 65.536 - EPS:
            raise ValueError(f"Duplicate CAM identity within a wrap epoch at {sender_file}:{tx['line']}")
        seen_gdt[tx["gdt"]] = tx["tx_s"]
    return result


def _join_receiver(receiver_file: Path, receiver: str, transmissions: list[dict]) -> dict[int, float]:
    by_gdt: dict[int, list[dict]] = {}
    for tx in transmissions:
        by_gdt.setdefault(tx["gdt"], []).append(tx)
    joined: dict[int, float] = {}
    previous = -math.inf
    for line, row in enumerate(_validate_msg_clocks(receiver_file), 2):
        if row["msg_type"] != "CAM" or row["tx_id"] != "2" or row["rx_id"] != receiver[3:] or row["rx_ok"] != "1":
            continue
        rx_s = _number(row["rx_t_s"], f"RX time at {receiver_file}:{line}")
        if not 0 <= rx_s <= HORIZON_S or rx_s < previous - EPS:
            raise ValueError(f"Invalid RX clock at {receiver_file}:{line}")
        previous = rx_s
        gdt_text = row["cam_gdt_ms"]
        if not gdt_text.isdigit() or int(gdt_text) not in by_gdt:
            raise ValueError(f"Unmatched CAM RX at {receiver_file}:{line}")
        candidates = [tx for tx in by_gdt[int(gdt_text)] if tx["tx_s"] <= rx_s + EPS]
        if not candidates:
            raise ValueError(f"RX precedes its CAM TX at {receiver_file}:{line}")
        latest = max(tx["tx_s"] for tx in candidates)
        if rx_s - latest > MAX_CAM_DELIVERY_S + EPS:
            raise ValueError(f"CAM RX has no recent TX within {MAX_CAM_DELIVERY_S}s at {receiver_file}:{line}")
        matches = [tx for tx in candidates if abs(tx["tx_s"] - latest) <= EPS]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous wrapped CAM RX at {receiver_file}:{line}")
        tx_index = transmissions.index(matches[0])
        if tx_index in joined:
            raise ValueError(f"Duplicate CAM RX for one TX at {receiver_file}:{line}")
        joined[tx_index] = rx_s
    return joined


def analyze(run_dir: Path, net_xml: Path, route_xml: Path) -> dict:
    artifacts = run_dir / "artifacts"
    _validate_route(route_xml)
    times, snapshots = load_snapshots(artifacts / "eva-netstate.xml", load_lanes(net_xml))
    txs = _transmissions(artifacts / "eva-veh2-MSG.csv")
    receiver_rows = []
    total_eligible = total_received = 0
    for receiver in RECEIVERS:
        joined = _join_receiver(artifacts / f"eva-{receiver}-MSG.csv", receiver, txs)
        eligible_indices = []
        for index, tx in enumerate(txs):
            state = _snapshot_at(times, snapshots, tx["tx_s"])
            if SENDER not in state or receiver not in state:
                continue
            a, b = state[SENDER], state[receiver]
            if math.hypot(a[0] - b[0], a[1] - b[1]) <= RANGE_M + EPS:
                eligible_indices.append(index)
        received = {index: rx_s for index, rx_s in joined.items() if index in eligible_indices}
        first_rx = min(received.values()) if received else None
        first_eligible = txs[eligible_indices[0]]["tx_s"] if eligible_indices else None
        total_eligible += len(eligible_indices)
        total_received += len(received)
        receiver_rows.append({"receiver": receiver, "eligible_tx": len(eligible_indices),
                              "received_unique": len(received),
                              "prr": len(received) / len(eligible_indices) if eligible_indices else None,
                              "first_eligible_tx_s": first_eligible,
                              "first_eligible_rx_s": first_rx,
                              "first_rx_delay_from_eligibility_s": (first_rx - first_eligible)
                                  if first_rx is not None and first_eligible is not None else None,
                              "first_rx_observed": first_rx is not None,
                              "first_rx_censored": bool(eligible_indices) and first_rx is None,
                              "first_rx_censor_s": HORIZON_S if eligible_indices and first_rx is None else None})
    return {"schema": 1, "run_dir": str(run_dir), "horizon_s": HORIZON_S,
            "sender": SENDER, "sender_route_type": "Car0", "receiver_count": len(RECEIVERS),
            "eligibility": {"distance_m": RANGE_M, "snapshot_policy": "nearest_prior_0.05s", "active": "present_in_snapshot"},
            "cam_join": "tx_id+cam_gdt_ms+latest_preceding_tx_time",
            "maximum_cam_delivery_s": MAX_CAM_DELIVERY_S, "eligible_tx": total_eligible,
            "received_unique": total_received, "prr": total_received / total_eligible if total_eligible else None,
            "receivers": receiver_rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--net", type=Path, required=True, help="SUMO net.xml used by the run")
    parser.add_argument("--route", type=Path, required=True, help="SUMO route XML used by the run")
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir.resolve(), args.net.resolve(), args.route.resolve()), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
