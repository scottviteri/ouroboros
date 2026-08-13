#!/usr/bin/env python3

from __future__ import annotations

import os
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent
KERNEL = REPO / "kernel.sh"


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
        self.lineage.mkdir()
        self.fakebin.mkdir()

        (self.lineage / "organism.el").write_text("(message \"seed\")\n")
        (self.lineage / "journal.md").write_text("# journal\n")
        run(
            "git",
            "init",
            f"--separate-git-dir={self.gitdir}",
            str(self.lineage),
            cwd=root,
        )
        run("git", "config", "user.name", "Kernel Test", cwd=self.lineage)
        run("git", "config", "user.email", "kernel@example.invalid", cwd=self.lineage)
        run("git", "add", "-A", cwd=self.lineage)
        run("git", "commit", "-m", "seed", cwd=self.lineage)

        self.write_executable(
            "timeout",
            "#!/bin/sh\nshift\nexec \"$@\"\n",
        )
        self.write_executable(
            "bwrap",
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$KERNEL_TEST_ARGS\"\n",
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
            }
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_executable(self, name: str, contents: str) -> None:
        path = self.fakebin / name
        path.write_text(contents)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def test_generation_bwrap_contract_is_networkless_and_credential_free(self) -> None:
        source = KERNEL.read_text()
        invocation = source.split('timeout "$WALL" bwrap', 1)[1].split(
            '    >"$LOG" 2>&1', 1
        )[0]
        self.assertIn("--unshare-net", invocation)
        self.assertIn('--ro-bind "$BROKER_DIR" /kernel', invocation)
        self.assertNotIn("API_KEY", invocation)
        self.assertNotIn("MODEL_PROVIDER", invocation)
        self.assertNotIn("MODEL_NAME", invocation)
        self.assertNotIn("resolv.conf", invocation)
        self.assertNotIn("/etc/ssl", invocation)

    def test_sandbox_gets_only_the_model_socket_not_backend_authority(self) -> None:
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError as exc:
            self.skipTest(f"host forbids Unix sockets: {exc}")
        else:
            probe.close()

        result = subprocess.run(
            ["bash", str(KERNEL)],
            cwd=REPO,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.args_file.read_text().splitlines()
        joined = "\n".join(arguments)

        self.assertIn("--unshare-net", arguments)
        self.assertIn("/kernel", arguments)
        self.assertIn("--clearenv", arguments)
        self.assertNotIn("selected-openai-secret", joined)
        self.assertNotIn("unselected-anthropic-secret", joined)
        self.assertNotIn("OPENAI_API_KEY", joined)
        self.assertNotIn("ANTHROPIC_API_KEY", joined)
        self.assertNotIn("MODEL_PROVIDER", joined)
        self.assertNotIn("MODEL_NAME", joined)
        self.assertNotIn("/etc/resolv.conf", arguments)
        self.assertNotIn("/etc/ssl", arguments)

    def test_rejects_unknown_provider_before_starting_a_generation(self) -> None:
        env = self.env | {"MODEL_PROVIDER": "unknown"}
        result = subprocess.run(
            ["bash", str(KERNEL)], cwd=REPO, env=env, text=True, capture_output=True
        )
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
