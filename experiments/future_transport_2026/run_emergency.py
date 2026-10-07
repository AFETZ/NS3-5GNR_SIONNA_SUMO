#!/usr/bin/env python3
"""Run one preregistered emergency-warning power/block replication.

The production cohort is 14 powers by 10 blocks.  This launcher deliberately
executes one cell at a time: a failed cell keeps its directory, manifest, and
simulator log for audit and can never overwrite a previous attempt.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "experiments/future_transport_2026/PROTOCOL.md"
SUMO_FOLDER = "src/automotive/examples/sumo_files_v2v_map/"
ROUTE = ROOT / "src/automotive/examples/sumo_files_v2v_map/cars.rou.xml"
SUMO_CONFIG = ROOT / "src/automotive/examples/sumo_files_v2v_map/map.sumo.cfg"
NET = ROOT / "src/automotive/examples/sumo_files_v2v_map/map.net.xml"
REROUTER = ROOT / "src/automotive/examples/sumo_files_v2v_map/rerouter.add.xml"
SOURCE = ROOT / "src/automotive/examples/v2v-emergencyVehicleAlert-nrv2x.cc"
SENSOR_SOURCE = ROOT / "src/automotive/model/utilities/sumo-sensor.cc"
SENSOR_HEADER = ROOT / "src/automotive/model/utilities/sumo-sensor.h"
SENSOR_KINEMATICS = ROOT / "src/automotive/model/utilities/sensor-kinematics.h"
APPLICATION_SOURCE = ROOT / "src/automotive/model/Applications/emergencyVehicleAlert.cc"
TRACI_SOURCE = ROOT / "src/traci/model/traci-client.cc"
POWERS = (-20, -15, -12, -9, -6, -3, 0, 3, 6, 9, 12, 15, 18, 20)
HORIZON_SECONDS = 100
VEHICLES = tuple(f"veh{number}" for number in range(1, 21))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_directory(destination: Path, power: int, block: int, pilot: bool) -> Path:
    cohort = "pilot" if pilot else "production"
    return destination / cohort / f"power-{power:+03d}dBm" / f"block-{block:02d}"


def command(power: int, block: int, output: Path) -> list[str]:
    """Return the exact argv sent to ns-3; all stochastic seeds are explicit."""
    prefix = output / "artifacts/eva"
    args = [
        "v2v-emergencyVehicleAlert-nrv2x",
        "--sumo-gui=0", f"--sim-time={HORIZON_SECONDS}", "--sumo-updates=0.05",
        f"--sumo-folder={SUMO_FOLDER}", "--mob-trace=cars.rou.xml",
        f"--sumo-config={SUMO_FOLDER}map.sumo.cfg",
        f"--sumo-seed={block}", "--RngSeed=1", f"--RngRun={block}",
        "--penetrationRate=1", f"--txPower={power}", "--sionna=0",
        "--centralFrequencyBandSl=5890000000", "--bandwidthBandSl=400", "--numerologyBwpSl=2",
        "--send-cam=true", "--send-cpm=false", "--sensor-reaction-enable=0",
        "--incident-enable=0", "--drop-triggered-reaction-enable=0",
        f"--sumo-port={3400 + POWERS.index(power)*10 + block}", f"--csv-log={prefix}",
        f"--netstate-dump-file={output / 'artifacts/eva-netstate.xml'}",
        "--sumo-collision-action=warn", "--sumo-collision-check-junctions=1",
        "--sumo-collision-stoptime-s=1000",
        f"--sumo-collision-output={output / 'artifacts/eva-collision.xml'}",
    ]
    return [str(ROOT / "ns3"), "run", "--no-build", shlex.join(args)]


def input_hashes() -> dict[str, str]:
    inputs = (PROTOCOL, ROUTE, SUMO_CONFIG, NET, REROUTER, SOURCE,
              SENSOR_SOURCE, SENSOR_HEADER, SENSOR_KINEMATICS,
              APPLICATION_SOURCE, TRACI_SOURCE, Path(__file__).resolve())
    return {str(path.relative_to(ROOT)): digest(path) for path in inputs}


def preflight() -> None:
    for path in (ROOT / "ns3", PROTOCOL, ROUTE, SUMO_CONFIG, NET, REROUTER,
                 SOURCE, SENSOR_SOURCE, SENSOR_HEADER, SENSOR_KINEMATICS, APPLICATION_SOURCE,
                 TRACI_SOURCE,
                 ROOT.parent / "environment-manifest.json"):
        if not path.is_file():
            raise FileNotFoundError(f"Required input absent: {path}")
    if not shutil.which("sumo"):
        raise RuntimeError("SUMO is not on PATH")
    target = ROOT / "build/src/automotive/examples/ns3-dev-v2v-emergencyVehicleAlert-nrv2x-optimized"
    if not target.is_file():
        raise FileNotFoundError(f"Build the scenario before running: {target}")


def validate_route() -> None:
    vehicle_ids = [node.attrib.get("id") for node in ET.parse(ROUTE).getroot().findall("vehicle")]
    if vehicle_ids != list(VEHICLES):
        raise ValueError(f"Expected fixed vehicles {VEHICLES}; found {vehicle_ids}")


def validate_csv(path: Path, time_column: str | None) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"Missing CSV header in {path.name}")
        rows = list(reader)
        if time_column is None:
            return
        if time_column not in reader.fieldnames:
            raise ValueError(f"Missing {time_column!r} column in {path.name}")
        times = [float(row[time_column]) for row in rows if row[time_column] != ""]
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        raise ValueError(f"Non-monotone {time_column} in {path.name}")


def inspect_outputs(output: Path) -> dict[str, object]:
    artifacts = output / "artifacts"
    collision = artifacts / "eva-collision.xml"
    collision_root = ET.parse(collision).getroot()
    if collision_root.tag != "collisions":
        raise ValueError(f"Unexpected collision XML root: {collision_root.tag}")
    last_netstate_time = None
    for _, element in ET.iterparse(artifacts / "eva-netstate.xml", events=("end",)):
        if element.tag == "timestep" and "time" in element.attrib:
            last_netstate_time = float(element.attrib["time"])
        element.clear()
    if last_netstate_time is None or not HORIZON_SECONDS - 0.15 <= last_netstate_time <= HORIZON_SECONDS:
        raise ValueError(f"Netstate ended at {last_netstate_time}; expected the {HORIZON_SECONDS}s horizon")
    required = [collision, artifacts / "eva-netstate.xml", output / "simulator.log"]
    for vehicle in VEHICLES:
        for suffix, time_column in (("CAM", None), ("MSG", "tx_t_s"),
                                    ("CTRL", "time_s"), ("SENSOR", "time_s"),
                                    ("PHY", "time_s"), ("PROFILE", None)):
            path = artifacts / f"eva-{vehicle}-{suffix}.csv"
            required.append(path)
            validate_csv(path, time_column)
    simulator_log = (output / "simulator.log").read_text(errors="replace")
    if not all(f"INFO-{vehicle}," in simulator_log for vehicle in VEHICLES):
        raise ValueError("Missing vehicle completion summaries")
    if any(marker in simulator_log for marker in ("NS_FATAL_ERROR", "Segmentation fault",
                                                 "peer shutdown", "malloc_consolidate")):
        raise RuntimeError("Simulator reported a fatal error")
    band = re.search(r"NR-SIDELINK-BANDWIDTH,bandwidthBandSl_100kHz=(\d+),operation_band_hz=([\d.eE+-]+)", simulator_log)
    phy = re.search(r"NR-SIDELINK-PHY,channel_bandwidth_hz=(\d+),resource_blocks=(\d+)", simulator_log)
    if not band or not phy or int(band[1]) != 400 or abs(float(band[2]) - 40e6) > 1 or int(phy[1]) != 40_000_000 or int(phy[2]) != 53:
        raise ValueError("NR sidelink frequency-band/PHY bandwidth audit failed")
    return {
        "file_records": [{"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
                         for path in required],
        "collision_records_total": len(collision_root.findall("collision")),
    }


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_one(power: int, block: int, destination: Path, pilot: bool, timeout: int) -> Path:
    preflight()
    output = run_directory(destination, power, block, pilot)
    if output.exists():
        raise FileExistsError(f"Preserving existing run: {output}")
    output.mkdir(parents=True)
    (output / "artifacts").mkdir()
    validate_route()
    manifest: dict[str, object] = {
        "schema": 1, "status": "started", "pilot_excluded": pilot,
        "campaign": "emergency_warning_power", "power_dbm": power, "block": block,
        "planned_horizon_seconds": HORIZON_SECONDS,
        "cam_identity_policy": (
            "cam_gdt_ms wraps after 65.536s; match CAM identities with tx_id, cam_gdt_ms, "
            "and a wrap-aware time segment (or analyze non-wrapping segments separately)"
        ),
        "rng_seed": 1, "rng_run": block, "sumo_seed": block,
        "ns3_stream_assignment": "scenario AssignStreams starts at 1; see hashed source",
        "sensor_rng_seed": 1,
        "sensor_rng_run": block,
        "sensor_streams": {
            vehicle: [100000 + int(vehicle[3:]) * 8 + offset for offset in range(3)]
            for vehicle in VEHICLES
        },
        "sionna_enabled": False, "inputs": input_hashes(),
        "code_manifest": json.loads((ROOT.parent / "environment-manifest.json").read_text()),
        "runtime_environment": json.loads((ROOT.parent / "runtime-environment.json").read_text())
                               if (ROOT.parent / "runtime-environment.json").is_file() else None,
        "runtime_packages": subprocess.check_output(["python3", "-m", "pip", "freeze"],
                                                    text=True).splitlines(),
        "command": command(power, block, output),
    }
    manifest_path = output / "manifest.json"
    write_manifest(manifest_path, manifest)
    started = time.monotonic()
    try:
        with (output / "simulator.log").open("x", encoding="utf-8") as log:
            result = subprocess.run(manifest["command"], cwd=ROOT, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
        manifest["simulator_exit_code"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"ns-3 exited with {result.returncode}")
        manifest.update(inspect_outputs(output))
        manifest["status"] = "completed_pending_metric_audit"
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = repr(error)
        raise
    finally:
        manifest["wall_seconds"] = round(time.monotonic() - started, 3)
        write_manifest(manifest_path, manifest)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--power", type=int, required=True, choices=POWERS)
    parser.add_argument("--block", type=int, required=True)
    parser.add_argument("--pilot", action="store_true", help="Store outside the production cohort.")
    parser.add_argument("--out", type=Path, default=Path("/research/runs"))
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true", help="Print command only; create no files.")
    args = parser.parse_args()
    if not 1 <= args.block <= 10:
        parser.error("block must be 1..10")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    output = run_directory(args.out, args.power, args.block, args.pilot)
    if args.dry_run:
        if output.exists():
            parser.error(f"refusing existing run directory: {output}")
        validate_route()
        print(shlex.join(command(args.power, args.block, output)))
        return
    print(run_one(args.power, args.block, args.out, args.pilot, args.timeout))


if __name__ == "__main__":
    main()
