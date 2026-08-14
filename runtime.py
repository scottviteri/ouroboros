#!/usr/bin/env python3
"""Trusted host-side helpers for lineage initialization and publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath


LINEAGE_SCHEMA = "ouroboros-lineage/v1"
RESERVED_TOP_LEVEL = frozenset({".git", ".ouroboros-lineage.json", "journal.md"})


class PublicationError(RuntimeError):
    """A speculative worktree cannot safely become committed lineage state."""


def instrument_fingerprint(paths: list[Path]) -> str:
    """Hash the ordered names and contents of the trusted runtime files."""
    digest = hashlib.sha256()
    for path in paths:
        name = path.name.encode("utf-8")
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def write_metadata(path: Path, fingerprint: str, seed_sha256: str) -> None:
    payload = {
        "schema": LINEAGE_SCHEMA,
        "instrument_fingerprint": fingerprint,
        "seed_sha256": seed_sha256,
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_fingerprint(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError("lineage metadata is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != LINEAGE_SCHEMA:
        raise PublicationError("lineage metadata has an unsupported schema")
    fingerprint = payload.get("instrument_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise PublicationError("lineage metadata has no valid instrument fingerprint")
    return fingerprint


def _safe_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute():
        raise PublicationError(f"absolute archive path is forbidden: {name!r}")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if any(part == ".." for part in parts):
        raise PublicationError(f"parent traversal is forbidden: {name!r}")
    if any(part == ".git" for part in parts):
        raise PublicationError("a speculative result may not contain Git control paths")
    if parts and parts[0] in RESERVED_TOP_LEVEL:
        raise PublicationError(f"kernel-owned path is forbidden: {parts[0]}")
    return parts


def extract_result(
    archive_path: Path,
    destination: Path,
    max_bytes: int,
    max_files: int,
) -> dict[str, int]:
    """Extract a bounded, Git-representable result archive without path traversal."""
    if max_bytes <= 0 or max_files <= 0:
        raise ValueError("publication limits must be positive")
    destination.mkdir(parents=True, exist_ok=False)

    with tarfile.open(archive_path, mode="r:*") as archive:
        members: list[tuple[tarfile.TarInfo, tuple[str, ...]]] = []
        paths_seen: set[tuple[str, ...]] = set()
        symlink_paths: set[tuple[str, ...]] = set()
        total_bytes = 0
        file_count = 0

        for member in archive:
            parts = _safe_parts(member.name)
            if not parts:
                continue
            if parts in paths_seen:
                raise PublicationError(f"duplicate archive path: {member.name!r}")
            paths_seen.add(parts)
            if any(parts[:index] in symlink_paths for index in range(1, len(parts))):
                raise PublicationError(f"archive path descends through a symlink: {member.name!r}")

            if member.isdir():
                pass
            elif member.isreg():
                file_count += 1
                total_bytes += member.size
            elif member.issym():
                file_count += 1
                symlink_paths.add(parts)
            else:
                raise PublicationError(
                    f"unsupported filesystem object in result: {member.name!r}"
                )
            if file_count > max_files:
                raise PublicationError("speculative result exceeds the file-count limit")
            if total_bytes > max_bytes:
                raise PublicationError("speculative result exceeds the byte limit")
            members.append((member, parts))

        for member, parts in members:
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                os.symlink(member.linkname, target)
                continue

            source = archive.extractfile(member)
            if source is None:
                raise PublicationError(f"could not read archive member: {member.name!r}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)

    return {"bytes": total_bytes, "files": file_count}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("paths", nargs="+", type=Path)

    metadata = commands.add_parser("write-metadata")
    metadata.add_argument("path", type=Path)
    metadata.add_argument("fingerprint")
    metadata.add_argument("seed_sha256")

    read = commands.add_parser("read-fingerprint")
    read.add_argument("path", type=Path)

    extract = commands.add_parser("extract-result")
    extract.add_argument("archive", type=Path)
    extract.add_argument("destination", type=Path)
    extract.add_argument("--max-bytes", type=int, required=True)
    extract.add_argument("--max-files", type=int, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "fingerprint":
            print(instrument_fingerprint(args.paths))
        elif args.command == "write-metadata":
            write_metadata(args.path, args.fingerprint, args.seed_sha256)
        elif args.command == "read-fingerprint":
            print(read_fingerprint(args.path))
        else:
            summary = extract_result(
                args.archive, args.destination, args.max_bytes, args.max_files
            )
            print(json.dumps(summary, sort_keys=True))
    except (OSError, tarfile.TarError, PublicationError, ValueError) as exc:
        print(f"ouroboros runtime: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
