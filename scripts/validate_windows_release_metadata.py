#!/usr/bin/env python3
"""Validate browser-mcp-server Windows release metadata."""

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
    allowed_root = {
        "schemaVersion", "toolkitVersion", "product", "displayName",
        "mcpServerName", "sourceFileCount", "sourcePolicy", "runtime",
        "browserExtension",
    }
    unknown_root = set(root) - allowed_root
    if unknown_root:
        raise MetadataValidationError(
            "KIT-METADATA.json contains unsupported fields: "
            + ", ".join(sorted(unknown_root))
        )
    if root.get("schemaVersion") != 1:
        raise MetadataValidationError("unsupported transfer metadata schema")
    if root.get("product") != "browser-mcp-server":
        raise MetadataValidationError("product must be browser-mcp-server")
    if root.get("displayName") != "浏览器助手":
        raise MetadataValidationError("displayName must be 浏览器助手")
    if root.get("mcpServerName") != "browser-mcp":
        raise MetadataValidationError("mcpServerName must be browser-mcp")
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
    digest = runtime.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise MetadataValidationError("runtime.sha256 must be a lowercase SHA-256 digest")
    size = runtime.get("sizeBytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise MetadataValidationError("runtime.sizeBytes must be a positive integer")

    extension = root.get("browserExtension")
    if extension is not None:
        extension = require_object(extension, "browserExtension")
        for field in ("extensionId", "version", "filename", "sizeBytes", "sha256", "path", "unpackedPath", "installation"):
            if field not in extension:
                raise MetadataValidationError(f"browserExtension.{field} is required")
        if not isinstance(extension.get("extensionId"), str) or not re.fullmatch(r"[a-z]{32}", extension["extensionId"]):
            raise MetadataValidationError("browserExtension.extensionId is invalid")
        if not isinstance(extension.get("version"), str) or not SEMVER_RE.fullmatch(extension["version"]):
            raise MetadataValidationError("browserExtension.version is invalid")
        if extension.get("path") != f"browser-extension/{extension.get('filename')}":
            raise MetadataValidationError("browserExtension.path must point inside browser-extension")
        if extension.get("unpackedPath") != "browser-extension/unpacked":
            raise MetadataValidationError("browserExtension.unpackedPath is invalid")
        if extension.get("installation") != "offline-user-policy-with-manual-unpacked-fallback":
            raise MetadataValidationError("browserExtension.installation is invalid")
        extension_size = extension.get("sizeBytes")
        if isinstance(extension_size, bool) or not isinstance(extension_size, int) or extension_size <= 0:
            raise MetadataValidationError("browserExtension.sizeBytes must be positive")
        if not isinstance(extension.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", extension["sha256"]):
            raise MetadataValidationError("browserExtension.sha256 must be lowercase SHA-256")

    node_version = tools.get("node")
    if not isinstance(node_version, str):
        raise MetadataValidationError("tools.node must be a version string")
    node_match = NODE_VERSION_RE.fullmatch(node_version)
    if not node_match or tuple(map(int, node_match.groups())) < (20, 19, 0):
        raise MetadataValidationError("tools.node must be an exact Node.js 20.19+ version")
    if tools.get("pnpm") != "11.19.0":
        raise MetadataValidationError("tools.pnpm must be exactly 11.19.0")


def validate_public_manifest(payload: Any) -> None:
    """Validate the top-level release-manifest.json carried by the ZIP."""
    root = require_object(payload, "release-manifest.json")
    if root.get("schemaVersion") != 1:
        raise MetadataValidationError("unsupported public release manifest schema")
    if root.get("product") != "browser-mcp-server":
        raise MetadataValidationError("product must be browser-mcp-server")
    if root.get("displayName") != "浏览器助手":
        raise MetadataValidationError("displayName must be 浏览器助手")
    if root.get("mcpServerName") != "browser-mcp":
        raise MetadataValidationError("mcpServerName must be browser-mcp")
    version = root.get("version")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise MetadataValidationError("version must be an exact semantic version")
    target = root.get("target")
    if target != {"system": "windows", "machine": "x64"}:
        raise MetadataValidationError("target must be exactly windows/x64")
    package = require_object(root.get("package"), "package")
    expected_name = f"browser-mcp-server-{version}-windows-x64"
    if package.get("name") != expected_name:
        raise MetadataValidationError(f"package.name must be exactly {expected_name}")
    if package.get("format") != "zip" or package.get("publicDelivery") != "zip-only":
        raise MetadataValidationError("public package must be ZIP-only")
    if package.get("singleTopLevelDirectory") is not True:
        raise MetadataValidationError("public package must have one top-level directory")
    expected_entries = {
        "entrypoint": "INSTALL.cmd",
        "configureEntrypoint": "CONFIGURE.cmd",
        "openConfigEntrypoint": "OPEN-CONFIG.cmd",
        "uninstallEntrypoint": "UNINSTALL.cmd",
        "checksumManifest": "SHA256SUMS.txt",
    }
    for key, expected in expected_entries.items():
        if package.get(key) != expected:
            raise MetadataValidationError(f"package.{key} must be {expected}")
    paths = require_object(root.get("paths"), "paths")
    if paths.get("installRoot") != r"%USERPROFILE%\browser-mcp-server" or paths.get("settings") != r"%USERPROFILE%\browser-mcp-server\config\settings.json":
        raise MetadataValidationError("public paths must use the browser-mcp-server USERPROFILE layout")
    traceability = require_object(root.get("traceability"), "traceability")
    archive = traceability.get("runtimeArchive")
    expected_archive = f"browser-agent-runtime-{version}-core-windows-x64.tar.gz"
    if archive != expected_archive:
        raise MetadataValidationError(f"runtimeArchive must be exactly {expected_archive}")
    digest = traceability.get("runtimeSha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise MetadataValidationError("runtimeSha256 must be a lowercase SHA-256 digest")
    source_count = traceability.get("sourceFileCount")
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count < 1:
        raise MetadataValidationError("traceability.sourceFileCount must be positive")
    if traceability.get("sourcePolicy") != "reviewed-allowlist":
        raise MetadataValidationError("traceability.sourcePolicy must be reviewed-allowlist")
    build = require_object(traceability.get("buildMetadata"), "traceability.buildMetadata")
    if build.get("runtimeVersion") != version:
        raise MetadataValidationError("buildMetadata.runtimeVersion must match version")
    if build.get("buildHost") != {"system": "windows", "machine": "x64"}:
        raise MetadataValidationError("buildMetadata.buildHost must be windows/x64")
    if build.get("target") != {"system": "windows", "machine": "x64"}:
        raise MetadataValidationError("buildMetadata.target must be windows/x64")
    if build.get("crossBuilt") is not False or build.get("targetCliSmokeTested") is not True:
        raise MetadataValidationError("public release must be a native CLI-tested Windows build")
    if build.get("bundledNode") is not True:
        raise MetadataValidationError("public release must include bundled Node.js")
    extension = root.get("browserExtension")
    if extension is not None:
        extension = require_object(extension, "browserExtension")
        if not isinstance(extension.get("extensionId"), str) or not re.fullmatch(r"[a-z]{32}", extension["extensionId"]):
            raise MetadataValidationError("browserExtension.extensionId is invalid")
        if not isinstance(extension.get("version"), str) or not SEMVER_RE.fullmatch(extension["version"]):
            raise MetadataValidationError("browserExtension.version is invalid")
        filename = extension.get("path")
        if not isinstance(filename, str) or not filename.startswith("payload/browser-extension/") or not filename.endswith(".crx"):
            raise MetadataValidationError("browserExtension.path must point to the payload CRX")
        if extension.get("unpackedPath") != "payload/browser-extension/unpacked":
            raise MetadataValidationError("browserExtension.unpackedPath is invalid")


def load_and_validate(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataValidationError(f"cannot read transfer metadata: {exc}") from exc
    # The ZIP carries a public ``release-manifest.json`` while its payload
    # retains the historical ``KIT-METADATA.json`` used by the installer. Both
    # include the product identity; distinguish them by their contract-shaped
    # package/traceability fields rather than by the shared product string.
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("package"), dict)
        and isinstance(payload.get("traceability"), dict)
    ):
        validate_public_manifest(payload)
    else:
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
