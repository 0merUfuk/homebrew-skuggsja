#!/usr/bin/env python3
"""Offline lifecycle guards; never execute Homebrew on the developer's prefix."""
import argparse
import hashlib
import io
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from lifecycle import (Lifecycle, inspect_receipts, load_release, native_archive, stable_version,
                       migration_snapshot, require_tap_only_change, linux_network_prefix, NETWORK_ENV_KEYS)


class LifecycleGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manifest(self):
        formula = self.root / "skuggsja.rb"
        formula.write_bytes(b"synthetic verified formula\n")
        data = {"version": "0.1.1", "tag": "v0.1.1", "source_sha": "a" * 40,
                "repository": "0merUfuk/skuggsja",
                "signer_workflow": "0merUfuk/skuggsja/.github/workflows/release.yml",
                "verified_attestations": 6, "formula_path": str(formula),
                "formula_sha256": hashlib.sha256(formula.read_bytes()).hexdigest()}
        path = self.root / "verification.json"
        path.write_text(json.dumps(data))
        return path, data

    def test_stable_versions_order_numerically(self):
        self.assertLess(stable_version("0.9.0"), stable_version("0.10.0"))

    def test_reject_noncanonical_versions(self):
        for value in ["v0.1.1", "01.1.1", "0.1.1-rc1", "0.1.1+meta", "0.1.1\n", ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                stable_version(value)

    def test_verified_formula_loads(self):
        path, data = self.manifest()
        self.assertEqual(load_release(path), data)

    def test_wrong_attested_repository_or_workflow_rejected(self):
        for key in ("repository", "signer_workflow"):
            path, data = self.manifest()
            data[key] = "other/project"
            path.write_text(json.dumps(data))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "repository or workflow"):
                load_release(path)

    def test_tapped_formula_changed_before_ruby_is_rejected(self):
        _, data = self.manifest()
        tap = self.root / "tap"
        (tap / "Formula").mkdir(parents=True)
        (tap / "Formula/skuggsja.rb").write_text("changed")
        check = Lifecycle.__new__(Lifecycle)
        with self.assertRaisesRegex(ValueError, "attested exact candidate"):
            check.verify_tap(tap, data)

    def test_tapped_symlink_is_rejected_even_if_target_bytes_match(self):
        _, data = self.manifest()
        tap = self.root / "tap"
        (tap / "Formula").mkdir(parents=True)
        (tap / "Formula/skuggsja.rb").symlink_to(data["formula_path"])
        check = Lifecycle.__new__(Lifecycle)
        with self.assertRaisesRegex(ValueError, "regular"):
            check.verify_tap(tap, data)

    def test_changed_formula_rejected_before_execution(self):
        path, data = self.manifest()
        Path(data["formula_path"]).write_text("changed")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            load_release(path)

    def test_incomplete_attestations_rejected(self):
        path, data = self.manifest()
        data["verified_attestations"] = 5
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "six"):
            load_release(path)

    def test_symlink_formula_rejected(self):
        path, data = self.manifest()
        link = self.root / "link.rb"
        link.symlink_to(data["formula_path"])
        data["formula_path"] = str(link)
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "regular"):
            load_release(path)

    def archive(self, kind=tarfile.REGTYPE, duplicate=False):
        path = self.root / "skuggsja_0.1.1_linux_arm64.tar.gz"
        with tarfile.open(path, "w:gz") as stream:
            for _ in range(2 if duplicate else 1):
                member = tarfile.TarInfo("skuggsja")
                member.type = kind
                member.size = 3 if kind == tarfile.REGTYPE else 0
                member.linkname = "../outside" if kind == tarfile.SYMTYPE else ""
                stream.addfile(member, io.BytesIO(b"abc") if member.size else None)
        return {"version": "0.1.1", "archives": [{"name": path.name, "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}

    def test_native_archive_bytes_selected_without_extraction(self):
        data = self.archive()
        self.assertEqual(native_archive(data, "linux", "arm64"), hashlib.sha256(b"abc").hexdigest())

    def test_wrong_architecture_cannot_fall_back(self):
        with self.assertRaisesRegex(ValueError, "one native archive"):
            native_archive(self.archive(), "linux", "x64")

    def test_modified_archive_rejected(self):
        data = self.archive()
        Path(data["archives"][0]["path"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "archive changed"):
            native_archive(data, "linux", "arm64")

    def test_archive_symlink_executable_rejected(self):
        with self.assertRaisesRegex(ValueError, "regular"):
            native_archive(self.archive(kind=tarfile.SYMTYPE), "linux", "arm64")

    def test_duplicate_archive_executable_rejected(self):
        with self.assertRaisesRegex(ValueError, "regular"):
            native_archive(self.archive(duplicate=True), "linux", "arm64")

    def write_receipt(self, version, tap):
        path = self.root / "skuggsja" / version / "INSTALL_RECEIPT.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"source": {"tap": tap}}))

    def test_every_retained_keg_receipt_is_checked(self):
        for version in ["0.1.0", "0.1.1"]:
            self.write_receipt(version, "skuggsjaci/upgrade")
        self.assertEqual(len(inspect_receipts(self.root, "SkuggsjaCI/upgrade", ["0.1.0", "0.1.1"])), 2)

    def test_old_keg_wrong_tap_cannot_hide_behind_current_keg(self):
        self.write_receipt("0.1.0", "other/tap")
        self.write_receipt("0.1.1", "skuggsjaci/upgrade")
        with self.assertRaisesRegex(ValueError, "wrong tap"):
            inspect_receipts(self.root, "skuggsjaci/upgrade", ["0.1.0", "0.1.1"])

    def test_missing_older_keg_fails_retention_gate(self):
        self.write_receipt("0.1.1", "skuggsjaci/upgrade")
        with self.assertRaisesRegex(ValueError, "versions"):
            inspect_receipts(self.root, "skuggsjaci/upgrade", ["0.1.0", "0.1.1"])

    def test_refuses_developer_homebrew_environment(self):
        args = argparse.Namespace(evidence_dir=self.root / "new")
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "hosted CI"):
            Lifecycle(args)
        self.assertFalse(args.evidence_dir.exists())

    def test_refuses_other_repository(self):
        args = argparse.Namespace(evidence_dir=self.root / "new")
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "other/repo"}, clear=True):
            with self.assertRaisesRegex(ValueError, "hosted CI"):
                Lifecycle(args)

    def test_state_mutation_and_touch_detected(self):
        check = Lifecycle.__new__(Lifecycle)
        check.canary = self.root / "state.json"
        check.canary.write_text("synthetic")
        check.canary_before = {"sha256": hashlib.sha256(check.canary.read_bytes()).hexdigest(),
                               "mtime_ns": check.canary.stat().st_mtime_ns}
        check.check_state()
        os.utime(check.canary, ns=(1, 1))
        with self.assertRaisesRegex(ValueError, "state changed"):
            check.check_state()

    def test_credentials_removed_and_trust_isolated(self):
        args = argparse.Namespace(evidence_dir=self.root / "new")
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "0merUfuk/homebrew-skuggsja",
               "RUNNER_TEMP": str(self.root), "GH_TOKEN": "synthetic-secret",
               "GITHUB_TOKEN": "synthetic-secret", "HOMEBREW_GITHUB_API_TOKEN": "synthetic-secret"}
        with patch.dict(os.environ, env, clear=True):
            check = Lifecycle(args)
        self.assertFalse(set(env) & {"GH_TOKEN", "GITHUB_TOKEN", "HOMEBREW_GITHUB_API_TOKEN"} & set(check.env))
        self.assertEqual(Path(check.env["HOMEBREW_USER_CONFIG_HOME"]).parent, args.evidence_dir.resolve())

    def test_linux_namespace_restores_private_environment_after_uid_drop(self):
        original = {key: str(self.root / key) for key in NETWORK_ENV_KEYS}
        original.update(HOME=os.environ["HOME"], PATH=os.environ["PATH"],
                        HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALL_FROM_API="1", HOMEBREW_NO_ANALYTICS="1",
                        USER="untrusted-inherited-name", GH_TOKEN="synthetic-secret")
        with patch("lifecycle.shutil.which", side_effect=lambda name: "/synthetic/bin/" + name):
            prefix = linux_network_prefix(original)
        env_index = prefix.index("/usr/bin/env")
        self.assertEqual(prefix[3:7], ["/synthetic/bin/unshare", "--net", "--", "/synthetic/bin/setpriv"])
        self.assertEqual(prefix[7:env_index], ["--reuid", str(os.getuid()), "--regid", str(os.getgid()), "--init-groups"])
        self.assertFalse(any("synthetic-secret" in argument or argument.startswith("GH_TOKEN=") for argument in prefix))
        # Reproduce the measured sudo damage without invoking sudo or Homebrew.
        corrupted = dict(os.environ, XDG_CONFIG_HOME="/synthetic/wrong-config", USER="root", LOGNAME="root")
        script = "import json,os; print(json.dumps({k:os.environ.get(k) for k in " + repr(list(NETWORK_ENV_KEYS) + ["USER", "LOGNAME"]) + "}))"
        result = subprocess.run([*prefix[env_index:], sys.executable, "-c", script], env=corrupted,
                                capture_output=True, text=True, check=True)
        actual = json.loads(result.stdout)
        for key in NETWORK_ENV_KEYS:
            self.assertEqual(actual[key], original[key], key)
        self.assertEqual(actual["USER"], pwd.getpwuid(os.getuid()).pw_name)
        self.assertEqual(actual["LOGNAME"], actual["USER"])

    def test_linux_namespace_rejects_missing_private_config(self):
        original = {key: "synthetic" for key in NETWORK_ENV_KEYS if key != "XDG_CONFIG_HOME"}
        with patch("lifecycle.shutil.which", side_effect=lambda name: "/synthetic/bin/" + name):
            with self.assertRaisesRegex(ValueError, "complete private environment"):
                linux_network_prefix(original)

    def migration_pair(self):
        cellar = self.root / "Cellar"
        for version in ("0.1.0", "0.1.1"):
            keg = cellar / "skuggsja" / version
            (keg / "bin").mkdir(parents=True)
            (keg / "bin/skuggsja").write_text("synthetic executable " + version)
            (keg / "INSTALL_RECEIPT.json").write_text(json.dumps({"source": {"tap": "0merufuk/thematrix", "path": "synthetic/formula.rb"}, "options": ["synthetic-option"]}))
        pin = self.root / "var/homebrew/pinned/skuggsja"
        pin.parent.mkdir(parents=True)
        pin.symlink_to(cellar / "skuggsja/0.1.1")
        before = migration_snapshot(self.root, cellar)
        after = json.loads(json.dumps(before))
        for entry in after["receipts"].values():
            entry["data"]["source"]["tap"] = "0merufuk/skuggsja"
            entry["sha256"] = "new-receipt-bytes"
            entry["mtime_ns"] += 1
        return before, after

    def test_migration_allows_only_actual_origin_change_for_both_retained_kegs(self):
        before, after = self.migration_pair()
        require_tap_only_change(before, after, ["0.1.0", "0.1.1"])

    def test_migration_rejects_rewriting_other_receipt_metadata(self):
        before, after = self.migration_pair()
        after["receipts"]["0.1.0"]["data"]["source"]["path"] = "different/formula.rb"
        with self.assertRaisesRegex(ValueError, "semantics"):
            require_tap_only_change(before, after, ["0.1.0", "0.1.1"])

    def test_migration_rejects_missing_retained_keg(self):
        before, after = self.migration_pair()
        del after["receipts"]["0.1.0"]
        with self.assertRaisesRegex(ValueError, "every original keg"):
            require_tap_only_change(before, after, ["0.1.0", "0.1.1"])

    def test_migration_rejects_changed_receipt_permissions(self):
        before, after = self.migration_pair()
        after["receipts"]["0.1.0"]["mode"] = before["receipts"]["0.1.0"]["mode"] ^ 0o100
        with self.assertRaisesRegex(ValueError, "receipt permissions"):
            require_tap_only_change(before, after, ["0.1.0", "0.1.1"])

    def test_migration_rejects_binary_or_pin_mutation(self):
        before, after = self.migration_pair()
        after["files"]["0.1.0/bin/skuggsja"]["mtime_ns"] += 1
        with self.assertRaisesRegex(ValueError, "non-receipt"):
            require_tap_only_change(before, after, ["0.1.0", "0.1.1"])
        after["files"] = json.loads(json.dumps(before["files"]))
        after["links"]["var/homebrew/pinned/skuggsja"]["target"] = "other-keg"
        with self.assertRaisesRegex(ValueError, "non-receipt"):
            require_tap_only_change(before, after, ["0.1.0", "0.1.1"])


if __name__ == "__main__":
    unittest.main()
