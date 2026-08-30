#!/usr/bin/env python3
"""Exercise the installed Playwright MCP against an offline authenticated page."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "2025-03-26"
SESSION_COOKIE = "pilot_session=offline-ci-authenticated"
AUTHENTICATED_MARKER = "OFFLINE-INTRANET-SESSION-REUSED"
MISSING_MARKER = "OFFLINE-INTRANET-SESSION-MISSING"


class ExerciseError(RuntimeError):
    pass


def _handler(authenticated_request: threading.Event) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            cookie = self.headers.get("Cookie", "")
            authenticated = any(
                item.strip() == SESSION_COOKIE for item in cookie.split(";")
            )
            marker = AUTHENTICATED_MARKER if authenticated else MISSING_MARKER
            if authenticated:
                authenticated_request.set()
            body = (
                "<!doctype html><html><head><title>Offline intranet session</title>"
                f"</head><body><h1>{marker}</h1></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


class McpProcess:
    def __init__(self, command: list[str], environment: dict[str, str]) -> None:
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )
        except OSError as exc:
            raise ExerciseError(f"cannot start installed Playwright MCP: {exc}") from exc
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=100)
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line_number, line in enumerate(self.process.stdout, 1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ExerciseError(
                        f"invalid MCP JSON on stdout line {line_number}: {line[:300]}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise ExerciseError("MCP stdout message is not a JSON object")
                self._messages.put(payload)
        except BaseException as exc:  # forwarded to the controlling thread
            self._messages.put(exc)
        finally:
            self._messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self._stderr.append(line.rstrip())

    def send(self, message: dict[str, Any]) -> None:
        if self.process.poll() is not None:
            raise ExerciseError(self._exit_details("MCP exited before request"))
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def request(self, request_id: int, method: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExerciseError(
                    self._exit_details(f"timed out waiting for MCP response {request_id}")
                )
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise ExerciseError(
                    self._exit_details(f"timed out waiting for MCP response {request_id}")
                ) from exc
            if message is None:
                raise ExerciseError(self._exit_details("MCP stdout closed unexpectedly"))
            if isinstance(message, BaseException):
                raise ExerciseError(str(message)) from message
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ExerciseError(
                    f"MCP {method} returned an error: {json.dumps(message['error'])}"
                )
            result = message.get("result")
            if not isinstance(result, dict):
                raise ExerciseError(f"MCP {method} returned no result object")
            if result.get("isError") is True:
                raise ExerciseError(
                    f"MCP {method} returned a tool error: "
                    f"{json.dumps(result, ensure_ascii=False)[-4000:]}"
                )
            return result

    def _exit_details(self, prefix: str) -> str:
        stderr = "\n".join(self._stderr)[-4000:]
        suffix = f"; stderr={stderr}" if stderr else ""
        return f"{prefix}; exit={self.process.poll()}{suffix}"

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def _validated_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ExerciseError(f"{label} is not a regular file: {resolved}")
    return resolved


def exercise(args: argparse.Namespace) -> tuple[str, int]:
    node = _validated_file(args.node_executable, "Node.js executable")
    cli = _validated_file(args.playwright_cli, "Playwright CLI")
    config = _validated_file(args.playwright_config, "Playwright config")
    policy_file = _validated_file(args.environment_policy, "MCP environment policy")
    token_file = _validated_file(args.token_file, "disposable extension token")
    browser_executable = _validated_file(args.browser_executable, "browser executable")
    profile = args.profile.resolve()
    if not profile.is_dir():
        raise ExerciseError(f"Chrome user data directory is missing: {profile}")
    try:
        token = token_file.read_text(encoding="utf-8").strip()
        policy_payload = json.loads(policy_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExerciseError(f"cannot read CI extension inputs: {exc}") from exc
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ExerciseError("disposable extension token has an invalid shape")
    policy = policy_payload.get("environment") if isinstance(policy_payload, dict) else None
    if not isinstance(policy, dict) or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in policy.items()
    ):
        raise ExerciseError("MCP environment policy has an invalid shape")

    authenticated_request = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(authenticated_request))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    port = server.server_address[1]
    environment = dict(os.environ)
    environment.update(policy)
    # Keep relay diagnostics available when CI fails before the extension
    # connection is established. The relay logger does not print auth tokens.
    environment["DEBUG"] = "pw:mcp:relay"
    environment["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = token
    environment["PWTEST_EXTENSION_USER_DATA_DIR"] = str(profile)
    mcp: McpProcess | None = None
    try:
        mcp = McpProcess(
            [
                str(node),
                str(cli),
                f"--browser={args.browser_channel}",
                f"--executable-path={browser_executable}",
                "--config",
                str(config),
            ],
            environment,
        )
        initialized = mcp.request(
            1,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "windows-extension-e2e", "version": "1"},
            },
            30,
        )
        if initialized.get("protocolVersion") != PROTOCOL_VERSION:
            raise ExerciseError("installed MCP returned an unexpected protocol version")
        mcp.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        listed = mcp.request(2, "tools/list", {}, 30)
        tools = listed.get("tools")
        if not isinstance(tools, list):
            raise ExerciseError("installed MCP returned no tool list")
        names = {
            item.get("name")
            for item in tools
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        required = {"browser_navigate", "browser_snapshot"}
        if not required.issubset(names):
            raise ExerciseError("installed MCP is missing extension exercise tools")
        url = f"http://127.0.0.1:{port}/authenticated-intranet-page"
        mcp.request(
            3,
            "tools/call",
            {"name": "browser_navigate", "arguments": {"url": url}},
            args.tool_timeout,
        )
        snapshot = mcp.request(
            4,
            "tools/call",
            {"name": "browser_snapshot", "arguments": {}},
            60,
        )
        rendered = json.dumps(snapshot, ensure_ascii=False)
        if AUTHENTICATED_MARKER not in rendered or MISSING_MARKER in rendered:
            raise ExerciseError(
                "MCP snapshot did not contain the authenticated marker; "
                f"authenticated_request={authenticated_request.is_set()}; "
                f"snapshot={rendered[-2000:]}"
            )
        if not authenticated_request.wait(timeout=2):
            raise ExerciseError("offline test server did not receive the persisted session cookie")
        server_info = initialized.get("serverInfo")
        version = (
            server_info.get("version", "unknown")
            if isinstance(server_info, dict)
            else "unknown"
        )
        return str(version), len(names)
    finally:
        if mcp is not None:
            mcp.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-executable", required=True, type=Path)
    parser.add_argument("--playwright-cli", required=True, type=Path)
    parser.add_argument("--playwright-config", required=True, type=Path)
    parser.add_argument("--environment-policy", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--browser-channel", required=True, choices=("chrome", "msedge"))
    parser.add_argument("--browser-executable", required=True, type=Path)
    parser.add_argument("--tool-timeout", type=int, default=90)
    args = parser.parse_args()
    if args.tool_timeout < 5:
        parser.error("--tool-timeout must be at least 5 seconds")
    try:
        version, tool_count = exercise(args)
    except ExerciseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        "PLAYWRIGHT EXTENSION MCP E2E PASSED: "
        f"server={version}, tools={tool_count}, session_cookie=reused"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
