#!/usr/bin/env python3
"""Bind checks to one checkout; never evaluate formula Ruby."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

FORMULA = "Formula/skuggsja.rb"
VERSION = re.compile(rb"^# Skuggsja release version: (0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", re.M)


def version(data):
    matches = list(VERSION.finditer(data))
    if len(matches) != 1:
        raise ValueError("Expected one canonical stable release-version comment")
    return tuple(int(value) for value in matches[0].groups())


def git(root, *args, allow_missing=False):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode and not allow_missing:
        raise RuntimeError(result.stderr.decode())
    return result


def prepare(root, expected_head, base_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise ValueError("Expected a full candidate commit SHA")
    base_formula = None
    if base_sha and base_sha != "0" * 40:
        if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            raise ValueError("Expected a full base commit SHA")
        git(root, "cat-file", "-e", f"{base_sha}^{{commit}}")
        base_file = git(root, "show", f"{base_sha}:{FORMULA}", allow_missing=True)
        if base_file.returncode == 0:
            base_formula = base_file.stdout
    actual = git(root, "rev-parse", "HEAD").stdout.decode().strip()
    if actual != expected_head:
        raise ValueError("Checkout does not match the event's exact candidate head")
    tracked = git(root, "show", f"{actual}:{FORMULA}", allow_missing=True)
    path = root / FORMULA
    if tracked.returncode:
        if base_formula is not None:
            raise ValueError("Formula is missing although the event base distributed it")
        if path.exists() or path.is_symlink():
            raise ValueError("Untracked formula cannot authorize a candidate")
        history = git(root, "log", "--format=%H", actual, "--", FORMULA).stdout
        if history.strip():
            raise ValueError("Formula removed after distribution existed")
        return {"has_formula": "false", "head_sha": actual,
                "scope": "Infrastructure bootstrap: no formula, no installation result"}
    if path.is_symlink() or not path.is_file() or path.read_bytes() != tracked.stdout:
        raise ValueError("Candidate formula differs from the checked commit")
    candidate = version(tracked.stdout)
    baseline = (0, 1, 0)
    if base_formula is not None:
        previous_version = version(base_formula)
        if previous_version < candidate:
            baseline = previous_version
        elif previous_version > candidate:
            raise ValueError("Candidate downgrades the base formula")
        elif base_formula != tracked.stdout:
            raise ValueError("Existing stable version formula bytes were replaced")
    if baseline >= candidate:
        raise ValueError("A strictly older released upgrade baseline is required")
    return {"has_formula": "true", "head_sha": actual,
            "formula_sha256": hashlib.sha256(tracked.stdout).hexdigest(),
            "tag": "v" + ".".join(map(str, candidate)),
            "baseline_tag": "v" + ".".join(map(str, baseline)),
            "scope": "Exact candidate; unchanged-formula/bootstrap baseline is explicitly v0.1.0"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--base-sha", default="")
    args = parser.parse_args()
    result = prepare(args.checkout.resolve(), args.expected_head, args.base_sha)
    print(json.dumps(result, indent=2))
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            for key, value in result.items():
                if key != "scope":
                    stream.write(f"{key}={value}\n")
    if result["has_formula"] == "false" and os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("Infrastructure bootstrap verified. No formula exists; native installation checks are not applicable and no installation pass is claimed.\n")


if __name__ == "__main__":
    main()
