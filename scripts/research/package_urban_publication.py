#!/usr/bin/env python3
"""Package the separate, post-freeze 270-cell V2V-Urban replication.

This command deliberately does not accept the original highway matrix.  It
audits each urban manifest and its recorded artifacts before producing a
deterministic archive with an in-archive SHA-256 index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile


STATUS = "completed_pending_metric_audit"
EXPECTED_JOBS = 270
ARMS = ("radar_only", "sionna_good", "sionna_bad", "native_good", "native_bad")
ACTIVE_ARMS = ARMS[1:]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def run_path(research: Path, recorded: object) -> Path:
    if not isinstance(recorded, str):
        raise ValueError("plan job has no output path")
    raw = PurePosixPath(recorded)
    if raw.is_absolute():
        try:
            raw = raw.relative_to("/research")
        except ValueError as error:
            raise ValueError(f"plan output is outside /research: {recorded}") from error
    return research.joinpath(*raw.parts)


def recorded_artifact(run: Path, name: object) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError(f"invalid recorded artifact name in {run}: {name!r}")
    direct, artifact = run / name, run / "artifacts" / name
    if direct.is_file() == artifact.is_file():
        raise ValueError(f"recorded artifact missing or ambiguous in {run}: {name}")
    return direct if direct.is_file() else artifact


def audit_manifest(run: Path, job: dict) -> None:
    manifest = read_json(run / "manifest.json")
    if (manifest.get("status") != STATUS or manifest.get("pilot_excluded") is not False
            or manifest.get("simulator_exit_code") != 0):
        raise ValueError(f"run is not a qualifying completed production cell: {run}")
    expected_cohort = "behavioral_intersection" if job["campaign"] == "urban_behavioral" else "radio_calibration"
    if (manifest.get("cohort"), manifest.get("arm"), manifest.get("block")) != (
            expected_cohort, job.get("arm"), job.get("block")):
        raise ValueError(f"urban manifest cell mismatch: {run}")
    if manifest.get("channel_scenario") != "V2V-Urban" or manifest.get("geometry_version") != "grounded_vehicle_v2":
        raise ValueError(f"urban channel scenario missing: {run}")
    channel = manifest.get("channel_audit")
    geometry = manifest.get("scene_geometry")
    if not isinstance(channel, dict) or channel.get("model") != "V2V-Urban" or channel.get("buildings_registered") != 2:
        raise ValueError(f"urban model/building audit missing: {run}")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("buildings"), list) or len(geometry["buildings"]) != 2:
        raise ValueError(f"urban scene geometry audit missing: {run}")
    if str(job.get("arm", "")).startswith("sionna_") and manifest.get("sionna_geometry_audit") != {
            "antenna_z_m": 1.5, "mesh_center_z_m": 0.65, "mesh_height_m": 1.3}:
        raise ValueError(f"corrected Sionna geometry audit missing: {run}")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict) or not inputs or any(not isinstance(v, str) or len(v) != 64 for v in inputs.values()):
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
        artifact = recorded_artifact(run, name)
        if artifact.stat().st_size != record["bytes"] or sha256(artifact).lower() != str(record["sha256"]).lower():
            raise ValueError(f"recorded artifact hash/size mismatch: {artifact}")
    if "simulator.log" not in names:
        raise ValueError(f"simulator log is not audited: {run}")


def qualify(research: Path) -> list[Path]:
    plan_path, progress_path = research / "run_matrix.urban.plan.json", research / "run_matrix.urban.progress.json"
    plan, progress = read_json(plan_path), read_json(progress_path)
    jobs = plan.get("jobs")
    if (plan.get("campaign") != "urban" or plan.get("channel_scenario") != "V2V-Urban"
            or plan.get("geometry_version") != "grounded_vehicle_v2"
            or not isinstance(jobs, list) or len(jobs) != EXPECTED_JOBS):
        raise ValueError("publication requires the exact 270-cell V2V-Urban plan")
    ordinals = {str(job.get("ordinal")) for job in jobs if isinstance(job, dict)}
    if ordinals != {str(i) for i in range(1, EXPECTED_JOBS + 1)}:
        raise ValueError("urban plan ordinals are not the exact 270-cell design")
    if progress.get("campaign") != "urban" or progress.get("failures", []) not in ([], None) or progress.get("blockers", []) not in ([], None):
        raise ValueError("urban production progress reports failures or blockers")
    states = progress.get("states")
    if not isinstance(states, dict) or set(states) != ordinals or any(states[key] != "completed" for key in ordinals):
        raise ValueError("all 270 planned urban cells must be completed")
    expected = ({("urban_behavioral", block, arm) for block in range(1, 31) for arm in ARMS}
                | {("urban_calibration", block, arm) for block in range(1, 31) for arm in ACTIVE_ARMS})
    cells, outputs = set(), []
    for job in jobs:
        if not isinstance(job, dict) or job.get("campaign") not in ("urban_behavioral", "urban_calibration"):
            raise ValueError("unexpected urban plan job")
        cell = (job.get("campaign"), job.get("block"), job.get("arm"))
        if cell in cells:
            raise ValueError(f"duplicate urban cohort cell: {cell}")
        cells.add(cell); run = run_path(research, job.get("out"))
        parent = "production" if job["campaign"] == "urban_behavioral" else "calibration-production"
        expected_run = research / "runs" / parent / f"block-{job['block']:02d}" / str(job["arm"])
        if run != expected_run:
            raise ValueError(f"urban plan output is not its canonical cohort path: {run}")
        audit_manifest(run, job); outputs.append(run)
    if cells != expected:
        raise ValueError("plan does not contain the exact urban behavioral and calibration cells")
    planned = set(outputs)
    observed = {item.parent for root in (research / "runs" / "production", research / "runs" / "calibration-production")
                if root.exists() for item in root.rglob("manifest.json")}
    if observed != planned:
        raise ValueError("urban run tree contains a partial, pilot, or duplicate run directory")
    return [plan_path, progress_path, *outputs]


def required_tree(path: Path, label: str) -> Path:
    if not path.is_dir() or not any(item.is_file() for item in path.rglob("*")):
        raise ValueError(f"missing or empty {label}: {path}")
    return path


def add_file(archive: tarfile.TarFile, source: Path, arcname: str) -> None:
    info = archive.gettarinfo(str(source), arcname=arcname); info.uid = info.gid = 0; info.uname = info.gname = ""; info.mtime = 0
    with source.open("rb") as stream: archive.addfile(info, stream)


def expand(sources: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    files = []
    for source, prefix in sources:
        if source.is_file(): files.append((source, prefix))
        else:
            files.extend((item, (Path(prefix) / item.relative_to(source)).as_posix()) for item in source.rglob("*") if item.is_file())
    return sorted(files, key=lambda value: value[1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=Path("/research")); parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--analysis-dir", type=Path, required=True); parser.add_argument("--figures-dir", type=Path, required=True); parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--highway-reference-manifest", type=Path, required=True, help="Frozen original highway execution manifest.")
    parser.add_argument("--protocol-amendment", type=Path, required=True, help="Post-freeze urban-replication amendment.")
    args = parser.parse_args(); research, repo, out = args.research_root.resolve(), args.repo_root.resolve(), args.out.resolve()
    if out.exists() or out.suffixes[-2:] != [".tar", ".xz"]: raise ValueError("--out must name a new .tar.xz file")
    qualifying = qualify(research)
    for provenance in (research / "environment-manifest.json", research / "runtime-environment.json", args.highway_reference_manifest, args.protocol_amendment):
        if not provenance.is_file(): raise ValueError(f"missing required provenance file: {provenance}")
    sources = [(path, (Path("urban-study") / path.relative_to(research)).as_posix()) for path in qualifying]
    sources += [(required_tree(args.analysis_dir, "analysis directory"), "urban-study/analysis"), (required_tree(args.figures_dir, "figures directory"), "urban-study/figures")]
    sources += [(research / "environment-manifest.json", "urban-study/provenance/environment-manifest.json"), (research / "runtime-environment.json", "urban-study/provenance/runtime-environment.json"), (args.highway_reference_manifest, "urban-study/provenance/original-highway-reference-manifest.json"), (args.protocol_amendment, "urban-study/provenance/protocol-amendment.md")]
    configs = ("experiments/future_transport_2026", "experiments/intersection_radar_comm/sumo", "src/automotive/examples/sumo_files_v2v_map", "src/sionna/sionna_v1_server_script.py", "src/traci/model/traci-client.cc", "src/automotive/model/utilities", "src/automotive/model/Applications/emergencyVehicleAlert.cc", "src/automotive/examples/v2v-emergencyVehicleAlert-nrv2x.cc", "tools/analysis", "tools/plots/future_transport_figures.py", "tools/plots/future_transport_scene.py", "scripts/research")
    for name in configs:
        path = repo / name
        if not path.exists(): raise ValueError(f"missing study source/configuration: {path}")
        sources.append((path, (Path("urban-study/source") / name).as_posix()))
    files = expand(sources)
    if len({name for _, name in files}) != len(files): raise ValueError("archive contains duplicate paths")
    if any(any(token in name.lower() for token in ("optix", "libnvoptix", "nvidia")) for _, name in files): raise ValueError("refusing proprietary NVIDIA/OptiX binary in publication archive")
    with tempfile.TemporaryDirectory(prefix="urban-publication-index-") as temporary:
        index_path = Path(temporary) / "SHA256SUMS"; index_path.write_text("".join(f"{sha256(path)}  {name}\n" for path, name in files), encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:xz", format=tarfile.PAX_FORMAT) as archive:
            for path, name in files: add_file(archive, path, name)
            add_file(archive, index_path, "urban-study/SHA256SUMS")
    print(json.dumps({"archive": str(out), "qualifying_runs": EXPECTED_JOBS, "indexed_files": len(files), "sha256": sha256(out)}, indent=2))


if __name__ == "__main__": main()
