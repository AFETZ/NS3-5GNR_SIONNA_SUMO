#!/usr/bin/env python3
"""Freeze the assembled simulator inputs and build artifacts.

This script is intended to run in the Linux research volume after the
simulator has been built.  It keeps the provenance recorded by ``bootstrap``
and replaces the source hashes with hashes of the files actually present in
the assembled build.  The manifest is replaced atomically only after every
required input and artifact has been checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


EXAMPLE = Path("ns-3-dev/build/src/automotive/examples/ns3-dev-v2v-emergencyVehicleAlert-nrv2x-optimized")
TRACI_LIBRARY = Path("ns-3-dev/build/lib/libns3-dev-traci-optimized.so")
FIXED_FILES = (
    Path("src/automotive/examples/CMakeLists.txt"),
    Path("src/traci/model/traci-client.cc"),
    Path("src/sionna/sionna_v1_server_script.py"),
    Path("tools/analysis/emergency_campaign.py"),
    Path("tools/analysis/emergency_summary.py"),
    Path("tools/analysis/intersection_campaign.py"),
    Path("tools/analysis/research_metrics.py"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(repo_root: Path, original: dict[str, str]) -> list[Path]:
    """Return the original bootstrap set plus the current research surface."""
    relative = set(original)
    relative.update(path.as_posix() for path in FIXED_FILES)
    for directory in (
        repo_root / "experiments/future_transport_2026",
        repo_root / "scripts/research",
        repo_root / "tests/research",
    ):
        if directory.is_dir():
            pattern = "**/*" if directory.name == "future_transport_2026" else "*.py"
            relative.update(
                path.relative_to(repo_root).as_posix()
                for path in directory.glob(pattern)
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
            )
    return [Path(name) for name in sorted(relative)]


def artifact_record(root: Path, relative: Path) -> dict[str, object]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"Required built artifact absent: {path}")
    return {
        "path": relative.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def freeze(root: Path) -> Path:
    manifest_path = root / "environment-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Bootstrap manifest absent: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original = manifest.get("research_files_sha256")
    if not isinstance(original, dict) or not original:
        raise ValueError("Manifest has no bootstrap research_files_sha256 map")

    repo_root = root / "ns-3-dev"
    if not repo_root.is_dir():
        raise FileNotFoundError(f"Assembled ns-3 checkout absent: {repo_root}")
    files = source_files(repo_root, {str(key): str(value) for key, value in original.items()})
    current: dict[str, str] = {}
    for relative in files:
        path = repo_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Required research file absent: {path}")
        current[relative.as_posix()] = sha256(path)

    manifest["bootstrap_research_files_sha256"] = dict(original)
    manifest["research_files_sha256"] = current
    manifest["built_artifacts"] = {
        "example_executable": artifact_record(root, EXAMPLE),
        "traci_library": artifact_record(root, TRACI_LIBRARY),
    }
    manifest["freeze_schema"] = 1

    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest_path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/research"))
    args = parser.parse_args()
    path = freeze(args.root.resolve())
    print(f"Frozen environment manifest: {path}")


if __name__ == "__main__":
    main()
