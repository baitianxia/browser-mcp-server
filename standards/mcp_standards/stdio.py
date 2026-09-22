"""Bounded MCP stdio handshake and tools/list smoke test."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Sequence


class SmokeError(RuntimeError):
    """Raised when a server violates the stdio or MCP handshake contract."""


@dataclass(frozen=True)
class SmokeReport:
    protocol_version: str
    server_name: str
    server_version: str
    tool_names: tuple[str, ...]
    stderr: str


DEFAULT_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)


def _reader(stream: object, channel: str, events: queue.Queue[tuple[str, bytes | None]]) -> None:
    try:
        for line in stream:  # type: ignore[operator]
            events.put((channel, line))
    finally:
        events.put((f"{channel}-eof", None))


def _send(process: subprocess.Popen[bytes], message: Mapping[str, object]) -> None:
    if process.stdin is None:
        raise SmokeError("MCP server stdin is unavailable")
    try:
        process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise SmokeError(f"MCP server stdin closed unexpectedly: {exc}") from exc


def smoke_stdio(
    command: Sequence[str],
    *,
    expected_server_name: str,
    required_tools: Sequence[str] = (),
    protocol_versions: Sequence[str] = DEFAULT_PROTOCOL_VERSIONS,
    environment: Mapping[str, str] | None = None,
    timeout: float = 20.0,
) -> SmokeReport:
    if not command or any(not isinstance(value, str) or not value for value in command):
        raise SmokeError("stdio command must be a non-empty string sequence")
    if not expected_server_name:
        raise SmokeError("expected MCP server name is required")
    if not protocol_versions or any(not isinstance(version, str) or not version for version in protocol_versions):
        raise SmokeError("at least one MCP protocol version is required")
    if any(not isinstance(name, str) or not name for name in required_tools):
        raise SmokeError("required MCP tool names must be non-empty strings")
    child_env = dict(os.environ)
    if environment:
        child_env.update(environment)
    try:
        process = subprocess.Popen(
            list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=child_env, shell=False,
        )
    except OSError as exc:
        raise SmokeError(f"cannot start MCP server: {exc}") from exc

    events: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
    stdout_thread = threading.Thread(target=_reader, args=(process.stdout, "stdout", events), daemon=True)
    stderr_thread = threading.Thread(target=_reader, args=(process.stderr, "stderr", events), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    stderr_parts: list[str] = []
    responses: dict[int, dict[str, object]] = {}
    deadline = time.monotonic() + timeout
    try:
        requested_version = protocol_versions[0]
        _send(process, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": requested_version,
                "capabilities": {},
                "clientInfo": {"name": "mcp-standards-smoke", "version": "1"},
            },
        })
        sent_followup = False
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                channel, raw = events.get(timeout=remaining)
            except queue.Empty:
                raise SmokeError("MCP stdio handshake timed out")
            if channel == "stderr":
                if raw:
                    stderr_parts.append(raw.decode("utf-8", errors="replace"))
                continue
            if channel == "stderr-eof":
                continue
            if channel == "stdout-eof":
                if 1 not in responses or 2 not in responses:
                    raise SmokeError("MCP server closed stdout before completing the smoke")
                continue
            if not raw or not raw.strip():
                continue
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SmokeError("MCP stdout contained non-UTF-8 or invalid JSON") from exc
            if not isinstance(message, dict):
                raise SmokeError("MCP stdout message is not a JSON object")
            if message.get("jsonrpc") != "2.0":
                raise SmokeError("MCP stdout message is not JSON-RPC 2.0")
            message_id = message.get("id")
            if isinstance(message_id, int) and not isinstance(message_id, bool):
                responses[message_id] = message
            if message_id == 1 and not sent_followup:
                result = message.get("result")
                if not isinstance(result, dict):
                    raise SmokeError("MCP initialize returned an error")
                actual_version = result.get("protocolVersion")
                if actual_version not in protocol_versions:
                    raise SmokeError(f"unsupported MCP protocol version returned: {actual_version!r}")
                _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
                _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                sent_followup = True
            if 1 in responses and 2 in responses:
                break

        initialize = responses.get(1, {}).get("result")
        if not isinstance(initialize, dict):
            raise SmokeError("MCP initialize response is missing result")
        server_info = initialize.get("serverInfo")
        if not isinstance(server_info, dict) or server_info.get("name") != expected_server_name:
            raise SmokeError(f"MCP server identity mismatch; expected {expected_server_name!r}")
        server_version = server_info.get("version")
        if not isinstance(server_version, str) or not server_version:
            raise SmokeError("MCP server did not report a version")
        tools_result = responses.get(2, {}).get("result")
        tools = tools_result.get("tools") if isinstance(tools_result, dict) else None
        if not isinstance(tools, list):
            raise SmokeError("MCP tools/list response is missing tools")
        tool_names = tuple(sorted(tool.get("name") for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)))
        if len(tool_names) != len(tools) or len(set(tool_names)) != len(tool_names):
            raise SmokeError("MCP tools/list contains invalid or duplicate tool names")
        missing = sorted(set(required_tools) - set(tool_names))
        if missing:
            raise SmokeError("MCP server is missing required tools: " + ", ".join(missing))
        return SmokeReport(
            protocol_version=str(initialize.get("protocolVersion")),
            server_name=expected_server_name,
            server_version=server_version,
            tool_names=tool_names,
            stderr="".join(stderr_parts)[-4000:],
        )
    finally:
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
