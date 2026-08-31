#!/usr/bin/env python3
"""Validate and render a fail-closed intranet Browser Agent deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import urlsplit


TOOL_VERSION = "1.0.12"
PLAYWRIGHT_MCP_VERSION = "0.0.79"
CHROME_DEVTOOLS_MCP_VERSION = "1.8.0"
MIN_NODE_VERSION = (20, 19, 0)

REQUIRED_CONFIRMATIONS = {
    "delete",
    "financial-transaction",
    "production-change",
    "account-permission-change",
    "representational-communication",
    "file-upload",
    "final-submission",
    "secret-transmission",
}

CHANNELS = {
    "chrome",
    "chrome-beta",
    "chrome-dev",
    "chrome-canary",
    "msedge",
    "msedge-beta",
    "msedge-dev",
    "msedge-canary",
}

ORIGIN_RE = re.compile(
    r"^(?P<scheme>https?)://"
    r"(?P<host>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)"
    r"(?::(?P<port>\*|[0-9]{1,5}))?$"
)
DEPLOYMENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:$")
WINDOWS_RESERVED_NAME_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)
WINDOWS_MCP_ENVIRONMENT_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "windows-mcp-environment.json"
)
ENVIRONMENT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ManifestError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be an object")
    return data


def _load_windows_mcp_environment() -> dict[str, str]:
    try:
        payload = json.loads(WINDOWS_MCP_ENVIRONMENT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"cannot load Windows MCP environment policy: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "environment"}:
        raise ManifestError("Windows MCP environment policy has an invalid root shape")
    if payload["schemaVersion"] != 1:
        raise ManifestError("unsupported Windows MCP environment policy schema")
    environment = payload["environment"]
    if not isinstance(environment, dict) or not environment:
        raise ManifestError("Windows MCP environment policy must be a non-empty object")
    for name, value in environment.items():
        if not isinstance(name, str) or not ENVIRONMENT_NAME_RE.fullmatch(name):
            raise ManifestError(f"invalid Windows MCP environment name: {name!r}")
        if not (name.startswith("PLAYWRIGHT_MCP_") or name in {"NODE_OPTIONS", "NODE_PATH"}):
            raise ManifestError(f"unexpected Windows MCP environment name: {name}")
        if not isinstance(value, str) or "\x00" in value:
            raise ManifestError(f"invalid Windows MCP environment value for {name}")
    required_values = {
        "NODE_OPTIONS": "",
        "NODE_PATH": "",
        "PLAYWRIGHT_MCP_CONFIG": "",
        "PLAYWRIGHT_MCP_PING_TIMEOUT_MS": "5000",
    }
    for name, expected in required_values.items():
        if environment.get(name) != expected:
            raise ManifestError(
                f"Windows MCP environment policy must set {name} to {expected!r}"
            )
    return dict(sorted(environment.items()))


def _shape(
    value: Any,
    path: str,
    required: Iterable[str],
    optional: Iterable[str],
    errors: list[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {}
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - value.keys())
    unknown = sorted(value.keys() - allowed)
    errors.extend(f"{path}.{key} is required" for key in missing)
    errors.extend(f"{path}.{key} is not allowed" for key in unknown)
    return value


def _string(value: Any, path: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
        return ""
    if value != value.strip():
        errors.append(f"{path} must not have leading or trailing whitespace")
    return value


def _integer(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if type(value) is not int:
        errors.append(f"{path} must be an integer")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{path} must be >= {minimum}")
    if maximum is not None and value > maximum:
        errors.append(f"{path} must be <= {maximum}")
    return value


def _boolean(value: Any, path: str, errors: list[str]) -> bool | None:
    if type(value) is not bool:
        errors.append(f"{path} must be a boolean")
        return None
    return value


def _absolute_path(
    value: Any, path: str, target_os: str | None, errors: list[str]
) -> str:
    result = _string(value, path, errors)
    if not result:
        return result
    if "\x00" in result:
        errors.append(f"{path} must not contain a NUL character")
        return result
    if target_os == "windows":
        pure = PureWindowsPath(result)
        unsafe_character = any(
            ord(character) < 32 or character in '<>"|?*'
            for character in result[2:]
        )
        unsafe_component = any(
            component.endswith((" ", "."))
            or WINDOWS_RESERVED_NAME_RE.fullmatch(component) is not None
            for component in pure.parts[1:]
        )
        if (
            not pure.is_absolute()
            or not WINDOWS_DRIVE_RE.fullmatch(pure.drive)
            or ".." in pure.parts
            or ":" in result[2:]
            or unsafe_character
            or unsafe_component
        ):
            errors.append(
                f"{path} must be a safe absolute local Windows drive path"
            )
    else:
        pure = PurePosixPath(result)
        if not pure.is_absolute() or ".." in pure.parts or "\\" in result:
            errors.append(f"{path} must be an absolute POSIX path without '..'")
    return result


def _list_of_strings(
    value: Any, path: str, errors: list[str], *, nonempty: bool = True
) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return []
    if nonempty and not value:
        errors.append(f"{path} must not be empty")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_string(item, f"{path}[{index}]", errors))
    if len(set(result)) != len(result):
        errors.append(f"{path} must not contain duplicates")
    return result


def _walk_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")


def _origin_parts(origin: str) -> tuple[str, str] | None:
    match = ORIGIN_RE.fullmatch(origin)
    if not match:
        return None
    port = match.group("port")
    if port and port != "*" and int(port) > 65535:
        return None
    host = match.group("host").lower()
    if host in {"*", ".", ".."} or ".." in host or host.startswith("."):
        return None
    return match.group("scheme"), host.strip("[]")


def _is_loopback(host: str) -> bool:
    host = host.lower().strip("[]")
    if host in {"localhost", "::1"}:
        return True
    if host.startswith("127."):
        parts = host.split(".")
        return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
    return False


def _loopback_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlsplit(endpoint)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and _is_loopback(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
        )
    except ValueError:
        return False


def _looks_like_default_profile(path: str) -> bool:
    normalized = path.replace("\\", "/").rstrip("/").lower()
    known_roots = (
        "/library/application support/google/chrome",
        "/library/application support/microsoft edge",
        "/.config/google-chrome",
        "/.config/microsoft-edge",
        "/appdata/local/google/chrome/user data",
        "/appdata/local/microsoft/edge/user data",
    )
    return normalized.endswith("/default") or any(root in normalized for root in known_roots)


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    production = manifest.get("environment") == "production"
    root_required = {
        "schemaVersion",
        "deploymentId",
        "environment",
        "target",
        "mode",
        "installRoot",
        "configRoot",
        "workspaceRoots",
        "output",
        "browser",
        "controls",
        "timeouts",
    }
    if production:
        root_required.add("network")
        root_required.add("dataBoundary")
    root = _shape(
        manifest,
        "$",
        required=root_required,
        optional={"$schema", "mcpScope", "nodeExecutable", "network", "dataBoundary"}
        - root_required,
        errors=errors,
    )

    if root.get("schemaVersion") != 1:
        errors.append("$.schemaVersion must equal 1")

    deployment_id = _string(root.get("deploymentId"), "$.deploymentId", errors)
    if deployment_id and not DEPLOYMENT_ID_RE.fullmatch(deployment_id):
        errors.append("$.deploymentId must match ^[a-z][a-z0-9-]{2,62}$")

    environment = root.get("environment")
    if environment not in {"development", "pilot", "production"}:
        errors.append("$.environment must be development, pilot, or production")

    target = _shape(
        root.get("target"),
        "$.target",
        required={"os", "arch"},
        optional=set(),
        errors=errors,
    )
    target_os = target.get("os")
    target_arch = target.get("arch")
    if target_os not in {"darwin", "linux", "windows"}:
        errors.append("$.target.os must be darwin, linux, or windows")
    if target_arch not in {"arm64", "x64"}:
        errors.append("$.target.arch must be arm64 or x64")
    if target_os == "windows" and target_arch != "x64":
        errors.append("$.target.arch must be x64 for Windows in V1")

    if target_os == "windows":
        if "nodeExecutable" not in root:
            errors.append("$.nodeExecutable is required for Windows")
        else:
            _absolute_path(
                root.get("nodeExecutable"), "$.nodeExecutable", target_os, errors
            )
    elif "nodeExecutable" in root:
        errors.append("$.nodeExecutable is only valid for Windows")

    mode = root.get("mode")
    if mode not in {"persistent", "extension", "cdp"}:
        errors.append("$.mode must be persistent, extension, or cdp")

    mcp_scope = root.get("mcpScope", "project")
    if mcp_scope not in {"project", "user", "managed"}:
        errors.append("$.mcpScope must be project, user, or managed")

    _absolute_path(root.get("installRoot"), "$.installRoot", target_os, errors)
    _absolute_path(root.get("configRoot"), "$.configRoot", target_os, errors)
    workspace_roots = _list_of_strings(
        root.get("workspaceRoots"),
        "$.workspaceRoots",
        errors,
        nonempty=mcp_scope != "user",
    )
    if mcp_scope == "user" and workspace_roots:
        errors.append("$.workspaceRoots must be empty for user-scoped MCP")
    for index, path in enumerate(workspace_roots):
        _absolute_path(path, f"$.workspaceRoots[{index}]", target_os, errors)

    output = _shape(
        root.get("output"),
        "$.output",
        required={"directory", "maxBytes", "retainSession", "retentionDays"},
        optional=set(),
        errors=errors,
    )
    _absolute_path(output.get("directory"), "$.output.directory", target_os, errors)
    _integer(output.get("maxBytes"), "$.output.maxBytes", errors, 1_048_576, 1_073_741_824)
    retain_session = _boolean(output.get("retainSession"), "$.output.retainSession", errors)
    retention_days = _integer(output.get("retentionDays"), "$.output.retentionDays", errors, 0, 30)
    if retain_session is False and retention_days not in {None, 0}:
        errors.append("$.output.retentionDays must be 0 when retainSession is false")
    if retain_session is True and retention_days == 0:
        errors.append("$.output.retentionDays must be > 0 when retainSession is true")

    browser = _shape(
        root.get("browser"),
        "$.browser",
        required={"channel", "profileOwner", "maxConcurrentAgents"},
        optional={
            "userDataDir",
            "executablePath",
            "cdpEndpoint",
            "devtoolsEndpoint",
            "extensionDistribution",
            "manualConnectionApproval",
        },
        errors=errors,
    )
    channel = browser.get("channel")
    if channel not in {"chrome", "msedge"}:
        errors.append("$.browser.channel must be chrome or msedge")
    _string(browser.get("profileOwner"), "$.browser.profileOwner", errors)
    if browser.get("maxConcurrentAgents") != 1:
        errors.append("$.browser.maxConcurrentAgents must equal 1")

    if mode == "persistent":
        profile = _absolute_path(
            browser.get("userDataDir"), "$.browser.userDataDir", target_os, errors
        )
        if profile and _looks_like_default_profile(profile):
            errors.append("$.browser.userDataDir appears to be a personal/default browser profile")
        for key in (
            "executablePath",
            "cdpEndpoint",
            "extensionDistribution",
            "manualConnectionApproval",
        ):
            if key in browser:
                errors.append(f"$.browser.{key} is not valid in persistent mode")
    elif mode == "extension":
        executable_path = browser.get("executablePath")
        if target_os == "windows":
            executable_path = _absolute_path(
                executable_path,
                "$.browser.executablePath",
                target_os,
                errors,
            )
            if executable_path and not executable_path.lower().endswith(".exe"):
                errors.append("$.browser.executablePath must point to a Windows .exe")
        elif executable_path is not None:
            errors.append(
                "$.browser.executablePath is only supported for Windows extension mode"
            )
        distribution = browser.get("extensionDistribution")
        if distribution not in {
            "managed-web-store",
            "managed-self-hosted-crx",
            "manual-pilot",
        }:
            errors.append(
                "$.browser.extensionDistribution must describe the approved extension channel"
            )
        if browser.get("manualConnectionApproval") is not True:
            errors.append("$.browser.manualConnectionApproval must be true")
        if environment == "production" and distribution == "manual-pilot":
            errors.append("manual-pilot extension distribution is forbidden in production")
        for key in ("userDataDir", "cdpEndpoint"):
            if key in browser:
                errors.append(f"$.browser.{key} is not valid in extension mode")
    elif mode == "cdp":
        endpoint = _string(browser.get("cdpEndpoint"), "$.browser.cdpEndpoint", errors)
        if endpoint and endpoint not in CHANNELS and not _loopback_endpoint(endpoint):
            errors.append(
                "$.browser.cdpEndpoint must be a supported Chrome channel or loopback HTTP(S) endpoint"
            )
        for key in (
            "userDataDir",
            "executablePath",
            "extensionDistribution",
            "manualConnectionApproval",
        ):
            if key in browser:
                errors.append(f"$.browser.{key} is not valid in cdp mode")

    origin_parts: list[tuple[str, str]] = []
    if root.get("network") is not None:
        network = _shape(
            root.get("network"),
            "$.network",
            required=(
                {"allowedOrigins", "enforcement", "enforcementOwner", "changeReference"}
                if production
                else {"allowedOrigins"}
            ),
            optional=(
                set()
                if production
                else {"enforcement", "enforcementOwner", "changeReference"}
            ),
            errors=errors,
        )
        origins = _list_of_strings(
            network.get("allowedOrigins"), "$.network.allowedOrigins", errors
        )
        for index, origin in enumerate(origins):
            parts = _origin_parts(origin)
            if parts is None:
                errors.append(
                    f"$.network.allowedOrigins[{index}] must be an explicit HTTP(S) origin without path; only a wildcard port is allowed"
                )
            else:
                origin_parts.append(parts)

        enforcement = network.get("enforcement")
        if enforcement is not None and enforcement not in {
            "egress-proxy",
            "host-firewall",
            "isolated-vdi",
            "loopback-only-demo",
        }:
            errors.append("$.network.enforcement is not a supported enforcement layer")
        if "enforcementOwner" in network:
            _string(network.get("enforcementOwner"), "$.network.enforcementOwner", errors)
        if "changeReference" in network:
            _string(network.get("changeReference"), "$.network.changeReference", errors)

        if environment in {"pilot", "production"}:
            if enforcement == "loopback-only-demo":
                errors.append("loopback-only-demo enforcement is forbidden outside development")
            for index, (scheme, host) in enumerate(origin_parts):
                if scheme != "https" and not _is_loopback(host):
                    errors.append(
                        f"$.network.allowedOrigins[{index}] must use HTTPS outside loopback"
                    )
        if enforcement == "loopback-only-demo" and any(
            not _is_loopback(host) for _, host in origin_parts
        ):
            errors.append("loopback-only-demo may contain only loopback origins")

    if root.get("dataBoundary") is not None:
        data_boundary = _shape(
            root.get("dataBoundary"),
            "$.dataBoundary",
            required={"classification", "modelRoute", "approved", "approvalReference"},
            optional=set(),
            errors=errors,
        )
        if data_boundary.get("classification") not in {
            "public-test",
            "internal",
            "confidential",
            "restricted",
        }:
            errors.append("$.dataBoundary.classification is invalid")
        _string(data_boundary.get("modelRoute"), "$.dataBoundary.modelRoute", errors)
        if data_boundary.get("approved") is not True:
            errors.append("$.dataBoundary.approved must be true before rendering")
        _string(data_boundary.get("approvalReference"), "$.dataBoundary.approvalReference", errors)

    controls = _shape(
        root.get("controls"),
        "$.controls",
        required={
            "transport",
            "humanAuthentication",
            "singleAgentPerProfile",
            "visionFallback",
            "devtools",
            "confirmationActions",
        },
        optional=set(),
        errors=errors,
    )
    if controls.get("transport") != "stdio":
        errors.append("$.controls.transport must equal stdio")
    if controls.get("humanAuthentication") is not True:
        errors.append("$.controls.humanAuthentication must be true")
    if controls.get("singleAgentPerProfile") is not True:
        errors.append("$.controls.singleAgentPerProfile must be true")
    _boolean(controls.get("visionFallback"), "$.controls.visionFallback", errors)
    devtools = _boolean(controls.get("devtools"), "$.controls.devtools", errors)
    confirmations = set(
        _list_of_strings(
            controls.get("confirmationActions"),
            "$.controls.confirmationActions",
            errors,
        )
    )
    unknown_confirmations = confirmations - REQUIRED_CONFIRMATIONS
    if unknown_confirmations:
        errors.append(
            "$.controls.confirmationActions contains unsupported values: "
            + ", ".join(sorted(unknown_confirmations))
        )
    missing_confirmations = REQUIRED_CONFIRMATIONS - confirmations
    if missing_confirmations:
        errors.append(
            "$.controls.confirmationActions is missing: "
            + ", ".join(sorted(missing_confirmations))
        )
    if devtools is True:
        endpoint = _string(
            browser.get("devtoolsEndpoint"), "$.browser.devtoolsEndpoint", errors
        )
        if endpoint and not _loopback_endpoint(endpoint):
            errors.append("$.browser.devtoolsEndpoint must be a loopback HTTP(S) endpoint")
    elif "devtoolsEndpoint" in browser:
        errors.append("$.browser.devtoolsEndpoint requires controls.devtools=true")

    timeouts = _shape(
        root.get("timeouts"),
        "$.timeouts",
        required={"actionMs", "navigationMs", "settleMs"},
        optional=set(),
        errors=errors,
    )
    _integer(timeouts.get("actionMs"), "$.timeouts.actionMs", errors, 1_000, 120_000)
    _integer(
        timeouts.get("navigationMs"),
        "$.timeouts.navigationMs",
        errors,
        5_000,
        300_000,
    )
    _integer(timeouts.get("settleMs"), "$.timeouts.settleMs", errors, 100, 10_000)

    for path, value in _walk_strings(root):
        if "REPLACE-ME" in value.upper():
            errors.append(f"{path} still contains a REPLACE-ME placeholder")

    return sorted(set(errors))


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _write_atomic(path: Path, content: bytes, mode: int = 0o640) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _render_playwright(manifest: dict[str, Any]) -> dict[str, Any]:
    mode = manifest["mode"]
    browser = manifest["browser"]
    controls = manifest["controls"]
    output = manifest["output"]

    config: dict[str, Any] = {
        "capabilities": ["core"] + (["vision"] if controls["visionFallback"] else []),
        "allowUnrestrictedFileAccess": False,
        "saveSession": output["retainSession"],
        "outputDir": output["directory"],
        "outputMaxSize": output["maxBytes"],
        "console": {"level": "warning"},
        "timeouts": {
            "action": manifest["timeouts"]["actionMs"],
            "navigation": manifest["timeouts"]["navigationMs"],
            "settle": manifest["timeouts"]["settleMs"],
        },
        "imageResponses": "allow" if controls["visionFallback"] else "omit",
        "snapshot": {"mode": "full", "boxes": controls["visionFallback"]},
        "codegen": "none",
    }
    if manifest.get("network", {}).get("allowedOrigins"):
        config["network"] = {
            "allowedOrigins": manifest["network"]["allowedOrigins"]
        }

    if mode == "extension":
        config["extension"] = True
    elif mode == "cdp":
        config["browser"] = {
            "browserName": "chromium",
            "cdpEndpoint": browser["cdpEndpoint"],
            "cdpTimeout": min(manifest["timeouts"]["navigationMs"], 60_000),
        }
    else:
        config["browser"] = {
            "browserName": "chromium",
            "userDataDir": browser["userDataDir"],
            "launchOptions": {
                "channel": browser["channel"],
                "headless": False,
            },
        }
    return config


def _join_target_path(manifest: dict[str, Any], root: str, *parts: str) -> str:
    path_type = PureWindowsPath if manifest["target"]["os"] == "windows" else PurePosixPath
    return str(path_type(root).joinpath(*parts))


def _render_mcp(manifest: dict[str, Any]) -> dict[str, Any]:
    windows = manifest["target"]["os"] == "windows"
    if windows:
        playwright_command = manifest["nodeExecutable"]
        playwright_arguments = [
            _join_target_path(
                manifest,
                manifest["installRoot"],
                "node_modules",
                "@playwright",
                "mcp",
                "cli.js",
            )
        ]
        devtools_command = manifest["nodeExecutable"]
        devtools_arguments = [
            _join_target_path(
                manifest,
                manifest["installRoot"],
                "node_modules",
                "chrome-devtools-mcp",
                "build",
                "src",
                "bin",
                "chrome-devtools-mcp.js",
            )
        ]
    else:
        playwright_command = _join_target_path(
            manifest, manifest["installRoot"], "bin", "playwright-mcp"
        )
        playwright_arguments = []
        devtools_command = _join_target_path(
            manifest, manifest["installRoot"], "bin", "chrome-devtools-mcp"
        )
        devtools_arguments = []
    playwright_server: dict[str, Any] = {
        "type": "stdio",
        "command": playwright_command,
        "args": playwright_arguments
        + (
            [f"--browser={manifest['browser']['channel']}"]
            + (
                [f"--executable-path={manifest['browser']['executablePath']}"]
                if windows
                else []
            )
            if manifest["mode"] == "extension"
            else []
        )
        + [
            "--config",
            _join_target_path(
                manifest, manifest["configRoot"], "playwright.config.json"
            ),
        ],
    }
    if windows:
        playwright_server["env"] = _load_windows_mcp_environment()
    servers: dict[str, Any] = {
        "intranet-browser-agent": playwright_server
    }
    if manifest["controls"]["devtools"]:
        servers["chrome-devtools"] = {
            "type": "stdio",
            "command": devtools_command,
            "args": devtools_arguments
            + [
                f"--browser-url={manifest['browser']['devtoolsEndpoint']}",
                "--no-usage-statistics",
                "--no-performance-crux",
                "--redact-network-headers",
            ],
            "env": {
                "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1",
                "CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1",
            },
        }
    return {"mcpServers": servers}


def render(manifest_path: Path, output_dir: Path, force: bool) -> list[Path]:
    manifest = _load_json(manifest_path)
    errors = validate_manifest(manifest)
    if errors:
        raise ManifestError("manifest validation failed:\n- " + "\n- ".join(errors))

    template_path = Path(__file__).resolve().parents[1] / "templates" / "CLAUDE.browser.md"
    generated_names = {
        "playwright.config.json",
        ".mcp.json",
        "deployment.lock.json",
        "CLAUDE.browser.md",
        "DEPLOYMENT.txt",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    collisions = sorted(name for name in generated_names if (output_dir / name).exists())
    if collisions and not force:
        raise ManifestError(
            "refusing to overwrite generated files without --force: " + ", ".join(collisions)
        )

    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    lock = {
        "schemaVersion": 1,
        "generator": {"name": "browser_agent.py", "version": TOOL_VERSION},
        "deploymentId": manifest["deploymentId"],
        "target": manifest["target"],
        "manifestSha256": manifest_digest,
        "runtime": {
            "playwrightMcp": PLAYWRIGHT_MCP_VERSION,
            "chromeDevtoolsMcp": (
                CHROME_DEVTOOLS_MCP_VERSION if manifest["controls"]["devtools"] else None
            ),
            "minimumNode": ".".join(map(str, MIN_NODE_VERSION)),
            "installRoot": manifest["installRoot"],
        },
    }
    boundary_step = (
        "Confirm the declared network enforcement and model-route approvals are active."
        if manifest["environment"] == "production"
        else "Confirm the dedicated browser Profile matches this pilot."
    )
    if manifest.get("mcpScope", "project") == "user":
        integration_steps = """3. Register the generated stdio server with Claude Code user scope.
4. Do not write any project .mcp.json or CLAUDE.md file."""
    else:
        integration_steps = """3. Place .mcp.json in the Claude Code project root.
4. Merge CLAUDE.browser.md into the project's current CLAUDE.md without replacing existing rules."""
    deployment_text = f"""Deployment: {manifest['deploymentId']}
Environment: {manifest['environment']}
Mode: {manifest['mode']}

1. Verify and extract the matching offline runtime at:
   {manifest['installRoot']}
2. Install playwright.config.json at:
   {_join_target_path(manifest, manifest['configRoot'], 'playwright.config.json')}
{integration_steps}
5. {boundary_step}
6. Let a human complete SSO/MFA in the dedicated browser Profile.
7. Run the acceptance cases in docs/acceptance.md before business use.

No extension token or login secret belongs in these generated files.
"""

    payloads = {
        "playwright.config.json": _json_bytes(_render_playwright(manifest)),
        ".mcp.json": _json_bytes(_render_mcp(manifest)),
        "deployment.lock.json": _json_bytes(lock),
        "CLAUDE.browser.md": template_path.read_bytes(),
        "DEPLOYMENT.txt": deployment_text.encode("utf-8"),
    }
    written: list[Path] = []
    for name in sorted(payloads):
        target = output_dir / name
        _write_atomic(target, payloads[name], 0o640)
        written.append(target)
    return written


def _version_tuple(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.match(text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _host_system() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def _host_machine() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    return machine


def _writable_ancestor(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if candidate.exists() and os.access(candidate, os.W_OK):
        return candidate
    return None


def _windows_path_is_within(path_value: str, root_value: str) -> bool:
    """Compare Windows paths case-insensitively without using the host path rules."""
    if not path_value.strip() or not root_value.strip():
        return False
    try:
        path = ntpath.normcase(ntpath.normpath(path_value))
        root = ntpath.normcase(ntpath.normpath(root_value))
        if not ntpath.isabs(path) or not ntpath.isabs(root):
            return False
        return ntpath.commonpath([path, root]) == root
    except ValueError:
        return False


def _resolved_path_is_within(path_value: str, root_value: str) -> bool:
    """Resolve existing links/junctions before checking the current host path."""
    if not path_value.strip() or not root_value.strip():
        return False
    try:
        path = os.path.normcase(os.path.realpath(path_value))
        root = os.path.normcase(os.path.realpath(root_value))
        return os.path.commonpath((path, root)) == root and path != root
    except (OSError, ValueError):
        return False


def _path_has_no_link_below_root(path_value: str, root_value: str) -> bool:
    """Allow a redirected root, but reject links/junctions below that root."""
    if not path_value.strip() or not root_value.strip():
        return False
    try:
        path_absolute = os.path.normcase(os.path.abspath(path_value))
        root_absolute = os.path.normcase(os.path.abspath(root_value))
        if os.path.commonpath((path_absolute, root_absolute)) != root_absolute:
            return False
        root_resolved = os.path.normcase(os.path.realpath(root_value))
        relative = os.path.relpath(path_absolute, root_absolute)
        expected = os.path.normcase(os.path.join(root_resolved, relative))
        actual = os.path.normcase(os.path.realpath(path_value))
        return actual == expected
    except (OSError, ValueError):
        return False


def _find_browser(channel: str) -> str | None:
    candidates: list[str]
    if channel == "msedge":
        candidates = [
            "microsoft-edge",
            "microsoft-edge-stable",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        if os.name == "nt":
            for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                root = os.environ.get(variable)
                if root:
                    candidates.append(
                        str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
                    )
    else:
        candidates = [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        if os.name == "nt":
            for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                root = os.environ.get(variable)
                if root:
                    candidates.append(
                        str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
                    )
    for candidate in candidates:
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def preflight(
    manifest_path: Path,
    runtime_root: Path,
    config_root: Path,
    run_cli_help: bool,
    project_root: Path | None = None,
) -> list[dict[str, str]]:
    manifest = _load_json(manifest_path)
    errors = validate_manifest(manifest)
    checks: list[dict[str, str]] = []
    if errors:
        return [
            {"name": "manifest", "status": "fail", "detail": error} for error in errors
        ]
    checks.append({"name": "manifest", "status": "pass", "detail": "validated"})

    target_system = manifest["target"]["os"]
    target_machine = manifest["target"]["arch"]
    host_system = _host_system()
    host_machine = _host_machine()
    host_matches = target_system == host_system and target_machine == host_machine
    checks.append(
        {
            "name": "host-target",
            "status": "pass" if host_matches else "fail",
            "detail": (
                f"host={host_system}/{host_machine}, target={target_system}/{target_machine}"
            ),
        }
    )
    if not host_matches:
        return checks

    if manifest.get("mcpScope", "project") == "user":
        checks.append(
            {
                "name": "mcp-scope",
                "status": "pass",
                "detail": "Claude Code user scope; no project root is bound at install time",
            }
        )
        if target_system == "windows":
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            scoped_paths = {
                "runtime-root": str(runtime_root),
                "manifest.installRoot": manifest["installRoot"],
                "manifest.nodeExecutable": manifest["nodeExecutable"],
                "config-root": str(config_root),
                "manifest.configRoot": manifest["configRoot"],
                "output.directory": manifest["output"]["directory"],
            }
            profile_path = manifest["browser"].get("userDataDir")
            if profile_path:
                scoped_paths["browser.userDataDir"] = profile_path
            outside = [
                name
                for name, value in scoped_paths.items()
                if not _windows_path_is_within(value, local_app_data)
            ]
            resolved_outside = [
                name
                for name, value in scoped_paths.items()
                if not _resolved_path_is_within(value, local_app_data)
            ]
            linked_paths = [
                name
                for name, value in scoped_paths.items()
                if not _path_has_no_link_below_root(value, local_app_data)
            ]
            checks.append(
                {
                    "name": "user-scope-paths",
                    "status": (
                        "pass"
                        if local_app_data
                        and not outside
                        and not resolved_outside
                        and not linked_paths
                        else "fail"
                    ),
                    "detail": (
                        f"runtime, config, output, and Profile are under {local_app_data}"
                        if local_app_data
                        and not outside
                        and not resolved_outside
                        and not linked_paths
                        else (
                            "LOCALAPPDATA is unavailable"
                            if not local_app_data
                            else (
                                "outside LOCALAPPDATA: " + ", ".join(outside)
                                if outside
                                else (
                                    "link/junction resolves outside LOCALAPPDATA: "
                                    + ", ".join(resolved_outside)
                                    if resolved_outside
                                    else "link/junction below LOCALAPPDATA: "
                                    + ", ".join(linked_paths)
                                )
                            )
                        )
                    ),
                }
            )
    else:
        actual_project_root = (project_root or Path.cwd()).resolve()
        allowed_workspace_roots = {Path(path).resolve() for path in manifest["workspaceRoots"]}
        checks.append(
            {
                "name": "project-root",
                "status": "pass" if actual_project_root in allowed_workspace_roots else "fail",
                "detail": (
                    f"approved workspace root: {actual_project_root}"
                    if actual_project_root in allowed_workspace_roots
                    else f"{actual_project_root} is not one of the declared workspaceRoots"
                ),
            }
        )
        checks.append(
            {
                "name": "project-root-directory",
                "status": "pass" if actual_project_root.is_dir() else "fail",
                "detail": str(actual_project_root),
            }
        )

    if target_system == "windows":
        node_candidates = [Path(manifest["nodeExecutable"])]
    else:
        node_candidates = [
            runtime_root / "node" / "node.exe",
            runtime_root / "node" / "bin" / "node",
        ]
        if os.environ.get("BROWSER_AGENT_NODE"):
            node_candidates.append(Path(os.environ["BROWSER_AGENT_NODE"]))
        path_node = shutil.which("node")
        if path_node:
            node_candidates.append(Path(path_node))
    node_path = next((item for item in node_candidates if os.access(item, os.X_OK)), None)
    if node_path is None:
        missing_node_detail = (
            f"configured Windows Node.js is missing or not executable: {manifest['nodeExecutable']}"
            if target_system == "windows"
            else "Node.js not found; bundle it or set BROWSER_AGENT_NODE"
        )
        checks.append(
            {
                "name": "node",
                "status": "fail",
                "detail": missing_node_detail,
            }
        )
    else:
        try:
            result = subprocess.run(
                [str(node_path), "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = _version_tuple(result.stdout)
            status = "pass" if version and version >= MIN_NODE_VERSION else "fail"
            checks.append(
                {
                    "name": "node",
                    "status": status,
                    "detail": f"{node_path} {result.stdout.strip()} (minimum {'.'.join(map(str, MIN_NODE_VERSION))})",
                }
            )
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append({"name": "node", "status": "fail", "detail": str(exc)})

    package_checks = [
        ("playwright-mcp", runtime_root / "node_modules" / "@playwright" / "mcp" / "package.json", PLAYWRIGHT_MCP_VERSION),
    ]
    if manifest["controls"]["devtools"]:
        package_checks.append(
            (
                "chrome-devtools-mcp",
                runtime_root / "node_modules" / "chrome-devtools-mcp" / "package.json",
                CHROME_DEVTOOLS_MCP_VERSION,
            )
        )
    for name, package_path, expected in package_checks:
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
            actual = package.get("version")
            checks.append(
                {
                    "name": name,
                    "status": "pass" if actual == expected else "fail",
                    "detail": f"installed={actual!r}, expected={expected}",
                }
            )
        except (OSError, json.JSONDecodeError) as exc:
            checks.append({"name": name, "status": "fail", "detail": str(exc)})

    browser_path = _find_browser(manifest["browser"]["channel"])
    checks.append(
        {
            "name": "browser",
            "status": "pass" if browser_path else "fail",
            "detail": browser_path or f"{manifest['browser']['channel']} executable not found",
        }
    )

    for name, raw_path in (
        ("config-directory", str(config_root)),
        ("output-directory", manifest["output"]["directory"]),
    ):
        ancestor = _writable_ancestor(Path(raw_path))
        checks.append(
            {
                "name": name,
                "status": "pass" if ancestor else "fail",
                "detail": f"writable ancestor: {ancestor}" if ancestor else f"no writable ancestor for {raw_path}",
            }
        )

    deployed_config = config_root / "playwright.config.json"
    try:
        actual_config = json.loads(deployed_config.read_text(encoding="utf-8"))
        expected_config = _render_playwright(manifest)
        matches = actual_config == expected_config
        permissions = deployed_config.stat().st_mode & 0o777
        checks.append(
            {
                "name": "playwright-config",
                "status": "pass" if matches else "fail",
                "detail": "matches deployment manifest" if matches else "does not match deployment manifest",
            }
        )
        if target_system == "windows" and manifest.get("mcpScope", "project") == "user":
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            inside_user_root = _windows_path_is_within(
                str(deployed_config), local_app_data
            )
            checks.append(
                {
                    "name": "playwright-config-user-scope",
                    "status": "pass" if inside_user_root else "fail",
                    "detail": (
                        f"under current user's LOCALAPPDATA: {deployed_config}"
                        if inside_user_root
                        else f"not under current user's LOCALAPPDATA: {deployed_config}"
                    ),
                }
            )
        elif target_system == "windows":
            checks.append(
                {
                    "name": "playwright-config-acl",
                    "status": "manual",
                    "detail": "verify NTFS ACL grants only the dedicated account and administrators",
                }
            )
        else:
            checks.append(
                {
                    "name": "playwright-config-permissions",
                    "status": "pass" if permissions & 0o007 == 0 else "fail",
                    "detail": f"mode={permissions:04o}; other-user access must be zero",
                }
            )
    except (OSError, json.JSONDecodeError) as exc:
        checks.append({"name": "playwright-config", "status": "fail", "detail": str(exc)})

    deployed_mcp = config_root / ".mcp.json"
    try:
        actual_mcp = json.loads(deployed_mcp.read_text(encoding="utf-8"))
        expected_mcp = _render_mcp(manifest)
        mcp_matches = actual_mcp == expected_mcp
        checks.append(
            {
                "name": "mcp-config",
                "status": "pass" if mcp_matches else "fail",
                "detail": (
                    "matches direct executable deployment manifest"
                    if mcp_matches
                    else "does not match deployment manifest"
                ),
            }
        )
    except (OSError, json.JSONDecodeError) as exc:
        checks.append({"name": "mcp-config", "status": "fail", "detail": str(exc)})

    if target_system == "windows":
        mcp_command_path = Path(manifest["nodeExecutable"])
        mcp_command_detail = "direct node.exe launch (no .cmd shell shim)"
    else:
        mcp_command_path = runtime_root / "bin" / "playwright-mcp"
        mcp_command_detail = "POSIX runtime wrapper"
    checks.append(
        {
            "name": "mcp-command",
            "status": "pass" if mcp_command_path.is_file() else "fail",
            "detail": f"{mcp_command_path} ({mcp_command_detail})",
        }
    )
    playwright_cli_path = (
        runtime_root / "node_modules" / "@playwright" / "mcp" / "cli.js"
    )
    checks.append(
        {
            "name": "playwright-cli-entry",
            "status": "pass" if playwright_cli_path.is_file() else "fail",
            "detail": str(playwright_cli_path),
        }
    )
    if target_system == "windows":
        expected_node_path = runtime_root / "node" / "node.exe"
        command_matches_runtime = ntpath.normcase(
            ntpath.normpath(str(mcp_command_path))
        ) == ntpath.normcase(ntpath.normpath(str(expected_node_path)))
        checks.append(
            {
                "name": "windows-mcp-node-binding",
                "status": "pass" if command_matches_runtime else "fail",
                "detail": (
                    "MCP command is the verified bundled node.exe"
                    if command_matches_runtime
                    else f"configured={mcp_command_path}, expected={expected_node_path}"
                ),
            }
        )

    if run_cli_help and node_path is not None:
        cli_paths = [
            ("playwright-cli", runtime_root / "node_modules" / "@playwright" / "mcp" / "cli.js")
        ]
        if manifest["controls"]["devtools"]:
            cli_paths.append(
                (
                    "devtools-cli",
                    runtime_root
                    / "node_modules"
                    / "chrome-devtools-mcp"
                    / "build"
                    / "src"
                    / "bin"
                    / "chrome-devtools-mcp.js",
                )
            )
        for name, cli_path in cli_paths:
            try:
                result = subprocess.run(
                    [str(node_path), str(cli_path), "--help"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                checks.append(
                    {
                        "name": name,
                        "status": "pass" if result.returncode == 0 else "fail",
                        "detail": f"exit={result.returncode}",
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                checks.append({"name": name, "status": "fail", "detail": str(exc)})

    if manifest["mode"] == "extension" and target_system == "windows":
        executable_path = Path(manifest["browser"]["executablePath"])
        checks.append(
            {
                "name": "extension-browser-executable",
                "status": "pass" if executable_path.is_file() else "fail",
                "detail": str(executable_path),
            }
        )
        checks.append(
            {
                "name": "extension-config",
                "status": "pass",
                "detail": (
                    f"existing {manifest['browser']['channel']} tabs with per-connection approval; "
                    "target installer verifies the approved extension separately"
                ),
            }
        )
    if manifest["environment"] == "production":
        checks.append(
            {
                "name": "external-boundaries",
                "status": "manual",
                "detail": "verify network enforcement and approved model route against their change records",
            }
        )
    elif manifest["mode"] == "persistent":
        profile_path = manifest["browser"]["userDataDir"]
        checks.append(
            {
                "name": "dedicated-profile",
                "status": "pass",
                "detail": f"dedicated non-default browser Profile configured: {profile_path}",
            }
        )
    elif manifest["mode"] == "extension":
        checks.append(
            {
                "name": "pilot-browser-boundary",
                "status": "pass",
                "detail": "tab access is granted through the Playwright Extension connection dialog",
            }
        )
    else:
        checks.append(
            {
                "name": "external-boundaries",
                "status": "manual",
                "detail": "verify the selected pilot browser connection mode",
            }
        )
    return checks


def _print_validation(errors: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    elif errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
    else:
        print("VALID")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=TOOL_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate a deployment manifest")
    validate_parser.add_argument("--manifest", required=True, type=Path)
    validate_parser.add_argument("--json", action="store_true", dest="as_json")

    render_parser = subparsers.add_parser("render", help="render deployable MCP configuration")
    render_parser.add_argument("--manifest", required=True, type=Path)
    render_parser.add_argument("--out", required=True, type=Path)
    render_parser.add_argument("--force", action="store_true")
    render_parser.add_argument("--json", action="store_true", dest="as_json")

    preflight_parser = subparsers.add_parser("preflight", help="check a staged runtime")
    preflight_parser.add_argument("--manifest", required=True, type=Path)
    preflight_parser.add_argument("--runtime-root", type=Path)
    preflight_parser.add_argument(
        "--config-root",
        type=Path,
        help="staged/deployed config directory; defaults to manifest configRoot",
    )
    preflight_parser.add_argument(
        "--project-root",
        type=Path,
        help="Claude Code project root; defaults to the current directory",
    )
    preflight_parser.add_argument("--skip-cli-help", action="store_true")
    preflight_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            errors = validate_manifest(_load_json(args.manifest))
            _print_validation(errors, args.as_json)
            return 1 if errors else 0
        if args.command == "render":
            written = render(args.manifest, args.out, args.force)
            if args.as_json:
                print(
                    json.dumps(
                        {"ok": True, "files": [str(path) for path in written]},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print("Rendered:")
                for path in written:
                    print(f"- {path}")
            return 0
        if args.command == "preflight":
            manifest = _load_json(args.manifest)
            runtime_root = args.runtime_root or Path(str(manifest.get("installRoot", "")))
            config_root = args.config_root or Path(str(manifest.get("configRoot", "")))
            checks = preflight(
                args.manifest,
                runtime_root,
                config_root,
                not args.skip_cli_help,
                args.project_root,
            )
            failed = any(check["status"] == "fail" for check in checks)
            if args.as_json:
                print(json.dumps({"ok": not failed, "checks": checks}, ensure_ascii=False, indent=2))
            else:
                for check in checks:
                    print(f"[{check['status'].upper():6}] {check['name']}: {check['detail']}")
            return 1 if failed else 0
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
