#!/usr/bin/env python3
"""Real temporary Git fixtures for candidate-head/bootstrap guards."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from prepare_candidate import prepare


class CandidateGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--initial-branch=main")
        (self.root / "README.md").write_text("Synthetic infrastructure\n")
        self.empty = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-m", "Synthetic candidate fixture")
        return self.git("rev-parse", "HEAD")

    def formula(self, version="0.1.1", extra=""):
        path = self.root / "Formula/skuggsja.rb"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"# Skuggsja release version: {version}\n" + extra)
        return self.commit()

    def test_bootstrap_explicitly_has_no_installation_result(self):
        result = prepare(self.root, self.empty, "0" * 40)
        self.assertEqual(result["has_formula"], "false")
        self.assertIn("no installation result", result["scope"])

    def test_initial_formula_uses_explicit_older_release_baseline(self):
        head = self.formula()
        self.assertEqual(prepare(self.root, head, self.empty)["baseline_tag"], "v0.1.0")

    def test_newer_candidate_uses_exact_base_version(self):
        old = self.formula("0.1.1")
        head = self.formula("0.2.0")
        self.assertEqual(prepare(self.root, head, old)["baseline_tag"], "v0.1.1")

    def test_wrong_candidate_head_fails(self):
        head = self.formula()
        with self.assertRaisesRegex(ValueError, "exact candidate"):
            prepare(self.root, self.empty, "")
        self.assertNotEqual(head, self.empty)

    def test_uncommitted_formula_change_fails(self):
        head = self.formula()
        (self.root / "Formula/skuggsja.rb").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checked commit"):
            prepare(self.root, head, self.empty)

    def test_same_version_byte_replacement_fails(self):
        base = self.formula()
        head = self.formula(extra="# changed under same stable version\n")
        with self.assertRaisesRegex(ValueError, "stable version"):
            prepare(self.root, head, base)

    def test_unchanged_formula_infrastructure_change_passes(self):
        base = self.formula()
        (self.root / "README.md").write_text("Another synthetic infrastructure checkpoint\n")
        head = self.commit()
        self.assertEqual(prepare(self.root, head, base)["baseline_tag"], "v0.1.0")

    def test_removed_formula_cannot_claim_bootstrap(self):
        base = self.formula()
        (self.root / "Formula/skuggsja.rb").unlink()
        head = self.commit()
        with self.assertRaisesRegex(ValueError, "event base"):
            prepare(self.root, head, base)

    def test_deleted_formula_history_rejects_even_without_base(self):
        self.formula()
        (self.root / "Formula/skuggsja.rb").unlink()
        head = self.commit()
        with self.assertRaisesRegex(ValueError, "removed"):
            prepare(self.root, head, "")

    def test_untracked_formula_rejected_in_bootstrap(self):
        path = self.root / "Formula/skuggsja.rb"
        path.parent.mkdir()
        path.write_text("# Skuggsja release version: 0.1.1\n")
        with self.assertRaisesRegex(ValueError, "Untracked"):
            prepare(self.root, self.empty, "")

    def test_malformed_base_rejected_before_bootstrap(self):
        with self.assertRaisesRegex(ValueError, "base commit SHA"):
            prepare(self.root, self.empty, "main")

    def test_unavailable_base_cannot_silently_select_bootstrap(self):
        with self.assertRaises(RuntimeError):
            prepare(self.root, self.empty, "a" * 40)

    def test_downgrade_rejected(self):
        old = self.formula("0.2.0")
        head = self.formula("0.1.1")
        with self.assertRaisesRegex(ValueError, "downgrades"):
            prepare(self.root, head, old)

    def test_no_real_upgrade_baseline_rejected(self):
        head = self.formula("0.1.0")
        with self.assertRaisesRegex(ValueError, "older"):
            prepare(self.root, head, self.empty)


if __name__ == "__main__":
    unittest.main()
