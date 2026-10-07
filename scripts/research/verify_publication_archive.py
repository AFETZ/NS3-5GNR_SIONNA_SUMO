#!/usr/bin/env python3
"""Read every member of an indexed research tar.xz and verify its SHA-256."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def _hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path) -> dict[str, object]:
    actual: dict[str, str] = {}
    index_lines: list[str] | None = None
    with tarfile.open(path, "r|xz") as archive:
        for member in archive:
            name = member.name
            relative = PurePosixPath(name)
            if not member.isfile() or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"archive contains a non-file or unsafe path: {name}")
            if name in actual:
                raise ValueError(f"duplicate archive member name: {name}")
            with archive.extractfile(member) as stream:
                if name.endswith("/SHA256SUMS"):
                    if index_lines is not None:
                        raise ValueError("expected one SHA256SUMS index")
                    index_lines = stream.read().decode("utf-8").splitlines()
                else:
                    actual[name] = _hash_stream(stream)
            if name.endswith("/SHA256SUMS"):
                actual[name] = ""
    if index_lines is None:
        raise ValueError("expected one SHA256SUMS index")
    recorded: dict[str, str] = {}
    for line in index_lines:
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid SHA256SUMS line")
        if name in recorded:
            raise ValueError("duplicate SHA256SUMS entry")
        recorded[name] = digest
    actual = {name: digest for name, digest in actual.items() if not name.endswith("/SHA256SUMS")}
    if set(recorded) != set(actual):
        raise ValueError("SHA256SUMS entries do not match archive members")
    for name, digest in actual.items():
        if digest != recorded[name]:
            raise ValueError(f"archive content hash mismatch: {name}")
    with path.open("rb") as stream:
        archive_sha256 = _hash_stream(stream)
    return {"archive": str(path), "verified_files": len(recorded),
            "archive_sha256": archive_sha256}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive.resolve()), indent=2))


if __name__ == "__main__":
    main()
