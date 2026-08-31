#!/usr/bin/env python3
"""Validate that a transfer kit is a Windows-native release artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
NODE_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class MetadataValidationError(RuntimeError):
    pass


def require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MetadataValidationError(f"{name} must be an object")
    return value


def validate_metadata(payload: Any) -> None:
    root = require_object(payload, "KIT-METADATA.json")
    if root.get("schemaVersion") != 1:
        raise MetadataValidationError("unsupported transfer metadata schema")
    if root.get("sourcePolicy") != "reviewed-allowlist":
        raise MetadataValidationError("sourcePolicy must be reviewed-allowlist")
    source_file_count = root.get("sourceFileCount")
    if (
        isinstance(source_file_count, bool)
        or not isinstance(source_file_count, int)
        or source_file_count < 1
    ):
        raise MetadataValidationError("sourceFileCount must be a positive integer")
    toolkit_version = root.get("toolkitVersion")
    if not isinstance(toolkit_version, str) or not SEMVER_RE.fullmatch(
        toolkit_version
    ):
        raise MetadataValidationError("toolkitVersion must be an exact semantic version")

    runtime = require_object(root.get("runtime"), "runtime")
    build = require_object(runtime.get("buildMetadata"), "runtime.buildMetadata")
    build_host = require_object(build.get("buildHost"), "buildHost")
    target = require_object(build.get("target"), "target")
    tools = require_object(build.get("tools"), "tools")

    if build.get("schemaVersion") != 1:
        raise MetadataValidationError("unsupported runtime build metadata schema")
    if build_host != {"system": "windows", "machine": "x64"}:
        raise MetadataValidationError("buildHost must be exactly windows/x64")
    if target != {"system": "windows", "machine": "x64"}:
        raise MetadataValidationError("target must be exactly windows/x64")
    if build.get("crossBuilt") is not False:
        raise MetadataValidationError("crossBuilt must be false")
    if build.get("targetCliSmokeTested") is not True:
        raise MetadataValidationError("targetCliSmokeTested must be true")
    if build.get("bundledNode") is not True:
        raise MetadataValidationError("bundledNode must be true")
    if build.get("profile") != "core":
        raise MetadataValidationError("Windows one-click release profile must be core")
    if build.get("runtimeVersion") != toolkit_version:
        raise MetadataValidationError("runtimeVersion must match toolkitVersion")

    archive = runtime.get("archive")
    expected_archive = (
        f"browser-agent-runtime-{toolkit_version}-core-windows-x64.tar.gz"
    )
    if archive != expected_archive:
        raise MetadataValidationError(
            f"runtime archive must be exactly {expected_archive}"
        )

    node_version = tools.get("node")
    if not isinstance(node_version, str):
        raise MetadataValidationError("tools.node must be a version string")
    node_match = NODE_VERSION_RE.fullmatch(node_version)
    if not node_match or tuple(map(int, node_match.groups())) < (20, 19, 0):
        raise MetadataValidationError("tools.node must be an exact Node.js 20.19+ version")
    if tools.get("pnpm") != "11.19.0":
        raise MetadataValidationError("tools.pnpm must be exactly 11.19.0")


def load_and_validate(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataValidationError(f"cannot read transfer metadata: {exc}") from exc
    validate_metadata(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path)
    args = parser.parse_args()
    try:
        load_and_validate(args.metadata)
    except MetadataValidationError as exc:
        print(f"INVALID WINDOWS RELEASE METADATA: {exc}", file=sys.stderr)
        return 2
    print("WINDOWS RELEASE METADATA: VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
