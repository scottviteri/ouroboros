#!/usr/bin/env python3
"""Read-only queries over an ouroboros lineage."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


FIELD_SEPARATOR = "\x1f"
RECORD_SEPARATOR = "\x1e"
MAX_DIFF_CHARS = 1_000_000
MAX_FILE_CHARS = 400_000
ALLOWED_GIT_COMMANDS = frozenset({"log", "show", "diff", "cat-file", "rev-list"})

GENERATION_SUBJECT = re.compile(
    r"^gen\s+(?P<number>\d+):\s+(?P<status>changed|no-change|died)\b"
)
JOURNAL_HEADING = re.compile(
    r"^##\s+gen\s+(?P<number>\d+)\s+[—-]\s+"
    r"(?P<status>changed|no-change|died)\b.*$",
    re.MULTILINE,
)


class GitReadError(RuntimeError):
    """A read-only git command could not be completed."""


@dataclass(frozen=True)
class Commit:
    sha: str
    parent: str | None
    date: str
    subject: str
    kind: str
    generation: int | None
    reported_generation: int | None

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["short_sha"] = self.sha[:7]
        result["number_matches_subject"] = (
            self.generation is None
            or self.reported_generation is None
            or self.generation == self.reported_generation
        )
        return result


def _resolve_git_pointer(path: Path) -> Path:
    """Resolve a worktree-style .git pointer without asking git to mutate it."""
    if not path.is_file():
        return path
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        raise ValueError(f"{path} is empty, not a git-dir pointer")
    first_line = lines[0]
    prefix = "gitdir: "
    if not first_line.startswith(prefix):
        raise ValueError(f"{path} is a file, but not a git-dir pointer")
    target = Path(first_line[len(prefix) :])
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve()


def resolve_paths(worktree_arg: str, git_dir_arg: str | None) -> tuple[Path, Path]:
    worktree = Path(worktree_arg).expanduser().resolve()
    if not worktree.is_dir():
        raise ValueError(f"lineage worktree does not exist: {worktree}")

    if git_dir_arg:
        git_dir = Path(git_dir_arg).expanduser().resolve()
    else:
        separate = Path(f"{worktree}.git")
        git_dir = separate if separate.exists() else worktree / ".git"

    git_dir = _resolve_git_pointer(git_dir)
    if not git_dir.is_dir():
        raise ValueError(
            f"git directory does not exist: {git_dir} "
            "(pass it explicitly with --git-dir)"
        )
    return worktree, git_dir


def clipped(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n[lineage reader truncated {omitted:,} characters]"


class LineageRepository:
    """In-memory index backed only by explicitly allowlisted git reads."""

    def __init__(self, worktree: Path, git_dir: Path) -> None:
        self.worktree = worktree
        self.git_dir = git_dir
        self.git_executable = shutil.which("git")
        if not self.git_executable:
            raise ValueError("git is required to inspect a lineage")

        self.commits = self._read_history()
        if not self.commits:
            raise ValueError("the lineage has no commits reachable from HEAD")
        self.generations = [c for c in self.commits if c.generation is not None]
        self._commits_by_sha = {c.sha: c for c in self.commits}
        self._generations_by_sha = {c.sha: c for c in self.generations}

    def _git(self, command: str, *arguments: str) -> str:
        if command not in ALLOWED_GIT_COMMANDS:
            raise AssertionError(f"git command is not read-only allowlisted: {command}")

        environment = os.environ.copy()
        environment.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "LC_ALL": "C",
            }
        )
        environment.pop("GIT_EXTERNAL_DIFF", None)

        invocation = [
            self.git_executable,
            f"--git-dir={self.git_dir}",
            f"--work-tree={self.worktree}",
            "--no-pager",
            command,
            *arguments,
        ]
        try:
            completed = subprocess.run(
                invocation,
                cwd=self.worktree,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitReadError(f"git {command} timed out") from exc

        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        if completed.returncode:
            detail = clipped(stderr or "unknown git error", 4_000)
            raise GitReadError(f"git {command} failed: {detail}")
        return stdout

    def _read_history(self) -> list[Commit]:
        output = self._git(
            "log",
            "--first-parent",
            "--reverse",
            f"--format=%H%x1f%P%x1f%aI%x1f%s%x1e",
            "HEAD",
        )
        commits: list[Commit] = []
        generation_count = 0
        for raw_record in output.split(RECORD_SEPARATOR):
            raw_record = raw_record.strip("\r\n")
            if not raw_record:
                continue
            fields = raw_record.split(FIELD_SEPARATOR, 3)
            if len(fields) != 4:
                raise GitReadError("could not parse git log output")
            sha, parents, date, subject = fields
            parent = parents.split()[0] if parents else None
            reported_generation: int | None = None

            if subject.startswith("gen "):
                generation_count += 1
                match = GENERATION_SUBJECT.match(subject)
                if match:
                    reported_generation = int(match.group("number"))
                    kind = match.group("status")
                else:
                    kind = "generation"
                generation: int | None = generation_count
            else:
                generation = None
                if subject == "external edit" or subject.startswith("external edit:"):
                    kind = "external"
                elif subject == "seed":
                    kind = "seed"
                else:
                    kind = "context"

            commits.append(
                Commit(
                    sha=sha,
                    parent=parent,
                    date=date,
                    subject=subject,
                    kind=kind,
                    generation=generation,
                    reported_generation=reported_generation,
                )
            )
        return commits

    def summary(self) -> dict[str, Any]:
        return {
            "worktree": str(self.worktree),
            "git_dir": str(self.git_dir),
            "head": self.commits[-1].sha,
            "commit_count": len(self.commits),
            "generation_count": len(self.generations),
            "commits": [commit.public() for commit in self.commits],
            "generations": [commit.public() for commit in self.generations],
            "artifacts": self.artifact_events(),
        }

    def generation(self, selector: str | int) -> Commit:
        """Resolve a generation number or an unambiguous generation SHA prefix."""
        text = str(selector)
        if text.isdigit():
            number = int(text)
            matches = [c for c in self.generations if c.generation == number]
        else:
            matches = [c for c in self.generations if c.sha.startswith(text)]
        if not matches:
            raise ValueError(f"unknown generation: {selector}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous generation SHA prefix: {selector}")
        return matches[0]

    @lru_cache(maxsize=512)
    def _changed_files(self, sha: str) -> tuple[tuple[str, str], ...]:
        commit = self._commits_by_sha[sha]
        if commit.parent:
            output = self._git(
                "diff",
                "--name-status",
                "-z",
                "--no-renames",
                commit.parent,
                commit.sha,
            )
        else:
            output = self._git(
                "show", "--format=", "--name-status", "-z", "--no-renames", commit.sha
            )

        tokens = output.split("\0")
        if tokens and tokens[-1] == "":
            tokens.pop()
        if len(tokens) % 2:
            raise GitReadError("could not parse changed-file list")
        return tuple(
            (tokens[index], tokens[index + 1]) for index in range(0, len(tokens), 2)
        )

    def _diff(self, commit: Commit, path: str | None = None) -> str:
        common = ["--no-ext-diff", "--no-textconv", "--no-color", "--unified=3"]
        if commit.parent:
            arguments = [*common, commit.parent, commit.sha]
            if path is not None:
                arguments.extend(["--", path])
            return self._git("diff", *arguments)

        arguments = ["--format=", *common, commit.sha]
        if path is not None:
            arguments.extend(["--", path])
        return self._git("show", *arguments)

    def _snapshot(self, sha: str, path: str) -> str | None:
        try:
            return self._git("show", f"{sha}:{path}")
        except GitReadError:
            return None

    def _journal_entry(self, commit: Commit) -> dict[str, Any]:
        assert commit.generation is not None
        try:
            journal = self._git("show", f"{commit.sha}:journal.md")
        except GitReadError:
            return {"found": False, "heading": "", "body": "", "raw": ""}

        header = re.compile(
            rf"^##\s+gen\s+{commit.generation}\s+[—-].*$", re.MULTILINE
        ).search(journal)
        if not header:
            return {"found": False, "heading": "", "body": "", "raw": ""}

        following = re.compile(r"^##\s+gen\s+\d+\s+[—-]", re.MULTILINE).search(
            journal, header.end()
        )
        end = following.start() if following else len(journal)
        raw = journal[header.start() : end].strip()
        heading, _, body = raw.partition("\n")
        return {
            "found": True,
            "heading": heading.removeprefix("## ").strip(),
            "body": body.strip(),
            "raw": raw,
        }

    def generation_details(self, selector: str | int) -> dict[str, Any]:
        commit = self.generation(selector)
        changed_files = self._changed_files(commit.sha)
        journal = self._journal_entry(commit)

        exit_match = re.search(r"(?m)^exit\s+(\d+)\s*$", journal["body"])
        if not exit_match:
            exit_match = re.search(r"\(exit\s+(\d+)\)", commit.subject)
        exit_code = int(exit_match.group(1)) if exit_match else None

        stderr_match = re.search(
            r"(?ms)^~~~[^\n]*\n(.*?)^~~~\s*$", journal["body"]
        )
        stderr_tail = stderr_match.group(1).rstrip() if stderr_match else ""

        failed_organism = None
        if commit.kind == "died" and commit.parent:
            loaded_source = self._snapshot(commit.parent, "organism.el")
            if loaded_source is not None:
                failed_organism = clipped(loaded_source, MAX_FILE_CHARS)

        other_files: list[dict[str, Any]] = []
        for status, path in changed_files:
            if path in {"organism.el", "journal.md"}:
                continue
            if path == "state.el":
                artifact_kind = "state"
            elif path.endswith(".rejected"):
                artifact_kind = "rejected"
            elif path.startswith("lineage/") and path.endswith(".el"):
                artifact_kind = "successor"
            else:
                artifact_kind = None

            snapshot = None
            if artifact_kind and not status.startswith("D"):
                value = self._snapshot(commit.sha, path)
                snapshot = clipped(value, MAX_FILE_CHARS) if value is not None else None
            other_files.append(
                {
                    "status": status,
                    "path": path,
                    "artifact_kind": artifact_kind,
                    "diff": clipped(self._diff(commit, path), MAX_FILE_CHARS),
                    "snapshot": snapshot,
                }
            )

        return {
            "commit": commit.public(),
            "journal": journal,
            "exit_code": exit_code,
            "stderr_tail": stderr_tail,
            "failed_organism": failed_organism,
            "touched_files": [
                {"status": status, "path": path} for status, path in changed_files
            ],
            "organism_diff": clipped(self._diff(commit, "organism.el"), MAX_DIFF_CHARS),
            "other_files": other_files,
        }

    def pairwise_diff(
        self, before_selector: str | int, after_selector: str | int
    ) -> dict[str, Any]:
        before = self.generation(before_selector)
        after = self.generation(after_selector)
        output = self._git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--unified=3",
            before.sha,
            after.sha,
        )
        names = self._git(
            "diff", "--name-only", "-z", "--no-renames", before.sha, after.sha
        )
        return {
            "from": before.public(),
            "to": after.public(),
            "files": [path for path in names.split("\0") if path],
            "diff": clipped(output, MAX_DIFF_CHARS),
        }

    def artifact_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for commit in self.generations:
            for status, path in self._changed_files(commit.sha):
                if path == "state.el":
                    kind = "state"
                elif path.endswith(".rejected"):
                    kind = "rejected"
                else:
                    continue
                events.append(
                    {
                        "generation": commit.generation,
                        "sha": commit.sha,
                        "status": status,
                        "path": path,
                        "kind": kind,
                    }
                )
        return events

    def verify(self) -> dict[str, Any]:
        """Check the derivable generation-number and journal recording contract."""
        errors: list[str] = []
        for commit in self.generations:
            if commit.reported_generation is None:
                errors.append(f"{commit.sha[:7]} has an unparseable generation subject")
            elif commit.reported_generation != commit.generation:
                errors.append(
                    f"{commit.sha[:7]} reports generation {commit.reported_generation}, "
                    f"but is generation {commit.generation} by first-parent count"
                )

            journal = self._journal_entry(commit)
            if not journal["found"]:
                errors.append(
                    f"generation {commit.generation} has no matching journal entry"
                )
                continue
            heading = JOURNAL_HEADING.match(f"## {journal['heading']}")
            if not heading:
                errors.append(
                    f"generation {commit.generation} has an unparseable journal heading"
                )
            elif heading.group("status") != commit.kind:
                errors.append(
                    f"generation {commit.generation} is {commit.kind} in git but "
                    f"{heading.group('status')} in the journal"
                )

        head_journal = self._snapshot(self.commits[-1].sha, "journal.md") or ""
        journal_numbers = [
            int(match.group("number")) for match in JOURNAL_HEADING.finditer(head_journal)
        ]
        expected_numbers = list(range(1, len(self.generations) + 1))
        if journal_numbers != expected_numbers:
            errors.append(
                "HEAD journal generation sequence is "
                f"{journal_numbers}, expected {expected_numbers}"
            )

        return {
            "valid": not errors,
            "commit_count": len(self.commits),
            "generation_count": len(self.generations),
            "errors": errors,
        }


def parse_args(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worktree", help="path to the lineage worktree")
    parser.add_argument(
        "--git-dir",
        help="path to its git directory (default: <worktree>.git, then <worktree>/.git)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary", help="emit the lineage index as JSON")
    generation = commands.add_parser(
        "generation", help="emit one generation's journal, diffs, and artifacts"
    )
    generation.add_argument("selector", help="generation number or commit SHA prefix")
    compare = commands.add_parser("compare", help="compare any two generations")
    compare.add_argument("before", help="earlier generation number or SHA prefix")
    compare.add_argument("after", help="later generation number or SHA prefix")
    commands.add_parser("verify", help="verify numbering and journal correspondence")
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    options = parse_args(arguments)
    try:
        worktree, git_dir = resolve_paths(options.worktree, options.git_dir)
        repository = LineageRepository(worktree, git_dir)
        if options.command == "summary":
            result = repository.summary()
        elif options.command == "generation":
            result = repository.generation_details(options.selector)
        elif options.command == "compare":
            result = repository.pairwise_diff(options.before, options.after)
        else:
            result = repository.verify()
    except (OSError, ValueError, GitReadError) as exc:
        print(f"lineage reader: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if options.command == "verify" and not result["valid"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
