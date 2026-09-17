#!/usr/bin/env python3
"""Plan and safely resume the preregistered production simulation matrix.

The scheduler never creates a pilot, never removes a run directory, and stops
after the first failed or non-resumable run.  Sionna jobs are serialized because
the existing runner owns UDP 8103; jobs without Sionna may use ``--workers``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESEARCH = Path("/research")
RUNS = RESEARCH / "runs"
EMERGENCY = HERE / "run_emergency.py"
CAMPAIGN = HERE / "run_campaign.py"
VALID_STATUS = "completed_pending_metric_audit"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jobs_for(campaign: str) -> list[dict[str, object]]:
    """Use seed blocks as the outer loop for a stable, auditable order."""
    emergency = load_module("matrix_emergency", EMERGENCY)
    intersection = load_module("matrix_intersection", CAMPAIGN)
    jobs: list[dict[str, object]] = []
    if campaign in ("emergency", "all"):
        for block in range(1, 11):
            for power in emergency.POWERS:
                out = RUNS / "production" / f"power-{power:+03d}dBm" / f"block-{block:02d}"
                jobs.append({"campaign": "emergency", "block": block, "power_dbm": power,
                             "sionna": False, "out": str(out),
                             "argv": ["python3", str(EMERGENCY), "--power", str(power), "--block",
                                      str(block), "--out", str(RUNS)]})
    if campaign in ("behavioral", "all"):
        for block in range(1, 31):
            for arm in intersection.ARMS:
                out = RUNS / "production" / f"block-{block:02d}" / arm
                jobs.append({"campaign": "behavioral", "block": block, "arm": arm,
                             "sionna": bool(intersection.ARMS[arm]["sionna"]), "out": str(out),
                             "argv": ["python3", str(CAMPAIGN), "--arm", arm, "--block", str(block),
                                      "--out", str(RUNS)]})
    if campaign in ("calibration", "all"):
        arms = tuple(arm for arm in intersection.ARMS if arm != "radar_only")
        for block in range(1, 31):
            for arm in arms:
                out = RUNS / "calibration-production" / f"block-{block:02d}" / arm
                jobs.append({"campaign": "calibration", "block": block, "arm": arm,
                             "sionna": bool(intersection.ARMS[arm]["sionna"]), "out": str(out),
                             "argv": ["python3", str(CAMPAIGN), "--arm", arm, "--block", str(block),
                                      "--calibration", "--out", str(RUNS)]})
    for ordinal, job in enumerate(jobs, 1):
        job["ordinal"] = ordinal
    return jobs


def input_hashes() -> dict[str, str]:
    emergency = load_module("matrix_emergency_hashes", EMERGENCY)
    intersection = load_module("matrix_intersection_hashes", CAMPAIGN)
    paths = set()
    # Keep this list identical to the campaign runner's manifest inputs.
    paths.update(Path(ROOT / name) for name in emergency.input_hashes())
    paths.update((intersection.SCENE, intersection.ROUTE, intersection.SUMO_CONFIG,
                  intersection.NET, intersection.SERVER, HERE / "PROTOCOL.md", CAMPAIGN,
                  HERE / "scene.manifest.json", ROOT / "src/automotive/model/utilities/sumo-sensor.cc",
                  ROOT / "src/automotive/model/utilities/sumo-sensor.h",
                  ROOT / "src/automotive/model/Applications/emergencyVehicleAlert.cc",
                  ROOT / "src/automotive/examples/v2v-emergencyVehicleAlert-nrv2x.cc"))
    paths.update((HERE / "meshes").glob("*.ply"))
    absent = sorted(str(path) for path in paths if not path.is_file())
    if absent:
        raise FileNotFoundError("Required scheduler input absent: " + ", ".join(absent))
    return {str(path.relative_to(ROOT)): sha256(path) for path in sorted(paths)}


def runtime_preflight(strict: bool) -> dict[str, object]:
    errors: list[str] = []
    try:
        hashes = input_hashes()
    except Exception as error:  # retain the plan for review even outside the container
        hashes, errors = {}, [repr(error)]
    environment = RESEARCH / "environment-manifest.json"
    runtime = RESEARCH / "runtime-environment.json"
    runtime_data = None
    for path, label in ((environment, "environment manifest"), (runtime, "runtime manifest")):
        if not path.is_file():
            errors.append(f"Missing {label}: {path}")
        else:
            try:
                if path == runtime:
                    runtime_data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                errors.append(f"Invalid {label}: {error}")
    image_id = os.environ.get("CONTAINER_IMAGE_ID")
    if not image_id and isinstance(runtime_data, dict):
        image_id = next((str(runtime_data[key]) for key in ("container_image_id", "image_id", "image_digest")
                         if runtime_data.get(key)), None)
    if not image_id:
        errors.append("Container image ID is required in CONTAINER_IMAGE_ID or runtime-environment.json")
    record = {"checked_at_unix": time.time(), "source_hashes": hashes,
              "environment_manifest_sha256": sha256(environment) if environment.is_file() else None,
              "runtime_manifest_sha256": sha256(runtime) if runtime.is_file() else None,
              "container_image_id": image_id, "errors": errors}
    if strict and errors:
        raise RuntimeError("Production preflight failed: " + "; ".join(errors))
    return record


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def artifact_path(out: Path, name: str) -> Path:
    return out / name if name in {"simulator.log", "sionna-server.log"} else out / "artifacts" / name


def audit_completed(job: dict[str, object], expected: dict[str, str]) -> tuple[bool, str]:
    out = Path(str(job["out"]))
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        return False, "existing directory has no manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return False, f"invalid manifest: {error}"
    if manifest.get("status") != VALID_STATUS:
        return False, f"manifest status is {manifest.get('status')!r}, expected {VALID_STATUS!r}"
    recorded = manifest.get("inputs")
    if not isinstance(recorded, dict) or not recorded:
        return False, "manifest has no input hashes"
    if any(expected.get(path) != digest for path, digest in recorded.items()):
        return False, "manifest input hashes do not match the current preflight"
    records = manifest.get("file_records")
    if not isinstance(records, list) or not records:
        return False, "manifest has no file hash records"
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            return False, "invalid file hash record"
        path = artifact_path(out, record["name"])
        if not path.is_file() or path.stat().st_size != record.get("bytes") or sha256(path) != record.get("sha256"):
            return False, f"artifact audit failed: {path}"
    return True, "completed and audited"


def progress_for(jobs: list[dict[str, object]], expected: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    states, blockers = {}, []
    for job in jobs:
        out = Path(str(job["out"]))
        key = str(job["ordinal"])
        if not out.exists():
            states[key] = "pending"
            continue
        valid, reason = audit_completed(job, expected)
        states[key] = "completed" if valid else "blocked_existing"
        if not valid:
            blockers.append(f"job {key}: {reason} ({out})")
    return states, blockers


def run_job(job: dict[str, object]) -> tuple[int, str]:
    result = subprocess.run(job["argv"], cwd=ROOT, text=True, capture_output=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", choices=("emergency", "behavioral", "calibration", "all"), required=True)
    parser.add_argument("--plan", "--dry-run", action="store_true", dest="plan", help="Write/review the job plan; launch nothing.")
    parser.add_argument("--execute", action="store_true", help="Launch the production plan after preflight.")
    parser.add_argument("--workers", type=int, default=2, help="Maximum concurrent non-Sionna runner processes.")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.plan and args.execute:
        parser.error("--plan and --execute cannot be combined")
    jobs = jobs_for(args.campaign)
    plan_path = RESEARCH / f"run_matrix.{args.campaign}.plan.json"
    progress_path = RESEARCH / f"run_matrix.{args.campaign}.progress.json"
    preflight = runtime_preflight(strict=args.execute)
    plan = {"schema": 1, "campaign": args.campaign, "jobs": jobs, "preflight": preflight}
    if plan_path.exists():
        existing = json.loads(plan_path.read_text(encoding="utf-8"))
        if existing.get("jobs") != jobs:
            raise RuntimeError(f"Existing plan differs; preserve it and choose a new campaign path: {plan_path}")
    else:
        atomic_json(plan_path, plan)
    states, blockers = progress_for(jobs, preflight["source_hashes"])
    progress = {"schema": 1, "campaign": args.campaign, "plan": str(plan_path), "updated_at_unix": time.time(),
                "states": states, "blockers": blockers}
    atomic_json(progress_path, progress)
    if args.plan or not args.execute:
        print(json.dumps({"plan": str(plan_path), "progress": str(progress_path), "jobs": len(jobs),
                          "preflight_errors": preflight["errors"],
                          "launch": "use --execute after review"}, indent=2))
        return
    if blockers:
        raise RuntimeError("Refusing to overwrite existing run directories:\n" + "\n".join(blockers))
    running: dict[object, dict[str, object]] = {}
    failed: list[str] = []
    pending = [job for job in jobs if states[str(job["ordinal"])] == "pending"]
    with ThreadPoolExecutor(max_workers=args.workers + 1) as pool:
        while pending or running:
            launched = False
            sionna_active = any(bool(job["sionna"]) for job in running.values())
            native_active = sum(not bool(job["sionna"]) for job in running.values())
            for job in list(pending):
                if failed:
                    break
                if not job["sionna"] and native_active >= args.workers:
                    continue
                if job["sionna"] and sionna_active:
                    continue
                future = pool.submit(run_job, job)
                running[future] = job
                pending.remove(job)
                states[str(job["ordinal"])] = "running"
                if job["sionna"]:
                    sionna_active = True
                else:
                    native_active += 1
                launched = True
            if not running:
                if pending and not failed:
                    raise RuntimeError("Scheduler cannot launch pending jobs")
                break
            if not launched or failed:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    job = running.pop(future)
                    code, output = future.result()
                    key = str(job["ordinal"])
                    valid, reason = audit_completed(job, preflight["source_hashes"])
                    states[key] = "completed" if code == 0 and valid else "failed"
                    if states[key] == "failed":
                        failed.append(f"job {key} exited {code}: {reason}; output: {output[-1000:]}")
                    progress.update({"updated_at_unix": time.time(), "states": states, "failures": failed})
                    atomic_json(progress_path, progress)
    if failed:
        raise RuntimeError("Campaign stopped after failure; existing outputs were preserved:\n" + "\n".join(failed))
    print(f"Completed {len(jobs)} planned jobs. Plan: {plan_path}; progress: {progress_path}")


if __name__ == "__main__":
    main()
