#!/usr/bin/env python3
"""Plan and safely resume the isolated V2V-Urban intersection replication.

This robustness cohort is deliberately separate from the frozen highway cohort.
It has 30 paired blocks: five behavioral arms and four radio-calibration arms.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CAMPAIGN = HERE / "run_campaign.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def jobs_for(runs: Path, campaign) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    for block in range(1, 31):
        for arm in campaign.ARMS:
            out = runs / "production" / f"block-{block:02d}" / arm
            jobs.append({"campaign": "urban_behavioral", "block": block, "arm": arm,
                         "sionna": bool(campaign.ARMS[arm]["sionna"]), "out": str(out),
                         "argv": ["python3", str(CAMPAIGN), "--arm", arm, "--block", str(block),
                                  "--out", str(runs), "--channel-scenario=V2V-Urban"]})
        for arm in (name for name in campaign.ARMS if name != "radar_only"):
            out = runs / "calibration-production" / f"block-{block:02d}" / arm
            jobs.append({"campaign": "urban_calibration", "block": block, "arm": arm,
                         "sionna": bool(campaign.ARMS[arm]["sionna"]), "out": str(out),
                         "argv": ["python3", str(CAMPAIGN), "--arm", arm, "--block", str(block),
                                  "--calibration", "--out", str(runs), "--channel-scenario=V2V-Urban"]})
    for ordinal, job in enumerate(jobs, 1):
        job["ordinal"] = ordinal
    return jobs


def urban_manifest_valid(out: Path) -> tuple[bool, str]:
    """Require the native-model and building audit before accepting a replicate."""
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return False, f"invalid urban manifest: {error}"
    if manifest.get("channel_scenario") != "V2V-Urban":
        return False, "manifest channel scenario is not V2V-Urban"
    if manifest.get("geometry_version") != "grounded_vehicle_v2":
        return False, "manifest geometry version is not grounded_vehicle_v2"
    audit = manifest.get("channel_audit")
    if not isinstance(audit, dict) or audit.get("model") != "V2V-Urban" or audit.get("buildings_registered") != 2:
        return False, "manifest lacks valid V2V-Urban building audit"
    geometry = manifest.get("scene_geometry")
    if not isinstance(geometry, dict) or len(geometry.get("buildings", [])) != 2:
        return False, "manifest lacks two-building scene geometry"
    if str(manifest.get("arm", "")).startswith("sionna_"):
        expected = {"antenna_z_m": 1.5, "mesh_center_z_m": 0.65, "mesh_height_m": 1.3}
        if manifest.get("sionna_geometry_audit") != expected:
            return False, "manifest lacks corrected Sionna antenna/mesh audit"
    return True, "urban channel and geometry audited"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=Path("/research"),
                        help="Mounted root of the dedicated nr-v2x-urban-study volume.")
    parser.add_argument("--plan", "--dry-run", action="store_true", dest="plan")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.plan and args.execute:
        parser.error("--plan and --execute cannot be combined")
    if args.workers < 1:
        parser.error("--workers must be positive")

    scheduler = load("urban_scheduler", HERE / "run_matrix.py")
    campaign = load("urban_campaign", CAMPAIGN)
    research, runs = args.research_root, args.research_root / "runs"
    scheduler.RESEARCH, scheduler.RUNS = research, runs
    base_audit = scheduler.audit_completed
    def audit_completed(job, expected):
        valid, reason = base_audit(job, expected)
        if not valid:
            return valid, reason
        return urban_manifest_valid(Path(str(job["out"])))
    scheduler.audit_completed = audit_completed
    jobs = jobs_for(runs, campaign)
    plan_path = research / "run_matrix.urban.plan.json"
    progress_path = research / "run_matrix.urban.progress.json"
    preflight = scheduler.runtime_preflight(strict=args.execute)
    preflight["urban_channel"] = "V2V-Urban"
    plan = {"schema": 1, "campaign": "urban", "jobs": jobs, "preflight": preflight,
            "channel_scenario": "V2V-Urban", "geometry_version": "grounded_vehicle_v2",
            "geometry_manifest": str(HERE / "scene.manifest.json")}
    if plan_path.exists():
        existing = json.loads(plan_path.read_text(encoding="utf-8"))
        if (existing.get("jobs") != jobs or existing.get("channel_scenario") != "V2V-Urban"
                or existing.get("geometry_version") != "grounded_vehicle_v2"):
            raise RuntimeError(f"Existing urban plan differs; outputs are preserved: {plan_path}")
    else:
        scheduler.atomic_json(plan_path, plan)
    states, blockers = scheduler.progress_for(jobs, preflight["source_hashes"])
    progress = {"schema": 1, "campaign": "urban", "plan": str(plan_path), "updated_at_unix": time.time(),
                "states": states, "blockers": blockers}
    scheduler.atomic_json(progress_path, progress)
    if args.plan or not args.execute:
        print(json.dumps({"plan": str(plan_path), "progress": str(progress_path), "jobs": len(jobs),
                          "preflight_errors": preflight["errors"], "launch": "use --execute after review"}, indent=2))
        return
    if blockers:
        raise RuntimeError("Refusing to overwrite existing urban run directories:\n" + "\n".join(blockers))
    running, failed = {}, []
    pending = [job for job in jobs if states[str(job["ordinal"])] == "pending"]
    with ThreadPoolExecutor(max_workers=args.workers + 1) as pool:
        while pending or running:
            launched = False
            sionna_active = any(bool(job["sionna"]) for job in running.values())
            native_active = sum(not bool(job["sionna"]) for job in running.values())
            for job in list(pending):
                if failed:
                    break
                if job["sionna"] and sionna_active:
                    continue
                if not job["sionna"] and native_active >= args.workers:
                    continue
                future = pool.submit(scheduler.run_job, job)
                running[future] = job; pending.remove(job); states[str(job["ordinal"])] = "running"
                sionna_active = sionna_active or bool(job["sionna"])
                native_active += not bool(job["sionna"]); launched = True
            if not running:
                break
            if not launched or failed:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    job = running.pop(future); code, output = future.result(); key = str(job["ordinal"])
                    valid, reason = scheduler.audit_completed(job, preflight["source_hashes"])
                    states[key] = "completed" if code == 0 and valid else "failed"
                    if states[key] == "failed":
                        failed.append(f"job {key} exited {code}: {reason}; output: {output[-1000:]}")
                    progress.update({"updated_at_unix": time.time(), "states": states, "failures": failed})
                    scheduler.atomic_json(progress_path, progress)
    if failed:
        raise RuntimeError("Urban campaign stopped after failure; outputs were preserved:\n" + "\n".join(failed))
    print(f"Completed {len(jobs)} urban jobs. Plan: {plan_path}; progress: {progress_path}")


if __name__ == "__main__":
    main()
