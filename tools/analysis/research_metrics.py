"""Strict event-level analysis for the prospective manuscript campaign.

CAM identity is (transmitting station, generationDeltaTime), not a fabricated
ns-3 packet UID. This analyzer rejects ambiguous identities (including wrap or
duplicate generation within one millisecond). Observation horizons must be less
than 65.536 s. Missing receptions are censored; missing files are errors.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def first_action(ctrl_csv: Path):
    events = {}
    previous = -math.inf
    for row in rows(ctrl_csv):
        timestamp = number(row.get("time_s"))
        if timestamp is None or timestamp < previous:
            raise ValueError(f"Invalid control clock in {ctrl_csv}: {row}")
        previous = timestamp
        event = row["event_type"]
        if event in ("cam_reaction", "sensor_reaction"):
            events.setdefault(event, timestamp)
    earliest = min(events.items(), key=lambda item: item[1]) if events else (None, None)
    return {"first_action_s": earliest[1], "first_action_source": earliest[0],
            "first_cam_action_s": events.get("cam_reaction"),
            "first_sensor_action_s": events.get("sensor_reaction")}


def collision_outcome(path: Path, pair=("veh2", "veh3")):
    root = ET.parse(path).getroot()
    if root.tag != "collisions":
        raise ValueError(f"Wrong collision logger in {path}")
    events = []
    for entry in root.findall("collision"):
        if {entry.get("collider"), entry.get("victim")} == set(pair):
            timestamp = number(entry.get("time"))
            if timestamp is None:
                raise ValueError(f"Missing collision timestamp in {path}")
            events.append(timestamp)
    return {"collision": bool(events), "first_collision_s": min(events) if events else None,
            "collision_records": len(events)}


def cam_identity(row):
    return (str(row["tx_id"]), str(row["cam_gdt_ms"]))


def analyze_cam_link(tx_file: Path, rx_file: Path, tx_id: str, rx_id: str,
                     start: float, end: float, eligible=None):
    """Count unique application deliveries for eligible TXs in [start,end).

    eligible(tx_time) can supply a spatial/activity condition. All matching RXs
    before `end` are counted, so this defines delivery in the declared
    half-open interval, not eventual delivery. Denominator remains undefined
    with zero eligible TXs.
    """
    if not 0 <= start < end < 65.536:
        raise ValueError("Use a non-wrapping CAM observation window below 65.536 s")
    transmissions = {}
    for line, row in enumerate(rows(tx_file), 2):
        timestamp = number(row.get("tx_t_s"))
        if row.get("msg_type") != "CAM" or str(row.get("tx_id")) != str(tx_id) or timestamp is None:
            continue
        key = cam_identity(row)
        if key in transmissions:
            raise ValueError(f"Ambiguous CAM identity {key} in {tx_file}")
        transmissions[key] = {"tx_s": timestamp, "tx_line": line}
    selected = {key: event for key, event in transmissions.items()
                if start <= event["tx_s"] < end and (eligible is None or eligible(event["tx_s"]))}
    reception = {}
    ignored = {}
    duplicates = 0
    for line, row in enumerate(rows(rx_file), 2):
        if str(row.get("tx_id")) != str(tx_id) or str(row.get("rx_id")) != str(rx_id):
            continue
        event_type = row.get("msg_type", "")
        if event_type != "CAM" or row.get("rx_ok") != "1":
            ignored[event_type] = ignored.get(event_type, 0) + 1
            continue
        timestamp = number(row.get("rx_t_s"))
        if timestamp is None:
            raise ValueError(f"Invalid reception timestamp at {rx_file}:{line}")
        key = cam_identity(row)
        if key not in transmissions:
            raise ValueError(f"Reception has no real TX identity {key} at {rx_file}:{line}")
        if timestamp + 1e-6 < transmissions[key]["tx_s"]:
            raise ValueError(f"Reception precedes transmission: {key}")
        if key not in selected or timestamp >= end:
            continue
        if key in reception:
            duplicates += 1
            if reception[key]["rx_s"] <= timestamp:
                continue
        reception[key] = {"rx_s": timestamp, "rx_line": line}
    joined = []
    for key, tx in selected.items():
        joined.append({"tx_id": key[0], "cam_gdt_ms": key[1], **tx,
                       "rx_s": reception.get(key, {}).get("rx_s"),
                       "rx_line": reception.get(key, {}).get("rx_line")})
    times = [event["rx_s"] for event in reception.values()]
    return {"eligible_tx": len(selected), "received_unique": len(reception),
            "prr": len(reception) / len(selected) if selected else None,
            "first_cam_rx_s": min(times) if times else None,
            "first_rx_censored": bool(selected) and not bool(times),
            "duplicate_rx_records": duplicates, "excluded_event_labels": ignored,
            "join_records": joined}


def wilson(successes, trials, z=1.959963984540054):
    if not 0 <= successes <= trials or trials <= 0:
        raise ValueError("Invalid binomial counts")
    p = successes / trials
    denominator = 1 + z*z/trials
    center = (p + z*z/(2*trials)) / denominator
    half = z * math.sqrt(p*(1-p)/trials + z*z/(4*trials*trials)) / denominator
    return max(0., center-half), min(1., center+half)


def survival_quantile(observations, probability):
    """Kaplan-Meier quantile; pairs are (time,event_observed). None if unreached."""
    if not 0 < probability < 1:
        raise ValueError("Quantile probability must be between zero and one")
    grouped = {}
    for timestamp, observed in observations:
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid event or censoring time")
        counts = grouped.setdefault(timestamp, [0, 0])
        counts[0 if observed else 1] += 1
    at_risk = sum(sum(c) for c in grouped.values())
    survival = 1.
    for timestamp, (events, censored) in sorted(grouped.items()):
        survival *= 1 - events / at_risk
        if survival <= 1 - probability + 1e-12:
            return timestamp
        at_risk -= events + censored
    return None
