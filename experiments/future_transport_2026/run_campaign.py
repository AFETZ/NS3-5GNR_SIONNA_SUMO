#!/usr/bin/env python3
"""Execute preregistered intersection arms with a durable run manifest.

Use one process at a time so SUMO/Sionna ports and cached scene state never
leak between runs. The pilot is excluded from paper estimates by construction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SCENE = ROOT / "experiments/future_transport_2026/scene.xml"
ROUTE = ROOT / "experiments/intersection_radar_comm/sumo/cars_intersection_radar_link.rou.xml"
SUMO_CONFIG = ROOT / "experiments/intersection_radar_comm/sumo/map_intersection_radar_link.sumo.cfg"
NET = ROOT / "src/automotive/examples/sumo_files_v2i_map/map.net.xml"
SERVER = ROOT / "src/sionna/sionna_v1_server_script.py"
ARMS = {
    "radar_only": {"sionna": False, "send_cam": False, "equiv_dbm": 23},
    "sionna_good": {"sionna": True, "send_cam": True, "equiv_dbm": 23},
    "sionna_bad": {"sionna": True, "send_cam": True, "equiv_dbm": -30},
    "native_good": {"sionna": False, "send_cam": True, "equiv_dbm": 23},
    "native_bad": {"sionna": False, "send_cam": True, "equiv_dbm": -30},
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight(arm):
    for path in (ROOT / "ns3", SCENE, ROUTE, SUMO_CONFIG, NET, SERVER,
                 ROOT / "src/traci/model/traci-client.cc",
                 ROOT / "experiments/future_transport_2026/PROTOCOL.md",
                 ROOT / "experiments/future_transport_2026/scene.manifest.json",
                 ROOT.parent / "environment-manifest.json"):
        if not path.is_file():
            raise FileNotFoundError(f"Required input absent: {path}")
    if not shutil.which("sumo"):
        raise RuntimeError("SUMO is not on PATH")
    target = ROOT / "build/src/automotive/examples/ns3-dev-v2v-emergencyVehicleAlert-nrv2x-optimized"
    if not target.is_file():
        raise FileNotFoundError(f"Build the example before running: {target}")
    if ARMS[arm]["sionna"]:
        if not (ROOT.parent / "runtime-environment.json").is_file():
            raise FileNotFoundError("Missing runtime GPU/OptiX provenance manifest")
        subprocess.run(["python3", "-c", "import sionna.rt, mitsuba"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ready_udp(port):
    # UDP has no listener handshake; check the process and /proc bind table.
    value = f"{port:04X}"
    for name in ("/proc/net/udp", "/proc/net/udp6"):
        for line in Path(name).read_text().splitlines()[1:]:
            if line.split()[1].endswith(":" + value):
                return True
    return False


def command(arm, block, out, calibration=False):
    cfg = ARMS[arm]
    prefix = out / "artifacts/eva"
    args = [
        "v2v-emergencyVehicleAlert-nrv2x",
        "--sumo-gui=0", "--sim-time=20", "--sumo-updates=0.05",
        "--sumo-folder=experiments/intersection_radar_comm/sumo/",
        "--mob-trace=cars_intersection_radar_link.rou.xml",
        "--sumo-config=experiments/intersection_radar_comm/sumo/map_intersection_radar_link.sumo.cfg",
        f"--sumo-seed={block}", f"--RngRun={block}", "--RngSeed=1",
        "--met-sup=1", "--penetrationRate=1", "--txPower=23",
        "--centralFrequencyBandSl=5890000000", "--bandwidthBandSl=400", "--numerologyBwpSl=2",
        f"--sionna={int(cfg['sionna'])}", "--sionna-local-machine=1",
        "--sionna-server-ip=127.0.0.1", "--sionna-verbose=0",
        f"--send-cam={'true' if cfg['send_cam'] else 'false'}", "--send-cpm=false",
        f"--per-vehicle-prr-profile=veh2:0.0:23,veh3:0.0:{cfg['equiv_dbm']}",
        f"--cam-reaction-distance-m={0 if calibration else 95}", "--cam-reaction-heading-deg=140",
        "--cam-reaction-target-lane=0", "--cam-reaction-speed-factor-target-lane=0.08",
        "--cam-reaction-speed-factor-other-lane=0.08", "--cam-reaction-action-duration-s=4.0",
        "--reaction-force-lane-change-enable=0", "--cpm-reaction-distance-m=0",
        "--cpm-reaction-ttc-s=0", f"--sensor-reaction-enable={0 if calibration else 1}",
        "--sensor-reaction-distance-m=14", "--sensor-reaction-ttc-s=1.0",
        "--sensor-reaction-focus-vehicle-id=veh2", "--sensor-reaction-period-ms=50",
        "--sensor-range-m=30", "--drop-triggered-reaction-enable=0",
        "--rx-drop-prob-cam=0", "--rx-drop-prob-cpm=0",
        "--rx-drop-prob-phy-cam=0", "--rx-drop-prob-phy-cpm=0",
        "--target-loss-profile-enable=0", "--incident-enable=0", "--crash-mode-enable=0",
        f"--sumo-port={(40000 if calibration else 30000) + block*10 + list(ARMS).index(arm)}",
        f"--csv-log={prefix}",
        f"--netstate-dump-file={out / 'artifacts/eva-netstate.xml'}",
        "--sumo-collision-action=warn", "--sumo-collision-check-junctions=1",
        "--sumo-collision-stoptime-s=1000",
        f"--sumo-collision-output={out / 'artifacts/eva-collision.xml'}",
    ]
    return [str(ROOT / "ns3"), "run", "--no-build", shlex.join(args)]


def start_server(out, timeout=120):
    log = (out / "sionna-server.log").open("w", encoding="utf-8")
    env = dict(os.environ, SIONNA_MI_VARIANT="cuda_ad_mono_polarized", CUDA_VISIBLE_DEVICES="0")
    cmd = ["python3", str(SERVER), "--path-to-xml-scenario", str(SCENE),
           "--local-machine", "--gpu", "1", "--seed", "42", "--port", "8103",
           "--frequency", "5890000000", "--bw", "40000000", "--max-depth", "5"]
    process = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if process.poll() is not None:
            log.close()
            raise RuntimeError(f"Sionna exited during setup: {process.returncode}")
        if ready_udp(8103):
            return process, log, cmd
        time.sleep(.2)
    process.terminate()
    log.close()
    raise TimeoutError("Sionna did not bind UDP 8103")


def stop_server(process, log):
    if process is None:
        return
    if process.poll() is None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"SHUTDOWN_SIONNA", ("127.0.0.1", 8103))
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
    log.close()


def inspect_outputs(out, arm):
    artifacts = out / "artifacts"
    collision = artifacts / "eva-collision.xml"
    root = ET.parse(collision).getroot()
    if root.tag != "collisions":
        raise ValueError(f"Unexpected collision XML root: {root.tag}")
    required = [artifacts / f"eva-{name}-{kind}.csv" for name in ("veh2", "veh3")
                for kind in ("CAM", "MSG", "CTRL", "SENSOR", "PHY", "PROFILE")]
    required += [collision, artifacts / "eva-netstate.xml", out / "simulator.log"]
    if ARMS[arm]["sionna"]:
        required.append(out / "sionna-server.log")
    absent = [str(path) for path in required if not path.exists()]
    if absent:
        raise FileNotFoundError(f"Required logs absent: {absent}")
    netstate = ET.parse(artifacts / "eva-netstate.xml").getroot()
    if netstate.tag != "netstate" or not len(netstate):
        raise ValueError("SUMO netstate is missing or empty")
    final_time = float(netstate[-1].get("time", "nan"))
    if final_time < 19.9 or final_time > 20.0:
        raise ValueError(f"SUMO did not reach the 20 s horizon: {final_time}")
    simulator_log = (out / "simulator.log").read_text(errors="replace")
    if not all(f"INFO-{vehicle}," in simulator_log for vehicle in ("veh2", "veh3")):
        raise ValueError("Missing vehicle completion summaries")
    if any(marker in simulator_log for marker in ("NS_FATAL_ERROR", "Segmentation fault",
                                                 "peer shutdown", "malloc_consolidate")):
        raise RuntimeError("Simulator reported a fatal error")
    if "Retrying Sionna request" in simulator_log:
        raise RuntimeError("Sionna request needed a retry")
    band = re.search(r"NR-SIDELINK-BANDWIDTH,bandwidthBandSl_100kHz=(\d+),operation_band_hz=([\d.eE+-]+)", simulator_log)
    phy = re.search(r"NR-SIDELINK-PHY,channel_bandwidth_hz=(\d+),resource_blocks=(\d+)", simulator_log)
    if not band or not phy or int(band[1]) != 400 or abs(float(band[2]) - 40e6) > 1 or int(phy[1]) != 40_000_000 or int(phy[2]) != 53:
        raise ValueError("NR sidelink frequency-band/PHY bandwidth audit failed")
    noise_figures = {}
    for vehicle, equivalent_dbm, base_db, effective_db in re.findall(
        r"PER-VEHICLE-EQUIV-DBM-APPLIED,id=(veh[23]),equiv_tx_power_dbm=([\d.+-]+),"
        r"base_noise_figure_db=([\d.+-]+),new_noise_figure_db=([\d.+-]+)", simulator_log
    ):
        if vehicle in noise_figures:
            raise ValueError(f"Duplicate receiver noise figure for {vehicle}")
        equivalent_dbm, base_db, effective_db = map(float, (equivalent_dbm, base_db, effective_db))
        expected_equivalent = 23 if vehicle == "veh2" else ARMS[arm]["equiv_dbm"]
        expected_effective = max(0.0, base_db + 23.0 - expected_equivalent)
        if abs(equivalent_dbm - expected_equivalent) > 1e-6 or abs(effective_db - expected_effective) > 1e-6:
            raise ValueError(f"Unexpected receiver noise figure for {vehicle}: {effective_db}")
        noise_figures[vehicle] = {"equiv_tx_power_dbm": equivalent_dbm,
                                  "base_noise_figure_db": base_db,
                                  "effective_noise_figure_db": effective_db,
                                  "additional_noise_figure_db": effective_db - base_db}
    if set(noise_figures) != {"veh2", "veh3"}:
        raise ValueError(f"Receiver noise-figure audit incomplete: {noise_figures}")
    sionna_audit = None
    if ARMS[arm]["sionna"]:
        log_text = (out / "sionna-server.log").read_text(errors="replace")
        if any(marker in log_text for marker in ("Traceback (most recent call last)",
                                                "Error encountered for source", "EXCEPTION -")):
            raise RuntimeError("Sionna server reported an error")
        gain_status = {}
        valid_paths = []
        for line in log_text.splitlines():
            if line.startswith("SIONNA_AUDIT kind=path_gain "):
                status = next((token.split("=", 1)[1] for token in line.split()
                               if token.startswith("status=")), "unknown")
                gain_status[status] = gain_status.get(status, 0) + 1
            elif line.startswith("SIONNA_AUDIT kind=ray_solve "):
                valid_paths.append(int(next(token.split("=", 1)[1] for token in line.split()
                                            if token.startswith("valid_paths="))))
        if not gain_status.get("ok") or not valid_paths or gain_status.get("invalid"):
            raise ValueError(f"Unusable Sionna audit: gains={gain_status}, solves={len(valid_paths)}")
        sionna_audit = {"path_gain_status_counts": gain_status,
                        "path_gain_request_count": sum(gain_status.values()),
                        "no_ray_sentinel_fraction": gain_status.get("no_path", 0) / sum(gain_status.values()),
                        "ray_solve_count": len(valid_paths),
                        "min_valid_paths_per_solve": min(valid_paths),
                        "max_valid_paths_per_solve": max(valid_paths)}
    records = [{"name": path.name, "sha256": digest(path), "bytes": path.stat().st_size}
               for path in required]
    return {"file_records": records, "collision_records_total": len(root.findall("collision")),
            "final_netstate_time_s": final_time, "noise_figure_audit": noise_figures,
            "sionna_audit": sionna_audit}


def run_one(arm, block, destination, pilot, deadline, calibration=False):
    preflight(arm)
    if calibration and arm == "radar_only":
        raise ValueError("Radar-only has no CAM link to calibrate")
    cohort = "calibration-pilot" if pilot else "calibration-production"
    if not calibration:
        cohort = "pilot" if pilot else "production"
    out = destination / cohort / f"block-{block:02d}" / arm
    if out.exists():
        raise FileExistsError(f"Preserving existing run: {out}")
    (out / "artifacts").mkdir(parents=True)
    if len(ET.parse(ROUTE).getroot().findall("vehicle")) != 2:
        raise ValueError("Expected exactly two SUMO vehicles")
    manifest = {
        "schema": 1, "status": "started", "pilot_excluded": bool(pilot), "arm": arm, "block": block,
        "cohort": "radio_calibration" if calibration else "behavioral_intersection",
        "rng_seed": 1, "rng_run": block, "sumo_seed": block,
        "sensor_streams": {str(i): [100000+i*8+j for j in range(3)] for i in (2,3)},
        "sionna_seed": 42 if ARMS[arm]["sionna"] else None,
        "code_manifest": json.loads((ROOT.parent / "environment-manifest.json").read_text()),
        "runtime_environment": json.loads((ROOT.parent / "runtime-environment.json").read_text())
                               if (ROOT.parent / "runtime-environment.json").is_file() else None,
        "runtime_packages": subprocess.check_output(["python3", "-m", "pip", "freeze"],
                                                    text=True).splitlines(),
        "inputs": {str(path.relative_to(ROOT)): digest(path) for path in (SCENE, ROUTE, SUMO_CONFIG, NET, SERVER,
                      ROOT / "experiments/future_transport_2026/PROTOCOL.md", Path(__file__).resolve(),
                      ROOT / "experiments/future_transport_2026/scene.manifest.json",
                      ROOT / "src/automotive/model/utilities/sumo-sensor.cc",
                      ROOT / "src/automotive/model/utilities/sumo-sensor.h",
                      ROOT / "src/automotive/model/utilities/sensor-kinematics.h",
                      ROOT / "src/automotive/model/Applications/emergencyVehicleAlert.cc",
                      ROOT / "src/traci/model/traci-client.cc",
                      ROOT / "src/automotive/examples/v2v-emergencyVehicleAlert-nrv2x.cc",
                      *(ROOT / "experiments/future_transport_2026/meshes").glob("*.ply"))},
        "analysis_window_s": [2.,5.] if calibration else [0.,20.],
        "command": command(arm,block,out,calibration),
    }
    path = out / "manifest.json"
    def write():path.write_text(json.dumps(manifest,indent=2,default=str)+"\n",encoding="utf-8")
    write()
    server = log = None
    begin = time.monotonic()
    try:
        if ARMS[arm]["sionna"]:
            server, log, server_command = start_server(out)
            manifest["sionna_server_command"] = server_command
        with (out / "simulator.log").open("w", encoding="utf-8") as stream:
            result = subprocess.run(manifest["command"],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=deadline)
        manifest["simulator_exit_code"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"ns-3 exit {result.returncode}")
        stop_server(server, log)
        server = log = None
        manifest.update(inspect_outputs(out,arm))
        manifest["status"] = "completed_pending_metric_audit"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = repr(exc)
        raise
    finally:
        stop_server(server,log)
        manifest["wall_seconds"] = round(time.monotonic()-begin,3)
        write()
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--block", type=int, required=True)
    parser.add_argument("--out", type=Path, default=Path("/research/runs"))
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--calibration", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.block <= 30:
        parser.error("block must be 1..30")
    if args.calibration and args.arm == "radar_only":
        parser.error("Radar-only has no CAM link to calibrate")
    if args.dry_run:
        print(shlex.join(command(args.arm,args.block,args.out,args.calibration)))
    else:
        print(run_one(args.arm,args.block,args.out,args.pilot,args.timeout,args.calibration))


if __name__ == "__main__":
    main()
