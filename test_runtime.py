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
            runtime.write_metadata(path, fingerprint, "b" * 64)
            self.assertEqual(runtime.read_fingerprint(path), fingerprint)
            self.assertEqual(json.loads(path.read_text())["schema"], runtime.LINEAGE_SCHEMA)

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
