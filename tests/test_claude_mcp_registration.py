from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "register_claude_user_mcp",
    ROOT / "scripts" / "register_claude_user_mcp.py",
)
assert SPEC and SPEC.loader
registration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registration)


class ClaudeMcpRegistrationTests(unittest.TestCase):
    def test_extension_registration_arguments_bind_exact_browser_channel(self) -> None:
        cli = Path(r"C:\Agent\cli.js")
        config = Path(r"C:\Agent\playwright.config.json")
        self.assertEqual(
            [
                str(cli),
                "--browser=chrome",
                "--config",
                str(config),
            ],
            registration._playwright_command_arguments(cli, config, "chrome"),
        )
        with self.assertRaises(registration.RegistrationError):
            registration._playwright_command_arguments(cli, config, "firefox")

    def paths(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        node_executable = root / "node.exe"
        node_executable.write_bytes(b"MZ")
        playwright_cli = root / "cli.js"
        playwright_cli.write_bytes(b"// fixed CLI\n")
        playwright_config = root / "playwright.config.json"
        playwright_config.write_bytes(b"{}\n")
        return (
            node_executable,
            playwright_cli,
            playwright_config,
            root / ".claude.json",
            root / "backup.json",
        )

    def write_registration(
        self,
        user_config: Path,
        node_executable: Path,
        playwright_cli: Path,
        playwright_config: Path,
    ) -> None:
        payload = (
            json.loads(user_config.read_text(encoding="utf-8"))
            if user_config.exists()
            else {}
        )
        payload.setdefault("mcpServers", {})["intranet-browser-agent"] = {
            "type": "stdio",
            "command": str(node_executable),
            "args": [str(playwright_cli), "--config", str(playwright_config)],
            "env": registration.load_mcp_environment(),
        }
        user_config.write_bytes(
            (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        )

    def test_uses_exact_user_scope_remove_add_get_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )
            original = b'{"keep":true}\r\n'
            user_config.write_bytes(original)
            calls: list[tuple[str, ...]] = []

            def runner(_executable, _prefix, arguments, **_kwargs):
                calls.append(tuple(arguments))
                if arguments[1] == "add":
                    self.write_registration(
                        user_config,
                        node_executable,
                        playwright_cli,
                        playwright_config,
                    )
                return subprocess.CompletedProcess(arguments, 0, "", "warning\n")

            with mock.patch.object(registration, "_run_claude", side_effect=runner):
                registration.register_user_mcp(
                    claude_executable="claude.exe",
                    claude_prefix=(),
                    server_name="intranet-browser-agent",
                    node_executable=node_executable,
                    playwright_cli=playwright_cli,
                    playwright_config=playwright_config,
                    user_config=user_config,
                    backup=backup,
                    reporter=lambda _message: None,
                )

            self.assertEqual(
                [
                    ("mcp", "remove", "intranet-browser-agent", "--scope", "user"),
                    (
                        "mcp",
                        "add",
                        "--transport",
                        "stdio",
                        "--scope",
                        "user",
                        "intranet-browser-agent",
                        *registration._mcp_environment_arguments(
                            registration.load_mcp_environment()
                        ),
                        "--",
                        str(node_executable),
                        str(playwright_cli),
                        "--config",
                        str(playwright_config),
                    ),
                    ("mcp", "get", "intranet-browser-agent"),
                ],
                calls,
            )
            self.assertEqual(original, backup.read_bytes())

    def test_missing_old_entry_and_success_stderr_are_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )
            results = iter(
                (
                    subprocess.CompletedProcess(
                        [],
                        1,
                        "",
                        "No user-scoped MCP server found with name: intranet-browser-agent",
                    ),
                    subprocess.CompletedProcess([], 0, "", "non-fatal warning"),
                    subprocess.CompletedProcess([], 0, "server", ""),
                )
            )
            def runner(_executable, _prefix, arguments, **_kwargs):
                result = next(results)
                if arguments[1] == "add" and result.returncode == 0:
                    self.write_registration(
                        user_config,
                        node_executable,
                        playwright_cli,
                        playwright_config,
                    )
                return result

            with mock.patch.object(registration, "_run_claude", side_effect=runner):
                registration.register_user_mcp(
                    claude_executable="claude.exe",
                    claude_prefix=(),
                    server_name="intranet-browser-agent",
                    node_executable=node_executable,
                    playwright_cli=playwright_cli,
                    playwright_config=playwright_config,
                    user_config=user_config,
                    backup=backup,
                    reporter=lambda _message: None,
                )
            self.assertFalse(backup.exists())

    def test_unexpected_remove_failure_stops_and_restores_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )
            original = b'{"keep":"before-remove"}\r\n'
            user_config.write_bytes(original)

            def runner(_executable, _prefix, arguments, **_kwargs):
                self.assertEqual("remove", arguments[1])
                user_config.write_bytes(b'{"partiallyChanged":true}\n')
                return subprocess.CompletedProcess(
                    arguments, 5, "", "permission denied"
                )

            with mock.patch.object(registration, "_run_claude", side_effect=runner):
                with self.assertRaisesRegex(
                    registration.RegistrationError,
                    "claude mcp remove exited with code 5",
                ):
                    registration.register_user_mcp(
                        claude_executable="claude.exe",
                        claude_prefix=(),
                        server_name="intranet-browser-agent",
                        node_executable=node_executable,
                        playwright_cli=playwright_cli,
                        playwright_config=playwright_config,
                        user_config=user_config,
                        backup=backup,
                        reporter=lambda _message: None,
                    )
            self.assertEqual(original, user_config.read_bytes())
            self.assertEqual(original, backup.read_bytes())

    def test_initial_backup_preserves_lf_and_crlf_bytes(self) -> None:
        for original in (b'{"lineEnding":"lf"}\n', b'{"lineEnding":"crlf"}\r\n'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (
                    node_executable,
                    playwright_cli,
                    playwright_config,
                    user_config,
                    backup,
                ) = self.paths(root)
                user_config.write_bytes(original)

                def runner(_executable, _prefix, arguments, **_kwargs):
                    if arguments[1] == "add":
                        self.write_registration(
                            user_config,
                            node_executable,
                            playwright_cli,
                            playwright_config,
                        )
                    return subprocess.CompletedProcess(arguments, 0, "", "")

                with mock.patch.object(
                    registration, "_run_claude", side_effect=runner
                ):
                    registration.register_user_mcp(
                        claude_executable="claude.exe",
                        claude_prefix=(),
                        server_name="intranet-browser-agent",
                        node_executable=node_executable,
                        playwright_cli=playwright_cli,
                        playwright_config=playwright_config,
                        user_config=user_config,
                        backup=backup,
                        reporter=lambda _message: None,
                    )

                self.assertEqual(original, backup.read_bytes())

    def test_add_failure_restores_existing_config_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )
            original = b'{"mcpServers":{"intranet-browser-agent":{"command":"old"}}}\r\n'
            user_config.write_bytes(original)

            def runner(_executable, _prefix, arguments, **_kwargs):
                if arguments[1] == "remove":
                    user_config.write_bytes(b"{}\n")
                    return subprocess.CompletedProcess(arguments, 0, "", "")
                return subprocess.CompletedProcess(arguments, 7, "", "add failed")

            with mock.patch.object(registration, "_run_claude", side_effect=runner):
                with self.assertRaises(registration.RegistrationError):
                    registration.register_user_mcp(
                        claude_executable="claude.exe",
                        claude_prefix=(),
                        server_name="intranet-browser-agent",
                        node_executable=node_executable,
                        playwright_cli=playwright_cli,
                        playwright_config=playwright_config,
                        user_config=user_config,
                        backup=backup,
                        reporter=lambda _message: None,
                    )
            self.assertEqual(original, user_config.read_bytes())
            self.assertEqual(original, backup.read_bytes())

    def test_add_success_with_wrong_user_entry_restores_original_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )
            original = b'{"keep":"original"}\r\n'
            user_config.write_bytes(original)

            def runner(_executable, _prefix, arguments, **_kwargs):
                if arguments[1] == "remove":
                    user_config.write_bytes(b"{}\n")
                    return subprocess.CompletedProcess(arguments, 0, "", "")
                if arguments[1] == "add":
                    user_config.write_bytes(
                        b'{"mcpServers":{"intranet-browser-agent":{"type":"stdio","command":"wrong.exe","args":[]}}}\n'
                    )
                    return subprocess.CompletedProcess(arguments, 0, "", "")
                self.fail("get must not run after an incorrect user entry")

            with mock.patch.object(registration, "_run_claude", side_effect=runner):
                with self.assertRaisesRegex(
                    registration.RegistrationError,
                    "does not match the verified node.exe/CLI/config paths",
                ):
                    registration.register_user_mcp(
                        claude_executable="claude.exe",
                        claude_prefix=(),
                        server_name="intranet-browser-agent",
                        node_executable=node_executable,
                        playwright_cli=playwright_cli,
                        playwright_config=playwright_config,
                        user_config=user_config,
                        backup=backup,
                        reporter=lambda _message: None,
                    )
            self.assertEqual(original, user_config.read_bytes())
            self.assertEqual(original, backup.read_bytes())

    def test_add_success_with_injected_environment_restores_original_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )
            original = b'{"keep":"original"}\n'
            user_config.write_bytes(original)

            def runner(_executable, _prefix, arguments, **_kwargs):
                if arguments[1] == "remove":
                    user_config.write_bytes(b"{}\n")
                    return subprocess.CompletedProcess(arguments, 0, "", "")
                if arguments[1] == "add":
                    self.write_registration(
                        user_config,
                        node_executable,
                        playwright_cli,
                        playwright_config,
                    )
                    payload = json.loads(user_config.read_text(encoding="utf-8"))
                    payload["mcpServers"]["intranet-browser-agent"]["env"] = {
                        "NODE_OPTIONS": "--require unexpected.js"
                    }
                    user_config.write_bytes(json.dumps(payload).encode("utf-8"))
                    return subprocess.CompletedProcess(arguments, 0, "", "")
                self.fail("get must not run after an injected environment")

            with mock.patch.object(registration, "_run_claude", side_effect=runner):
                with self.assertRaisesRegex(
                    registration.RegistrationError, "exact hardened environment"
                ):
                    registration.register_user_mcp(
                        claude_executable="claude.exe",
                        claude_prefix=(),
                        server_name="intranet-browser-agent",
                        node_executable=node_executable,
                        playwright_cli=playwright_cli,
                        playwright_config=playwright_config,
                        user_config=user_config,
                        backup=backup,
                        reporter=lambda _message: None,
                    )
            self.assertEqual(original, user_config.read_bytes())
            self.assertEqual(original, backup.read_bytes())

    def test_environment_policy_covers_all_pinned_playwright_overrides(self) -> None:
        expected_playwright_names = {
            "PLAYWRIGHT_MCP_ALLOWED_HOSTS",
            "PLAYWRIGHT_MCP_ALLOWED_ORIGINS",
            "PLAYWRIGHT_MCP_ALLOW_UNRESTRICTED_FILE_ACCESS",
            "PLAYWRIGHT_MCP_BLOCKED_ORIGINS",
            "PLAYWRIGHT_MCP_BLOCK_SERVICE_WORKERS",
            "PLAYWRIGHT_MCP_BROWSER",
            "PLAYWRIGHT_MCP_CAPS",
            "PLAYWRIGHT_MCP_CDP_ENDPOINT",
            "PLAYWRIGHT_MCP_CDP_HEADERS",
            "PLAYWRIGHT_MCP_CDP_TIMEOUT",
            "PLAYWRIGHT_MCP_CODEGEN",
            "PLAYWRIGHT_MCP_CONFIG",
            "PLAYWRIGHT_MCP_CONSOLE_LEVEL",
            "PLAYWRIGHT_MCP_DEVICE",
            "PLAYWRIGHT_MCP_ENDPOINT",
            "PLAYWRIGHT_MCP_EXECUTABLE_PATH",
            "PLAYWRIGHT_MCP_EXTENSION",
            "PLAYWRIGHT_MCP_GRANT_PERMISSIONS",
            "PLAYWRIGHT_MCP_HEADLESS",
            "PLAYWRIGHT_MCP_HOST",
            "PLAYWRIGHT_MCP_IGNORE_HTTPS_ERRORS",
            "PLAYWRIGHT_MCP_IMAGE_RESPONSES",
            "PLAYWRIGHT_MCP_INIT_PAGE",
            "PLAYWRIGHT_MCP_INIT_SCRIPT",
            "PLAYWRIGHT_MCP_ISOLATED",
            "PLAYWRIGHT_MCP_MOBILE",
            "PLAYWRIGHT_MCP_NO_SANDBOX",
            "PLAYWRIGHT_MCP_OUTPUT_DIR",
            "PLAYWRIGHT_MCP_OUTPUT_MAX_SIZE",
            "PLAYWRIGHT_MCP_PING_TIMEOUT_MS",
            "PLAYWRIGHT_MCP_PORT",
            "PLAYWRIGHT_MCP_PROXY_BYPASS",
            "PLAYWRIGHT_MCP_PROXY_SERVER",
            "PLAYWRIGHT_MCP_REMOTE_HEADERS",
            "PLAYWRIGHT_MCP_SANDBOX",
            "PLAYWRIGHT_MCP_SAVE_SESSION",
            "PLAYWRIGHT_MCP_SECRETS_FILE",
            "PLAYWRIGHT_MCP_SHARED_BROWSER_CONTEXT",
            "PLAYWRIGHT_MCP_SNAPSHOT_BOXES",
            "PLAYWRIGHT_MCP_SNAPSHOT_MODE",
            "PLAYWRIGHT_MCP_STORAGE_STATE",
            "PLAYWRIGHT_MCP_TEST_ID_ATTRIBUTE",
            "PLAYWRIGHT_MCP_TIMEOUT_ACTION",
            "PLAYWRIGHT_MCP_TIMEOUT_NAVIGATION",
            "PLAYWRIGHT_MCP_TIMEOUT_SETTLE",
            "PLAYWRIGHT_MCP_USER_AGENT",
            "PLAYWRIGHT_MCP_USER_DATA_DIR",
            "PLAYWRIGHT_MCP_VIEWPORT_SIZE",
        }
        environment = registration.load_mcp_environment()
        self.assertEqual(
            expected_playwright_names,
            {name for name in environment if name.startswith("PLAYWRIGHT_MCP_")},
        )
        self.assertEqual("", environment["NODE_OPTIONS"])
        self.assertEqual("", environment["NODE_PATH"])
        self.assertEqual("5000", environment["PLAYWRIGHT_MCP_PING_TIMEOUT_MS"])
        self.assertNotIn("PLAYWRIGHT_MCP_EXTENSION_TOKEN", environment)

    def test_get_failure_removes_newly_created_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )

            def runner(_executable, _prefix, arguments, **_kwargs):
                if arguments[1] == "add":
                    self.write_registration(
                        user_config,
                        node_executable,
                        playwright_cli,
                        playwright_config,
                    )
                    return subprocess.CompletedProcess(arguments, 0, "", "")
                if arguments[1] == "get":
                    return subprocess.CompletedProcess(arguments, 8, "", "get failed")
                return subprocess.CompletedProcess(arguments, 1, "", "not found")

            with mock.patch.object(registration, "_run_claude", side_effect=runner):
                with self.assertRaises(registration.RegistrationError):
                    registration.register_user_mcp(
                        claude_executable="claude.exe",
                        claude_prefix=(),
                        server_name="intranet-browser-agent",
                        node_executable=node_executable,
                        playwright_cli=playwright_cli,
                        playwright_config=playwright_config,
                        user_config=user_config,
                        backup=backup,
                        reporter=lambda _message: None,
                    )
            self.assertFalse(user_config.exists())

    def test_self_test_runs_as_a_real_subprocess(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "register_claude_user_mcp.py"),
                "self-test",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("SELF-TEST PASSED", result.stdout)

    def test_entrypoint_survives_narrow_code_page_and_unicode_cli_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_cli = root / "fake_claude.py"
            fake_cli.write_bytes(registration._FAKE_CLAUDE_SOURCE.encode("utf-8"))
            node_executable, playwright_cli, playwright_config, user_config, backup = (
                self.paths(root)
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "FAKE_CLAUDE_CONFIG": str(user_config),
                    "FAKE_CLAUDE_EVENTS": str(root / "events.jsonl"),
                    "FAKE_CLAUDE_ADD_STDERR": "1",
                    "FAKE_CLAUDE_UNICODE_STDERR": "1",
                    "PYTHONIOENCODING": "cp1252:strict",
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "register_claude_user_mcp.py"),
                    "register",
                    "--claude-executable",
                    sys.executable,
                    "--claude-prefix",
                    str(fake_cli),
                    "--server-name",
                    "intranet-browser-agent",
                    "--node-executable",
                    str(node_executable),
                    "--playwright-cli",
                    str(playwright_cli),
                    "--playwright-config",
                    str(playwright_config),
                    "--user-config",
                    str(user_config),
                    "--backup",
                    str(backup),
                ],
                check=False,
                capture_output=True,
                env=environment,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr.decode("ascii"))
            self.assertIn(b"\\u672a\\u53d1\\u73b0", result.stdout)
            payload = json.loads(user_config.read_text(encoding="utf-8"))
            self.assertIn("intranet-browser-agent", payload["mcpServers"])

    def test_native_claude_command_is_executed_without_a_shell(self) -> None:
        command = registration._native_command(
            r"C:\Tools\claude.exe", (), ("mcp", "get", "intranet-browser-agent")
        )
        self.assertEqual(
            [
                r"C:\Tools\claude.exe",
                "mcp",
                "get",
                "intranet-browser-agent",
            ],
            command,
        )


if __name__ == "__main__":
    unittest.main()
