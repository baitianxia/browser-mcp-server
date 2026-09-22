"""Transactional user-scope MCP registration shared by MCP projects."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .claude_code import ClaudeInvocation, ProbeResult, invoke_claude


class RegistrationError(RuntimeError):
    """Raised when registration fails and rollback has been attempted."""


Reporter = Callable[[str], None]
Entry = Mapping[str, object]


def _output(result: ProbeResult) -> str:
    return f"{result.stdout}\n{result.stderr}".strip()


def is_missing_user_entry(result: ProbeResult, server_name: str) -> bool:
    """Accept only an exact, user-scope missing-entry diagnostic.

    Claude has used several wordings over time. A broad substring match is
    unsafe: it can mistake another scope, another server, or a permission
    failure for a harmless first-install state.
    """

    if result.exit_code != 1:
        return False
    name = re.escape(server_name)
    patterns = (
        rf"(?i:No\s+(?:user-scoped\s+)?MCP\s+server\s+found\s+with\s+name\s*:)\s*{name}",
        rf"(?i:No\s+MCP\s+server\s+named)\s+['\"]{name}['\"]\s+(?i:in\s+user\s+scope)",
    )
    return any(re.fullmatch(pattern, _output(result)) is not None for pattern in patterns)


def _copy_bytes(source: Path, destination: Path, *, overwrite: bool) -> None:
    if not source.is_file():
        raise RegistrationError(f"Claude configuration is not a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise RegistrationError(f"backup already exists: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    try:
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _restore(user_config: Path, backup: Path, *, was_present: bool) -> None:
    if was_present:
        _copy_bytes(backup, user_config, overwrite=True)
    elif user_config.exists():
        if not user_config.is_file():
            raise RegistrationError(f"refusing to remove non-file user configuration: {user_config}")
        user_config.unlink()


def _entry_from_config(user_config: Path, server_name: str) -> object:
    try:
        payload = json.loads(user_config.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"cannot read Claude user configuration after add: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistrationError("Claude user configuration root is not an object")
    servers = payload.get("mcpServers")
    return servers.get(server_name) if isinstance(servers, dict) else None


def _expected_entry(entry: Entry) -> dict[str, object]:
    command = entry.get("command")
    args = entry.get("args", [])
    env = entry.get("env", {})
    if not isinstance(command, str) or not command or "\x00" in command:
        raise RegistrationError("MCP entry command must be a non-empty string")
    if not isinstance(args, Sequence) or isinstance(args, (str, bytes)) or any(not isinstance(item, str) or "\x00" in item for item in args):
        raise RegistrationError("MCP entry args must be a string sequence")
    if not isinstance(env, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) or "\x00" in k or "\x00" in v for k, v in env.items()):
        raise RegistrationError("MCP entry env must be a string mapping")
    normalized: dict[str, object] = {"command": command, "args": list(args), "env": dict(sorted(env.items()))}
    if "type" in entry:
        if entry["type"] != "stdio":
            raise RegistrationError("user-scope registration only supports stdio entries")
        normalized["type"] = "stdio"
    return normalized


def _verify_entry(user_config: Path, server_name: str, expected: dict[str, object]) -> None:
    actual = _entry_from_config(user_config, server_name)
    if not isinstance(actual, dict):
        raise RegistrationError(f"user-scoped MCP entry was not written: {server_name}")
    for key, value in expected.items():
        if actual.get(key) != value:
            raise RegistrationError(f"user-scoped MCP entry field does not match: {key}")


def _add_arguments(server_name: str, entry: dict[str, object]) -> tuple[str, ...]:
    environment = entry.get("env", {})
    assert isinstance(environment, dict)
    values: list[str] = ["mcp", "add", "--transport", "stdio", "--scope", "user"]
    for key, value in sorted(environment.items()):
        values.extend(("--env", f"{key}={value}"))
    values.extend((server_name, "--", str(entry["command"])))
    values.extend(str(item) for item in entry["args"])
    return tuple(values)


def _failure(label: str, result: ProbeResult) -> str:
    details = [f"{label} exited with code {result.exit_code}"]
    if result.error:
        details.append("error: " + result.error)
    if result.stdout.strip():
        details.append("stdout: " + result.stdout.strip()[-2000:])
    if result.stderr.strip():
        details.append("stderr: " + result.stderr.strip()[-2000:])
    return "; ".join(details)


def register_user_mcp(
    invocation: ClaudeInvocation,
    *,
    server_name: str,
    user_config: Path,
    backup: Path,
    entry: Entry,
    reporter: Reporter = print,
    timeout: float = 90.0,
) -> None:
    if not server_name or any(character.isspace() for character in server_name):
        raise RegistrationError("server name must be non-empty and contain no whitespace")
    expected = _expected_entry(entry)
    if backup.exists():
        raise RegistrationError(f"backup already exists: {backup}")
    was_present = user_config.is_file()
    if user_config.exists() and not was_present:
        raise RegistrationError(f"Claude user configuration is not a regular file: {user_config}")
    if was_present:
        _copy_bytes(user_config, backup, overwrite=False)
    try:
        remove = invoke_claude(invocation, ("mcp", "remove", server_name, "--scope", "user"), timeout=timeout)
        if remove.exit_code == 0:
            reporter("旧的用户级 MCP 条目已清理。")
        elif remove.exit_code == 1 and is_missing_user_entry(remove, server_name):
            reporter("未发现旧的用户级 MCP 条目，继续首次注册。")
        else:
            raise RegistrationError(_failure("claude mcp remove", remove))

        add = invoke_claude(invocation, _add_arguments(server_name, expected), timeout=timeout)
        if not add.succeeded:
            raise RegistrationError(_failure("claude mcp add", add))
        _verify_entry(user_config, server_name, expected)

        get = invoke_claude(invocation, ("mcp", "get", server_name), timeout=timeout)
        if not get.succeeded:
            raise RegistrationError(_failure("claude mcp get", get))
        reporter("Claude Code 用户级 MCP 注册、配置核对和读取验证已完成。")
    except Exception as exc:
        try:
            _restore(user_config, backup, was_present=was_present)
        except Exception as rollback_exc:
            raise RegistrationError(f"{exc}; ROLLBACK FAILED: {rollback_exc}") from rollback_exc
        raise RegistrationError(f"{exc}; Claude user configuration restored") from exc


def unregister_user_mcp(
    invocation: ClaudeInvocation,
    *,
    server_name: str,
    user_config: Path,
    backup: Path,
    expected_entry: Entry,
    reporter: Reporter = print,
    timeout: float = 90.0,
) -> None:
    expected = _expected_entry(expected_entry)
    was_present = user_config.is_file()
    if backup.exists():
        raise RegistrationError(f"backup already exists: {backup}")
    if was_present:
        _copy_bytes(user_config, backup, overwrite=False)
    try:
        if was_present:
            _verify_entry(user_config, server_name, expected)
        remove = invoke_claude(invocation, ("mcp", "remove", server_name, "--scope", "user"), timeout=timeout)
        if remove.exit_code == 1 and is_missing_user_entry(remove, server_name):
            _restore(user_config, backup, was_present=was_present)
            reporter("用户级 MCP 条目已经是移除状态。")
            return
        if remove.exit_code != 0:
            raise RegistrationError(_failure("claude mcp remove", remove))
        if user_config.is_file() and isinstance(_entry_from_config(user_config, server_name), dict):
            raise RegistrationError("Claude user configuration still contains the removed MCP entry")
        reporter("Claude Code 用户级 MCP 条目已移除。")
    except Exception as exc:
        try:
            _restore(user_config, backup, was_present=was_present)
        except Exception as rollback_exc:
            raise RegistrationError(f"{exc}; ROLLBACK FAILED: {rollback_exc}") from rollback_exc
        raise RegistrationError(f"{exc}; Claude user configuration restored") from exc
