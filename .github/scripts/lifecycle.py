#!/usr/bin/env python3
"""Hosted-runner package lifecycle; personal Homebrew prefixes are never used."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile

PUBLIC_TAP = "0merUfuk/skuggsja"
LOCAL_TAP = "skuggsjaci/candidate"
UPGRADE_TAP = "skuggsjaci/upgrade"
COMPLETIONS = ("etc/bash_completion.d/skuggsja", "share/zsh/site-functions/_skuggsja",
               "share/fish/vendor_completions.d/skuggsja.fish")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_version(value):
    require(re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value),
            "Expected a canonical stable version")
    return tuple(map(int, value.split(".")))


def load_release(path):
    data = json.loads(path.read_text())
    require(data.get("repository") == "0merUfuk/skuggsja" and
            data.get("signer_workflow") == "0merUfuk/skuggsja/.github/workflows/release.yml",
            "Unexpected attested release repository or workflow")
    stable_version(data["version"])
    require(data["tag"] == "v" + data["version"], "Version/tag mismatch")
    require(re.fullmatch(r"[0-9a-f]{40}", data["source_sha"]), "Missing exact source SHA")
    require(data["verified_attestations"] == 6, "All six release attestations are required")
    formula = Path(data["formula_path"])
    require(formula.is_file() and not formula.is_symlink(), "Verified formula must be regular")
    require(digest(formula) == data["formula_sha256"], "Verified formula bytes changed")
    return data


def native_archive(data, system, arch):
    goarch = {"x64": "amd64", "arm64": "arm64"}[arch]
    name = f"skuggsja_{data['version']}_{system}_{goarch}.tar.gz"
    entries = [item for item in data["archives"] if item["name"] == name]
    require(len(entries) == 1, "Exactly one native archive must be attested")
    archive = Path(entries[0]["path"])
    require(digest(archive) == entries[0]["sha256"], "Attested native archive changed")
    with tarfile.open(archive, "r:gz") as stream:
        members = [item for item in stream.getmembers() if item.name == "skuggsja"]
        require(len(members) == 1 and members[0].isfile(), "Archive executable must be regular")
        return hashlib.sha256(stream.extractfile(members[0]).read()).hexdigest()


def inspect_receipts(cellar, expected_tap, expected_versions):
    result = []
    for receipt in sorted((cellar / "skuggsja").glob("*/INSTALL_RECEIPT.json")):
        data = json.loads(receipt.read_text())
        require(data.get("source", {}).get("tap", "").lower() == expected_tap.lower(),
                "Installed keg receipt names the wrong tap")
        result.append({"version": receipt.parent.name, "tap": data["source"]["tap"],
                       "sha256": digest(receipt)})
    require({row["version"] for row in result} == set(expected_versions),
            "Actual installed keg versions differ from expected lifecycle")
    return result


def migration_snapshot(prefix, cellar):
    """Keep receipt semantics separate so only source.tap may change."""
    receipts, files, links = {}, {}, {}
    rack = cellar / "skuggsja"
    for item in sorted(rack.rglob("*")):
        relative = str(item.relative_to(rack))
        if item.name == "INSTALL_RECEIPT.json":
            require(item.is_file() and not item.is_symlink(), "Migration receipt must be regular")
            receipts[item.parent.name] = {"data": json.loads(item.read_text()),
                                          "sha256": digest(item), "mtime_ns": item.stat().st_mtime_ns,
                                          "mode": item.stat().st_mode & 0o777}
        elif item.is_symlink():
            files[relative] = {"target": os.readlink(item), "mtime_ns": item.lstat().st_mtime_ns}
        elif item.is_file():
            files[relative] = {"sha256": digest(item), "mtime_ns": item.stat().st_mtime_ns,
                               "mode": item.stat().st_mode & 0o777}
        else:
            require(item.is_dir(), "Unexpected special file in migration keg")
            files[relative] = {"directory": True}
    for relative in ("bin/skuggsja", "opt/skuggsja", "var/homebrew/pinned/skuggsja", *COMPLETIONS):
        item = prefix / relative
        links[relative] = ({"target": os.readlink(item), "mtime_ns": item.lstat().st_mtime_ns}
                           if item.is_symlink() else {"exists": item.exists()})
    return {"receipts": receipts, "files": files, "links": links}


def require_tap_only_change(before, after, expected_versions):
    require(set(before["receipts"]) == set(after["receipts"]) == set(expected_versions),
            "Migration must retain every original keg")
    require(before["files"] == after["files"] and before["links"] == after["links"],
            "Migration changed non-receipt files, links or pin")
    for version in expected_versions:
        require(before["receipts"][version]["mode"] == after["receipts"][version]["mode"],
                "Migration changed receipt permissions")
        old = before["receipts"][version]["data"]
        new = after["receipts"][version]["data"]
        require(old["source"]["tap"] == "0merufuk/thematrix" and new["source"]["tap"] == "0merufuk/skuggsja",
                "Migration did not update the actual old receipt origin")
        expected = json.loads(json.dumps(old))
        expected["source"]["tap"] = "0merufuk/skuggsja"
        require(new == expected, "Migration changed receipt semantics beyond source.tap")


class Lifecycle:
    def __init__(self, args):
        self.args = args
        self.root = args.evidence_dir.resolve()
        require(not self.root.exists(), "Evidence directory must be new")
        require(os.environ.get("GITHUB_ACTIONS") == "true" and
                os.environ.get("GITHUB_REPOSITORY") == "0merUfuk/homebrew-skuggsja",
                "Run only in this tap's disposable hosted CI")
        require(Path(os.environ["RUNNER_TEMP"]).resolve() in self.root.parents,
                "Evidence must be inside this runner's temporary directory")
        self.root.mkdir(mode=0o700, parents=True)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"GH_TOKEN", "GITHUB_TOKEN", "HOMEBREW_GITHUB_API_TOKEN"}}
        self.env.update(HOMEBREW_NO_ANALYTICS="1", HOMEBREW_NO_AUTO_UPDATE="1",
                        HOMEBREW_NO_INSTALL_CLEANUP="1")
        for key, name in (("HOMEBREW_USER_CONFIG_HOME", "brew-config"), ("XDG_CONFIG_HOME", "xdg-config"),
                          ("HOMEBREW_CACHE", "brew-cache"), ("HOMEBREW_LOGS", "brew-logs")):
            self.env[key] = str(self.root / name)
            Path(self.env[key]).mkdir(mode=0o700)
        self.commands, self.checks, self.taps = [], [], []
        self.canary = self.canary_before = self.prefix = self.cellar = None

    def save(self, name, data):
        (self.root / name).write_text(json.dumps(data, indent=2) + "\n")

    def run(self, *command, allow_failure=False, cwd=None, env=None):
        number = len(self.commands) + 1
        result = subprocess.run(command, env=self.env if env is None else env, cwd=cwd or self.args.checkout,
                                capture_output=True, text=True, timeout=900)
        (self.root / f"{number:03d}.stdout").write_text(result.stdout)
        (self.root / f"{number:03d}.stderr").write_text(result.stderr)
        self.commands.append({"number": number, "command": list(command), "exit_code": result.returncode})
        print(f"[{number}] {' '.join(command)}: exit {result.returncode}", flush=True)
        self.save("commands.json", self.commands)
        if not allow_failure:
            require(result.returncode == 0, f"Command failed; inspect evidence command {number}")
        self.check_state()
        return result

    def check(self, label, condition):
        require(condition, label)
        self.checks.append(label)

    def check_state(self):
        if self.canary is not None:
            require(self.canary.is_file() and not self.canary.is_symlink() and
                    {"sha256": digest(self.canary), "mtime_ns": self.canary.stat().st_mtime_ns} == self.canary_before,
                    "Existing synthetic application state changed")

    def bind_source(self, directory, release):
        require(self.run("git", "-C", str(directory), "rev-parse", "HEAD").stdout.strip() == release["source_sha"],
                "Source checkout differs from attested source")
        require((directory / "scripts/verify-install.cjs").is_file(), "Tagged verifier missing")

    def commit_formula(self, directory):
        self.run("git", "-C", str(directory), "add", "--", "Formula/skuggsja.rb")
        self.run("git", "-C", str(directory), "-c", "user.name=Synthetic packaging CI",
                 "-c", "user.email=synthetic@example.invalid", "commit", "-m", "Verified lifecycle formula")

    def make_remote(self, name, formula):
        directory = self.root / name
        (directory / "Formula").mkdir(parents=True)
        shutil.copyfile(formula, directory / "Formula/skuggsja.rb")
        self.run("git", "init", "--initial-branch=main", str(directory))
        self.commit_formula(directory)
        return directory

    def tap(self, tap, remote=None):
        require(tap.lower() not in self.run("brew", "tap").stdout.lower().splitlines(),
                "Never replace an existing tap")
        # Homebrew 6 evaluates formulae while tapping. Mount Git first, verify
        # its bytes, then grant formula-only trust before any Ruby evaluation.
        # Public mode clones the actual public GitHub remote, never a fixture.
        brew_repository = Path(self.run("brew", "--repository").stdout.strip())
        owner, name = tap.lower().split("/")
        destination = brew_repository / "Library/Taps" / owner / ("homebrew-" + name)
        require(not destination.exists() and not destination.is_symlink(), "Tap path already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = remote.as_uri() if remote else "https://github.com/0merUfuk/homebrew-skuggsja.git"
        self.run("git", "clone", "--no-hardlinks", url, str(destination))
        self.taps.append(tap)
        return destination

    def verify_tap(self, tap_path, release, expected_head=None):
        formula = tap_path / "Formula/skuggsja.rb"
        require(formula.is_file() and not formula.is_symlink(), "Tapped formula must be regular")
        require(digest(formula) == release["formula_sha256"],
                "Tapped formula differs from the attested exact candidate")
        if expected_head:
            require(self.run("git", "-C", str(tap_path), "rev-parse", "HEAD").stdout.strip() == expected_head,
                    "Public tap advanced beyond this exact CI head")

    def installed(self, tap, release, source, expected_versions, label):
        qualified = tap + "/skuggsja"
        keg = Path(self.run("brew", "--prefix", qualified).stdout.strip())
        binary = keg / "bin/skuggsja"
        require(digest(binary) == native_archive(release, self.args.platform, self.args.arch),
                "Installed executable differs from attested native archive")
        require(self.run(str(binary), "version").stdout.strip() == "skuggsja " + release["version"],
                "Wrong installed version")
        self.check(label + ": linked executable", (self.prefix / "bin/skuggsja").resolve() == binary.resolve())
        for relative in COMPLETIONS:
            item = keg / relative
            self.check(label + ": " + relative, item.is_file() and item.stat().st_size > 0 and
                       (self.prefix / relative).resolve() == item.resolve())
        self.save(label + "-receipts.json", inspect_receipts(self.cellar, tap, expected_versions))
        self.run("brew", "test", "--verbose", qualified)
        smoke = self.run("node", str(source / "scripts/verify-install.cjs"), str(binary), release["version"])
        match = re.search(r"Installed CLI smoke: ([1-9][0-9]*)/\1 checks passed", smoke.stdout)
        require(match is not None, "Tagged synthetic CLI verifier did not record a full pass")
        self.checks.append(label + ": " + match.group(1) + " synthetic installed-CLI checks")

    def uninstall(self, tap):
        self.run("brew", "uninstall", "--force", "--formula", tap + "/skuggsja")
        self.check("uninstall removes all owned kegs", not list((self.cellar / "skuggsja").glob("*/INSTALL_RECEIPT.json")))
        for relative in ("bin/skuggsja", *COMPLETIONS):
            target = self.prefix / relative
            self.check("uninstall removes " + relative, not target.exists() and not target.is_symlink())

    def migration_phase(self, candidate):
        import fcntl
        old_tap, new_tap = "0merUfuk/thematrix", PUBLIC_TAP
        old_formula, new_formula = old_tap + "/skuggsja", new_tap + "/skuggsja"
        releases = [load_release(self.args.migration_v010), load_release(self.args.migration_v011)]
        require([r["tag"] for r in releases] == ["v0.1.0", "v0.1.1"], "Migration fixtures require both original releases")
        checks = []

        def check(label, condition):
            require(condition, "Migration: " + label)
            checks.append(label)
            self.save("migration-progress.json", {"status": "in_progress", "completed_checks": checks})

        def snapshot(name):
            value = migration_snapshot(self.prefix, self.cellar)
            self.save(name + ".json", value)
            return value

        def command(*args, failure=False):
            result = self.run(*network_prefix, brew_binary, "skuggsja-migrate", *args, allow_failure=failure, env=helper_env)
            if failure:
                check("held formula lock refuses apply", result.returncode != 0)
            data = json.loads(result.stdout)
            return data

        helper_env = dict(self.env, HOMEBREW_NO_INSTALL_FROM_API="1", HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1")
        brew_binary = shutil.which("brew", path=helper_env["PATH"])
        require(brew_binary is not None, "Homebrew executable missing")
        if self.args.platform == "darwin":
            sandbox = shutil.which("sandbox-exec")
            require(sandbox is not None, "Network-denied helper verification requires sandbox-exec")
            network_prefix = [sandbox, "-p", "(version 1) (allow default) (deny network*)"]
        else:
            commands = [shutil.which(name) for name in ("sudo", "unshare", "setpriv")]
            require(all(commands), "Network-denied helper verification requires sudo, unshare and setpriv")
            kept = ["HOME", "PATH", "XDG_CONFIG_HOME", "HOMEBREW_USER_CONFIG_HOME", "HOMEBREW_CACHE", "HOMEBREW_LOGS",
                    "HOMEBREW_NO_AUTO_UPDATE", "HOMEBREW_NO_ANALYTICS", "HOMEBREW_NO_INSTALL_CLEANUP", "HOMEBREW_NO_INSTALL_FROM_API"]
            network_prefix = [commands[0], "-n", "--preserve-env=" + ",".join(kept), commands[1], "--net", "--",
                              commands[2], "--reuid", str(os.getuid()), "--regid", str(os.getgid()), "--init-groups"]
        probe = self.run(*network_prefix, sys.executable, "-c",
                         "import errno,socket; s=socket.socket(); s.settimeout(2); n=s.connect_ex(('198.51.100.1',443)); print(n); assert n in (errno.EPERM,errno.EACCES,errno.ENETUNREACH)", env=helper_env)
        check("network-denial control rejects a TEST-NET connection", probe.returncode == 0)
        self.taps = []
        check("existing package lifecycle left no kegs", not list((self.cellar / "skuggsja").glob("*/INSTALL_RECEIPT.json")))
        # Remove only trust granted inside this job's private configuration.
        self.run("brew", "untrust", "--formula", new_formula)
        self.run("brew", "untrust", "--command", new_tap + "/skuggsja-migrate")
        remote = self.make_remote("migration-old-remote", Path(releases[0]["formula_path"]))
        old_path = self.tap(old_tap, remote)
        self.verify_tap(old_path, releases[0])
        self.run("brew", "trust", "--formula", old_formula)
        self.run("brew", "install", "--formula", old_formula)
        shutil.copyfile(releases[1]["formula_path"], remote / "Formula/skuggsja.rb")
        self.commit_formula(remote)
        self.run("git", "-C", str(old_path), "pull", "--ff-only")
        self.verify_tap(old_path, releases[1])
        self.run("brew", "upgrade", "--formula", old_formula)
        for release in releases:
            binary = self.cellar / "skuggsja" / release["version"] / "bin/skuggsja"
            check(release["version"] + " actual native binary", digest(binary) == native_archive(release, self.args.platform, self.args.arch))
            check(release["version"] + " actual version", self.run(str(binary), "version").stdout.strip() == "skuggsja " + release["version"])
        self.run("brew", "pin", old_formula)
        check("two authentic old-origin kegs retained", len(inspect_receipts(self.cellar, old_tap, ["0.1.0", "0.1.1"])) == 2)
        check("test-created pin exists", (self.prefix / "var/homebrew/pinned/skuggsja").is_symlink())
        before = snapshot("migration-before-update")
        (remote / "Formula/skuggsja.rb").unlink()
        (remote / "tap_migrations.json").write_text(json.dumps({"skuggsja": new_tap}) + "\n")
        self.run("git", "-C", str(remote), "add", "--all")
        self.run("git", "-C", str(remote), "-c", "user.name=Synthetic packaging CI", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-m", "Move synthetic old formula to the dedicated tap")
        cutover = self.run("git", "-C", str(remote), "rev-parse", "HEAD").stdout.strip()
        update = self.run("brew", "update", "--force", "--verbose")
        check("real update fetched fixture cutover", self.run("git", "-C", str(old_path), "rev-parse", "HEAD").stdout.strip() == cutover)
        check("untrusted absent destination is refused", "Not automatically tapping 0merufuk/skuggsja" in update.stdout + update.stderr)
        check("refusal preserves both pinned kegs", snapshot("migration-after-refusal") == before)
        destination = self.tap(new_tap)
        # Keep the real canonical origin; fetch the exact PR object, never copy
        # an unverified working tree over an older remote-main checkout.
        self.run("git", "-C", str(destination), "fetch", "--no-tags", "origin", self.args.expected_head)
        self.run("git", "-C", str(destination), "checkout", "--detach", self.args.expected_head)
        self.verify_tap(destination, candidate, self.args.expected_head)
        for relative in ("cmd/skuggsja-migrate.rb", "libexec/migration_helper.rb", "libexec/migration-releases.json"):
            check(relative + " is exact candidate source", digest(destination / relative) == digest(self.args.checkout / relative))
        self.run("brew", "trust", "--formula", new_formula)
        self.run("brew", "trust", "--command", new_tap + "/skuggsja-migrate")
        default = command()
        check("default command reports both planned receipt changes", default["status"] == "ready" and
              set(default["would_change_versions"]) == {"0.1.0", "0.1.1"} and default["changed_versions"] == [])
        check("default command does not write", snapshot("migration-after-default-check") == before)
        explicit = command("--check")
        check("explicit check reports ready", explicit["status"] == "ready" and explicit["changed_versions"] == [])
        check("explicit check does not write", snapshot("migration-after-explicit-check") == before)
        lock = self.prefix / "var/homebrew/locks/skuggsja.formula.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            denied = command("--apply", failure=True)
            check("lock failure is explicit", denied["status"] == "failed" and denied["changed_versions"] == [])
            check("lock refusal does not write", snapshot("migration-after-lock-refusal") == before)
        applied = command("--apply")
        check("apply reports both changed versions", applied["status"] == "migrated" and set(applied["changed_versions"]) == {"0.1.0", "0.1.1"})
        check("apply retains a backup directory", isinstance(applied["backup_directory"], str) and Path(applied["backup_directory"]).is_dir())
        after = snapshot("migration-after-apply")
        require_tap_only_change(before, after, ["0.1.0", "0.1.1"])
        check("all receipt semantics except source.tap and all other keg files/links/pin preserved", True)
        repeated = command("--apply")
        check("repeated apply is idempotent", repeated["status"] == "up_to_date" and repeated["changed_versions"] == [])
        check("idempotent apply does not rewrite receipts", snapshot("migration-after-idempotent-apply") == after)
        self.run("brew", "unpin", new_formula)
        check("only the test-created pin was removed", not (self.prefix / "var/homebrew/pinned/skuggsja").exists())
        self.run("brew", "trust", "--formula", new_formula)
        self.run("brew", "reinstall", "--formula", new_formula)
        expected_versions = ["0.1.0", candidate["version"]]
        check("qualified reinstall retains correct origin for every remaining keg", len(inspect_receipts(self.cellar, new_tap, expected_versions)) == len(set(expected_versions)))
        binary = self.cellar / "skuggsja" / candidate["version"] / "bin/skuggsja"
        check("reinstalled current binary remains attested", digest(binary) == native_archive(candidate, self.args.platform, self.args.arch))
        for relative in ("bin/skuggsja", *COMPLETIONS):
            check("reinstalled linked " + relative, (self.prefix / relative).resolve() == (binary.parent.parent / relative).resolve())
        self.run("brew", "test", "--verbose", new_formula)
        smoke = self.run("node", str(self.args.source / "scripts/verify-install.cjs"), str(binary), candidate["version"])
        match = re.search(r"Installed CLI smoke: ([1-9][0-9]*)/\1 checks passed", smoke.stdout)
        check("exact-source synthetic installed CLI passes after recovery", match is not None)
        self.run("brew", "uninstall", "--force", "--formula", new_formula)
        check("migration fixture cleanup removes all kegs", not list((self.cellar / "skuggsja").glob("*/INSTALL_RECEIPT.json")))
        for tap in reversed(self.taps):
            self.run("brew", "untrust", "--formula", tap + "/skuggsja")
            if tap == new_tap:
                self.run("brew", "untrust", "--command", tap + "/skuggsja-migrate")
            self.run("brew", "untap", tap)
        self.check_state()
        self.save("migration-result.json", {"status": "passed", "passed": len(checks), "checks": checks,
                  "synthetic_cli_checks": int(match.group(1)), "candidate_head": self.args.expected_head,
                  "versions_before": ["0.1.0", "0.1.1"], "versions_after_reinstall": expected_versions,
                  "scope": "Separate native explicit receipt recovery after real untrusted migration refusal; both retained kegs and pin preserved. Earlier reinstall failure evidence remains unchanged."})
        print(f"Native migration recovery: {len(checks)} checks passed; separate from package lifecycle")

    def execute(self):
        candidate, baseline = load_release(self.args.candidate), load_release(self.args.baseline)
        require(stable_version(baseline["version"]) < stable_version(candidate["version"]), "Upgrade must cross real versions")
        require(sys.platform == self.args.platform, "Unexpected native OS")
        require(platform.machine() in {"arm64": {"arm64", "aarch64"}, "x64": {"x86_64", "AMD64"}}[self.args.arch],
                "Unexpected native CPU")
        require(self.run("git", "-C", str(self.args.checkout), "rev-parse", "HEAD").stdout.strip() == self.args.expected_head,
                "Lifecycle checkout is not the checked PR/event head")
        require(digest(self.args.checkout / "Formula/skuggsja.rb") == candidate["formula_sha256"], "Candidate bytes changed")
        self.bind_source(self.args.source, candidate)
        self.bind_source(self.args.baseline_source, baseline)
        self.prefix = Path(self.run("brew", "--prefix").stdout.strip())
        self.cellar = Path(self.run("brew", "--cellar").stdout.strip())
        require(not (self.prefix / "bin/skuggsja").exists() and not (self.prefix / "bin/skuggsja").is_symlink()
                and not list((self.cellar / "skuggsja").glob("*/INSTALL_RECEIPT.json")), "Existing Skuggsja is outside this test")
        self.run("brew", "--version")
        cache = Path.home() / "Library/Caches" if sys.platform == "darwin" else Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        state = cache / "skuggsja/rewind.json"
        require(not state.exists() and not state.is_symlink(), "Never overwrite an existing runner artifact")
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.write_text('{"synthetic_lifecycle_state":"preserve across package operations"}\n')
        self.canary, self.canary_before = state, {"sha256": digest(state), "mtime_ns": state.stat().st_mtime_ns}
        self.save("state-before.json", self.canary_before)
        if self.args.mode == "public":
            fresh_tap = PUBLIC_TAP
            tap_path = self.tap(fresh_tap)
            self.verify_tap(tap_path, candidate, self.args.expected_head)
        else:
            fresh_tap = LOCAL_TAP
            remote = self.make_remote("candidate-remote", Path(candidate["formula_path"]))
            tap_path = self.tap(fresh_tap, remote)
            self.verify_tap(tap_path, candidate)
        qualified = fresh_tap + "/skuggsja"
        self.run("brew", "trust", "--formula", qualified)
        self.run("brew", "style", "--display-cop-names", qualified)
        self.run("brew", "audit", "--strict", qualified)
        self.run("brew", "install", "--formula", qualified)
        self.installed(fresh_tap, candidate, self.args.source, [candidate["version"]], "fresh")
        self.uninstall(fresh_tap)
        remote = self.make_remote("upgrade-remote", Path(baseline["formula_path"]))
        upgrade_path = self.tap(UPGRADE_TAP, remote)
        self.verify_tap(upgrade_path, baseline)
        self.run("brew", "trust", "--formula", UPGRADE_TAP + "/skuggsja")
        self.run("brew", "install", "--formula", UPGRADE_TAP + "/skuggsja")
        self.installed(UPGRADE_TAP, baseline, self.args.baseline_source, [baseline["version"]], "baseline")
        shutil.copyfile(candidate["formula_path"], remote / "Formula/skuggsja.rb")
        self.commit_formula(remote)
        self.run("git", "-C", str(upgrade_path), "pull", "--ff-only")
        self.verify_tap(upgrade_path, candidate)
        self.run("brew", "upgrade", "--formula", UPGRADE_TAP + "/skuggsja")
        versions = [baseline["version"], candidate["version"]]
        self.installed(UPGRADE_TAP, candidate, self.args.source, versions, "upgraded")
        self.run("brew", "reinstall", "--formula", UPGRADE_TAP + "/skuggsja")
        self.installed(UPGRADE_TAP, candidate, self.args.source, versions, "reinstalled")
        self.uninstall(UPGRADE_TAP)
        for tap in reversed(self.taps):
            self.run("brew", "untap", tap)
        self.check_state()
        self.save("state-after.json", {"sha256": digest(state), "mtime_ns": state.stat().st_mtime_ns})
        self.save("result.json", {"passed": len(self.checks), "checks": self.checks, "mode": self.args.mode,
                  "candidate_head": self.args.expected_head, "candidate_version": candidate["version"],
                  "baseline_version": baseline["version"], "state_preserved": True,
                  "platform": self.args.platform, "arch": self.args.arch,
                  "scope": "Actual fresh install, real version upgrade, retained keg receipts, reinstall/uninstall and synthetic CLI. Tap migration is separate."})
        print(f"Native lifecycle: {len(self.checks)} checks passed; {self.args.mode}; real {baseline['version']} -> {candidate['version']} upgrade")
        if self.args.verify_migration:
            self.migration_phase(candidate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "candidate", "baseline", "source", "baseline-source", "evidence-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--mode", choices=("candidate", "public"), required=True)
    parser.add_argument("--platform", choices=("darwin", "linux"), required=True)
    parser.add_argument("--arch", choices=("arm64", "x64"), required=True)
    parser.add_argument("--verify-migration", action="store_true")
    parser.add_argument("--migration-v010", type=Path)
    parser.add_argument("--migration-v011", type=Path)
    args = parser.parse_args()
    if args.verify_migration and (args.migration_v010 is None or args.migration_v011 is None):
        parser.error("--verify-migration requires both --migration-v010 and --migration-v011")
    lifecycle = Lifecycle(args)
    try:
        lifecycle.execute()
    except Exception as error:
        lifecycle.save("failure.json", {"error": str(error), "completed_checks": lifecycle.checks})
        raise


if __name__ == "__main__":
    main()
