import subprocess
import tempfile
import unittest
from pathlib import Path

from server import ALLOWED_GIT_COMMANDS, LineageRepository, resolve_paths


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "testdata" / "sample-lineage.bundle"


class LineageRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.worktree = Path(cls.temporary_directory.name) / "sample"
        subprocess.run(
            ["git", "clone", "-q", str(FIXTURE), str(cls.worktree)], check=True
        )
        worktree, git_dir = resolve_paths(str(cls.worktree), None)
        cls.repository = LineageRepository(worktree, git_dir)

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def generation(self, number):
        return next(
            commit for commit in self.repository.generations if commit.generation == number
        )

    def test_counts_generations_by_subject_prefix(self):
        self.assertEqual(len(self.repository.commits), 10)
        self.assertEqual(len(self.repository.generations), 5)
        self.assertEqual(
            [commit.generation for commit in self.repository.generations],
            [1, 2, 3, 4, 5],
        )

        external = [
            commit for commit in self.repository.commits if commit.kind == "external"
        ]
        self.assertEqual(len(external), 2)
        self.assertTrue(all(commit.generation is None for commit in external))

    def test_died_generation_exposes_exit_and_stderr(self):
        details = self.repository.generation_details(self.generation(1).sha)
        self.assertEqual(details["exit_code"], 255)
        self.assertIn('error("boom")', details["stderr_tail"])
        self.assertIn("gen 1 — died", details["journal"]["heading"])

    def test_changed_generation_has_organism_diff(self):
        details = self.repository.generation_details(self.generation(2).sha)
        self.assertIn("diff --git a/organism.el b/organism.el", details["organism_diff"])

    def test_zero_organism_diff_still_exposes_artifacts(self):
        details = self.repository.generation_details(self.generation(5).sha)
        self.assertEqual(details["organism_diff"], "")
        paths = {item["path"] for item in details["touched_files"]}
        self.assertIn("state.el", paths)
        self.assertIn("lineage/gen-0001.el", paths)
        self.assertIn("lineage/gen-0001.rejected", paths)

        rejected = next(
            item for item in details["other_files"] if item["path"].endswith(".rejected")
        )
        self.assertEqual(rejected["artifact_kind"], "rejected")
        self.assertTrue(rejected["snapshot"])

    def test_arbitrary_generation_pair_diff(self):
        result = self.repository.pairwise_diff(
            self.generation(1).sha, self.generation(5).sha
        )
        self.assertEqual(result["from"]["generation"], 1)
        self.assertEqual(result["to"]["generation"], 5)
        self.assertIn("state.el", result["files"])
        self.assertTrue(result["diff"])

    def test_summary_indexes_state_and_rejected_artifacts(self):
        artifacts = self.repository.summary()["artifacts"]
        self.assertEqual({item["kind"] for item in artifacts}, {"state", "rejected"})
        self.assertTrue(all(item["generation"] == 5 for item in artifacts))

    def test_viewer_does_not_dirty_fixture_clone(self):
        self.repository.summary()
        self.repository.generation_details(self.generation(5).sha)
        status = subprocess.run(
            ["git", "-C", str(self.worktree), "status", "--porcelain"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertEqual(status, "")


class PathResolutionTests(unittest.TestCase):
    def test_reads_fixture_with_separate_git_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree = Path(directory) / "lineage"
            git_dir = Path(f"{worktree}.git")
            subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    f"--separate-git-dir={git_dir}",
                    str(FIXTURE),
                    str(worktree),
                ],
                check=True,
            )
            resolved_worktree, resolved_git_dir = resolve_paths(str(worktree), None)
            repository = LineageRepository(resolved_worktree, resolved_git_dir)
            self.assertEqual(resolved_git_dir, git_dir)
            self.assertEqual(len(repository.generations), 5)

    def test_default_falls_back_to_worktree_dot_git(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree = Path(directory) / "lineage"
            (worktree / ".git").mkdir(parents=True)
            _, git_dir = resolve_paths(str(worktree), None)
            self.assertEqual(git_dir, worktree / ".git")

    def test_default_prefers_separate_sibling_git_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree = Path(directory) / "lineage"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            sibling = Path(f"{worktree}.git")
            sibling.mkdir()
            _, git_dir = resolve_paths(str(worktree), None)
            self.assertEqual(git_dir, sibling)

    def test_git_command_allowlist_is_read_only(self):
        self.assertEqual(
            ALLOWED_GIT_COMMANDS,
            {"log", "show", "diff", "cat-file", "rev-list"},
        )


if __name__ == "__main__":
    unittest.main()
