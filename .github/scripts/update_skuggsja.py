#!/usr/bin/env python3
"""Stage an attested stable formula candidate; never alter the tap or execute Ruby."""

import argparse
import json
from pathlib import Path
import sys

from verify_release import (ROOT, VerificationError, formula_data, manifest_outputs,
                            read_formula, release_metadata, verify_release, version_key,
                            write_outputs)


def update(evidence_dir, current_formula, tag=None):
    old_content = read_formula(current_formula, missing_ok=True)
    old_version = formula_data(old_content)[0] if old_content is not None else None
    metadata = release_metadata(tag, missing_ok=tag is None)
    if metadata is None:
        write_outputs({"changed": False})
        return {"changed": False, "reason": "No public stable upstream release is available."}
    version = metadata["version"]
    if old_version is not None and version_key(version) < version_key(old_version):
        raise VerificationError("Refusing to roll back the current stable formula")
    manifest = verify_release(evidence_dir, metadata=metadata)
    candidate = read_formula(manifest["formula_path"])
    if read_formula(current_formula, missing_ok=True) != old_content:
        raise VerificationError("Current formula changed during verification; refusing the candidate")
    if old_version == version and candidate != old_content:
        raise VerificationError("Existing stable version changed bytes; refusing in-place replacement")
    result = {"changed": candidate != old_content, **manifest_outputs(manifest)}
    write_outputs(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", "--output-dir", required=True, type=Path)
    parser.add_argument("--current-formula", type=Path, default=ROOT / "Formula" / "skuggsja.rb")
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(json.dumps(update(args.evidence_dir, args.current_formula, args.tag), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, OSError, ValueError) as error:
        print("Skuggsja candidate preparation failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
