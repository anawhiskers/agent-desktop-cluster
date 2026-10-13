#!/usr/bin/env python3
"""Create or verify a hash manifest of public source files only."""
import argparse
import hashlib
import json
from pathlib import Path


def snapshot(root):
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {".git", "__pycache__", ".venv", "build", "dist"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("Public source must not contain a symlink")
        if path.is_file() and path.name != "SOURCE_MANIFEST.json":
            files[str(relative)] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    actual = snapshot(args.root)
    path = args.root / "SOURCE_MANIFEST.json"
    if args.write:
        path.write_text(json.dumps(dict(schema=1, files=actual), sort_keys=True, indent=2) + "\n")
        print("Manifest written for " + str(len(actual)) + " public files")
        return 0
    expected = json.loads(path.read_text())["files"]
    print("PASS: source manifest matches" if actual == expected else "FAIL: public source changed; review before updating the manifest")
    return 0 if actual == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
