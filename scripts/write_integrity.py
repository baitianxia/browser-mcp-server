#!/usr/bin/env python3
"""Write regular-file hashes and an explicit symlink inventory for a bundle root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path


EXCLUDED = {"SHA256SUMS", "SYMLINKS.json"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--reject-links", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    files: list[tuple[str, str]] = []
    links: list[dict[str, str]] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(dirnames + filenames):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if relative in EXCLUDED:
                continue
            if is_link_or_reparse(path):
                if args.reject_links:
                    parser.error(f"link/reparse point is forbidden: {relative}")
                try:
                    target = os.readlink(path)
                except OSError as exc:
                    parser.error(f"cannot read link/reparse point {relative}: {exc}")
                links.append({"path": relative, "target": target})
            elif path.is_file():
                files.append((relative, sha256(path)))

    links.sort(key=lambda item: item["path"])
    (root / "SYMLINKS.json").write_text(
        json.dumps({"schemaVersion": 1, "links": links}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    files.append(("SYMLINKS.json", sha256(root / "SYMLINKS.json")))
    files.sort()
    (root / "SHA256SUMS").write_text(
        "".join(f"{digest}  {path}\n" for path, digest in files),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
