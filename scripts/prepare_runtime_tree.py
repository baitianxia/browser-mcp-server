#!/usr/bin/env python3
"""Remove package-manager metadata and prune the fixed core runtime dependency set."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


CORE_ROOT_PACKAGES = {"@playwright", "playwright", "playwright-core"}
CORE_SCOPED_PACKAGES = {"@playwright": {"mcp"}}
PNPM_METADATA = {
    ".bin",
    ".modules.yaml",
    ".package-map.json",
    ".pnpm",
    ".pnpm-workspace-state-v1.json",
}


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def prepare(node_modules: Path, profile: str) -> None:
    if not node_modules.is_dir():
        raise ValueError(f"not a directory: {node_modules}")
    for name in PNPM_METADATA:
        remove_path(node_modules / name)
    for child in node_modules.iterdir():
        if child.name.startswith("."):
            remove_path(child)

    if profile == "core":
        for child in node_modules.iterdir():
            if child.name not in CORE_ROOT_PACKAGES:
                remove_path(child)
        for scope, allowed in CORE_SCOPED_PACKAGES.items():
            scope_root = node_modules / scope
            if not scope_root.is_dir():
                raise ValueError(f"required package scope missing: {scope}")
            for child in scope_root.iterdir():
                if child.name not in allowed:
                    remove_path(child)

    required = (
        node_modules / "@playwright" / "mcp" / "package.json",
        node_modules / "playwright" / "package.json",
        node_modules / "playwright-core" / "package.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("required runtime package missing: " + ", ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-modules", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=("core", "diagnostic"))
    args = parser.parse_args()
    try:
        prepare(args.node_modules.resolve(), args.profile)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
