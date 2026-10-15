#!/usr/bin/env python3

from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import runtime


class RuntimeTests(unittest.TestCase):
    def test_observation_keeps_bounded_whole_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / "audit.jsonl"
            stderr = root / "stderr"
            frames = "".join(f"  frame-{i}()\n" for i in range(50))
            stderr.write_text("Error: wrong-type-argument (stringp nil)\n" + frames)
            runtime.write_generation_observation(
                root / "small.json", audit, stderr, {"generation": 1}
            )
            small = json.loads((root / "small.json").read_text())
            self.assertIn("wrong-type-argument", small["stderr"])
            self.assertNotIn("wrong-type-argument", small["stderr_tail"])
            self.assertEqual(small["stderr_bytes"], len(stderr.read_bytes()))

            keep = runtime.STDERR_KEEP_BYTES
            stderr.write_bytes(b"E" * keep + b"M" * (3 * keep) + b"T" * keep)
            runtime.write_generation_observation(
                root / "large.json", audit, stderr, {"generation": 2}
            )
            large = json.loads((root / "large.json").read_text())
            self.assertEqual(large["stderr_bytes"], 5 * keep)
            self.assertTrue(large["stderr"].startswith("E" * keep))
            self.assertTrue(large["stderr"].endswith("T" * keep))
            self.assertIn(f"{3 * keep} bytes omitted", large["stderr"])
            self.assertLess(len(large["stderr"]), 2 * keep + 100)

    def test_fingerprint_changes_with_trusted_runtime_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kernel.sh"
            path.write_text("first\n")
            before = runtime.instrument_fingerprint([path])
            path.write_text("second\n")
            after = runtime.instrument_fingerprint([path])
            self.assertNotEqual(before, after)

    def test_metadata_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".ouroboros-lineage.json"
            fingerprint = "a" * 64
            runtime.write_metadata(
                path,
                fingerprint,
                "b" * 64,
                "git@example.invalid:instrument.git",
                "agent/test",
                "c" * 40,
            )
            self.assertEqual(
                runtime.read_field(path, "instrument_fingerprint"), fingerprint
            )
            self.assertEqual(runtime.read_field(path, "instrument_commit"), "c" * 40)
            self.assertEqual(json.loads(path.read_text())["schema"], runtime.LINEAGE_SCHEMA)

    def test_observation_metadata_pins_instrument_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            runtime.write_observation_metadata(
                path,
                "lineage-test",
                "observations/lineage-test",
                "git@example.invalid:instrument.git",
                "agent/test",
                "d" * 40,
                "a" * 64,
                "b" * 64,
            )
            self.assertEqual(
                runtime.read_observation_field(path, "instrument_commit"), "d" * 40
            )

    def make_archive(self, path: Path, entries: list[tuple[str, bytes]]) -> None:
        with tarfile.open(path, "w") as archive:
            for name, contents in entries:
                info = tarfile.TarInfo(name)
                info.size = len(contents)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(contents))

    def test_extracts_bounded_regular_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "result.tar"
            self.make_archive(archive, [("organism.el", b"(message \"ok\")\n")])
            summary = runtime.extract_result(archive, root / "tree", 1024, 10)
            self.assertEqual(summary["files"], 1)
            self.assertEqual((root / "tree/organism.el").read_bytes(), b'(message "ok")\n')

    def test_rejects_reserved_git_and_journal_paths(self) -> None:
        for name in [".git/config", "journal.md", ".ouroboros-lineage.json"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "result.tar"
                self.make_archive(archive, [(name, b"forbidden")])
                with self.assertRaises(runtime.PublicationError):
                    runtime.extract_result(archive, root / "tree", 1024, 10)

    def test_rejects_parent_traversal_and_size_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traversal = root / "traversal.tar"
            self.make_archive(traversal, [("../escape", b"no")])
            with self.assertRaises(runtime.PublicationError):
                runtime.extract_result(traversal, root / "tree-a", 1024, 10)

            oversized = root / "oversized.tar"
            self.make_archive(oversized, [("large", b"12345")])
            with self.assertRaises(runtime.PublicationError):
                runtime.extract_result(oversized, root / "tree-b", 4, 10)


if __name__ == "__main__":
    unittest.main()
