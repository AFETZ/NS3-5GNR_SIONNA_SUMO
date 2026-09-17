#!/usr/bin/env python3
"""Package the separate 270-cell dynamic V2V-Urban replication.

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
CAMPAIGN_VERSION = "urban_dynamic_v3"
PLAN_NAME = f"run_matrix.{CAMPAIGN_VERSION}.plan.json"
PROGRESS_NAME = f"run_matrix.{CAMPAIGN_VERSION}.progress.json"
CONDITION_UPDATE_MS = 100
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


def relative_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid {label} path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe {label} path: {value!r}")
    return Path(*path.parts)


def hash_map(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"missing {label}")
    checked: dict[str, str] = {}
    for name, digest in value.items():
        relative_path(name, label)
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise ValueError(f"invalid {label} digest for {name!r}")
        checked[str(name)] = digest.lower()
    return checked


def validate_execution_checkout(research: Path, repo: Path, runs: list[Path]) -> list[tuple[Path, str]]:
    """Require the checkout that actually executed the grounded urban cohort."""
    environment = read_json(research / "environment-manifest.json")
    frozen = hash_map(environment.get("research_files_sha256"), "frozen research_files_sha256")
    observed: dict[str, str] = {}
    for name, digest in frozen.items():
        path = repo / relative_path(name, "frozen")
        if not path.is_file() or sha256(path).lower() != digest:
            raise ValueError(f"--repo-root is not the frozen execution checkout: {name}")
    for run in runs:
        manifest = read_json(run / "manifest.json")
        inputs = hash_map(manifest.get("inputs"), f"manifest inputs in {run}")
        for name, digest in inputs.items():
            prior = observed.setdefault(name, digest)
            if prior != digest:
                raise ValueError(f"inconsistent execution input hash across runs: {name}")
            path = repo / relative_path(name, "manifest input")
            if not path.is_file() or sha256(path).lower() != digest:
                raise ValueError(f"execution input does not match --repo-root: {name}")
            if name in frozen and frozen[name] != digest:
                raise ValueError(f"execution input disagrees with frozen manifest: {name}")
    execution_files = dict(frozen)
    execution_files.update(observed)
    return [(repo / relative_path(name, "execution"), (Path("urban-study") / "source" / name).as_posix())
            for name in sorted(execution_files)]


def validate_analysis_and_figures(analysis: Path, figures: Path, research: Path,
                                 analysis_repo: Path | None) -> list[tuple[Path, str]]:
    analysis_manifest = read_json(analysis / "analysis-manifest.json")
    expected = {"schema": 1, "cohort": CAMPAIGN_VERSION,
                "input_plan_sha256": sha256(research / PLAN_NAME),
                "input_environment_manifest_sha256": sha256(research / "environment-manifest.json")}
    if any(analysis_manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("analysis manifest is not bound to this execution cohort")
    output_hashes = hash_map(analysis_manifest.get("output_files_sha256"), "analysis output files")
    for name, digest in output_hashes.items():
        path = analysis / relative_path(name, "analysis output")
        if not path.is_file() or sha256(path).lower() != digest:
            raise ValueError(f"analysis output hash mismatch: {name}")
    exact_manifest_tree(analysis, "analysis-manifest.json", output_hashes, "analysis")
    sources: list[tuple[Path, str]] = []
    source_map = analysis_manifest.get("analysis_files_sha256", {})
    if analysis_repo is not None:
        hashes = hash_map(source_map, "analysis_files_sha256")
        for name, digest in hashes.items():
            path = analysis_repo / relative_path(name, "analysis")
            if not path.is_file() or sha256(path).lower() != digest:
                raise ValueError(f"post-freeze analysis source hash mismatch: {name}")
            sources.append((path, (Path("urban-study") / "post-freeze-analysis-source" / name).as_posix()))
    elif source_map not in ({}, None):
        hash_map(source_map, "analysis_files_sha256")
    figures_manifest = read_json(figures / "figure-manifest.json")
    allowed = {"figure_1_synthetic_intersection", "figure_c_calibration_prr",
               "figure_d_behavioral_collisions", "figure_e_sionna_no_ray_sentinel"}
    if (figures_manifest.get("schema") != 1 or figures_manifest.get("cohort") != CAMPAIGN_VERSION
            or figures_manifest.get("input_analysis_manifest_sha256") != sha256(analysis / "analysis-manifest.json")):
        raise ValueError("figure manifest is not bound to this cohort's analysis")
    hashes = hash_map(figures_manifest.get("files"), "figure files")
    stems = {Path(name).stem for name in hashes}
    if not stems or not stems <= allowed:
        raise ValueError(f"figure manifest contains figures outside the {CAMPAIGN_VERSION} cohort")
    for name, digest in hashes.items():
        path = figures / relative_path(name, "figure")
        if not path.is_file() or sha256(path).lower() != digest:
            raise ValueError(f"figure hash mismatch: {name}")
    exact_manifest_tree(figures, "figure-manifest.json", hashes, "figure")
    return sources


def validate_reference_provenance(research: Path, repo: Path, highway: Path, amendment: Path) -> list[Path]:
    """Verify and return the pre-outcome exclusion/reference audit documents."""
    environment = read_json(research / "environment-manifest.json")
    if environment.get("study_variant") != "post_freeze_v2v_urban_dynamic_replication":
        raise ValueError("not the frozen dynamic-urban environment")
    hash_map(read_json(highway).get("research_files_sha256"), "highway reference research_files_sha256")
    frozen = hash_map(environment.get("research_files_sha256"),
                      "frozen research_files_sha256")
    if sha256(highway) != environment.get("highway_environment_reference_sha256"):
        raise ValueError("highway reference does not match dynamic environment")
    grounded = research / "environment-manifest.grounded-static-reference.json"
    grounded_progress = research / "run_matrix.grounded-static.progress.json"
    for path, key in ((grounded, "grounded_static_environment_reference_sha256"),
                      (grounded_progress, "grounded_static_progress_sha256")):
        if not path.is_file() or sha256(path) != environment.get(key):
            raise ValueError(f"excluded grounded-static reference hash mismatch: {path}")
    try:
        relative = amendment.resolve().relative_to(repo).as_posix()
    except ValueError:
        raise ValueError("protocol amendment must come from the frozen execution checkout")
    expected = frozen.get(relative)
    if expected is None or sha256(amendment).lower() != expected:
        raise ValueError("protocol amendment does not match the frozen execution manifest")
    return [grounded, grounded_progress]


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
    if (manifest.get("channel_scenario") != "V2V-Urban"
            or manifest.get("geometry_version") != "grounded_vehicle_v2"
            or manifest.get("campaign_version") != CAMPAIGN_VERSION
            or manifest.get("channel_condition_update_ms") != CONDITION_UPDATE_MS):
        raise ValueError(f"urban channel scenario missing: {run}")
    channel = manifest.get("channel_audit")
    geometry = manifest.get("scene_geometry")
    if not isinstance(channel, dict) or channel.get("model") != "V2V-Urban" or channel.get("buildings_registered") != 2:
        raise ValueError(f"urban model/building audit missing: {run}")
    if (channel.get("actual_condition_model") != "ns3::ThreeGppV2vUrbanChannelConditionModel"
            or channel.get("channel_condition_update_ms") != CONDITION_UPDATE_MS
            or channel.get("three_gpp_channel_update_ms") != 0
            or channel.get("shadowing_enabled") is not False):
        raise ValueError(f"dynamic channel-condition audit missing: {run}")
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
    plan_path, progress_path = research / PLAN_NAME, research / PROGRESS_NAME
    plan, progress = read_json(plan_path), read_json(progress_path)
    jobs = plan.get("jobs")
    if (plan.get("campaign") != CAMPAIGN_VERSION or plan.get("channel_scenario") != "V2V-Urban"
            or plan.get("geometry_version") != "grounded_vehicle_v2"
            or plan.get("channel_condition_update_ms") != CONDITION_UPDATE_MS
            or not isinstance(jobs, list) or len(jobs) != EXPECTED_JOBS):
        raise ValueError("publication requires the exact 270-cell V2V-Urban plan")
    ordinals = {str(job.get("ordinal")) for job in jobs if isinstance(job, dict)}
    if ordinals != {str(i) for i in range(1, EXPECTED_JOBS + 1)}:
        raise ValueError("urban plan ordinals are not the exact 270-cell design")
    if progress.get("campaign") != CAMPAIGN_VERSION or progress.get("failures", []) not in ([], None) or progress.get("blockers", []) not in ([], None):
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


def declared_run_files(run: Path) -> list[Path]:
    manifest = read_json(run / "manifest.json")
    declared = {run / "manifest.json"}
    for record in manifest["file_records"]:
        declared.add(recorded_artifact(run, record["name"]))
    observed = {path for path in run.rglob("*") if path.is_file()}
    if observed != declared:
        extra = sorted(str(path.relative_to(run)) for path in observed - declared)
        missing = sorted(str(path.relative_to(run)) for path in declared - observed)
        raise ValueError(f"run tree has undeclared or missing files: extra={extra}, missing={missing}")
    return sorted(declared)


def exact_manifest_tree(root: Path, manifest_name: str, hashes: dict[str, str], label: str) -> list[Path]:
    manifest = root / manifest_name
    declared = {manifest, *(root / relative_path(name, label) for name in hashes)}
    observed = {path for path in root.rglob("*") if path.is_file()}
    if observed != declared:
        extra = sorted(str(path.relative_to(root)) for path in observed - declared)
        missing = sorted(str(path.relative_to(root)) for path in declared - observed)
        raise ValueError(f"{label} tree has undeclared or missing files: extra={extra}, missing={missing}")
    return sorted(declared)


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
    parser.add_argument("--research-root", type=Path, default=Path("/research"))
    parser.add_argument("--repo-root", type=Path, required=True,
                        help="Frozen /research/ns-3-dev execution checkout.")
    parser.add_argument("--analysis-repo-root", type=Path,
                        help="Optional post-freeze checkout named by analysis-manifest.json.")
    parser.add_argument("--analysis-dir", type=Path, required=True); parser.add_argument("--figures-dir", type=Path, required=True); parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--highway-reference-manifest", type=Path, required=True, help="Frozen original highway execution manifest.")
    parser.add_argument("--protocol-amendment", type=Path, required=True, help="Post-freeze urban-replication amendment.")
    args = parser.parse_args(); research, repo, out = args.research_root.resolve(), args.repo_root.resolve(), args.out.resolve()
    if out.exists() or out.suffixes[-2:] != [".tar", ".xz"]: raise ValueError("--out must name a new .tar.xz file")
    qualifying = qualify(research)
    execution_source = validate_execution_checkout(research, repo, qualifying[2:])
    analysis_source = validate_analysis_and_figures(
        args.analysis_dir.resolve(), args.figures_dir.resolve(), research,
        args.analysis_repo_root.resolve() if args.analysis_repo_root else None)
    for provenance in (research / "environment-manifest.json", research / "runtime-environment.json", args.highway_reference_manifest, args.protocol_amendment):
        if not provenance.is_file(): raise ValueError(f"missing required provenance file: {provenance}")
    excluded_references = validate_reference_provenance(
        research, repo, args.highway_reference_manifest.resolve(), args.protocol_amendment.resolve())
    sources = [(path, (Path("urban-study") / path.relative_to(research)).as_posix()) for path in qualifying[:2]]
    for run in qualifying[2:]:
        sources += [(path, (Path("urban-study") / path.relative_to(research)).as_posix())
                    for path in declared_run_files(run)]
    analysis_root, figures_root = args.analysis_dir.resolve(), args.figures_dir.resolve()
    analysis_hashes = hash_map(read_json(analysis_root / "analysis-manifest.json").get("output_files_sha256"), "analysis output files")
    figure_hashes = hash_map(read_json(figures_root / "figure-manifest.json").get("files"), "figure files")
    sources += [(path, (Path("urban-study") / "analysis" / path.relative_to(analysis_root)).as_posix())
                for path in exact_manifest_tree(analysis_root, "analysis-manifest.json", analysis_hashes, "analysis")]
    sources += [(path, (Path("urban-study") / "figures" / path.relative_to(figures_root)).as_posix())
                for path in exact_manifest_tree(figures_root, "figure-manifest.json", figure_hashes, "figure")]
    sources += [(research / "environment-manifest.json", "urban-study/provenance/environment-manifest.json"), (research / "runtime-environment.json", "urban-study/provenance/runtime-environment.json"), (args.highway_reference_manifest, "urban-study/provenance/original-highway-reference-manifest.json"), (args.protocol_amendment, "urban-study/provenance/protocol-amendment.md")]
    sources += [(path, (Path("urban-study/provenance") / path.name).as_posix()) for path in excluded_references]
    sources += execution_source + analysis_source
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
