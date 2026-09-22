from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "standards"))

from mcp_standards import claude_code  # noqa: E402
from mcp_standards.claude_code import (  # noqa: E402
    ClaudeInvocation,
    ProbeResult,
    discover_claude,
)
from mcp_standards.cli import main as cli_main  # noqa: E402
from mcp_standards.registration import (  # noqa: E402
    RegistrationError,
    is_missing_user_entry,
    register_user_mcp,
    unregister_user_mcp,
)
from mcp_standards.stdio import smoke_stdio  # noqa: E402


class McpStandardsTests(unittest.TestCase):
    @staticmethod
    def _write_fake_pe(path: Path, machine: int = 0x8664) -> None:
        payload = bytearray(128)
        payload[0:2] = b"MZ"
        payload[0x3C:0x40] = (64).to_bytes(4, "little")
        payload[64:68] = b"PE\0\0"
        payload[68:70] = machine.to_bytes(2, "little")
        path.write_bytes(payload)

    def test_missing_user_entry_requires_exact_name_and_scope(self) -> None:
        cases = (
            ("No user-scoped MCP server found with name: browser-mcp", True),
            ("No MCP server found with name:    browser-mcp", True),
            ('No MCP server named "browser-mcp" in user scope', True),
            ('No MCP server named "other-mcp" in user scope', False),
            ('No MCP server named "browser-mcp" in project scope', False),
            ("No user-scoped MCP server found with name: browser-mcp-extra", False),
            ('No MCP server named "browser-mcp" in user scope; permission denied', False),
            ("permission denied", False),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                result = ProbeResult(("mcp", "remove"), 1, "", message)
                self.assertEqual(expected, is_missing_user_entry(result, "browser-mcp"))

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_discovery_explicit_path_probes_version_and_mcp_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / "claude"
            fake.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import sys
                    if sys.argv[1:] == ["--version"]:
                        print("2.1.99")
                    elif sys.argv[1:] == ["mcp", "--help"]:
                        print("mcp help")
                    else:
                        raise SystemExit(2)
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            result = discover_claude(
                explicit_path=fake,
                environment={**os.environ, "PATH": os.environ.get("PATH", "")},
                platform_name="linux",
            )
            self.assertIsNotNone(result.selected)
            assert result.selected is not None
            self.assertEqual("path-native", result.selected.kind)
            self.assertTrue(result.selected.version.succeeded)
            self.assertTrue(result.selected.capability.succeeded)
            self.assertEqual("accepted", result.diagnostics[-1].reason)

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_explicit_invalid_path_does_not_fall_back_to_path_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "claude"
            valid.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            valid.chmod(valid.stat().st_mode | stat.S_IXUSR)
            result = discover_claude(
                explicit_path=root / "missing-claude",
                environment={"PATH": str(root)},
                platform_name="linux",
                probe=False,
            )
            self.assertIsNone(result.selected)
            self.assertEqual(["not_found"], [item.reason for item in result.diagnostics])

    def test_windows_native_and_npm_javascript_bins_are_separated_without_running_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "native.exe"
            self._write_fake_pe(native)
            with (
                mock.patch.object(claude_code, "_final_path", side_effect=lambda path, _platform: path),
                mock.patch.object(claude_code, "_resolve_external_file", side_effect=lambda path, _platform: path),
            ):
                native_result = discover_claude(
                    explicit_path=native,
                    environment={},
                    platform_name="win32",
                    probe=False,
                )
            self.assertIsNotNone(native_result.selected)
            assert native_result.selected is not None
            self.assertEqual("native", native_result.selected.kind)
            self.assertEqual((), native_result.selected.prefix)

            command = root / "claude.cmd"
            command.write_text("@echo off\n", encoding="utf-8")
            node = root / "node.exe"
            self._write_fake_pe(node)
            package = root / "node_modules" / "@anthropic-ai" / "claude-code"
            (package / "bin").mkdir(parents=True)
            (package / "package.json").write_text(
                '{"name":"@anthropic-ai/claude-code","bin":{"claude":"bin/cli.js"}}',
                encoding="utf-8",
            )
            (package / "bin" / "cli.js").write_text("console.log('fixture');\n", encoding="utf-8")
            with (
                mock.patch.object(claude_code, "_final_path", side_effect=lambda path, _platform: path),
                mock.patch.object(claude_code, "_resolve_external_file", side_effect=lambda path, _platform: path),
            ):
                npm_result = discover_claude(
                    explicit_path=command,
                    environment={"PATH": ""},
                    platform_name="win32",
                    probe=False,
                )
            self.assertIsNotNone(npm_result.selected)
            assert npm_result.selected is not None
            self.assertEqual("npm-js", npm_result.selected.kind)
            self.assertEqual((str(package / "bin" / "cli.js"),), npm_result.selected.prefix)
            self.assertEqual(node, npm_result.selected.executable)

    def test_windows_apps_candidate_is_rejected_before_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "Microsoft" / "WindowsApps" / "claude.exe"
            candidate.parent.mkdir(parents=True)
            self._write_fake_pe(candidate)
            result = discover_claude(
                explicit_path=candidate,
                environment={},
                platform_name="win32",
                probe=False,
            )
            self.assertIsNone(result.selected)
            self.assertEqual("path_rejected_windowsapps", result.diagnostics[0].reason)

    def test_npm_package_identity_and_bin_boundary_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = root / "claude.cmd"
            command.write_text("@echo off\n", encoding="utf-8")
            package = root / "node_modules" / "@anthropic-ai" / "claude-code"
            package.mkdir(parents=True)
            package_json = package / "package.json"
            package_json.write_text(
                '{"name":"wrong-package","bin":"bin/cli.js"}', encoding="utf-8"
            )
            with (
                mock.patch.object(claude_code, "_final_path", side_effect=lambda path, _platform: path),
                mock.patch.object(claude_code, "_resolve_external_file", side_effect=lambda path, _platform: path),
            ):
                wrong_identity = discover_claude(
                    explicit_path=command,
                    environment={},
                    platform_name="win32",
                    probe=False,
                )
            self.assertEqual("wrong_identity", wrong_identity.diagnostics[0].reason)

            package_json.write_text(
                '{"name":"@anthropic-ai/claude-code","bin":"../outside.js"}',
                encoding="utf-8",
            )
            with (
                mock.patch.object(claude_code, "_final_path", side_effect=lambda path, _platform: path),
                mock.patch.object(claude_code, "_resolve_external_file", side_effect=lambda path, _platform: path),
            ):
                escaped_bin = discover_claude(
                    explicit_path=command,
                    environment={},
                    platform_name="win32",
                    probe=False,
                )
            self.assertEqual("invalid_bin", escaped_bin.diagnostics[0].reason)

    def test_registration_restores_original_bytes_when_add_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_config = root / "claude.json"
            backup = root / "backup.json"
            original = b'{"keep":true}\r\n'
            user_config.write_bytes(original)
            invocation = ClaudeInvocation(root / "claude.exe", root / "claude.exe", (), "native", "test", root / "claude.exe")
            calls: list[tuple[str, ...]] = []

            def fake_invoke(_invocation: ClaudeInvocation, arguments: tuple[str, ...], **_kwargs: object) -> ProbeResult:
                calls.append(arguments)
                if arguments[1] == "remove":
                    return ProbeResult(arguments, 1, "", "No MCP server named \"browser-mcp\" in user scope")
                return ProbeResult(arguments, 7, "", "add failed")

            with mock.patch("mcp_standards.registration.invoke_claude", side_effect=fake_invoke):
                with self.assertRaises(RegistrationError):
                    register_user_mcp(
                        invocation,
                        server_name="browser-mcp",
                        user_config=user_config,
                        backup=backup,
                        entry={"command": "node.exe", "args": ["server.js"], "env": {}},
                        reporter=lambda _message: None,
                    )
            self.assertEqual(original, user_config.read_bytes())
            self.assertEqual(original, backup.read_bytes())
            self.assertEqual("remove", calls[0][1])
            self.assertEqual("add", calls[1][1])

    def test_registration_success_verifies_config_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_config = root / "claude.json"
            backup = root / "backup.json"
            invocation = ClaudeInvocation(root / "claude.exe", root / "claude.exe", (), "native", "test", root / "claude.exe")
            entry = {"command": "node.exe", "args": ["server.js"], "env": {"Z": "2", "A": "1"}}
            calls: list[tuple[str, ...]] = []

            def fake_invoke(_invocation: ClaudeInvocation, arguments: tuple[str, ...], **_kwargs: object) -> ProbeResult:
                calls.append(arguments)
                if arguments[1] == "add":
                    user_config.write_text(
                        json.dumps({
                            "mcpServers": {
                                "browser-mcp": {
                                    "command": "node.exe",
                                    "args": ["server.js"],
                                    "env": {"A": "1", "Z": "2"},
                                }
                            }
                        }),
                        encoding="utf-8",
                    )
                    return ProbeResult(arguments, 0, "", "warning")
                if arguments[1] == "remove":
                    return ProbeResult(arguments, 1, "", "No MCP server named \"browser-mcp\" in user scope")
                return ProbeResult(arguments, 0, "browser-mcp", "")

            with mock.patch("mcp_standards.registration.invoke_claude", side_effect=fake_invoke):
                register_user_mcp(
                    invocation,
                    server_name="browser-mcp",
                    user_config=user_config,
                    backup=backup,
                    entry=entry,
                    reporter=lambda _message: None,
                )
            self.assertFalse(backup.exists())
            self.assertEqual(["remove", "add", "get"], [call[1] for call in calls])
            self.assertEqual(("--env", "A=1", "--env", "Z=2"), calls[1][6:10])

    def test_unregistration_requires_matching_project_entry_before_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_config = root / "claude.json"
            backup = root / "backup.json"
            user_config.write_text(json.dumps({"mcpServers": {"browser-mcp": {"command": "other.exe"}}}), encoding="utf-8")
            invocation = ClaudeInvocation(root / "claude.exe", root / "claude.exe", (), "native", "test", root / "claude.exe")
            with mock.patch("mcp_standards.registration.invoke_claude") as invoke:
                with self.assertRaises(RegistrationError):
                    unregister_user_mcp(
                        invocation,
                        server_name="browser-mcp",
                        user_config=user_config,
                        backup=backup,
                        expected_entry={"command": "node.exe", "args": [], "env": {}},
                        reporter=lambda _message: None,
                    )
                invoke.assert_not_called()
            self.assertEqual(json.dumps({"mcpServers": {"browser-mcp": {"command": "other.exe"}}}), user_config.read_text(encoding="utf-8"))

    def test_unregistration_removes_matching_entry_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_config = root / "claude.json"
            backup = root / "backup.json"
            entry = {"command": "node.exe", "args": ["server.js"], "env": {}}
            user_config.write_text(json.dumps({"mcpServers": {"browser-mcp": entry}, "keep": True}), encoding="utf-8")
            original = user_config.read_bytes()
            invocation = ClaudeInvocation(root / "claude.exe", root / "claude.exe", (), "native", "test", root / "claude.exe")

            def fake_invoke(_invocation: ClaudeInvocation, arguments: tuple[str, ...], **_kwargs: object) -> ProbeResult:
                self.assertEqual(("mcp", "remove", "browser-mcp", "--scope", "user"), arguments)
                user_config.write_text(json.dumps({"keep": True}), encoding="utf-8")
                return ProbeResult(arguments, 0, "", "")

            with mock.patch("mcp_standards.registration.invoke_claude", side_effect=fake_invoke):
                unregister_user_mcp(
                    invocation,
                    server_name="browser-mcp",
                    user_config=user_config,
                    backup=backup,
                    expected_entry=entry,
                    reporter=lambda _message: None,
                )
            self.assertEqual(original, backup.read_bytes())
            self.assertEqual({"keep": True}, json.loads(user_config.read_text(encoding="utf-8")))

    def test_stdio_smoke_and_cli_wrapper_validate_identity_and_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = root / "server.py"
            server.write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys
                    for line in sys.stdin:
                        request = json.loads(line)
                        if request.get("method") == "initialize":
                            print(json.dumps({
                                "jsonrpc": "2.0", "id": request["id"],
                                "result": {
                                    "protocolVersion": "2025-11-25",
                                    "serverInfo": {"name": "fixture-mcp", "version": "1.2.3"},
                                    "capabilities": {"tools": {}},
                                },
                            }), flush=True)
                        elif request.get("method") == "tools/list":
                            print(json.dumps({
                                "jsonrpc": "2.0", "id": request["id"],
                                "result": {"tools": [{"name": "hello", "description": "test"}]},
                            }), flush=True)
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            command = [sys.executable, str(server)]
            report = smoke_stdio(command, expected_server_name="fixture-mcp", required_tools=("hello",))
            self.assertEqual("2025-11-25", report.protocol_version)
            self.assertEqual(("hello",), report.tool_names)
            self.assertEqual(0, cli_main([
                "smoke-stdio",
                "--expected-server-name",
                "fixture-mcp",
                "--required-tool",
                "hello",
                "--",
                *command,
            ]))


if __name__ == "__main__":
    unittest.main()
