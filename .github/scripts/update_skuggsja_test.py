#!/usr/bin/env python3
"""Offline trust-boundary tests; every GitHub request is a local fake."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import update_skuggsja as updater
import verify_release as verifier

SOURCE_SHA = "a" * 40


def digest(content):
    return hashlib.sha256(content).hexdigest()


def make_release(version="1.2.3", extra_ruby=""):
    payloads = {name: (name + " synthetic archive\n").encode()
                for name in verifier.archive_names(version, include_windows=True)}
    pairs = "".join(f'  url "https://github.com/{verifier.UPSTREAM}/releases/download/v{version}/{name}"\n'
                    f'  sha256 "{digest(payloads[name])}"\n'
                    for name in verifier.archive_names(version))
    payloads["skuggsja.rb"] = (f"# Skuggsja release version: {version}\nclass Skuggsja < Formula\n"
                               + pairs + extra_ruby + "end\n").encode()
    payloads["checksums.txt"] = "".join(digest(content) + "  " + name + "\n"
                                            for name, content in payloads.items()
                                            if name != "skuggsja.rb").encode()
    metadata = {"tag_name": "v" + version, "draft": False, "prerelease": False, "assets": []}
    for index, (name, content) in enumerate(payloads.items(), 1):
        metadata["assets"].append({
            "name": name, "id": index, "size": len(content), "state": "uploaded",
            "digest": "sha256:" + digest(content),
            "browser_download_url": f"https://github.com/{verifier.UPSTREAM}/releases/download/v{version}/{name}",
        })
    return metadata, payloads


class FakeGitHub:
    def __init__(self, metadata, payloads):
        self.metadata = metadata
        self.payloads = payloads
        self.calls = []
        self.status = 200
        self.commit = SOURCE_SHA
        self.download_failure = None
        self.attestation_failure = None
        self.attestation_output = [{"verificationResult": {"statement": {}}}]
        self.on_verify = None
        self.release_reads = 0
        self.commit_reads = 0
        self.changed_metadata = None
        self.changed_commit = None

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "api":
            endpoint = args[-1]
            if "/releases/" in endpoint:
                self.release_reads += 1
                data = self.changed_metadata if self.release_reads > 1 and self.changed_metadata is not None else self.metadata
                return subprocess.CompletedProcess(args, 0 if self.status == 200 else 1,
                    "HTTP/2.0 " + str(self.status) + " Test\r\n\r\n" + json.dumps(data), "")
            if "/commits/" in endpoint:
                self.commit_reads += 1
                commit = self.changed_commit if self.commit_reads > 1 and self.changed_commit is not None else self.commit
                return subprocess.CompletedProcess(args, 0, "HTTP/2.0 200 Test\n\n" + json.dumps({"sha": commit}), "")
        if args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            if name == self.download_failure:
                return subprocess.CompletedProcess(args, 1, "", "download failed")
            destination = Path(args[args.index("--dir") + 1]) / name
            destination.write_bytes(self.payloads[name])
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("attestation", "verify"):
            name = Path(args[2]).name
            if self.on_verify is not None:
                self.on_verify(name)
            return subprocess.CompletedProcess(args, int(name == self.attestation_failure),
                                               json.dumps(self.attestation_output), "")
        raise AssertionError("Unexpected gh command: " + repr(args))


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skuggsja-updater-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.current = self.base / "tap" / "Formula" / "skuggsja.rb"
        self.current.parent.mkdir(parents=True)
        self.canary = self.current.with_name("unrelated.rb")
        self.canary.write_bytes(b"Unrelated formula is never changed.\n")
        self.evidence = self.base / "evidence"
        self.output = self.base / "github-output"
        self.metadata, self.payloads = make_release()
        self.fake = FakeGitHub(self.metadata, self.payloads)
        self.gh_patch = patch.object(verifier, "gh", self.fake)
        self.gh_patch.start()
        self.addCleanup(self.gh_patch.stop)
        self.env_patch = patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.output)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def tearDown(self):
        self.assertEqual(self.canary.read_bytes(), b"Unrelated formula is never changed.\n")

    def set_current(self, version="1.0.0"):
        self.current.write_bytes(make_release(version)[1]["skuggsja.rb"])
        return self.current.read_bytes()

    def update(self, tag=None):
        return updater.update(self.evidence, self.current, tag)

    def reject_update(self, pattern, tag=None):
        before = self.current.read_bytes() if self.current.exists() else None
        with self.assertRaisesRegex(verifier.VerificationError, pattern):
            self.update(tag)
        self.assertEqual(self.current.read_bytes() if self.current.exists() else None, before)
        self.assertFalse(self.output.exists())

    def sync_asset(self, name):
        asset = next(item for item in self.metadata["assets"] if item["name"] == name)
        asset.update(size=len(self.payloads[name]), digest="sha256:" + digest(self.payloads[name]))

    def test_bootstrap_only_stages_candidate_and_six_verified_assets(self):
        result = self.update()
        self.assertTrue(result["changed"])
        self.assertFalse(self.current.exists())
        self.assertEqual(Path(result["formula_path"]).read_bytes(), self.payloads["skuggsja.rb"])
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        self.assertEqual(manifest["verified_attestations"], 6)
        self.assertEqual(len(manifest["archives"]), 4)
        self.assertEqual(manifest["source_sha"], SOURCE_SHA)
        self.assertEqual({item["name"] for item in manifest["archives"]}, set(verifier.archive_names("1.2.3")))
        self.assertIn("changed=true\n", self.output.read_text())

    def test_every_attestation_pins_repository_workflow_tag_commit_and_hosted_runner(self):
        self.update()
        calls = [call for call in self.fake.calls if call[:2] == ("attestation", "verify")]
        self.assertEqual(len(calls), 6)
        for call in calls:
            for flag, expected in {"--hostname": "github.com", "--repo": verifier.UPSTREAM,
                                   "--signer-workflow": verifier.WORKFLOW,
                                   "--source-ref": "refs/tags/v1.2.3", "--source-digest": SOURCE_SHA,
                                   "--format": "json"}.items():
                self.assertEqual(call[call.index(flag) + 1], expected)
            self.assertIn("--deny-self-hosted-runners", call)

    def test_explicit_tag_requests_tag_endpoint(self):
        self.update("v1.2.3")
        self.assertTrue(self.fake.calls[0][-1].endswith("/releases/tags/v1.2.3"))

    def test_latest_missing_release_is_noop(self):
        self.fake.status = 404
        self.assertFalse(self.update()["changed"])
        self.assertEqual(self.output.read_text(), "changed=false\n")
        self.assertFalse(self.evidence.exists())

    def test_missing_explicit_tag_is_failure(self):
        self.fake.status = 404
        self.reject_update("metadata request failed", "v1.2.3")

    def test_http_errors_fail_closed(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.fake.status = status
                self.reject_update("metadata request failed")

    def test_untrusted_manual_tag_rejected_before_network(self):
        for tag in ("v01.2.3", "1.2.3", "v1.2.3-rc.1", "v1.2.3+build", "v1.2.3\n", "v1.2.3;false"):
            with self.subTest(tag=tag):
                self.reject_update("canonical stable", tag)
        self.assertEqual(self.fake.calls, [])

    def test_release_tag_cannot_disagree_with_explicit_request(self):
        self.reject_update("does not match", "v9.0.0")

    def test_draft_and_prerelease_rejected(self):
        for field in ("draft", "prerelease"):
            with self.subTest(field=field):
                self.metadata[field] = True
                self.reject_update("Draft and prerelease")
                self.metadata[field] = False

    def test_missing_duplicate_and_extra_assets_rejected(self):
        original = copy.deepcopy(self.metadata["assets"])
        for assets in (original[:-1], original + [original[0]], original[:-1] + [original[0]]):
            with self.subTest(assets=len(assets)):
                self.metadata["assets"] = assets
                self.reject_update("expected assets|duplicated")

    def test_invalid_api_digest_size_id_state_or_url_rejected(self):
        original = self.metadata["assets"][0].copy()
        for key, value in (("digest", None), ("digest", "sha256:" + "A" * 64), ("size", -1),
                           ("id", True), ("state", "new"), ("browser_download_url", "https://example.invalid/asset")):
            with self.subTest(field=key):
                self.metadata["assets"][0] = {**original, key: value}
                self.reject_update("asset lacks|asset URL")

    def test_rollback_rejected_before_download(self):
        self.set_current("2.0.0")
        self.reject_update("roll back")
        self.assertEqual(len(self.fake.calls), 1)

    def test_numeric_upgrade_stages_without_changing_current(self):
        old = self.set_current("1.9.0")
        self.fake.metadata, self.fake.payloads = make_release("1.10.0")
        self.assertTrue(self.update()["changed"])
        self.assertEqual(self.current.read_bytes(), old)

    def test_same_version_is_idempotent_after_full_verification(self):
        self.set_current("1.2.3")
        self.assertFalse(self.update()["changed"])
        self.assertEqual(len([call for call in self.fake.calls if call[:2] == ("attestation", "verify")]), 6)

    def test_same_version_replacement_is_rejected(self):
        self.set_current("1.2.3")
        self.fake.metadata, self.fake.payloads = make_release(extra_ruby="  # changed\n")
        self.reject_update("changed bytes")

    def test_invalid_current_formula_fails_before_network(self):
        self.current.write_bytes(b"invalid")
        self.reject_update("release-version comment")
        self.assertEqual(self.fake.calls, [])

    def test_formula_symlink_and_symlink_parent_rejected(self):
        self.current.symlink_to(self.canary)
        self.reject_update("symlinks")
        self.current.unlink()
        alias = self.base / "alias"
        alias.symlink_to(self.current.parent, target_is_directory=True)
        with self.assertRaisesRegex(verifier.VerificationError, "symlinks"):
            updater.update(self.evidence, alias / "skuggsja.rb")

    def test_output_inside_checkout_rejected(self):
        with patch.object(verifier, "ROOT", self.base / "tap"):
            with self.assertRaisesRegex(verifier.VerificationError, "outside the tap"):
                updater.update(self.base / "tap" / "candidate", self.current)

    def test_evidence_directory_cannot_reuse_stale_manifest(self):
        self.evidence.mkdir()
        (self.evidence / "verification.json").write_text("stale")
        self.reject_update("must be empty")

    def test_invalid_source_commit_rejected_before_download(self):
        self.fake.commit = "main"
        self.reject_update("valid source commit")
        self.assertFalse(self.evidence.exists())

    def test_each_artifact_attestation_failure_blocks_approval(self):
        for name in ["skuggsja.rb", "checksums.txt", *verifier.archive_names("1.2.3")]:
            with self.subTest(asset=name):
                self.evidence = self.base / ("evidence-" + name)
                self.fake.attestation_failure = name
                self.reject_update("attestation failed")
                self.assertFalse((self.evidence / "verification.json").exists())

    def test_download_failure_blocks_approval(self):
        self.fake.download_failure = "skuggsja.rb"
        self.reject_update("Unable to download")

    def test_payload_mutation_is_caught_by_api_digest(self):
        name = verifier.archive_names("1.2.3")[0]
        self.payloads[name] = b"x" * len(self.payloads[name])
        self.reject_update("API digest mismatch")

    def test_payload_size_mismatch_rejected(self):
        self.payloads["skuggsja.rb"] += b"extra"
        self.reject_update("size mismatch")

    def test_checksum_disagreement_with_api_rejected(self):
        self.payloads["checksums.txt"] = self.payloads["checksums.txt"].replace(
            digest(self.payloads[verifier.archive_names("1.2.3")[0]]).encode(), b"0" * 64)
        self.sync_asset("checksums.txt")
        self.reject_update("Checksum disagrees")

    def test_formula_checksum_disagreement_rejected_even_if_formula_is_attested(self):
        self.payloads["skuggsja.rb"] = self.payloads["skuggsja.rb"].replace(
            digest(self.payloads[verifier.archive_names("1.2.3")[0]]).encode(), b"0" * 64)
        self.sync_asset("skuggsja.rb")
        self.reject_update("Formula SHA256 disagrees")

    def test_changed_source_tag_rejected_at_final_check(self):
        self.fake.changed_commit = "b" * 40
        self.reject_update("changed during verification")
        self.assertFalse((self.evidence / "verification.json").exists())

    def test_changed_asset_metadata_rejected_at_final_check(self):
        self.fake.changed_metadata = copy.deepcopy(self.metadata)
        self.fake.changed_metadata["assets"][0]["id"] += 100
        self.reject_update("changed during verification")

    def test_current_checkout_mutation_cannot_get_candidate_output(self):
        self.set_current()
        changed = make_release("1.1.0")[1]["skuggsja.rb"]
        self.fake.on_verify = lambda _name: self.current.write_bytes(changed)
        with self.assertRaisesRegex(verifier.VerificationError, "Current formula changed"):
            self.update()
        self.assertEqual(self.current.read_bytes(), changed)
        self.assertFalse(self.output.exists())

    def test_candidate_must_equal_attested_bytes_before_approval(self):
        self.set_current("1.2.3")
        self.current.write_bytes(self.current.read_bytes() + b"# unreviewed alteration\n")
        with self.assertRaisesRegex(verifier.VerificationError, "bytes differ"):
            verifier.verify_release(self.evidence, formula=self.current)
        self.assertFalse((self.evidence / "verification.json").exists())

    def test_candidate_changed_during_verification_rejected(self):
        self.set_current("1.2.3")
        self.fake.on_verify = lambda _name: self.current.write_bytes(self.payloads["skuggsja.rb"] + b"# mutation\n")
        with self.assertRaisesRegex(verifier.VerificationError, "Candidate formula changed"):
            verifier.verify_release(self.evidence, formula=self.current)

    def test_verified_candidate_records_exact_input_path_and_manifest(self):
        self.set_current("1.2.3")
        result = verifier.verify_release(self.evidence, formula=self.current)
        self.assertEqual(result["candidate_formula_path"], str(self.current))
        self.assertEqual(result["formula_sha256"], digest(self.current.read_bytes()))

    def test_candidate_tag_mismatch_rejected_before_network(self):
        self.set_current("1.2.3")
        with self.assertRaisesRegex(verifier.VerificationError, "does not match"):
            verifier.verify_release(self.evidence, formula=self.current, tag="v9.0.0")
        self.assertEqual(self.fake.calls, [])

    def test_baseline_fetch_can_explicitly_verify_an_older_release(self):
        self.fake.metadata, self.fake.payloads = make_release("0.1.0")
        result = verifier.verify_release(self.evidence, tag="v0.1.0")
        self.assertEqual(result["version"], "0.1.0")
        self.assertIsNone(result["candidate_formula_path"])

    def test_formula_is_data_and_never_executed(self):
        self.fake.metadata, self.fake.payloads = make_release(extra_ruby='  raise "must not execute"\n')
        self.assertTrue(self.update()["changed"])
        self.assertTrue(all(call[0] in ("api", "release", "attestation") for call in self.fake.calls))

    def test_empty_successful_attestation_output_is_rejected(self):
        self.fake.attestation_output = []
        self.reject_update("no verified attestations")

    def test_formula_marker_and_url_structure_guards(self):
        content = self.payloads["skuggsja.rb"]
        cases = [content.replace(b"# Skuggsja release version: 1.2.3\n", b""),
                 b"# Skuggsja release version: 1.2.3\n" + content,
                 content.replace(b"version: 1.2.3", b"version: 01.2.3"),
                 content + b'  version "1.2.3"\n',
                 content.replace(b"https://github.com/", b"https://example.invalid/", 1),
                 content.replace(b"_linux_arm64.tar.gz", b"_linux_amd64.tar.gz"),
                 content + b'  sha256 "' + b"a" * 64 + b'"\n']
        for content in cases:
            with self.subTest(content=content[:40]):
                with self.assertRaises(verifier.VerificationError):
                    verifier.formula_data(content)

    def test_duplicate_missing_and_traversal_checksums_rejected(self):
        content = self.payloads["checksums.txt"]
        for changed in [content + content.splitlines(keepends=True)[0],
                        b"\n".join(content.splitlines()[:-1]),
                        content.replace(b"  skuggsja", b"  ../skuggsja", 1)]:
            with self.subTest(content=changed[:20]):
                with self.assertRaises(verifier.VerificationError):
                    verifier.checksums_data(changed, "1.2.3")

    def test_workflow_output_control_characters_are_rejected(self):
        with self.assertRaisesRegex(verifier.VerificationError, "control character"):
            verifier.write_outputs({"path": "safe\nchanged=true"})
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
