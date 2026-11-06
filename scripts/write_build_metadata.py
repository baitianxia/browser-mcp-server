#!/usr/bin/env python3
"""Write deterministic build metadata for the browser-mcp-server runtime."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import browser_agent  # noqa: E402


def minimum_node_version(profile: str) -> str:
    """Lowest Node.js the runtime profile supports on the target."""
    floor = (
        browser_agent.MIN_DEVTOOLS_NODE_VERSION
        if profile == "diagnostic"
        else browser_agent.MIN_NODE_VERSION
    )
    return ".".join(map(str, floor))


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
    if args.bundled_node and target["system"] == "windows":
        # Windows packages must not carry node.exe or any other executable;
        # they run on the target's existing, organization-approved Node.js.
        parser.error("Windows runtimes use the target's existing Node.js; --bundled-node is not allowed")
    payload = {
        "schemaVersion": 1,
        "runtimeVersion": "1.0.22",
        "profile": args.profile,
        "buildHost": build_host,
        "target": target,
        "crossBuilt": args.cross_built,
        "targetCliSmokeTested": not args.cross_built,
        "tools": {"node": args.node_version, "pnpm": args.pnpm_version},
        "bundledNode": args.bundled_node,
        "nodeRuntime": {
            "source": "bundled" if args.bundled_node else "system",
            "minimumVersion": minimum_node_version(args.profile),
        },
        "sourceDateEpoch": os.environ.get("SOURCE_DATE_EPOCH"),
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
