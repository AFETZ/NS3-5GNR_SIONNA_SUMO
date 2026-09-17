#!/usr/bin/env python3
"""Create a deterministic, verified archive of the completed 2026 study.

This is deliberately a post-production command.  It refuses any partial,
failed, pilot, or unaudited cohort before writing an archive.  The archive
contains raw run directories, summaries and figures supplied by the analysis
step, frozen provenance, and the source/configuration files needed to inspect
the study.  It never copies Docker volumes or NVIDIA/OptiX runtime binaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile


STATUS = "completed_pending_metric_audit"
EXPECTED_JOBS = 410


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"JSON object required: {path}")
    return data


def run_path(research: Path, recorded: object) -> Path:
    """Map the immutable /research path recorded in the plan to this volume."""
    if not isinstance(recorded, str):
        raise ValueError("plan job has no output path")
    raw = PurePosixPath(recorded)
    if raw.is_absolute():
        try:
            relative = raw.relative_to(PurePosixPath("/research"))
        except ValueError as error:
            raise ValueError(f"plan output is outside /research: {raw}") from error
        return research / Path(*relative.parts)
    return research / Path(*raw.parts)


def recorded_artifact(run: Path, name: object) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError(f"invalid recorded artifact name in {run}: {name!r}")
    direct, artifact = run / name, run / "artifacts" / name
    if direct.is_file() == artifact.is_file():
        raise ValueError(f"recorded artifact missing or ambiguous in {run}: {name}")
    return direct if direct.is_file() else artifact


def audit_manifest(run: Path, job: dict) -> None:
    manifest = read_json(run / "manifest.json")
    if manifest.get("status") != STATUS or manifest.get("pilot_excluded") is not False:
        raise ValueError(f"run is not a qualifying completed production cell: {run}")
    if manifest.get("simulator_exit_code") != 0:
        raise ValueError(f"run has nonzero/missing simulator exit code: {run}")
    campaign = job.get("campaign")
    if campaign == "emergency":
        if (manifest.get("campaign"), manifest.get("power_dbm"), manifest.get("block")) != (
                "emergency_warning_power", job.get("power_dbm"), job.get("block")):
            raise ValueError(f"emergency manifest cell mismatch: {run}")
    elif campaign in ("behavioral", "calibration"):
        expected_cohort = "behavioral_intersection" if campaign == "behavioral" else "radio_calibration"
        if (manifest.get("cohort"), manifest.get("arm"), manifest.get("block")) != (
                expected_cohort, job.get("arm"), job.get("block")):
            raise ValueError(f"{campaign} manifest cell mismatch: {run}")
    else:
        raise ValueError(f"unexpected plan campaign: {campaign!r}")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict) or not inputs or any(
            not isinstance(value, str) or len(value) != 64 for value in inputs.values()):
        raise ValueError(f"manifest input hashes missing/invalid: {run}")
    records = manifest.get("file_records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"manifest file records missing: {run}")
    names: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not {"name", "bytes", "sha256"} <= set(record):
            raise ValueError(f"invalid file record: {run}")
        name = record["name"]
        if name in names:
            raise ValueError(f"duplicate file record {name!r}: {run}")
        names.add(name)
        path = recorded_artifact(run, name)
        if path.stat().st_size != record["bytes"] or sha256(path).lower() != str(record["sha256"]).lower():
            raise ValueError(f"recorded artifact hash/size mismatch: {path}")
    if "simulator.log" not in names:
        raise ValueError(f"simulator log is not audited: {run}")


def qualify(research: Path) -> list[Path]:
    plan_path = research / "run_matrix.all.plan.json"
    progress_path = research / "run_matrix.all.progress.json"
    plan, progress = read_json(plan_path), read_json(progress_path)
    jobs = plan.get("jobs")
    if plan.get("campaign") != "all" or not isinstance(jobs, list) or len(jobs) != EXPECTED_JOBS:
        raise ValueError("publication requires the exact 410-cell all-campaign plan")
    ordinals = {str(job.get("ordinal")) for job in jobs if isinstance(job, dict)}
    if len(ordinals) != EXPECTED_JOBS or ordinals != {str(i) for i in range(1, EXPECTED_JOBS + 1)}:
        raise ValueError("plan ordinals are not the exact 410-cell design")
    if progress.get("campaign") != "all" or progress.get("failures", []) not in ([], None):
        raise ValueError("production progress reports failures")
    if progress.get("blockers", []) not in ([], None):
        raise ValueError("production progress reports blocked cells")
    states = progress.get("states")
    if not isinstance(states, dict) or set(states) != ordinals or any(states[key] != "completed" for key in ordinals):
        raise ValueError("all 410 planned cells must be completed before packaging")
    outputs: list[Path] = []
    cells: set[tuple] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("invalid plan job")
        cell = (job.get("campaign"), job.get("block"), job.get("arm"), job.get("power_dbm"))
        if cell in cells:
            raise ValueError(f"duplicate cohort cell in plan: {cell}")
        cells.add(cell)
        run = run_path(research, job.get("out"))
        audit_manifest(run, job)
        outputs.append(run)
    emergency_cells = {("emergency", block, None, power) for block in range(1, 11)
                       for power in (-20, -15, -12, -9, -6, -3, 0, 3, 6, 9, 12, 15, 18, 20)}
    behavioral_cells = {("behavioral", block, arm, None) for block in range(1, 31)
                        for arm in ("radar_only", "sionna_good", "sionna_bad", "native_good", "native_bad")}
    calibration_cells = {("calibration", block, arm, None) for block in range(1, 31)
                         for arm in ("sionna_good", "sionna_bad", "native_good", "native_bad")}
    if cells != emergency_cells | behavioral_cells | calibration_cells:
        raise ValueError("plan does not contain the frozen emergency, behavioral, and calibration cohort cells")
    return [plan_path, progress_path, *outputs]


def required_tree(path: Path, label: str) -> list[Path]:
    if not path.is_dir() or not any(item.is_file() for item in path.rglob("*")):
        raise ValueError(f"missing or empty {label}: {path}")
    return [path]


def add_file(archive: tarfile.TarFile, source: Path, arcname: str) -> None:
    info = archive.gettarinfo(str(source), arcname=arcname)
    info.uid = info.gid = 0; info.uname = info.gname = ""
    info.mtime = 0
    with source.open("rb") as stream:
        archive.addfile(info, stream)


def expand(sources: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for source, prefix in sources:
        if source.is_file():
            files.append((source, prefix))
        else:
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                files.append((path, (Path(prefix) / path.relative_to(source)).as_posix()))
    return sorted(files, key=lambda item: item[1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=Path("/research"))
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--analysis-dir", type=Path, required=True, help="Complete analysis outputs.")
    parser.add_argument("--figures-dir", type=Path, required=True, help="Final figure files and figure manifest.")
    parser.add_argument("--out", type=Path, required=True, help="New .tar.xz archive path.")
    args = parser.parse_args()
    research, repo, out = args.research_root.resolve(), args.repo_root.resolve(), args.out.resolve()
    if out.exists() or out.suffixes[-2:] != [".tar", ".xz"]:
        raise ValueError("--out must name a new .tar.xz file")
    qualifying = qualify(research)
    sources: list[tuple[Path, str]] = []
    for path in qualifying:
        sources.append((path, (Path("study") / path.relative_to(research)).as_posix()))
    sources += [(path, (Path("study") / "analysis" / path.name).as_posix()) for path in required_tree(args.analysis_dir, "analysis directory")]
    sources += [(path, (Path("study") / "figures" / path.name).as_posix()) for path in required_tree(args.figures_dir, "figures directory")]
    for name in ("environment-manifest.json", "runtime-environment.json"):
        path = research / name
        if not path.is_file(): raise ValueError(f"missing frozen provenance file: {path}")
        sources.append((path, (Path("study") / "provenance" / name).as_posix()))
    configs = ("experiments/future_transport_2026", "experiments/intersection_radar_comm/sumo",
               "src/automotive/examples/sumo_files_v2v_map", "src/sionna/sionna_v1_server_script.py",
               "src/traci/model/traci-client.cc", "src/automotive/model/utilities",
               "src/automotive/model/Applications/emergencyVehicleAlert.cc",
               "src/automotive/examples/v2v-emergencyVehicleAlert-nrv2x.cc",
               "tools/analysis", "tools/plots/future_transport_figures.py",
               "tools/plots/future_transport_scene.py", "scripts/research")
    for name in configs:
        path = repo / name
        if not path.exists(): raise ValueError(f"missing study source/configuration: {path}")
        sources.append((path, (Path("study") / "source" / name).as_posix()))
    files = expand(sources)
    forbidden = ("optix", "libnvoptix", "nvidia")
    if any(any(token in name.lower() for token in forbidden) for _, name in files):
        raise ValueError("refusing proprietary NVIDIA/OptiX binary in publication archive")
    index = "".join(f"{sha256(path)}  {name}\n" for path, name in files)
    with tempfile.TemporaryDirectory(prefix="publication-index-") as temporary:
        index_path = Path(temporary) / "SHA256SUMS"
        index_path.write_text(index, encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:xz", format=tarfile.PAX_FORMAT) as archive:
            for path, name in files: add_file(archive, path, name)
            add_file(archive, index_path, "study/SHA256SUMS")
    print(json.dumps({"archive": str(out), "qualifying_runs": EXPECTED_JOBS,
                      "indexed_files": len(files), "sha256": sha256(out)}, indent=2))


if __name__ == "__main__":
    main()
