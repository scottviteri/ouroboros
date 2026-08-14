#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent
KERNEL = REPO / "kernel.sh"
INITIALIZER = REPO / "init-lineage.sh"
SEED = REPO / "organism.el"


def run(*args: str, cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


class KernelBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.lineage = root / "lineage"
        self.gitdir = root / "lineage.git"
        self.fakebin = root / "bin"
        self.args_file = root / "bwrap-args"
        self.scope_args_file = root / "systemd-run-args"
        self.sandbox_result = root / "sandbox-result"
        self.fakebin.mkdir()
        self.sandbox_result.mkdir()

        init_env = os.environ.copy()
        init_env.update(
            {
                "LINEAGE": str(self.lineage),
                "GITDIR": str(self.gitdir),
                "OUROBOROS_GIT_NAME": "Kernel Test",
                "OUROBOROS_GIT_EMAIL": "kernel@example.invalid",
            }
        )
        run("bash", str(INITIALIZER), cwd=REPO, env=init_env)
        (self.sandbox_result / "organism.el").write_bytes(
            (self.lineage / "organism.el").read_bytes()
        )

        self.write_executable("timeout", "#!/bin/sh\nshift\nexec \"$@\"\n")
        self.write_executable(
            "bwrap",
            """#!/bin/sh
printf '%s\n' "$@" > "$KERNEL_TEST_ARGS"
if [ "${KERNEL_TEST_BWRAP_EXIT:-0}" -ne 0 ]; then
  echo "synthetic sandbox failure" >&2
  exit "$KERNEL_TEST_BWRAP_EXIT"
fi
exec tar -C "$KERNEL_TEST_RESULT" -cf - .
""",
        )
        self.write_executable(
            "systemd-run",
            """#!/bin/sh
printf '%s\n' "$@" > "$KERNEL_TEST_SCOPE_ARGS"
if [ "${KERNEL_TEST_SCOPE_FAIL:-0}" = 1 ]; then
  exit 1
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --user|--scope|--quiet|--collect|--expand-environment=no) shift ;;
    -p) shift 2 ;;
    *) break ;;
  esac
done
exec "$@"
""",
        )

        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.fakebin}:{self.env['PATH']}",
                "LINEAGE": str(self.lineage),
                "GITDIR": str(self.gitdir),
                "GENERATIONS": "1",
                "MODEL_PROVIDER": "openai",
                "MODEL_NAME": "gpt-5.6",
                "OPENAI_API_KEY": "selected-openai-secret",
                "ANTHROPIC_API_KEY": "unselected-anthropic-secret",
                "KERNEL_TEST_ARGS": str(self.args_file),
                "KERNEL_TEST_SCOPE_ARGS": str(self.scope_args_file),
                "KERNEL_TEST_RESULT": str(self.sandbox_result),
            }
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_executable(self, name: str, contents: str) -> None:
        path = self.fakebin / name
        path.write_text(contents)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def invoke(self, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(KERNEL)],
            cwd=REPO,
            env=self.env | environment,
            text=True,
            capture_output=True,
        )

    def test_initializer_copies_canonical_main_repo_seed(self) -> None:
        self.assertEqual((self.lineage / "organism.el").read_bytes(), SEED.read_bytes())
        root = run("git", "rev-list", "--max-parents=0", "HEAD", cwd=self.lineage).stdout.strip()
        metadata = json.loads(
            run(
                "git", "show", f"{root}:.ouroboros-lineage.json", cwd=self.lineage
            ).stdout
        )
        self.assertEqual(metadata["schema"], "ouroboros-lineage/v1")
        self.assertEqual(len(metadata["instrument_fingerprint"]), 64)
        self.assertEqual(
            run("git", "log", "--format=%s", cwd=self.lineage).stdout.splitlines(),
            ["seed"],
        )

    def test_initializer_refuses_to_reuse_existing_paths(self) -> None:
        result = subprocess.run(
            ["bash", str(INITIALIZER)],
            cwd=REPO,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("new paths", result.stderr)

    def test_changed_trusted_runtime_cannot_continue_existing_lineage(self) -> None:
        instrument = Path(self.tempdir.name) / "changed-instrument"
        instrument.mkdir()
        for name in [
            "organism.el",
            "init-lineage.sh",
            "kernel.sh",
            "model_broker.py",
            "runtime.py",
            "sandbox_runner.sh",
        ]:
            shutil.copy2(REPO / name, instrument / name)
        changed_lineage = Path(self.tempdir.name) / "changed-lineage"
        changed_gitdir = Path(self.tempdir.name) / "changed-lineage.git"
        env = self.env | {
            "LINEAGE": str(changed_lineage),
            "GITDIR": str(changed_gitdir),
        }
        run("bash", str(instrument / "init-lineage.sh"), cwd=instrument, env=env)
        with (instrument / "sandbox_runner.sh").open("a") as runner:
            runner.write("\n# changed physics\n")
        result = subprocess.run(
            ["bash", str(instrument / "kernel.sh")],
            cwd=instrument,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("start a new lineage", result.stderr)

    def test_generation_uses_disposable_size_limited_worktree(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.args_file.read_text().splitlines()
        joined = "\n".join(arguments)
        self.assertIn("--size", arguments)
        self.assertIn("268435456", arguments)
        self.assertIn("--tmpfs", arguments)
        self.assertIn("/work", arguments)
        self.assertNotIn(str(self.lineage), joined)
        self.assertNotIn("--bind", arguments)
        self.assertIn("--ro-bind", arguments)
        self.assertIn("/kernel", arguments)

    def test_generation_has_aggregate_cpu_memory_and_task_ceilings(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.scope_args_file.read_text().splitlines()
        self.assertIn("CPUQuota=20%", arguments)
        self.assertIn("MemoryMax=1G", arguments)
        self.assertIn("MemorySwapMax=0", arguments)
        self.assertIn("TasksMax=64", arguments)
        self.assertIn("--expand-environment=no", arguments)
        self.assertIn("bwrap", arguments)

    def test_scope_failure_is_preflight_not_an_organism_death(self) -> None:
        result = self.invoke(KERNEL_TEST_SCOPE_FAIL="1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("aggregate resource scope", result.stderr)
        self.assertFalse(self.args_file.exists())
        log = run("git", "log", "--format=%s", cwd=self.lineage).stdout.splitlines()
        self.assertEqual(log, ["seed"])

    def test_sandbox_gets_no_backend_authority_or_host_worktree(self) -> None:
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError as exc:
            self.skipTest(f"host forbids Unix sockets: {exc}")
        else:
            probe.close()

        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.args_file.read_text().splitlines()
        joined = "\n".join(arguments)
        self.assertIn("--unshare-net", arguments)
        self.assertIn("--clearenv", arguments)
        self.assertNotIn("selected-openai-secret", joined)
        self.assertNotIn("unselected-anthropic-secret", joined)
        self.assertNotIn("OPENAI_API_KEY", joined)
        self.assertNotIn("ANTHROPIC_API_KEY", joined)
        self.assertNotIn("MODEL_PROVIDER", joined)
        self.assertNotIn("MODEL_NAME", joined)
        self.assertNotIn(str(self.lineage), joined)

    def test_nonzero_exit_never_publishes_speculative_result(self) -> None:
        (self.sandbox_result / "unpublished.txt").write_text("must not appear\n")
        result = self.invoke(KERNEL_TEST_BWRAP_EXIT="9")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.lineage / "unpublished.txt").exists())
        self.assertIn("gen 1 — died", (self.lineage / "journal.md").read_text())
        self.assertIn(
            "staged writes discarded",
            run("git", "log", "-1", "--format=%s", cwd=self.lineage).stdout,
        )

    def test_other_file_change_gets_unambiguous_journal_message(self) -> None:
        (self.sandbox_result / "state.el").write_text("(:generation 1)\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        journal = (self.lineage / "journal.md").read_text()
        subject = run("git", "log", "-1", "--format=%s", cwd=self.lineage).stdout
        self.assertIn("changed — organism unchanged; 1 other paths", journal)
        self.assertIn("changed (organism unchanged; 1 other paths)", subject)

    def test_gitignore_cannot_hide_successful_hereditary_files(self) -> None:
        (self.sandbox_result / ".gitignore").write_text("hidden.txt\n")
        (self.sandbox_result / "hidden.txt").write_text("still hereditary\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        tracked = run("git", "ls-files", cwd=self.lineage).stdout.splitlines()
        self.assertIn(".gitignore", tracked)
        self.assertIn("hidden.txt", tracked)

    def test_no_change_has_no_zero_zero_diffstat(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        journal = (self.lineage / "journal.md").read_text()
        self.assertIn("gen 1 — no-change", journal)
        self.assertNotIn("+0/-0", journal)

    def test_rejects_unknown_provider_before_generation(self) -> None:
        result = self.invoke(MODEL_PROVIDER="unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("MODEL_PROVIDER", result.stderr)
        self.assertFalse(self.args_file.exists())

    def test_rejects_missing_selected_key(self) -> None:
        env = self.env.copy()
        env.pop("OPENAI_API_KEY")
        result = subprocess.run(
            ["bash", str(KERNEL)], cwd=REPO, env=env, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("OPENAI_API_KEY", result.stderr)
        self.assertFalse(self.args_file.exists())


if __name__ == "__main__":
    unittest.main()
