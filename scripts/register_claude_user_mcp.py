#!/usr/bin/env python3
"""Transactionally register the Windows pilot MCP in Claude Code user scope."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import Callable, Mapping, MutableMapping, Sequence


class RegistrationError(RuntimeError):
    pass


Reporter = Callable[[str], None]
MCP_ENVIRONMENT_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "windows-mcp-environment.json"
)
ENVIRONMENT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
EXTENSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
EXTENSION_TOKEN_INPUT_ENV = "INTRANET_BROWSER_AGENT_EXTENSION_TOKEN_INPUT"


def _configure_standard_streams() -> None:
    """Keep status/error reporting from aborting on narrow Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def _validated_mcp_environment(environment: Mapping[str, str]) -> dict[str, str]:
    if not environment:
        raise RegistrationError("Windows MCP environment policy must not be empty")
    validated: dict[str, str] = {}
    for name, value in environment.items():
        if not isinstance(name, str) or not ENVIRONMENT_NAME_RE.fullmatch(name):
            raise RegistrationError(f"invalid Windows MCP environment name: {name!r}")
        if not (name.startswith("PLAYWRIGHT_MCP_") or name in {"NODE_OPTIONS", "NODE_PATH"}):
            raise RegistrationError(f"unexpected Windows MCP environment name: {name}")
        if not isinstance(value, str) or "\x00" in value:
            raise RegistrationError(f"invalid Windows MCP environment value for {name}")
        validated[name] = value
    required_values = {
        "NODE_OPTIONS": "",
        "NODE_PATH": "",
        "PLAYWRIGHT_MCP_CONFIG": "",
        "PLAYWRIGHT_MCP_PING_TIMEOUT_MS": "5000",
    }
    for name, expected in required_values.items():
        if validated.get(name) != expected:
            raise RegistrationError(
                f"Windows MCP environment policy must set {name} to {expected!r}"
            )
    return dict(sorted(validated.items()))


def load_mcp_environment(path: Path = MCP_ENVIRONMENT_PATH) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"cannot load Windows MCP environment policy: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "environment"}:
        raise RegistrationError("Windows MCP environment policy has an invalid root shape")
    if payload["schemaVersion"] != 1 or not isinstance(payload["environment"], dict):
        raise RegistrationError("unsupported Windows MCP environment policy schema")
    return _validated_mcp_environment(payload["environment"])


def _validated_extension_token(token: str) -> str:
    if not EXTENSION_TOKEN_RE.fullmatch(token):
        raise RegistrationError("extension token has an invalid format")
    try:
        decoded = base64.urlsafe_b64decode(token + "=")
    except (ValueError, binascii.Error) as exc:
        raise RegistrationError("extension token is not valid base64url") from exc
    if len(decoded) != 32:
        raise RegistrationError("extension token must contain 32 random bytes")
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != token:
        raise RegistrationError("extension token is not canonical base64url")
    return token


def _extension_token_from_text(value: str) -> str:
    token = value.strip()
    prefix = "PLAYWRIGHT_MCP_EXTENSION_TOKEN="
    if token.startswith(prefix):
        token = token[len(prefix) :].strip()
    return _validated_extension_token(token)


def load_extension_token(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 1024:
            raise RegistrationError(
                f"extension token file is missing, not regular, or too large: {path}"
            )
        token = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise RegistrationError(f"cannot read extension token file: {exc}") from exc
    return _extension_token_from_text(token)


def load_extension_token_stream(stream: object = sys.stdin) -> str:
    reader = getattr(stream, "read", None)
    if not callable(reader):
        raise RegistrationError("extension token input stream is not readable")
    try:
        token = reader(1025)
    except (OSError, UnicodeError) as exc:
        raise RegistrationError(f"cannot read extension token input: {exc}") from exc
    if not isinstance(token, str) or len(token) > 1024:
        raise RegistrationError("extension token input is too large")
    return _extension_token_from_text(token)


def load_extension_token_environment(
    environment: MutableMapping[str, str] | None = None,
) -> str:
    """Consume the transient token before any Claude child process is started."""
    source = os.environ if environment is None else environment
    token = source.pop(EXTENSION_TOKEN_INPUT_ENV, None)
    if token is None:
        raise RegistrationError("extension token input environment is missing")
    return _extension_token_from_text(token)


def _mcp_environment_arguments(environment: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        item
        for name, value in sorted(environment.items())
        for item in ("--env", f"{name}={value}")
    )


def _playwright_command_arguments(
    playwright_cli: Path,
    playwright_config: Path,
    browser_channel: str | None,
    browser_executable: Path | None = None,
) -> list[str]:
    if browser_channel not in {None, "chrome", "msedge"}:
        raise RegistrationError(f"unsupported browser channel: {browser_channel}")
    arguments = [str(playwright_cli)]
    if browser_channel is not None:
        if browser_executable is None:
            raise RegistrationError(
                "extension browser channel requires an explicit browser executable"
            )
        arguments.append(f"--browser={browser_channel}")
        arguments.append(f"--executable-path={browser_executable}")
    elif browser_executable is not None:
        raise RegistrationError("browser executable requires an extension browser channel")
    arguments.extend(("--config", str(playwright_config)))
    return arguments


def _atomic_copy(source: Path, destination: Path, *, overwrite: bool) -> None:
    if not source.is_file():
        raise RegistrationError(f"backup source is not a regular file: {source}")
    if destination.exists() and not overwrite:
        raise RegistrationError(f"refusing to overwrite backup: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _native_command(
    executable: str, prefix: Sequence[str], arguments: Sequence[str]
) -> list[str]:
    return [executable, *prefix, *arguments]


def _run_claude(
    executable: str,
    prefix: Sequence[str],
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = _native_command(executable, prefix, arguments)
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=dict(environment) if environment is not None else None,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RegistrationError(f"cannot execute Claude Code CLI: {exc}") from exc


def _failure(
    command_name: str,
    result: subprocess.CompletedProcess[str],
    *,
    secrets: Sequence[str] = (),
) -> str:
    def redacted(value: str) -> str:
        for secret in secrets:
            if secret:
                value = value.replace(secret, "<redacted>")
        return value

    details: list[str] = [f"{command_name} exited with code {result.returncode}"]
    if result.stdout.strip():
        details.append("stdout: " + redacted(result.stdout.strip())[-2000:])
    if result.stderr.strip():
        details.append("stderr: " + redacted(result.stderr.strip())[-2000:])
    return "; ".join(details)


def _remove_result_is_missing(result: subprocess.CompletedProcess[str]) -> bool:
    output = f"{result.stdout}\n{result.stderr}"
    return re.search(
        r"\bno\s+(?:user-scoped\s+)?mcp\s+server\s+found\s+with\s+name\s*:",
        output,
        flags=re.IGNORECASE,
    ) is not None


def _restore_user_config(
    user_config: Path, backup: Path, *, was_present: bool
) -> None:
    if was_present:
        if not backup.is_file():
            raise RegistrationError(f"rollback backup is missing: {backup}")
        _atomic_copy(backup, user_config, overwrite=True)
    elif user_config.exists():
        if not user_config.is_file():
            raise RegistrationError(
                f"refusing to remove non-file user configuration: {user_config}"
            )
        user_config.unlink()


def _existing_extension_tokens(user_config: Path, server_name: str) -> tuple[str, ...]:
    """Return only known token-shaped values for diagnostic redaction."""
    try:
        payload = json.loads(user_config.read_text(encoding="utf-8-sig"))
        servers = payload.get("mcpServers") if isinstance(payload, dict) else None
        entry = servers.get(server_name) if isinstance(servers, dict) else None
        environment = entry.get("env") if isinstance(entry, dict) else None
        token = (
            environment.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
            if isinstance(environment, dict)
            else None
        )
        if isinstance(token, str) and EXTENSION_TOKEN_RE.fullmatch(token):
            return (token,)
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return ()


def _verify_user_registration(
    user_config: Path,
    *,
    server_name: str,
    node_executable: Path,
    playwright_cli: Path,
    playwright_config: Path,
    browser_channel: str | None,
    browser_executable: Path | None,
    mcp_environment: Mapping[str, str],
) -> None:
    try:
        payload = json.loads(user_config.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(
            f"cannot verify Claude user configuration after add: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RegistrationError("Claude user configuration root is not an object")
    servers = payload.get("mcpServers")
    entry = servers.get(server_name) if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        raise RegistrationError(
            f"user-scoped MCP entry was not written: {server_name}"
        )
    if entry.get("type") not in {None, "stdio"}:
        raise RegistrationError("user-scoped MCP entry transport is not stdio")
    unexpected_fields = set(entry) - {"type", "command", "args", "env"}
    if unexpected_fields:
        raise RegistrationError(
            "user-scoped MCP entry contains unexpected fields: "
            + ", ".join(sorted(unexpected_fields))
        )
    expected_command = str(node_executable)
    expected_arguments = _playwright_command_arguments(
        playwright_cli,
        playwright_config,
        browser_channel,
        browser_executable,
    )
    if entry.get("command") != expected_command or entry.get("args") != expected_arguments:
        raise RegistrationError(
            "user-scoped MCP entry does not match the verified node.exe/CLI/config paths"
        )
    entry_environment = entry.get("env")
    if entry_environment != dict(mcp_environment):
        raise RegistrationError(
            "user-scoped MCP entry does not contain the exact hardened environment"
        )


def register_user_mcp(
    *,
    claude_executable: str,
    claude_prefix: Sequence[str],
    server_name: str,
    node_executable: Path,
    playwright_cli: Path,
    playwright_config: Path,
    browser_channel: str | None = None,
    browser_executable: Path | None = None,
    user_config: Path,
    backup: Path,
    reporter: Reporter = print,
    environment: Mapping[str, str] | None = None,
    mcp_environment: Mapping[str, str] | None = None,
    extension_token: str | None = None,
) -> None:
    if not server_name or any(character.isspace() for character in server_name):
        raise RegistrationError("server name must be non-empty and contain no whitespace")
    if os.name == "nt" and not claude_executable.lower().endswith(".exe"):
        raise RegistrationError(
            "the resolved Claude Code launcher must be a Windows .exe"
        )
    if not claude_executable or "\x00" in claude_executable or any(
        not isinstance(item, str) or "\x00" in item for item in claude_prefix
    ):
        raise RegistrationError("invalid resolved Claude Code invocation")
    if not node_executable.is_file():
        raise RegistrationError(
            f"Node.js executable is not a regular file: {node_executable}"
        )
    if not playwright_cli.is_file():
        raise RegistrationError(f"Playwright CLI is not a regular file: {playwright_cli}")
    if not playwright_config.is_file():
        raise RegistrationError(
            f"Playwright config is not a regular file: {playwright_config}"
        )
    if browser_channel is not None:
        if browser_executable is None or not browser_executable.is_file():
            raise RegistrationError(
                "browser executable is not a regular file: "
                f"{browser_executable}"
            )
        if browser_executable.suffix.lower() != ".exe":
            raise RegistrationError("browser executable must end in .exe")
    elif browser_executable is not None:
        raise RegistrationError("browser executable requires an extension browser channel")
    registered_environment = (
        load_mcp_environment()
        if mcp_environment is None
        else _validated_mcp_environment(mcp_environment)
    )
    if extension_token is not None:
        extension_token = _validated_extension_token(extension_token)
        registered_environment = dict(registered_environment)
        registered_environment["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = extension_token
        registered_environment = _validated_mcp_environment(registered_environment)

    was_present = user_config.is_file()
    if user_config.exists() and not was_present:
        raise RegistrationError(f"Claude user config is not a regular file: {user_config}")
    if was_present:
        _atomic_copy(user_config, backup, overwrite=False)
    redaction_secrets = tuple(
        dict.fromkeys(
            (*_existing_extension_tokens(user_config, server_name), extension_token or "")
        )
    )

    change_started = False
    try:
        change_started = True
        remove_result = _run_claude(
            claude_executable,
            claude_prefix,
            ("mcp", "remove", server_name, "--scope", "user"),
            environment=environment,
        )
        if remove_result.returncode == 0:
            reporter("已清理旧的用户级 MCP 条目，正在注册新版本。")
        elif _remove_result_is_missing(remove_result):
            reporter("未发现可清理的旧 MCP 条目（首次安装时正常），继续注册。")
        else:
            raise RegistrationError(
                _failure(
                    "claude mcp remove",
                    remove_result,
                    secrets=redaction_secrets,
                )
            )

        add_result = _run_claude(
            claude_executable,
            claude_prefix,
            (
                "mcp",
                "add",
                "--transport",
                "stdio",
                "--scope",
                "user",
                server_name,
                *_mcp_environment_arguments(registered_environment),
                "--",
                str(node_executable),
                *_playwright_command_arguments(
                    playwright_cli,
                    playwright_config,
                    browser_channel,
                    browser_executable,
                ),
            ),
            environment=environment,
        )
        if add_result.returncode != 0:
            raise RegistrationError(
                _failure(
                    "claude mcp add",
                    add_result,
                    secrets=redaction_secrets,
                )
            )

        _verify_user_registration(
            user_config,
            server_name=server_name,
            node_executable=node_executable,
            playwright_cli=playwright_cli,
            playwright_config=playwright_config,
            browser_channel=browser_channel,
            browser_executable=browser_executable,
            mcp_environment=registered_environment,
        )

        get_result = _run_claude(
            claude_executable,
            claude_prefix,
            ("mcp", "get", server_name),
            environment=environment,
        )
        if get_result.returncode != 0:
            raise RegistrationError(
                _failure(
                    "claude mcp get",
                    get_result,
                    secrets=redaction_secrets,
                )
            )
        reporter("Claude Code 用户级 MCP 注册和读取验证已完成。")
    except Exception as exc:
        if not change_started:
            raise
        try:
            _restore_user_config(user_config, backup, was_present=was_present)
        except Exception as rollback_exc:
            raise RegistrationError(
                f"{exc}; ROLLBACK FAILED: {rollback_exc}"
            ) from exc
        raise RegistrationError(f"{exc}; Claude user configuration restored") from exc


_FAKE_CLAUDE_SOURCE = textwrap.dedent(
    r"""
    import json
    import os
    import sys
    from pathlib import Path

    args = sys.argv[1:]
    config = Path(os.environ["FAKE_CLAUDE_CONFIG"])
    events = Path(os.environ["FAKE_CLAUDE_EVENTS"])
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\n")

    def load():
        if not config.exists():
            return {}
        return json.loads(config.read_text(encoding="utf-8"))

    def save(payload):
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    if args[:2] == ["mcp", "remove"]:
        name = args[2]
        payload = load()
        servers = payload.setdefault("mcpServers", {})
        if name not in servers:
            print(f"No user-scoped MCP server found with name: {name}", file=sys.stderr)
            raise SystemExit(1)
        del servers[name]
        save(payload)
        raise SystemExit(0)

    if args[:2] == ["mcp", "add"]:
        if os.environ.get("FAKE_CLAUDE_FAIL_ADD") == "1":
            print("simulated add failure", file=sys.stderr)
            raise SystemExit(7)
        scope_index = args.index("--scope")
        separator = args.index("--")
        name = args[scope_index + 2]
        mcp_environment = {}
        option_index = scope_index + 3
        while option_index < separator:
            if args[option_index] != "--env" or option_index + 1 >= separator:
                print("unexpected fake Claude add option", file=sys.stderr)
                raise SystemExit(9)
            environment_item = args[option_index + 1]
            if "=" not in environment_item:
                print("invalid fake Claude environment option", file=sys.stderr)
                raise SystemExit(9)
            environment_name, environment_value = environment_item.split("=", 1)
            mcp_environment[environment_name] = environment_value
            option_index += 2
        command = args[separator + 1]
        command_arguments = args[separator + 2 :]
        payload = load()
        payload.setdefault("mcpServers", {})[name] = {
            "type": "stdio",
            "command": (
                "wrong.exe"
                if os.environ.get("FAKE_CLAUDE_WRONG_ENTRY") == "1"
                else command
            ),
            "args": command_arguments,
            "env": mcp_environment,
        }
        save(payload)
        if os.environ.get("FAKE_CLAUDE_ADD_STDERR") == "1":
            if os.environ.get("FAKE_CLAUDE_UNICODE_STDERR") == "1":
                sys.stderr.buffer.write("模拟的非致命警告 ✓\n".encode("utf-8"))
            else:
                print("simulated non-fatal warning", file=sys.stderr)
        raise SystemExit(0)

    if args[:2] == ["mcp", "get"]:
        if os.environ.get("FAKE_CLAUDE_FAIL_GET") == "1":
            print("simulated get failure", file=sys.stderr)
            raise SystemExit(8)
        name = args[2]
        if name not in load().get("mcpServers", {}):
            print("server is missing", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)

    print("unexpected fake Claude arguments", args, file=sys.stderr)
    raise SystemExit(9)
    """
).lstrip()


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="claude-mcp-registration-self-test-") as value:
        root = Path(value)
        fake_cli = root / "fake_claude.py"
        fake_cli.write_bytes(_FAKE_CLAUDE_SOURCE.encode("utf-8"))
        node_executable = root / "node.exe"
        node_executable.write_bytes(b"MZ")
        playwright_cli = root / "cli.js"
        playwright_cli.write_bytes(b"// fixed CLI\n")
        playwright_config = root / "playwright.config.json"
        playwright_config.write_bytes(b"{}\n")
        browser_executable = root / "chrome.exe"
        browser_executable.write_bytes(b"MZ")
        user_config = root / ".claude.json"
        events = root / "events.jsonl"
        base_environment = dict(os.environ)
        base_environment.update(
            {
                "FAKE_CLAUDE_CONFIG": str(user_config),
                "FAKE_CLAUDE_EVENTS": str(events),
                "FAKE_CLAUDE_ADD_STDERR": "1",
            }
        )
        quiet: Reporter = lambda _message: None
        mcp_environment = load_mcp_environment()

        original = b'{"unrelated":{"keep":true}}\r\n'
        user_config.write_bytes(original)
        first_backup = root / "first-install.bak"
        register_user_mcp(
            claude_executable=sys.executable,
            claude_prefix=(str(fake_cli),),
            server_name="intranet-browser-agent",
            node_executable=node_executable,
            playwright_cli=playwright_cli,
            playwright_config=playwright_config,
            browser_channel="chrome",
            browser_executable=browser_executable,
            user_config=user_config,
            backup=first_backup,
            reporter=quiet,
            environment=base_environment,
        )
        payload = json.loads(user_config.read_text(encoding="utf-8"))
        if first_backup.read_bytes() != original or payload["unrelated"] != {"keep": True}:
            raise RegistrationError("first-install backup or unrelated config was not preserved")
        expected_events = [
            ["mcp", "remove", "intranet-browser-agent", "--scope", "user"],
            [
                "mcp",
                "add",
                "--transport",
                "stdio",
                "--scope",
                "user",
                "intranet-browser-agent",
                *_mcp_environment_arguments(mcp_environment),
                "--",
                str(node_executable),
                str(playwright_cli),
                "--browser=chrome",
                f"--executable-path={browser_executable}",
                "--config",
                str(playwright_config),
            ],
            ["mcp", "get", "intranet-browser-agent"],
        ]
        actual_events = [json.loads(line) for line in events.read_text().splitlines()]
        if actual_events != expected_events:
            raise RegistrationError(f"unexpected Claude CLI sequence: {actual_events!r}")

        upgrade_original = b'{"mcpServers":{"intranet-browser-agent":{"command":"old"}}}\n'
        user_config.write_bytes(upgrade_original)
        add_failure_environment = dict(base_environment)
        add_failure_environment["FAKE_CLAUDE_FAIL_ADD"] = "1"
        try:
            register_user_mcp(
                claude_executable=sys.executable,
                claude_prefix=(str(fake_cli),),
                server_name="intranet-browser-agent",
                node_executable=node_executable,
                playwright_cli=playwright_cli,
                playwright_config=playwright_config,
                user_config=user_config,
                backup=root / "add-failure.bak",
                reporter=quiet,
                environment=add_failure_environment,
            )
        except RegistrationError:
            pass
        else:
            raise RegistrationError("simulated add failure unexpectedly succeeded")
        if user_config.read_bytes() != upgrade_original:
            raise RegistrationError("existing user config was not restored after add failure")

        wrong_entry_original = b'{"keep":"before-wrong-entry"}\r\n'
        user_config.write_bytes(wrong_entry_original)
        wrong_entry_environment = dict(base_environment)
        wrong_entry_environment["FAKE_CLAUDE_WRONG_ENTRY"] = "1"
        try:
            register_user_mcp(
                claude_executable=sys.executable,
                claude_prefix=(str(fake_cli),),
                server_name="intranet-browser-agent",
                node_executable=node_executable,
                playwright_cli=playwright_cli,
                playwright_config=playwright_config,
                user_config=user_config,
                backup=root / "wrong-entry.bak",
                reporter=quiet,
                environment=wrong_entry_environment,
            )
        except RegistrationError:
            pass
        else:
            raise RegistrationError("simulated wrong user entry unexpectedly succeeded")
        if user_config.read_bytes() != wrong_entry_original:
            raise RegistrationError("user config was not restored after wrong entry")

        user_config.unlink()
        get_failure_environment = dict(base_environment)
        get_failure_environment["FAKE_CLAUDE_FAIL_GET"] = "1"
        try:
            register_user_mcp(
                claude_executable=sys.executable,
                claude_prefix=(str(fake_cli),),
                server_name="intranet-browser-agent",
                node_executable=node_executable,
                playwright_cli=playwright_cli,
                playwright_config=playwright_config,
                user_config=user_config,
                backup=root / "get-failure.bak",
                reporter=quiet,
                environment=get_failure_environment,
            )
        except RegistrationError:
            pass
        else:
            raise RegistrationError("simulated get failure unexpectedly succeeded")
        if user_config.exists():
            raise RegistrationError("new user config was not removed after get failure")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_parser = subparsers.add_parser("register")
    register_parser.add_argument("--claude-executable", required=True)
    register_parser.add_argument("--claude-prefix", action="append", default=[])
    register_parser.add_argument("--server-name", required=True)
    register_parser.add_argument("--node-executable", required=True, type=Path)
    register_parser.add_argument("--playwright-cli", required=True, type=Path)
    register_parser.add_argument("--playwright-config", required=True, type=Path)
    register_parser.add_argument(
        "--browser-channel", choices=("chrome", "msedge")
    )
    register_parser.add_argument("--browser-executable", type=Path)
    register_parser.add_argument("--user-config", required=True, type=Path)
    register_parser.add_argument("--backup", required=True, type=Path)
    token_source = register_parser.add_mutually_exclusive_group()
    token_source.add_argument("--extension-token-file", type=Path)
    token_source.add_argument("--extension-token-stdin", action="store_true")
    token_source.add_argument("--extension-token-environment", action="store_true")
    subparsers.add_parser("self-test")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "self-test":
            self_test()
            print("CLAUDE MCP REGISTRATION SELF-TEST PASSED")
        else:
            extension_token = (
                load_extension_token(args.extension_token_file)
                if args.extension_token_file
                else load_extension_token_stream()
                if args.extension_token_stdin
                else load_extension_token_environment()
                if args.extension_token_environment
                else None
            )
            register_user_mcp(
                claude_executable=args.claude_executable,
                claude_prefix=args.claude_prefix,
                server_name=args.server_name,
                node_executable=args.node_executable,
                playwright_cli=args.playwright_cli,
                playwright_config=args.playwright_config,
                browser_channel=args.browser_channel,
                browser_executable=args.browser_executable,
                user_config=args.user_config,
                backup=args.backup,
                extension_token=extension_token,
            )
    except (OSError, RegistrationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    _configure_standard_streams()
    raise SystemExit(main())
