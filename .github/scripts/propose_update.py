#!/usr/bin/env python3
"""Open one formula-only candidate PR; never push main or execute release code."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from verify_release import formula_data, read_formula, version_key

REPOSITORY = "0merUfuk/homebrew-skuggsja"
FORMULA = "Formula/skuggsja.rb"


def api(method, endpoint, body=None, missing=False):
    command = ["gh", "api", "--hostname", "github.com", "--include", "--method", method,
               f"repos/{REPOSITORY}/{endpoint}"]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            capture_output=True, text=True, timeout=120,
                            env={**os.environ, "GH_HOST": "github.com"})
    header, separator, content = result.stdout.replace("\r\n", "\n").partition("\n\n")
    status = re.search(r"^HTTP/[0-9.]+ ([0-9]{3})\b", header)
    if missing and result.returncode and status and status[1] == "404":
        return None
    if result.returncode or not separator or not status or status[1] not in {"200", "201"}:
        raise RuntimeError("GitHub API request failed: " + method + " " + endpoint)
    return json.loads(content)


def content_at(call, ref):
    result = call("GET", f"contents/{FORMULA}?ref={ref}", missing=True)
    if result is None:
        return None
    if result.get("type") != "file" or result.get("encoding") != "base64":
        raise ValueError("Formula must be an ordinary GitHub content file")
    encoded = "".join(result["content"].splitlines())
    content = base64.b64decode(encoded, validate=True)
    if len(content) > 65536:
        raise ValueError("Formula exceeds 64 KiB")
    return content


def propose(manifest, expected_main, call=api):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_main):
        raise ValueError("Expected a full bootstrap/main commit SHA")
    tag = manifest["tag"]
    if not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
        raise ValueError("Only canonical stable tags can name candidate branches")
    if manifest["version"] != tag[1:] or manifest["verified_attestations"] != 6:
        raise ValueError("Candidate verification is incomplete")
    if manifest["repository"] != "0merUfuk/skuggsja" or manifest["signer_workflow"] != "0merUfuk/skuggsja/.github/workflows/release.yml":
        raise ValueError("Unexpected release provenance identity")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["source_sha"]):
        raise ValueError("Expected an exact verified source commit")
    formula = read_formula(manifest["formula_path"])
    if hashlib.sha256(formula).hexdigest() != manifest["formula_sha256"]:
        raise ValueError("Attested staged formula changed")
    candidate_version, _ = formula_data(formula)
    if candidate_version != manifest["version"]:
        raise ValueError("Candidate formula version differs from its verification manifest")
    main = call("GET", "git/ref/heads/main")["object"]["sha"]
    if main != expected_main:
        raise ValueError("Main advanced during candidate preparation; retry against its new head")
    current = content_at(call, main)
    if current == formula:
        return {"status": "already_imported", "tag": tag}
    if current is not None:
        current_version, _ = formula_data(current)
        if version_key(current_version) > version_key(candidate_version):
            raise ValueError("Candidate would roll back the current main formula")
        if current_version == candidate_version:
            raise ValueError("Existing stable version has different formula bytes on main")
    branch = "update-skuggsja-" + tag
    existing = call("GET", "git/ref/heads/" + branch, missing=True)
    prs = call("GET", f"pulls?state=all&head=0merUfuk:{branch}&base=main&per_page=100")
    if len(prs) > 1:
        raise ValueError("More than one PR exists for this version")
    if prs and prs[0]["state"] == "closed":
        return {"status": "closed_candidate_requires_maintainer", "url": prs[0]["html_url"], "tag": tag}
    if prs and (prs[0]["state"] != "open" or not existing):
        raise ValueError("Open candidate PR must have its expected version branch")
    parents = [main]
    if existing:
        old_head = existing["object"]["sha"]
        if content_at(call, old_head) != formula:
            raise ValueError("Existing version branch has different formula bytes")
        comparison = call("GET", f"compare/{main}...{old_head}")
        files = comparison.get("files")
        if (not isinstance(files, list) or len(files) != 1 or files[0].get("filename") != FORMULA
                or files[0].get("status") not in {"added", "modified"}):
            raise ValueError("Existing candidate branch contains non-formula changes")
        commit = call("GET", "git/commits/" + old_head)
        if main in [item["sha"] for item in commit["parents"]]:
            check_main(call, main)
            return {"status": "candidate_reused", "tag": tag, "head": old_head,
                    "url": prs[0]["html_url"] if prs else create_pr(call, tag, branch)["html_url"]}
        parents = [old_head, main]
    base_tree = call("GET", "git/commits/" + main)["tree"]["sha"]
    check_main(call, main)
    blob = call("POST", "git/blobs", {"content": base64.b64encode(formula).decode(), "encoding": "base64"})["sha"]
    tree = call("POST", "git/trees", {"base_tree": base_tree,
                "tree": [{"path": FORMULA, "mode": "100644", "type": "blob", "sha": blob}]})["sha"]
    commit = call("POST", "git/commits", {"message": f"chore(skuggsja): import {tag}",
                  "tree": tree, "parents": parents})["sha"]
    # Recheck before moving a branch; a failed check leaves only unreferenced Git objects.
    check_main(call, main)
    if existing:
        call("PATCH", "git/refs/heads/" + branch, {"sha": commit, "force": False})
    else:
        call("POST", "git/refs", {"ref": "refs/heads/" + branch, "sha": commit})
    pr = prs[0] if prs else create_pr(call, tag, branch)
    return {"status": "candidate_open", "tag": tag, "head": commit, "url": pr["html_url"]}


def check_main(call, expected):
    if call("GET", "git/ref/heads/main")["object"]["sha"] != expected:
        raise ValueError("Main advanced during proposal; retry against its new head")


def create_pr(call, tag, branch):
    return call("POST", "pulls", {"title": f"chore(skuggsja): import {tag}", "head": branch, "base": "main",
        "body": f"Import the attested Skuggsja {tag} formula from the existing upstream release.\n\n"
                "The importer verified all four native archives, checksums and formula against the exact release workflow, tag and source commit. "
                "This PR changes only Formula/skuggsja.rb and does not publish binaries.\n\n"
                "Approve workflows to run when GitHub requests it. Review the exact candidate head after all four native lifecycle checks pass; merging remains the maintainer's publication gate."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-main", required=True)
    args = parser.parse_args()
    if os.getenv("GITHUB_REPOSITORY") != REPOSITORY or os.getenv("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Importer is restricted to this tap's main workflow")
    result = propose(json.loads(args.manifest.read_text()), args.expected_main)
    print(json.dumps(result, indent=2))
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"Candidate status: {result['status']}. {result.get('url', '')}\n")
            stream.write("No formula was promoted to main. Workflow approval and protected-branch review remain required.\n")


if __name__ == "__main__":
    main()
