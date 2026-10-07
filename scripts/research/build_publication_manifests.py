#!/usr/bin/env python3
"""Bind analysis outputs and cohort-owned figures to frozen execution inputs.

Create the manifests only after the complete analysis has passed its own run
gate. The publication packagers recheck every hash against the source files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


COHORTS = {
    "frozen_highway": ("run_matrix.all.plan.json", {
        "figure_a_emergency_prr", "figure_b_emergency_km_p90"}),
    "urban_dynamic_v3": ("run_matrix.urban_dynamic_v3.plan.json", {
        "figure_1_synthetic_intersection", "figure_c_calibration_prr",
        "figure_d_behavioral_collisions", "figure_e_sionna_no_ray_sentinel"}),
}


def expected_figure_files(stems: set[str]) -> set[str]:
    """Return the required paired raster/vector filenames for these stems."""
    return {f"{stem}.{suffix}" for stem in stems for suffix in ("png", "svg")}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(name: str) -> Path:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {name!r}")
    return Path(*path.parts)


def files_below(root: Path, excluded: set[str]) -> dict[str, str]:
    if not root.is_dir():
        raise ValueError(f"missing directory: {root}")
    result = {}
    for file in sorted(item for item in root.rglob("*") if item.is_file()):
        name = file.relative_to(root).as_posix()
        if name not in excluded:
            result[name] = sha256(file)
    if not result:
        raise ValueError(f"no files to manifest in {root}")
    return result


def build(cohort: str, research: Path, analysis: Path, figures: Path,
          analysis_repo: Path, analysis_sources: list[str]) -> tuple[dict, dict]:
    plan_name, allowed_stems = COHORTS[cohort]
    plan, environment = research / plan_name, research / "environment-manifest.json"
    if not plan.is_file() or not environment.is_file():
        raise ValueError("frozen plan/environment manifest missing")
    if not analysis_sources:
        raise ValueError("at least one --analysis-source is required")
    source_hashes = {}
    for name in sorted(set(analysis_sources)):
        path = analysis_repo / safe_relative(name)
        if not path.is_file():
            raise ValueError(f"analysis source missing: {path}")
        source_hashes[name] = sha256(path)
    outputs = files_below(analysis, {"analysis-manifest.json"})
    figure_files = files_below(figures, {"figure-manifest.json"})
    expected = expected_figure_files(allowed_stems)
    if set(figure_files) != expected:
        raise ValueError(f"wrong or incomplete figure set for {cohort}: "
                         f"expected {sorted(expected)}, found {sorted(figure_files)}")
    analysis_manifest = {
        "schema": 1, "cohort": cohort,
        "input_plan_sha256": sha256(plan),
        "input_environment_manifest_sha256": sha256(environment),
        "analysis_files_sha256": source_hashes,
        "output_files_sha256": outputs,
    }
    return analysis_manifest, {"schema": 1, "cohort": cohort, "files": figure_files}


def encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_manifests(analysis: Path, figures: Path, analysis_manifest: dict,
                    figure_manifest: dict) -> None:
    analysis_path, figure_path = analysis / "analysis-manifest.json", figures / "figure-manifest.json"
    if analysis_path.exists() or figure_path.exists():
        raise FileExistsError("refusing to overwrite an existing publication manifest")
    analysis_path.write_bytes(encoded(analysis_manifest))
    figure_manifest["input_analysis_manifest_sha256"] = sha256(analysis_path)
    figure_path.write_bytes(encoded(figure_manifest))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=sorted(COHORTS), required=True)
    parser.add_argument("--research-root", type=Path, default=Path("/research"))
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--figures-dir", type=Path, required=True)
    parser.add_argument("--analysis-repo-root", type=Path, required=True)
    parser.add_argument("--analysis-source", action="append", required=True,
                        help="Repository-relative source file; may be repeated.")
    args = parser.parse_args()
    analysis_manifest, figure_manifest = build(
        args.cohort, args.research_root.resolve(), args.analysis_dir.resolve(),
        args.figures_dir.resolve(), args.analysis_repo_root.resolve(), args.analysis_source)
    write_manifests(args.analysis_dir, args.figures_dir, analysis_manifest, figure_manifest)
    print(json.dumps({"cohort": args.cohort, "analysis_outputs": len(analysis_manifest["output_files_sha256"]),
                      "figures": len(figure_manifest["files"])}))


if __name__ == "__main__":
    main()
