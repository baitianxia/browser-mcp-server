#!/usr/bin/env python3
"""Perform a browser-free MCP stdio handshake against the fixed Playwright CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


PROTOCOL_VERSION = "2025-03-26"
REQUIRED_TOOLS = {
    "browser_click_and_wait",
    "browser_navigate",
    "browser_read_tooltip",
    "browser_select_custom_option",
    "browser_snapshot",
}
MCP_ENVIRONMENT_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "windows-mcp-environment.json"
)


class SmokeError(RuntimeError):
    pass


def _load_mcp_environment(path: Path = MCP_ENVIRONMENT_PATH) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"cannot load Windows MCP environment policy: {exc}") from exc
    environment = payload.get("environment") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != 1
        or not isinstance(environment, dict)
    ):
        raise SmokeError("Windows MCP environment policy has an invalid shape")
    if not environment or any(
        not isinstance(name, str)
        or not isinstance(value, str)
        or "\x00" in name
        or "\x00" in value
        for name, value in environment.items()
    ):
        raise SmokeError("Windows MCP environment policy contains invalid values")
    return dict(environment)


def smoke(
    node_executable: Path,
    playwright_cli: Path,
    playwright_config: Path,
    *,
    timeout: int = 20,
    mcp_environment: dict[str, str] | None = None,
    browser_channel: str | None = None,
    browser_executable: Path | None = None,
) -> tuple[str, int]:
    for label, path in (
        ("Node.js executable", node_executable),
        ("Playwright CLI", playwright_cli),
        ("Playwright config", playwright_config),
    ):
        if not path.is_file():
            raise SmokeError(f"{label} is not a regular file: {path}")

    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "intranet-release-smoke", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    request = "\n".join(json.dumps(message) for message in messages) + "\n"
    if browser_channel not in {None, "chrome", "msedge"}:
        raise SmokeError(f"unsupported browser channel: {browser_channel}")
    if browser_channel is not None:
        if browser_executable is None or not browser_executable.is_file():
            raise SmokeError(
                "extension-mode browser executable is not a regular file: "
                f"{browser_executable}"
            )
    elif browser_executable is not None:
        raise SmokeError("browser executable requires an extension browser channel")
    command = [str(node_executable), str(playwright_cli)]
    if browser_channel is not None:
        command.append(f"--browser={browser_channel}")
        command.append(f"--executable-path={browser_executable}")
    command.extend(("--config", str(playwright_config)))
    child_environment = dict(os.environ)
    child_environment.update(
        _load_mcp_environment() if mcp_environment is None else mcp_environment
    )
    try:
        result = subprocess.run(
            command,
            input=request,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SmokeError(f"cannot execute Playwright MCP stdio smoke: {exc}") from exc
    if result.returncode != 0:
        details = result.stderr.strip()[-2000:] or result.stdout.strip()[-2000:]
        raise SmokeError(
            f"Playwright MCP exited with code {result.returncode}: {details}"
        )

    responses: list[dict[str, object]] = []
    for line_number, line in enumerate(result.stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SmokeError(
                f"invalid JSON-RPC output on line {line_number}: {line[:500]}"
            ) from exc
        if not isinstance(response, dict):
            raise SmokeError(f"JSON-RPC output on line {line_number} is not an object")
        responses.append(response)

    initialize = next((item for item in responses if item.get("id") == 1), None)
    initialize_result = initialize.get("result") if isinstance(initialize, dict) else None
    if not isinstance(initialize_result, dict):
        raise SmokeError("Playwright MCP did not return an initialize result")
    if initialize_result.get("protocolVersion") != PROTOCOL_VERSION:
        raise SmokeError("Playwright MCP returned an unexpected protocol version")
    server_info = initialize_result.get("serverInfo")
    if not isinstance(server_info, dict) or server_info.get("name") != "Playwright":
        raise SmokeError("Playwright MCP returned unexpected server information")
    server_version = server_info.get("version")
    if not isinstance(server_version, str) or not server_version:
        raise SmokeError("Playwright MCP did not report a server version")

    tools_response = next((item for item in responses if item.get("id") == 2), None)
    tools_result = tools_response.get("result") if isinstance(tools_response, dict) else None
    tools = tools_result.get("tools") if isinstance(tools_result, dict) else None
    if not isinstance(tools, list) or not tools:
        raise SmokeError("Playwright MCP returned no tools")
    tool_names = {
        item.get("name")
        for item in tools
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    missing = sorted(REQUIRED_TOOLS - tool_names)
    if missing:
        raise SmokeError("Playwright MCP is missing required tools: " + ", ".join(missing))
    if len(tool_names) != len(tools):
        raise SmokeError("Playwright MCP returned duplicate or invalid tool definitions")
    return server_version, len(tools)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-executable", required=True, type=Path)
    parser.add_argument("--playwright-cli", required=True, type=Path)
    parser.add_argument("--playwright-config", required=True, type=Path)
    parser.add_argument("--browser-channel", choices=("chrome", "msedge"))
    parser.add_argument("--browser-executable", type=Path)
    args = parser.parse_args()
    try:
        server_version, tool_count = smoke(
            args.node_executable,
            args.playwright_cli,
            args.playwright_config,
            browser_channel=args.browser_channel,
            browser_executable=args.browser_executable,
        )
    except SmokeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"PLAYWRIGHT MCP STDIO SMOKE PASSED: server={server_version}, tools={tool_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
