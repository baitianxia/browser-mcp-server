#!/usr/bin/env python3
"""Reject links, native binaries, and target-incompatible packages in node_modules."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any


NATIVE_SUFFIXES = {".dll", ".dylib", ".exe", ".node", ".so"}
NPM_OS = {"darwin": "darwin", "linux": "linux", "windows": "win32"}
NPM_ARCH = {"arm64": "arm64", "x64": "x64"}


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def constraint_allows(value: Any, target: str) -> bool:
    if not isinstance(value, list) or not value:
        return True
    positive = {item for item in value if isinstance(item, str) and not item.startswith("!")}
    negative = {item[1:] for item in value if isinstance(item, str) and item.startswith("!")}
    return target not in negative and (not positive or target in positive)


def portability_errors(root: Path, target_os: str, target_arch: str) -> list[str]:
    errors: list[str] = []
    seen_packages: set[tuple[str, str]] = set()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(dirnames + filenames):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if is_link_or_reparse(path):
                errors.append(f"link/reparse point: {relative}")
            elif path.is_file() and path.suffix.lower() in NATIVE_SUFFIXES:
                errors.append(f"native binary: {relative}")

        package_path = directory_path / "package.json"
        if not package_path.is_file() or is_link_or_reparse(package_path):
            continue
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid package metadata {package_path.relative_to(root)}: {exc}")
            continue
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        identity = (name, version)
        if identity in seen_packages:
            continue
        seen_packages.add(identity)
        if not constraint_allows(package.get("os"), NPM_OS[target_os]):
            errors.append(f"package excludes target OS: {name}@{version}")
        if not constraint_allows(package.get("cpu"), NPM_ARCH[target_arch]):
            errors.append(f"package excludes target architecture: {name}@{version}")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--target-os", required=True, choices=tuple(NPM_OS))
    parser.add_argument("--target-arch", required=True, choices=tuple(NPM_ARCH))
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    errors = portability_errors(args.root.resolve(), args.target_os, args.target_arch)
    if errors:
        print("NOT PORTABLE")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PORTABLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
