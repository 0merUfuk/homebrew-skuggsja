#!/usr/bin/env python3
"""Verify public release artifacts as data before Homebrew evaluates any Ruby."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

UPSTREAM = "0merUfuk/skuggsja"
WORKFLOW = UPSTREAM + "/.github/workflows/release.yml"
STABLE = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
SHA256 = r"[0-9a-f]{64}"
ROOT = Path(__file__).resolve().parents[2]


class VerificationError(Exception):
    """A failed release guard; no candidate is approved for evaluation."""


def gh(*args):
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True,
                              timeout=180, check=False,
                              env={**os.environ, "GH_HOST": "github.com"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError("GitHub CLI could not complete release verification") from error


def api(endpoint, missing_ok=False):
    response = gh("api", "--hostname", "github.com", "--include", "repos/" + UPSTREAM + endpoint)
    header, separator, body = response.stdout.replace("\r\n", "\n").partition("\n\n")
    status = re.search(r"^HTTP/[0-9.]+ ([0-9]{3})\b", header)
    if missing_ok and status and status[1] == "404" and response.returncode:
        return None
    if response.returncode or not separator or not status or status[1] != "200":
        raise VerificationError("GitHub metadata request failed: " + endpoint)
    try:
        data = json.loads(body)
    except ValueError as error:
        raise VerificationError("Invalid GitHub metadata: " + endpoint) from error
    if not isinstance(data, dict):
        raise VerificationError("GitHub metadata must be an object: " + endpoint)
    return data


def stable_tag(tag):
    if not isinstance(tag, str) or not re.fullmatch("v" + STABLE, tag):
        raise VerificationError("Release tag must be canonical stable vMAJOR.MINOR.PATCH")
    return tag


def version_key(version):
    return tuple(map(int, version.split(".")))


def archive_names(version, include_windows=False):
    systems = ("darwin", "linux", "windows") if include_windows else ("darwin", "linux")
    return [f"skuggsja_{version}_{system}_{arch}." + ("zip" if system == "windows" else "tar.gz")
            for system in systems for arch in ("amd64", "arm64")]


def release_metadata(tag=None, missing_ok=False):
    if tag is not None:
        stable_tag(tag)
    release = api("/releases/tags/" + tag if tag else "/releases/latest", missing_ok=missing_ok)
    if release is None:
        return None
    found_tag = stable_tag(release.get("tag_name"))
    if tag is not None and found_tag != tag:
        raise VerificationError("Requested release tag does not match GitHub metadata")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise VerificationError("Draft and prerelease artifacts cannot enter the stable tap")
    expected = set(archive_names(found_tag[1:], include_windows=True) + ["skuggsja.rb", "checksums.txt"])
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) != len(expected):
        raise VerificationError("Release must contain exactly the eight expected assets")
    index = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise VerificationError("Invalid release asset metadata")
        name = asset.get("name")
        digest = asset.get("digest")
        if not isinstance(name, str) or name not in expected or name in index:
            raise VerificationError("Missing, duplicated or unexpected release asset")
        if (not isinstance(digest, str) or not re.fullmatch("sha256:" + SHA256, digest)
                or type(asset.get("id")) is not int or asset["id"] <= 0
                or type(asset.get("size")) is not int or not 0 < asset["size"] <= 256 * 1024 * 1024
                or asset.get("state") != "uploaded"):
            raise VerificationError("Release asset lacks a valid digest, size, ID or uploaded state")
        expected_url = f"https://github.com/{UPSTREAM}/releases/download/{found_tag}/{name}"
        if asset.get("browser_download_url") != expected_url:
            raise VerificationError("Release asset URL is outside the exact upstream tag")
        index[name] = {"id": asset["id"], "size": asset["size"], "sha256": digest[7:]}
    return {"tag": found_tag, "version": found_tag[1:], "assets": index}


def source_commit(tag):
    commit = api("/commits/" + stable_tag(tag)).get("sha")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise VerificationError("Release tag does not resolve to a valid source commit")
    return commit


def read_formula(path, missing_ok=False):
    path = Path(path)
    if any(parent.is_symlink() for parent in [path, *path.parents]):
        raise VerificationError("Formula path must not contain symlinks")
    if not path.exists() and missing_ok:
        return None
    if not path.is_file() or path.stat().st_size > 65536:
        raise VerificationError("Formula must be an ordinary file of at most 64 KiB")
    return path.read_bytes()


def formula_data(content):
    try:
        text = content.decode("utf-8")
    except UnicodeError as error:
        raise VerificationError("Formula is not UTF-8 text") from error
    markers = re.findall(r"^# Skuggsja release version: (.*)$", text, re.MULTILINE)
    if len(markers) != 1 or not re.fullmatch(STABLE, markers[0]):
        raise VerificationError("Formula must have exactly one canonical release-version comment")
    version = markers[0]
    if re.search(r"^[ \t]*version\b", text, re.MULTILINE):
        raise VerificationError("Formula version must be inferred from its release URLs")
    pairs = re.findall(r'^[ \t]*url "([^"\r\n]+)"\n[ \t]*sha256 "(' + SHA256 + r')"$', text, re.MULTILINE)
    expected = {f"https://github.com/{UPSTREAM}/releases/download/v{version}/{name}" for name in archive_names(version)}
    if (len(pairs) != 4 or {url for url, _ in pairs} != expected
            or len(re.findall(r"^[ \t]*url\b", text, re.MULTILINE)) != 4
            or len(re.findall(r"^[ \t]*sha256\b", text, re.MULTILINE)) != 4):
        raise VerificationError("Formula must pair exactly four expected release URLs with SHA256 values")
    return version, {url.rsplit("/", 1)[1]: digest for url, digest in pairs}


def checksums_data(content, version):
    try:
        lines = content.decode("ascii").splitlines()
    except UnicodeError as error:
        raise VerificationError("Release checksums are not ASCII") from error
    checksums = {}
    for line in lines:
        match = re.fullmatch("(" + SHA256 + r")  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in checksums:
            raise VerificationError("Malformed or duplicated release checksum")
        checksums[match[2]] = match[1]
    if set(checksums) != set(archive_names(version, include_windows=True)):
        raise VerificationError("Checksums must cover exactly the six release archives")
    return checksums


def evidence_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise VerificationError("Evidence directory must not be a symlink")
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise VerificationError("Evidence must be staged outside the tap checkout")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise VerificationError("Evidence directory must be empty")
    return path


def download_verified(name, metadata, source_sha, directory):
    artifact = directory / name
    result = gh("release", "download", metadata["tag"], "--repo", "https://github.com/" + UPSTREAM,
                "--pattern", name, "--dir", str(directory))
    expected = metadata["assets"][name]
    if result.returncode or artifact.is_symlink() or not artifact.is_file():
        raise VerificationError("Unable to download release asset: " + name)
    if artifact.stat().st_size != expected["size"]:
        raise VerificationError("Release asset size mismatch: " + name)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != expected["sha256"]:
        raise VerificationError("Release API digest mismatch: " + name)
    verified = gh("attestation", "verify", str(artifact), "--hostname", "github.com", "--repo", UPSTREAM,
                  "--signer-workflow", WORKFLOW, "--source-ref", "refs/tags/" + metadata["tag"],
                  "--source-digest", source_sha, "--deny-self-hosted-runners", "--format", "json")
    if verified.returncode:
        raise VerificationError("Release attestation failed: " + name)
    try:
        attestations = json.loads(verified.stdout)
    except ValueError as error:
        raise VerificationError("Attestation verifier did not return JSON: " + name) from error
    if not isinstance(attestations, list) or not attestations:
        raise VerificationError("Attestation verifier returned no verified attestations: " + name)
    (directory / (name + ".attestation.json")).write_text(verified.stdout, encoding="utf-8")
    return {"name": name, "path": str(artifact), "sha256": digest, "size": expected["size"]}


def verify_release(directory, tag=None, formula=None, metadata=None):
    candidate = read_formula(formula) if formula is not None else None
    if candidate is not None:
        candidate_version, _ = formula_data(candidate)
        if tag is not None and stable_tag(tag) != "v" + candidate_version:
            raise VerificationError("Candidate formula does not match the requested tag")
        tag = "v" + candidate_version
    metadata = metadata or release_metadata(tag)
    tag = metadata["tag"]
    source_sha = source_commit(tag)
    directory = evidence_directory(directory)
    records = {}
    for name in ("skuggsja.rb", "checksums.txt"):
        records[name] = download_verified(name, metadata, source_sha, directory)
    content = read_formula(directory / "skuggsja.rb")
    version, formula_hashes = formula_data(content)
    if version != metadata["version"]:
        raise VerificationError("Attested formula version differs from the selected release")
    if candidate is not None and content != candidate:
        raise VerificationError("Candidate formula bytes differ from the attested release asset")
    checksum_path = directory / "checksums.txt"
    if checksum_path.stat().st_size > 65536:
        raise VerificationError("Release checksum file exceeds 64 KiB")
    checksums = checksums_data(checksum_path.read_bytes(), version)
    for name, digest in checksums.items():
        if digest != metadata["assets"][name]["sha256"]:
            raise VerificationError("Checksum disagrees with release API digest: " + name)
    for name in archive_names(version):
        if formula_hashes[name] != checksums[name]:
            raise VerificationError("Formula SHA256 disagrees with release checksum: " + name)
        records[name] = download_verified(name, metadata, source_sha, directory)
    # Recheck mutable release metadata and tag resolution, without treating them as immutable.
    if release_metadata(tag) != metadata or source_commit(tag) != source_sha:
        raise VerificationError("Release assets or source tag changed during verification")
    if formula is not None and read_formula(formula) != candidate:
        raise VerificationError("Candidate formula changed during verification")
    manifest = {
        "repository": UPSTREAM, "signer_workflow": WORKFLOW, "tag": tag, "version": version,
        "source_sha": source_sha, "formula_sha256": records["skuggsja.rb"]["sha256"],
        "formula_path": records["skuggsja.rb"]["path"],
        "manifest_path": str(directory / "verification.json"),
        "archives": [records[name] for name in archive_names(version)],
        "assets": list(records.values()), "verified_attestations": len(records),
        "candidate_formula_path": str(Path(formula).resolve()) if formula is not None else None,
    }
    (directory / "verification.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def write_outputs(values):
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        lines = []
        for key, value in values.items():
            value = str(value).lower() if isinstance(value, bool) else str(value)
            if any(ord(character) < 32 for character in value):
                raise VerificationError("Unsafe control character in workflow output")
            lines.append(key + "=" + value + "\n")
        with open(output, "a", encoding="utf-8") as stream:
            stream.writelines(lines)


def manifest_outputs(manifest):
    return {key: manifest[key] for key in ("version", "tag", "source_sha", "formula_sha256", "formula_path", "manifest_path")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", "--output-dir", required=True, type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--formula", type=Path)
    args = parser.parse_args()
    if args.tag is None and args.formula is None:
        parser.error("--tag or --formula is required")
    manifest = verify_release(args.evidence_dir, tag=args.tag, formula=args.formula)
    write_outputs(manifest_outputs(manifest))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, OSError, ValueError) as error:
        print("Skuggsja release verification failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
