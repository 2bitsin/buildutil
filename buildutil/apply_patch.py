#!/usr/bin/env python3
"""Apply one unified-diff patch to a source file, writing the patched result elsewhere.

The build's generic patch-overlay step (buildutil.cmake) runs this once per discovered
patch: it copies --source verbatim to --output, then `git apply`s --patch against that copy.
The source file is never touched. A source that carries no patch is simply read in place by
the resolution rule, so this helper only ever runs for files that do have one.

The patch is applied with `git apply -p1` from the output's directory, so the diff's `a/<name>`
/ `b/<name>` headers resolve against the copied file — write patches with the bare target file
name under a/ and b/."""
import argparse
import shutil
import subprocess
from pathlib import Path


def apply_patch(source: Path, patch: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    # git apply runs from the output dir so `-p1` resolves the diff's a/<name> / b/<name> onto the
    # copied file; the patch path itself is made absolute first so cwd never affects finding it.
    result = subprocess.run(["git", "apply", "-p1", str(patch.resolve())],
                            cwd=output.parent, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"apply_patch: {patch} did not apply to {source}:\n{result.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="the unpatched source file")
    parser.add_argument("--patch", required=True, type=Path, help="the unified-diff patch")
    parser.add_argument("--output", required=True, type=Path, help="where to write the patched copy")
    args = parser.parse_args()
    apply_patch(args.source, args.patch, args.output)


if __name__ == "__main__":
    main()
