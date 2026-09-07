#!/usr/bin/env python3
"""Offline proposal tests: fake GitHub graph, no Git or remote writes."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import propose_update as proposer
from update_skuggsja_test import make_release

MAIN = "1" * 40
OLD_MAIN = "2" * 40
OLD_HEAD = "3" * 40
NEW_HEAD = "4" * 40
BRANCH = "update-skuggsja-v1.2.3"


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.main = MAIN
        self.main_reads = 0
        self.advance_on_read = None
        self.branch = None
        self.prs = []
        self.contents = {}
        self.commits = {MAIN: {"tree": {"sha": "5" * 40}, "parents": [{"sha": OLD_MAIN}]}}
        self.files = [{"filename": proposer.FORMULA, "status": "added"}]

    def __call__(self, method, endpoint, body=None, missing=False):
        self.calls.append((method, endpoint, body, missing))
        if method == "GET":
            if endpoint == "git/ref/heads/main":
                self.main_reads += 1
                if self.main_reads == self.advance_on_read:
                    self.main = "9" * 40
                return {"object": {"sha": self.main}}
            if endpoint == "git/ref/heads/" + BRANCH:
                return {"object": {"sha": self.branch}} if self.branch else None
            if endpoint.startswith("contents/"):
                ref = endpoint.split("?ref=")[1]
                data = self.contents.get(ref)
                return {"type": "file", "encoding": "base64", "content": base64.b64encode(data).decode()} if data else None
            if endpoint.startswith("pulls?"):
                return self.prs
            if endpoint.startswith("compare/"):
                return {"files": self.files}
            if endpoint.startswith("git/commits/"):
                return self.commits[endpoint.rsplit("/", 1)[1]]
        if method == "POST":
            if endpoint == "git/blobs":
                return {"sha": "6" * 40}
            if endpoint == "git/trees":
                return {"sha": "7" * 40}
            if endpoint == "git/commits":
                return {"sha": NEW_HEAD}
            if endpoint == "git/refs":
                if self.branch:
                    raise RuntimeError("Concurrent branch creation")
                self.branch = body["sha"]
                return {"object": {"sha": self.branch}}
            if endpoint == "pulls":
                if self.prs:
                    raise RuntimeError("Duplicate PR")
                pr = {"state": "open", "html_url": "https://github.com/0merUfuk/homebrew-skuggsja/pull/1"}
                self.prs.append(pr)
                return pr
        if method == "PATCH" and endpoint == "git/refs/heads/" + BRANCH:
            if body["force"] is not False:
                raise AssertionError("Force update attempted")
            self.branch = body["sha"]
            return {"object": {"sha": self.branch}}
        raise AssertionError("Unexpected fake API request: " + repr((method, endpoint, body)))

    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]


class ProposerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skuggsja-proposal-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.formula = self.root / "skuggsja.rb"
        self.formula.write_bytes(make_release()[1]["skuggsja.rb"])
        self.manifest = {
            "tag": "v1.2.3", "version": "1.2.3", "verified_attestations": 6,
            "repository": "0merUfuk/skuggsja",
            "signer_workflow": "0merUfuk/skuggsja/.github/workflows/release.yml",
            "source_sha": "a" * 40, "formula_path": str(self.formula),
            "formula_sha256": hashlib.sha256(self.formula.read_bytes()).hexdigest(),
        }
        self.fake = FakeAPI()

    def propose(self):
        return proposer.propose(self.manifest, MAIN, call=self.fake)

    def reject(self, message):
        with self.assertRaisesRegex(ValueError, message):
            self.propose()
        self.assertFalse(self.fake.writes())

    def existing_branch(self, parent=MAIN, pr=True):
        self.fake.branch = OLD_HEAD
        self.fake.contents[OLD_HEAD] = self.formula.read_bytes()
        self.fake.commits[OLD_HEAD] = {"parents": [{"sha": parent}]}
        if pr:
            self.fake.prs = [{"state": "open", "html_url": "https://github.com/0merUfuk/homebrew-skuggsja/pull/1"}]

    def test_bootstrap_creates_one_formula_only_branch_and_pr(self):
        result = self.propose()
        self.assertEqual(result["status"], "candidate_open")
        calls = self.fake.writes()
        self.assertEqual([item[1] for item in calls], ["git/blobs", "git/trees", "git/commits", "git/refs", "pulls"])
        self.assertEqual(base64.b64decode(calls[0][2]["content"]), self.formula.read_bytes())
        self.assertEqual(calls[1][2], {"base_tree": "5" * 40,
            "tree": [{"path": "Formula/skuggsja.rb", "mode": "100644", "type": "blob", "sha": "6" * 40}]})
        self.assertEqual(calls[2][2]["parents"], [MAIN])
        self.assertEqual(calls[3][2], {"ref": "refs/heads/" + BRANCH, "sha": NEW_HEAD})
        self.assertEqual(calls[4][2]["head"], BRANCH)
        self.assertEqual(calls[4][2]["base"], "main")
        self.assertIn("Approve workflows", calls[4][2]["body"])
        self.assertFalse(any(item[1] == "git/refs/heads/main" for item in calls))

    def test_invalid_expected_main_rejected_before_api(self):
        with self.assertRaisesRegex(ValueError, "full bootstrap/main"):
            proposer.propose(self.manifest, "main", self.fake)
        self.assertEqual(self.fake.calls, [])

    def test_expected_main_must_match_remote_head(self):
        self.fake.main = OLD_MAIN
        self.reject("Main advanced")

    def test_untrusted_tag_cannot_name_ref(self):
        for tag in ["1.2.3", "v01.2.3", "v1.2.3-rc.1", "v1.2.3\n", "v1.2.3/other"]:
            with self.subTest(tag=tag):
                self.manifest["tag"] = tag
                self.reject("canonical stable")
        self.assertEqual(self.fake.calls, [])

    def test_incomplete_or_wrong_provenance_rejected(self):
        for key, value in [("version", "9.0.0"), ("verified_attestations", 5),
                           ("repository", "attacker/skuggsja"), ("signer_workflow", "other.yml"),
                           ("source_sha", "main")]:
            with self.subTest(key=key):
                old = self.manifest[key]
                self.manifest[key] = value
                self.reject("incomplete|provenance|source commit")
                self.manifest[key] = old
        self.assertEqual(self.fake.calls, [])

    def test_formula_mutation_rejected_before_api(self):
        self.formula.write_bytes(self.formula.read_bytes() + b"# mutation\n")
        self.reject("staged formula changed")
        self.assertEqual(self.fake.calls, [])

    def test_formula_marker_must_agree_with_manifest(self):
        self.manifest.update(tag="v2.0.0", version="2.0.0")
        self.reject("version differs")

    def test_already_imported_version_has_no_writes(self):
        self.fake.contents[MAIN] = self.formula.read_bytes()
        self.assertEqual(self.propose()["status"], "already_imported")
        self.assertFalse(self.fake.writes())

    def test_rollback_against_main_is_rejected_independently(self):
        self.fake.contents[MAIN] = make_release("2.0.0")[1]["skuggsja.rb"]
        self.reject("roll back")

    def test_same_version_different_main_bytes_is_rejected(self):
        self.fake.contents[MAIN] = make_release(extra_ruby="  # another same-version asset\n")[1]["skuggsja.rb"]
        self.reject("different formula bytes on main")

    def test_open_matching_candidate_reused_without_writes(self):
        self.existing_branch()
        result = self.propose()
        self.assertEqual(result["status"], "candidate_reused")
        self.assertEqual(result["head"], OLD_HEAD)
        self.assertFalse(self.fake.writes())

    def test_existing_matching_branch_without_pr_creates_only_pr(self):
        self.existing_branch(pr=False)
        self.assertEqual(self.propose()["status"], "candidate_reused")
        self.assertEqual([call[1] for call in self.fake.writes()], ["pulls"])

    def test_closed_pr_requires_maintainer_and_is_never_reopened(self):
        self.existing_branch()
        self.fake.prs[0]["state"] = "closed"
        self.assertEqual(self.propose()["status"], "closed_candidate_requires_maintainer")
        self.assertFalse(self.fake.writes())

    def test_multiple_prs_for_version_fail_closed(self):
        self.existing_branch()
        self.fake.prs *= 2
        self.reject("More than one PR")

    def test_open_pr_with_missing_branch_is_rejected(self):
        self.existing_branch()
        self.fake.branch = None
        self.reject("expected version branch")

    def test_same_version_branch_bytes_cannot_be_replaced(self):
        self.existing_branch()
        self.fake.contents[OLD_HEAD] += b"# changed\n"
        self.reject("different formula bytes")

    def test_non_formula_or_ambiguous_existing_changes_are_rejected(self):
        self.existing_branch()
        for files in [[], None, [{"filename": "README.md", "status": "modified"}],
                      [{"filename": proposer.FORMULA, "status": "renamed"}],
                      [{"filename": proposer.FORMULA, "status": "modified"}, {"filename": "other", "status": "added"}]]:
            with self.subTest(files=files):
                self.fake.files = files
                self.reject("non-formula changes")

    def test_newer_main_refresh_uses_merge_parent_fast_forward_without_new_pr(self):
        self.existing_branch(parent=OLD_MAIN)
        result = self.propose()
        self.assertEqual(result["status"], "candidate_open")
        calls = self.fake.writes()
        self.assertEqual([item[1] for item in calls], ["git/blobs", "git/trees", "git/commits", "git/refs/heads/" + BRANCH])
        self.assertEqual(calls[1][2]["base_tree"], self.fake.commits[MAIN]["tree"]["sha"])
        self.assertEqual(calls[2][2]["parents"], [OLD_HEAD, MAIN])
        self.assertEqual(calls[3][2], {"sha": NEW_HEAD, "force": False})

    def test_main_advancing_during_inspection_blocks_all_writes(self):
        self.fake.advance_on_read = 2
        self.reject("Main advanced")

    def test_main_advancing_after_object_creation_blocks_branch_and_pr(self):
        self.fake.advance_on_read = 3
        with self.assertRaisesRegex(ValueError, "Main advanced"):
            self.propose()
        self.assertEqual([call[1] for call in self.fake.writes()], ["git/blobs", "git/trees", "git/commits"])
        self.assertIsNone(self.fake.branch)
        self.assertFalse(self.fake.prs)

    def test_main_advancing_before_reusing_candidate_blocks_pr_creation(self):
        self.existing_branch(pr=False)
        self.fake.advance_on_read = 2
        self.reject("Main advanced")

    def test_api_transport_pins_host_and_sends_structured_json(self):
        response = subprocess.CompletedProcess([], 0, "HTTP/2.0 201 Created\r\n\r\n{\"sha\":\"ok\"}", "")
        with patch.object(proposer.subprocess, "run", return_value=response) as run:
            self.assertEqual(proposer.api("POST", "git/blobs", {"content": "literal\ntext"}), {"sha": "ok"})
        args, kwargs = run.call_args
        self.assertIn("github.com", args[0])
        self.assertEqual(json.loads(kwargs["input"]), {"content": "literal\ntext"})
        self.assertEqual(kwargs["env"]["GH_HOST"], "github.com")
        self.assertEqual(kwargs["timeout"], 120)

    def test_missing_api_result_requires_real_404(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                response = subprocess.CompletedProcess([], 1, f"HTTP/2.0 {status} Error\n\n{{}}", "misleading HTTP 404")
                with patch.object(proposer.subprocess, "run", return_value=response):
                    with self.assertRaises(RuntimeError):
                        proposer.api("GET", "git/ref/heads/missing", missing=True)
        response = subprocess.CompletedProcess([], 1, "HTTP/2.0 404 Missing\n\n{}", "")
        with patch.object(proposer.subprocess, "run", return_value=response):
            self.assertIsNone(proposer.api("GET", "git/ref/heads/missing", missing=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
