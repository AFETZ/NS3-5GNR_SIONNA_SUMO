#!/usr/bin/env python3
"""Deterministic analysis of the prospective intersection campaign.

The unit of inference is a seed/run (and blocks for resampling), never an
individual CAM packet.  The input layout is ``ROOT/block-XX/ARM``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from research_metrics import analyze_cam_link, collision_outcome, first_action, survival_quantile, wilson

HORIZON = 20.0
NETSTATE_TERMINAL = 19.95  # SUMO omits the endpoint on its 0.05 s update grid.
BEHAVIOR_ARMS = ("radar_only", "sionna_good", "sionna_bad", "native_good", "native_bad")
CALIBRATION_ARMS = ("sionna_good", "sionna_bad", "native_good", "native_bad")
REQUIRED = ("eva-veh2-MSG.csv", "eva-veh3-MSG.csv", "eva-veh3-CTRL.csv", "eva-collision.xml", "eva-netstate.xml")
PRODUCTION_BLOCKS = tuple(f"block-{block:02d}" for block in range(1, 31))
COHORTS = {"behavior": "behavioral_intersection", "calibration": "radio_calibration"}


def _finite(value, label):
    try: value = float(value)
    except (TypeError, ValueError) as exc: raise ValueError(f"invalid {label}: {value!r}") from exc
    if not math.isfinite(value): raise ValueError(f"invalid {label}: {value!r}")
    return value


def _artifact_dir(run: Path) -> Path:
    return run / "artifacts" if (run / "artifacts").is_dir() else run


def _manifest(run: Path):
    path = run / "manifest.json"
    if not path.is_file():
        path = run / "run_manifest.json"
    if not path.is_file(): raise ValueError(f"missing completed manifest: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    status = str(data.get("status", data.get("state", data.get("completed", "")))).lower()
    if not (status in ("completed", "complete", "true", "1") or status.startswith("completed_")):
        raise ValueError(f"manifest is not completed: {path}")
    hashes = data.get("file_hashes", data.get("sha256", data.get("hashes", {})))
    if hashes and not isinstance(hashes, dict): raise ValueError(f"invalid file hashes: {path}")
    for name, expected in hashes.items():
        candidate = run / name
        if not candidate.is_file(): candidate = _artifact_dir(run) / name
        if not candidate.is_file(): raise ValueError(f"hashed file missing: {name}")
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual.lower() != str(expected).lower(): raise ValueError(f"hash mismatch: {candidate}")
    records = data.get("file_records", [])
    if not isinstance(records, list): raise ValueError(f"invalid file_records: {path}")
    for record in records:
        if not isinstance(record, dict) or not record.get("name") or not record.get("sha256"):
            raise ValueError(f"invalid file record: {path}")
        candidate = _artifact_dir(run) / record["name"]
        if not candidate.is_file(): candidate = run / record["name"]
        if not candidate.is_file(): raise ValueError(f"recorded file missing: {record['name']}")
        if hashlib.sha256(candidate.read_bytes()).hexdigest().lower() != str(record["sha256"]).lower():
            raise ValueError(f"hash mismatch: {candidate}")
        if "bytes" in record and candidate.stat().st_size != int(record["bytes"]):
            raise ValueError(f"size mismatch: {candidate}")
    return data


def _audit_clean(run: Path):
    # Audit files are deliberately read as bytes: comments/formatting cannot hide a token.
    files = [p for p in run.rglob("*") if p.is_file() and ("audit" in p.name.lower() or p.suffix.lower() in (".log", ".err"))]
    for path in files:
        text = path.read_bytes().lower()
        if b"fatal" in text or b"fallback" in text: raise ValueError(f"fatal/fallback audit: {path}")


def _netstate(path: Path, end=NETSTATE_TERMINAL):
    snapshots, previous = [], -math.inf
    for _, node in ET.iterparse(path, events=("end",)):
        if node.tag != "timestep": continue
        t = _finite(node.get("time"), "netstate time")
        if t < previous: raise ValueError("netstate timestamps are not ordered")
        state = {}
        for lane in node.iter("lane"):
            lane_id = lane.get("id")
            for vehicle in lane.findall("vehicle"):
                vid = vehicle.get("id")
                if not vid or vid in state: raise ValueError("invalid netstate vehicle")
                state[vid] = (lane_id, _finite(vehicle.get("pos"), "position"), _finite(vehicle.get("speed"), "speed"))
        snapshots.append((t, state)); previous = t; node.clear()
    if not snapshots or abs(snapshots[-1][0] - end) > 1e-5: raise ValueError(f"netstate must end at {end:g}s")
    return snapshots


def _trajectory(snapshots, end):
    return [(t, state.get("veh2"), state.get("veh3")) for t, state in snapshots if 0 <= t < end]


def _required(run: Path):
    artifacts = _artifact_dir(run)
    missing = [name for name in REQUIRED if not (artifacts / name).is_file()]
    if missing: raise ValueError(f"missing required files in {run}: {', '.join(missing)}")
    return artifacts


def _sionna_no_path(manifest, arm):
    """Return the ray-tracer no-path *sentinel*, never a radio-loss metric."""
    if not arm.startswith("sionna_"):
        return "NA", "NA", "not_applicable_native"
    audit = manifest.get("sionna_audit")
    if not isinstance(audit, dict):
        if manifest.get("pilot_excluded") is True:
            return None, None, "sionna_audit_missing_legacy_pilot"
        raise ValueError("missing Sionna audit in production manifest")
    counts = audit.get("path_gain_status_counts")
    if not isinstance(counts, dict) or not counts:
        raise ValueError("invalid Sionna audit in production manifest")
    try:
        total = sum(int(value) for value in counts.values())
        no_path = int(counts.get("no_path", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Sionna audit in production manifest") from exc
    if total <= 0 or no_path < 0 or not set(counts).issubset({"ok", "no_path"}) or int(counts.get("ok", 0)) <= 0:
        raise ValueError("invalid Sionna audit in production manifest")
    if audit.get("path_gain_request_count") is not None and int(audit["path_gain_request_count"]) != total:
        raise ValueError("Sionna path-gain request count differs from statuses")
    reported_fraction = audit.get("no_ray_sentinel_fraction")
    if reported_fraction is not None and abs(_finite(reported_fraction, "Sionna no-ray fraction") - no_path / total) > 1e-12:
        raise ValueError("Sionna no-ray fraction differs from statuses")
    return no_path, (no_path / total if total else None), "sionna_no_path_sentinel_not_physical_loss"


def analyze_run(run: Path, kind: str, block: str, arm: str):
    manifest = _manifest(run); _audit_clean(run); artifacts = _required(run)
    snapshots = _netstate(artifacts / "eva-netstate.xml")
    collision = collision_outcome(artifacts / "eva-collision.xml", ("veh2", "veh3"))
    # Behavioral timing is observed over the whole 20 s run.  Calibration is
    # deliberately restricted to the pre-conflict [2, 5) application window.
    link = analyze_cam_link(artifacts / "eva-veh2-MSG.csv", artifacts / "eva-veh3-MSG.csv", "2", "3", *( (0, 20) if kind == "behavior" else (2, 5) ))
    no_path, no_path_fraction, no_path_label = _sionna_no_path(manifest, arm)
    row = {"block": block, "arm": arm, "kind": kind, "run_dir": str(run), "collision": int(collision["collision"]),
           "collision_time_s": collision["first_collision_s"], "first_cam_rx_s": link["first_cam_rx_s"],
           "cam_prr": link["prr"], "eligible_cam_tx": link["eligible_tx"], "received_cam": link["received_unique"],
           "sionna_no_path_count": no_path, "sionna_no_path_fraction": no_path_fraction,
           "sionna_no_path_label": no_path_label}
    if kind == "behavior":
        action = first_action(artifacts / "eva-veh3-CTRL.csv")
        row.update(first_action_s=action["first_action_s"], first_action_source=action["first_action_source"],
                   radar_prr=None, whole_run_prr_descriptive=link["prr"])
    else:
        action = first_action(artifacts / "eva-veh3-CTRL.csv")
        if action["first_action_s"] is not None: raise ValueError(f"controller action in calibration run: {run}")
        row.update(first_action_s=None, first_action_source=None)
    return row, _trajectory(snapshots, 2 if kind == "behavior" else 5)


def _requested_arms(value, expected, kind):
    arms = tuple(item for item in value.split(",") if item)
    if len(arms) != len(expected) or set(arms) != set(expected):
        raise ValueError(f"{kind} arms must be exactly: {', '.join(expected)}")
    return expected


def _radio_configuration(run: Path):
    log = run / "simulator.log"
    if not log.is_file():
        raise ValueError(f"missing simulator log: {log}")
    text = log.read_text(encoding="utf-8", errors="replace")
    band = re.search(r"NR-SIDELINK-BANDWIDTH,bandwidthBandSl_100kHz=(\d+),operation_band_hz=([\d.eE+-]+)", text)
    phy = re.search(r"NR-SIDELINK-PHY,channel_bandwidth_hz=(\d+),resource_blocks=(\d+)", text)
    if (not band or not phy or int(band[1]) != 400 or abs(float(band[2]) - 40e6) > 1
            or int(phy[1]) != 40_000_000 or int(phy[2]) != 53):
        raise ValueError(f"NR 40 MHz/53 RB audit failed: {log}")


def _production_metadata(manifest, run: Path, kind: str, block: str, arm: str):
    if manifest.get("schema") != 1:
        raise ValueError(f"invalid manifest schema: {run}")
    if manifest.get("status") != "completed_pending_metric_audit":
        raise ValueError(f"invalid manifest status: {run}")
    if manifest.get("pilot_excluded") is not False:
        raise ValueError(f"pilot or unspecified exclusion status: {run}")
    if manifest.get("arm") != arm or manifest.get("block") != int(block.removeprefix("block-")):
        raise ValueError(f"manifest arm/block mismatch: {run}")
    if manifest.get("cohort") != COHORTS[kind]:
        raise ValueError(f"manifest cohort mismatch: {run}")
    if manifest.get("simulator_exit_code") != 0:
        raise ValueError(f"nonzero or missing simulator exit code: {run}")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError(f"missing manifest input SHA map: {run}")
    if any(not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
           for name, digest in inputs.items()):
        raise ValueError(f"invalid manifest input SHA map: {run}")
    _radio_configuration(run)
    _sionna_no_path(manifest, arm)
    return inputs


def _assert_matched_trajectories(trajectories, kind: str, block: str):
    baseline = trajectories[0][1]
    for arm, trajectory in trajectories[1:]:
        if trajectory != baseline:
            window = "t < 2 s" if kind == "behavior" else "t < 5 s"
            raise ValueError(f"pre-intervention trajectory differs ({window}) in {block}/{arm}")


def _production_gate(runs_root: Path, specifications, full_arms_by_kind=None):
    """Reject any campaign that cannot support the registered paired analysis."""
    all_inputs = None
    for kind, root_name, arms in specifications:
        root = runs_root / root_name
        actual_blocks = {path.name for path in root.glob("block-*") if path.is_dir()} if root.is_dir() else set()
        if actual_blocks != set(PRODUCTION_BLOCKS):
            raise ValueError(f"{kind} blocks must be exactly block-01..block-30; found {sorted(actual_blocks)}")
        for block in PRODUCTION_BLOCKS:
            block_dir = root / block
            actual_arms = {path.name for path in block_dir.iterdir() if path.is_dir()}
            required_arms = full_arms_by_kind[kind] if full_arms_by_kind else arms
            if actual_arms != set(required_arms):
                raise ValueError(f"{kind} arms in {block} must be exactly: {', '.join(required_arms)}")
            paired = []
            for arm in arms:
                run = block_dir / arm
                manifest = _manifest(run)
                inputs = _production_metadata(manifest, run, kind, block, arm)
                if all_inputs is None:
                    all_inputs = inputs
                elif inputs != all_inputs:
                    raise ValueError(f"manifest input SHA map differs: {run}")
                artifacts = _required(run)
                paired.append((arm, _trajectory(_netstate(artifacts / "eva-netstate.xml"), 2 if kind == "behavior" else 5)))
            _assert_matched_trajectories(paired, kind, block)


def _bootstrap(rows, arm_a, arm_b, field, draws=2000, seed=20260917):
    grouped = defaultdict(dict)
    for row in rows:
        if row[field] is not None: grouped[row["block"]][row["arm"]] = float(row[field])
    units = [v for v in grouped.values() if arm_a in v and arm_b in v]
    if not units: return None
    rng = random.Random(seed); values = []
    for _ in range(draws):
        sample = [rng.choice(units) for _ in units]
        values.append(sum(x[arm_a] - x[arm_b] for x in sample) / len(sample))
    values.sort(); return {"estimate": sum(x[arm_a] - x[arm_b] for x in units) / len(units), "n_blocks": len(units),
                            "ci95": [values[int(.025 * draws)], values[int(.975 * draws) - 1]]}


def summarize(rows):
    out = {"collision": {}, "first_action": {}, "paired_discordance": [], "bootstrap": {}}
    for arm in sorted({r["arm"] for r in rows if r["kind"] == "behavior"}):
        items = [r for r in rows if r["kind"] == "behavior" and r["arm"] == arm]
        s = sum(r["collision"] for r in items); out["collision"][arm] = {"runs": len(items), "collisions": s, "rate": s / len(items) if items else None, "wilson95": wilson(s, len(items)) if items else None}
        observations, sources = [], defaultdict(int)
        for row in items:
            action_s = row.get("first_action_s")
            if action_s is None:
                observations.append((HORIZON, False))
            else:
                action_s = _finite(action_s, "first action time")
                if not 0 <= action_s <= HORIZON:
                    raise ValueError(f"first action outside completed horizon: {arm}")
                observations.append((action_s, True))
                sources[str(row.get("first_action_source"))] += 1
        out["first_action"][arm] = {
            "time_origin": "scenario_start_s", "method": "Kaplan-Meier with no-action right censoring at 20 s",
            "events": sum(event for _, event in observations),
            "right_censored": sum(not event for _, event in observations),
            "median_s": survival_quantile(observations, .5) if observations else None,
            "p90_s": survival_quantile(observations, .9) if observations else None,
            "source_counts": dict(sorted(sources.items()))}
    arms = sorted(out["collision"])
    for i, a in enumerate(arms):
        for b in arms[i + 1:]:
            paired = {(r["block"], r["arm"]): r["collision"] for r in rows if r["kind"] == "behavior"}
            blocks = sorted({block for block, arm in paired if arm == a and (block, b) in paired})
            pairs = [(paired[(block, a)], paired[(block, b)]) for block in blocks]
            if pairs: out["paired_discordance"].append({"a": a, "b": b, "a_only": sum(x and not y for x,y in pairs), "b_only": sum(y and not x for x,y in pairs), "pairs": len(pairs)})
    for kind, field in (("behavior", "collision"), ("calibration", "cam_prr")):
        arms = sorted({r["arm"] for r in rows if r["kind"] == kind})
        for i, a in enumerate(arms):
            for b in arms[i + 1:]:
                result = _bootstrap([r for r in rows if r["kind"] == kind], a, b, field)
                if result: out["bootstrap"][f"{kind}:{field}:{a}-{b}"] = result
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=Path("/research/runs"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--behavior-arms", default=",".join(BEHAVIOR_ARMS))
    parser.add_argument("--calibration-arms", default=",".join(CALIBRATION_ARMS))
    parser.add_argument("--analysis-view", choices=("full", "native_configuration_sensitivity"), default="full",
                        help="Use the full matched campaign or the predeclared native-only view of a complete original cohort.")
    args = parser.parse_args(); rows = []
    if args.analysis_view == "native_configuration_sensitivity":
        if args.behavior_arms != ",".join(BEHAVIOR_ARMS) or args.calibration_arms != ",".join(CALIBRATION_ARMS):
            parser.error("--analysis-view selects fixed arms; omit --behavior-arms and --calibration-arms")
        specifications = (
            ("behavior", "production", ("radar_only", "native_good", "native_bad")),
            ("calibration", "calibration-production", ("native_good", "native_bad")),
        )
        full_arms_by_kind = {"behavior": BEHAVIOR_ARMS, "calibration": CALIBRATION_ARMS}
    else:
        specifications = (
            ("behavior", "production", _requested_arms(args.behavior_arms, BEHAVIOR_ARMS, "behavior")),
            ("calibration", "calibration-production", _requested_arms(args.calibration_arms, CALIBRATION_ARMS, "calibration")),
        )
        full_arms_by_kind = None
    _production_gate(args.runs_root, specifications, full_arms_by_kind)
    for kind, root_name, arms in specifications:
        for block in PRODUCTION_BLOCKS:
            block_dir = args.runs_root / root_name / block
            for arm in arms:
                run = block_dir / arm
                row, trajectory = analyze_run(run, kind, block_dir.name, arm)
                rows.append(row)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "runs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r})); writer.writeheader(); writer.writerows(rows)
    (args.out / "runs.json").write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summarize(rows), indent=2, sort_keys=True), encoding="utf-8")

if __name__ == "__main__": main()
