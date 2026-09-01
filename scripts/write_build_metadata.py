#!/usr/bin/env python3
"""Write deterministic build metadata for an offline runtime."""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path


def normalized_system(value: str) -> str:
    lowered = value.lower()
    if lowered.startswith("win"):
        return "windows"
    if lowered in {"darwin", "linux"}:
        return lowered
    return lowered


def normalized_machine(value: str) -> str:
    lowered = value.lower()
    if lowered in {"amd64", "x86_64"}:
        return "x64"
    if lowered in {"aarch64", "arm64"}:
        return "arm64"
    return lowered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=("core", "diagnostic"))
    parser.add_argument("--node-version", required=True)
    parser.add_argument("--pnpm-version", required=True)
    parser.add_argument("--target-system", choices=("darwin", "linux", "windows"))
    parser.add_argument("--target-machine", choices=("arm64", "x64"))
    parser.add_argument("--cross-built", action="store_true")
    parser.add_argument("--bundled-node", action="store_true")
    args = parser.parse_args()
    if (args.target_system is None) != (args.target_machine is None):
        parser.error("--target-system and --target-machine must be provided together")
    build_host = {
        "system": normalized_system(platform.system()),
        "machine": normalized_machine(platform.machine()),
    }
    target = (
        {"system": args.target_system, "machine": args.target_machine}
        if args.target_system
        else build_host
    )
    if args.cross_built and target == build_host:
        parser.error("--cross-built requires a target different from the build host")
    payload = {
        "schemaVersion": 1,
        "runtimeVersion": "1.0.15",
        "profile": args.profile,
        "buildHost": build_host,
        "target": target,
        "crossBuilt": args.cross_built,
        "targetCliSmokeTested": not args.cross_built,
        "tools": {"node": args.node_version, "pnpm": args.pnpm_version},
        "bundledNode": args.bundled_node,
        "sourceDateEpoch": os.environ.get("SOURCE_DATE_EPOCH"),
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
