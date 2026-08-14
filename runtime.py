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


LINEAGE_SCHEMA = "ouroboros-lineage/v2"
OBSERVATION_SCHEMA = "ouroboros-observations/v1"
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


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_metadata(
    path: Path,
    fingerprint: str,
    seed_sha256: str,
    instrument_repository: str,
    instrument_ref: str,
    instrument_commit: str,
) -> None:
    payload = {
        "schema": LINEAGE_SCHEMA,
        "instrument_repository": instrument_repository,
        "instrument_ref": instrument_ref,
        "instrument_commit": instrument_commit,
        "instrument_fingerprint": fingerprint,
        "seed_sha256": seed_sha256,
    }
    _write_json(path, payload)


def read_metadata(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError("lineage metadata is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != LINEAGE_SCHEMA:
        raise PublicationError("lineage metadata has an unsupported schema")
    fingerprint = payload.get("instrument_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise PublicationError("lineage metadata has no valid instrument fingerprint")
    commit = payload.get("instrument_commit")
    if (
        not isinstance(commit, str)
        or len(commit) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise PublicationError("lineage metadata has no valid instrument commit")
    return payload


def read_field(path: Path, field: str) -> str:
    value = read_metadata(path).get(field)
    if not isinstance(value, str):
        raise PublicationError(f"lineage metadata has no valid {field}")
    return value


def write_observation_metadata(
    path: Path,
    lineage_branch: str,
    observation_branch: str,
    instrument_repository: str,
    instrument_ref: str,
    instrument_commit: str,
    instrument_fingerprint: str,
    seed_sha256: str,
) -> None:
    _write_json(
        path,
        {
            "schema": OBSERVATION_SCHEMA,
            "lineage_branch": lineage_branch,
            "observation_branch": observation_branch,
            "instrument_repository": instrument_repository,
            "instrument_ref": instrument_ref,
            "instrument_commit": instrument_commit,
            "instrument_fingerprint": instrument_fingerprint,
            "seed_sha256": seed_sha256,
        },
    )


def read_observation_field(path: Path, field: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError("observation metadata is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != OBSERVATION_SCHEMA:
        raise PublicationError("observation metadata has an unsupported schema")
    value = payload.get(field)
    if not isinstance(value, str):
        raise PublicationError(f"observation metadata has no valid {field}")
    return value


def constraint_manifest(args: argparse.Namespace) -> dict[str, object]:
    return {
        "disclosure": "canonical",
        "generation": {
            "wall_seconds": args.wall,
            "aggregate_cpu_seconds": args.cpu_seconds,
            "cpu_quota_percent": args.cpu_quota_percent,
            "memory_max": args.memory_max,
            "memory_swap_max": args.memory_swap_max,
            "tasks_max": args.tasks_max,
        },
        "filesystem": {
            "work_bytes": args.work_bytes,
            "published_files": args.work_files,
            "tmp_bytes": args.tmp_bytes,
            "run_bytes": args.run_bytes,
        },
        "model": {
            "max_prompt_bytes": args.model_prompt_bytes,
            "max_output_tokens_per_request": args.model_output_tokens,
            "request_timeout_seconds": args.model_timeout,
            "spending_budget": {"currency": "USD", "amount": args.model_budget},
        },
        "boundary": {
            "ip_network": False,
            "kernel_files_read_only": True,
            "git_database_mounted": False,
        },
        "publication": {
            "requires_exit_zero": True,
            "supported_objects": ["directory", "regular_file", "symlink"],
            "speculative_writes_discarded_on_failure": True,
        },
    }


def write_generation_observation(
    path: Path,
    audit_path: Path,
    stderr_path: Path,
    fields: dict[str, object],
) -> None:
    model_calls: list[object] = []
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                model_calls.append(json.loads(line))
    stderr_tail = ""
    if stderr_path.exists():
        stderr_tail = "\n".join(stderr_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[-20:])
    _write_json(
        path,
        {
            "schema": "ouroboros-generation-observation/v1",
            **fields,
            "model_calls": model_calls,
            "stderr_tail": stderr_tail,
        },
    )


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
    metadata.add_argument("instrument_repository")
    metadata.add_argument("instrument_ref")
    metadata.add_argument("instrument_commit")

    read = commands.add_parser("read-field")
    read.add_argument("path", type=Path)
    read.add_argument("field")

    observation_metadata = commands.add_parser("write-observation-metadata")
    observation_metadata.add_argument("path", type=Path)
    observation_metadata.add_argument("lineage_branch")
    observation_metadata.add_argument("observation_branch")
    observation_metadata.add_argument("instrument_repository")
    observation_metadata.add_argument("instrument_ref")
    observation_metadata.add_argument("instrument_commit")
    observation_metadata.add_argument("instrument_fingerprint")
    observation_metadata.add_argument("seed_sha256")

    read_observation = commands.add_parser("read-observation-field")
    read_observation.add_argument("path", type=Path)
    read_observation.add_argument("field")

    constraints = commands.add_parser("constraints-json")
    constraints.add_argument("--wall", type=int, required=True)
    constraints.add_argument("--cpu-seconds", type=int, required=True)
    constraints.add_argument("--cpu-quota-percent", type=int, required=True)
    constraints.add_argument("--memory-max", required=True)
    constraints.add_argument("--memory-swap-max", required=True)
    constraints.add_argument("--tasks-max", type=int, required=True)
    constraints.add_argument("--work-bytes", type=int, required=True)
    constraints.add_argument("--work-files", type=int, required=True)
    constraints.add_argument("--tmp-bytes", type=int, required=True)
    constraints.add_argument("--run-bytes", type=int, required=True)
    constraints.add_argument("--model-prompt-bytes", type=int, required=True)
    constraints.add_argument("--model-output-tokens", type=int, required=True)
    constraints.add_argument("--model-timeout", type=int, required=True)
    constraints.add_argument("--model-budget", required=True)

    observation = commands.add_parser("write-generation-observation")
    observation.add_argument("path", type=Path)
    observation.add_argument("--audit", type=Path, required=True)
    observation.add_argument("--stderr", type=Path, required=True)
    observation.add_argument("--generation", type=int, required=True)
    observation.add_argument("--started-at", required=True)
    observation.add_argument("--duration-ms", type=int, required=True)
    observation.add_argument("--outcome", required=True)
    observation.add_argument("--exit-status", type=int, required=True)
    observation.add_argument("--systemd-result", required=True)
    observation.add_argument("--cpu-usage-nsec", type=int)
    observation.add_argument("--memory-peak-bytes", type=int)
    observation.add_argument("--oom-kills", type=int)
    observation.add_argument("--lineage-commit", required=True)
    observation.add_argument("--result-archive-bytes", type=int, required=True)
    observation.add_argument("--published-bytes", type=int)
    observation.add_argument("--published-files", type=int)
    observation.add_argument("--provider", required=True)
    observation.add_argument("--model", required=True)
    observation.add_argument("--constraints-json", required=True)

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
            write_metadata(
                args.path,
                args.fingerprint,
                args.seed_sha256,
                args.instrument_repository,
                args.instrument_ref,
                args.instrument_commit,
            )
        elif args.command == "read-field":
            print(read_field(args.path, args.field))
        elif args.command == "write-observation-metadata":
            write_observation_metadata(
                args.path,
                args.lineage_branch,
                args.observation_branch,
                args.instrument_repository,
                args.instrument_ref,
                args.instrument_commit,
                args.instrument_fingerprint,
                args.seed_sha256,
            )
        elif args.command == "read-observation-field":
            print(read_observation_field(args.path, args.field))
        elif args.command == "constraints-json":
            print(json.dumps(constraint_manifest(args), separators=(",", ":")))
        elif args.command == "write-generation-observation":
            fields = {
                "generation": args.generation,
                "started_at": args.started_at,
                "duration_ms": args.duration_ms,
                "outcome": args.outcome,
                "exit_status": args.exit_status,
                "systemd_result": args.systemd_result,
                "cpu_usage_nsec": args.cpu_usage_nsec,
                "memory_peak_bytes": args.memory_peak_bytes,
                "oom_kills": args.oom_kills,
                "lineage_commit": args.lineage_commit,
                "result_archive_bytes": args.result_archive_bytes,
                "published_bytes": args.published_bytes,
                "published_files": args.published_files,
                "provider": args.provider,
                "model": args.model,
                "constraints": json.loads(args.constraints_json),
            }
            write_generation_observation(
                args.path, args.audit, args.stderr, fields
            )
        elif args.command == "extract-result":
            summary = extract_result(
                args.archive, args.destination, args.max_bytes, args.max_files
            )
            print(json.dumps(summary, sort_keys=True))
    except (
        OSError,
        json.JSONDecodeError,
        tarfile.TarError,
        PublicationError,
        ValueError,
    ) as exc:
        print(f"ouroboros runtime: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
