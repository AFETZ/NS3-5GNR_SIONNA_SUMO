#!/usr/bin/env python3
"""Shared helpers for the strict sidelink + Sionna thesis package."""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


class ManagedSionnaServer:
    """Launch a local Sionna server and wait until the scene is ready."""

    def __init__(self, scene_path: str | Path, log_path: str | Path, ready_timeout_s: int = 240):
        self.scene_path = resolve_repo_path(scene_path)
        self.log_path = Path(log_path)
        self.ready_timeout_s = ready_timeout_s
        self.process: subprocess.Popen[str] | None = None
        self._log_handle = None
        self._drain_thread: threading.Thread | None = None
        self._tail: list[str] = []

    def _record_line(self, line: str) -> None:
        if self._log_handle is not None:
            self._log_handle.write(line)
            self._log_handle.flush()
        self._tail.append(line.rstrip())
        if len(self._tail) > 50:
            self._tail = self._tail[-50:]

    def _drain_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._record_line(line)

    def start(self) -> None:
        if self.process is not None:
            return

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("w")
        env = os.environ.copy()
        env.setdefault("SIONNA_VERBOSE", "0")
        env.setdefault("PYTHONUNBUFFERED", "1")
        command = [str(package_root() / "scripts" / "start_sionna_server.sh"), str(self.scene_path)]
        self.process = subprocess.Popen(
            command,
            cwd=repo_root(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert self.process.stdout is not None

        ready = False
        deadline = time.time() + self.ready_timeout_s
        while time.time() < deadline:
            if self.process.poll() is not None:
                tail = "\n".join(self._tail[-20:])
                raise RuntimeError(
                    f"Sionna server exited before readiness for scene {self.scene_path}\n{tail}"
                )
            readable, _, _ = select.select([self.process.stdout], [], [], 1.0)
            if not readable:
                continue
            line = self.process.stdout.readline()
            if not line:
                continue
            self._record_line(line)
            if "Setup complete." in line:
                ready = True
                break

        if not ready:
            self.stop()
            tail = "\n".join(self._tail[-20:])
            raise TimeoutError(
                f"Sionna server was not ready after {self.ready_timeout_s}s for scene {self.scene_path}\n{tail}"
            )

        self._drain_thread = threading.Thread(target=self._drain_output, daemon=True)
        self._drain_thread.start()

    def stop(self) -> None:
        if self.process is None:
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None
            return

        if self.process.poll() is None:
            try:
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)

        if self._drain_thread is not None and self._drain_thread.is_alive():
            self._drain_thread.join(timeout=2)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self.process = None
        self._drain_thread = None

    def __enter__(self) -> "ManagedSionnaServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        return json.load(handle)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_repo_path(path_like: str | Path) -> Path:
    path = Path(path_like)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == package_root().name:
        return repo_root() / "experiments" / path
    return repo_root() / path


def resolve_repo_dir_arg(path_like: str | Path) -> str:
    resolved = str(resolve_repo_path(path_like))
    return resolved if resolved.endswith("/") else f"{resolved}/"


def load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = resolve_repo_path(manifest_path)
    defaults_file = package_root() / "manifests" / "strict_defaults.json"
    defaults = load_json(defaults_file)
    manifest = load_json(manifest_file)
    merged = deep_merge(defaults, manifest)
    merged["_manifest_path"] = str(manifest_file)
    merged["_defaults_path"] = str(defaults_file)
    merged["_repo_root"] = str(repo_root())
    validate_manifest(merged)
    return merged


def validate_manifest(manifest: dict[str, Any]) -> None:
    forbidden = [item.lower() for item in manifest.get("forbidden_patterns", [])]
    manifest_for_scan = dict(manifest)
    manifest_for_scan.pop("forbidden_patterns", None)
    serialized = json.dumps(manifest_for_scan, sort_keys=True).lower()
    for pattern in forbidden:
        if pattern and pattern in serialized:
            manifest_path = manifest.get("_manifest_path", "<manifest>")
            raise ValueError(
                f"Forbidden legacy shim '{pattern}' found in manifest {manifest_path}"
            )

    enable_sensing = manifest.get("radio", {}).get("enableSensing")
    if enable_sensing != 1 and enable_sensing is not True:
        raise ValueError("Strict manifest must set radio.enableSensing=1")


def format_cli_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def normalize_cli_list_value_args(argv: list[str], option_names: set[str]) -> list[str]:
    """Attach comma-separated numeric list values to option tokens for argparse.

    argparse treats values such as "-132,-126,-120" as a new option when they are
    passed as a separate argv token after a flag. Rewriting them to
    "--flag=-132,-126,-120" preserves the intended meaning for both manual CLI
    calls and nested subprocess forwarding.
    """

    list_value_pattern = re.compile(r"^-?\d+(?:,-?\d+)+$")
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        current = argv[index]
        if (
            current in option_names
            and index + 1 < len(argv)
            and list_value_pattern.fullmatch(argv[index + 1]) is not None
        ):
            normalized.append(f"{current}={argv[index + 1]}")
            index += 2
            continue
        normalized.append(current)
        index += 1
    return normalized


def build_behavior_args(manifest: dict[str, Any]) -> list[str]:
    run = manifest["run"]
    radio = manifest["radio"]
    behavior = manifest["behavior"]
    args = [
        f"--sumo-gui={int(run['sumo_gui'])}",
        f"--sim-time={run['sim_time_s']}",
        f"--sumo-updates={run['sumo_updates']}",
        f"--sumo-folder={resolve_repo_dir_arg(manifest['sumo_folder'])}",
        f"--mob-trace={manifest['mob_trace']}",
        "--met-sup=1",
        f"--penetrationRate={format_cli_value(behavior.get('penetrationRate', 1))}",
        f"--txPower={radio['txPower']}",
        f"--RngRun={run['rng_run']}",
        f"--sumo-seed={run['sumo_seed']}",
        f"--sionna={int(run['sionna_enabled'])}",
        f"--sionna-local-machine={int(run['sionna_local_machine'])}",
        f"--sionna-server-ip={run['sionna_server_ip']}",
        f"--sionna-verbose={int(run['sionna_verbose'])}",
        f"--sumo-config={resolve_repo_path(manifest['sumo_config'])}",
    ]

    ordered_radio_keys = [
        "centralFrequencyBandSl",
        "bandwidthBandSl",
        "tddPattern",
        "slBitMap",
        "numerologyBwpSl",
        "slSensingWindow",
        "slSelectionWindow",
        "slSubchannelSize",
        "slMaxNumPerReserve",
        "slProbResourceKeep",
        "slMaxTxTransNumPssch",
        "ReservationPeriod",
        "enableSensing",
        "t1",
        "t2",
        "slThresPsschRsrp",
        "mcs",
        "enableChannelRandomness",
        "channelUpdatePeriod",
    ]
    for key in ordered_radio_keys:
        if key in radio:
            args.append(f"--{key}={format_cli_value(radio[key])}")

    for key, value in behavior.items():
        if key == "penetrationRate":
            continue
        args.append(f"--{key}={format_cli_value(value)}")
    return args


def build_metrics_args(manifest: dict[str, Any], out_prefix: Path) -> list[str]:
    run = manifest["run"]
    radio = manifest["radio"]
    args = [
        f"--sim-time={run['sim_time_s']}",
        f"--sumo-gui={int(run['sumo_gui'])}",
        f"--sumo-config={resolve_repo_path(manifest['sumo_config'])}",
        f"--sumo-folder={resolve_repo_dir_arg(manifest['sumo_folder'])}",
        f"--mob-trace={manifest['mob_trace']}",
        f"--sumo-port={36000 + int(run['rng_run'])}",
        f"--sumo-seed={run['sumo_seed']}",
        f"--out-prefix={out_prefix}",
        f"--sionna={int(run['sionna_enabled'])}",
        f"--sionna-server-ip={run['sionna_server_ip']}",
        f"--sionna-local-machine={int(run['sionna_local_machine'])}",
        f"--sionna-verbose={int(run['sionna_verbose'])}",
        f"--centralFrequencyBandSl={radio['centralFrequencyBandSl']}",
        f"--bandwidthBandSl={radio['bandwidthBandSl']}",
        f"--txPower={radio['txPower']}",
        f"--tddPattern={radio['tddPattern']}",
        f"--slBitMap={radio['slBitMap']}",
        f"--numerologyBwpSl={radio['numerologyBwpSl']}",
        f"--slSensingWindow={radio['slSensingWindow']}",
        f"--slSelectionWindow={radio['slSelectionWindow']}",
        f"--slSubchannelSize={radio['slSubchannelSize']}",
        f"--slMaxNumPerReserve={radio['slMaxNumPerReserve']}",
        f"--slProbResourceKeep={radio['slProbResourceKeep']}",
        f"--slMaxTxTransNumPssch={radio['slMaxTxTransNumPssch']}",
        f"--ReservationPeriod={radio['ReservationPeriod']}",
        f"--enableSensing={radio['enableSensing']}",
        f"--t1={radio['t1']}",
        f"--t2={radio['t2']}",
        f"--slThresPsschRsrp={radio['slThresPsschRsrp']}",
        f"--mcs={radio['mcs']}",
        f"--enableChannelRandomness={radio['enableChannelRandomness']}",
        f"--channelUpdatePeriod={radio['channelUpdatePeriod']}",
    ]
    return args


def list_manifests_for_scenario(scenario: str) -> list[Path]:
    manifest_dir = package_root() / "manifests" / scenario
    if not manifest_dir.exists():
        raise FileNotFoundError(f"Unknown scenario manifest directory: {manifest_dir}")
    return sorted(manifest_dir.glob("*.json"))


def manifest_run_dir(out_root: Path, manifest: dict[str, Any], seed: int | None = None) -> Path:
    scenario_id = manifest["scenario_id"]
    mode = manifest["mode"]
    chosen_seed = seed if seed is not None else int(manifest["run"]["rng_run"])
    return out_root / scenario_id / mode / f"seed-{chosen_seed:03d}"
