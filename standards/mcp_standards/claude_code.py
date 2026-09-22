"""Claude Code discovery, validation, and bounded capability probes.

The resolver deliberately treats the user's Claude installation as an external
dependency. It never installs packages, changes PATH, follows a shell shim as
the process executable, or silently replaces an explicitly supplied path.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Mapping, Sequence


class ClaudeDiscoveryError(RuntimeError):
    """Raised when a supplied invocation cannot be safely executed."""


@dataclass(frozen=True)
class ProbeResult:
    arguments: tuple[str, ...]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and not self.timed_out and self.exit_code == 0


@dataclass(frozen=True)
class ClaudeInvocation:
    """A shell-free command specification for Claude Code."""

    command_path: Path
    executable: Path
    prefix: tuple[str, ...]
    kind: str
    source: str
    final_path: Path
    version: ProbeResult | None = None
    capability: ProbeResult | None = None

    @property
    def argv_prefix(self) -> tuple[str, ...]:
        return (str(self.executable), *self.prefix)


@dataclass(frozen=True)
class CandidateDiagnostic:
    source: str
    candidate: str
    accepted: bool
    reason: str
    final_path: str | None = None
    kind: str | None = None


@dataclass
class DiscoveryResult:
    selected: ClaudeInvocation | None = None
    diagnostics: list[CandidateDiagnostic] = field(default_factory=list)


DEFAULT_PROBE_TIMEOUT = 20.0
SUPPORTED_NATIVE_MACHINES = {0x8664, 0xAA64}
WINDOWS_CLAUDE_EXTENSIONS = {".exe", ".cmd"}
WINDOWS_APPS_MARKERS = (
    "\\microsoft\\windowsapps\\",
    "\\program files\\windowsapps\\",
)


def _is_windows(platform_name: str | None = None) -> bool:
    return (platform_name or sys.platform).startswith("win")


def _clean_output(value: str, limit: int = 2000) -> str:
    value = value.replace("\x00", "")
    return value[-limit:]


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().absolute()


def _is_windows_apps(path: Path) -> bool:
    normalized = str(path).replace("/", "\\").lower()
    return any(marker in normalized for marker in WINDOWS_APPS_MARKERS)


def _windows_final_path(path: Path) -> Path:
    """Resolve a Windows link/junction through a file handle.

    ``Path.resolve`` is useful for ordinary links, but a handle-based lookup
    also covers directory junctions and matches the boundary used by the
    native Windows discovery implementations.
    """

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    final_name = kernel32.GetFinalPathNameByHandleW
    final_name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    final_name.restype = ctypes.c_uint32

    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if handle in {None, invalid_handle}:
        error = ctypes.get_last_error()
        raise OSError(error, f"CreateFileW failed for {path}")
    try:
        capacity = 512
        for _ in range(5):
            buffer = ctypes.create_unicode_buffer(capacity)
            length = final_name(handle, buffer, capacity, 0)
            if length == 0:
                error = ctypes.get_last_error()
                raise OSError(error, f"GetFinalPathNameByHandleW failed for {path}")
            if length < capacity:
                value = buffer.value
                if value.startswith("\\\\?\\UNC\\"):
                    raise ClaudeDiscoveryError("external Claude path resolves to UNC")
                if value.startswith("\\\\?\\"):
                    value = value[4:]
                return Path(value)
            capacity = length + 1
    finally:
        close_handle(handle)
    raise ClaudeDiscoveryError("external Claude path is too long")


def _final_path(path: Path, platform_name: str) -> Path:
    if platform_name.startswith("win"):
        return _windows_final_path(path)
    return path.resolve()


def _is_pe(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                return False
            handle.seek(0x3C)
            offset_bytes = handle.read(4)
            if len(offset_bytes) != 4:
                return False
            offset = int.from_bytes(offset_bytes, "little", signed=False)
            handle.seek(offset)
            if handle.read(4) != b"PE\0\0":
                return False
            machine_bytes = handle.read(2)
            return int.from_bytes(machine_bytes, "little", signed=False) in SUPPORTED_NATIVE_MACHINES
    except (OSError, ValueError):
        return False


def _validate_executable(path: Path, platform_name: str) -> tuple[Path, str | None]:
    if not path.is_file():
        return path, "not_found"
    if _is_windows(platform_name):
        if _is_windows_apps(path):
            return path, "path_rejected_windowsapps"
        try:
            final_path = _final_path(path, platform_name)
        except (OSError, ClaudeDiscoveryError):
            return path, "path_unresolved"
        if _is_windows_apps(final_path):
            return final_path, "path_rejected_windowsapps"
        if not _is_pe(final_path):
            return final_path, "invalid_pe_or_architecture"
        return final_path, None
    if not os.access(path, os.X_OK):
        return path, "not_executable"
    try:
        return _final_path(path, platform_name), None
    except (OSError, ClaudeDiscoveryError):
        return path, "path_unresolved"


def _package_root(command_path: Path) -> Path | None:
    for parent in (command_path.parent, *command_path.parents):
        candidate = parent / "node_modules" / "@anthropic-ai" / "claude-code"
        if (candidate / "package.json").is_file():
            return candidate
    return None


def _resolve_external_file(path: Path, platform_name: str) -> Path:
    """Resolve an external regular file without treating its link as managed content."""

    if platform_name.startswith("win"):
        return _windows_final_path(path)
    return path.resolve()


def _path_within(root: Path, child: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(child))) == str(root)
    except ValueError:
        return False


def _resolve_npm(
    command_path: Path,
    source: str,
    platform_name: str,
    environment: Mapping[str, str],
) -> tuple[ClaudeInvocation | None, str | None]:
    command_final, _command_reason = _validate_executable(command_path, platform_name)
    package_root = _package_root(command_final)
    if package_root is None:
        return None, "invalid_package"
    package_file = package_root / "package.json"
    try:
        package_file_final = _resolve_external_file(package_file, platform_name)
        package_root = package_file_final.parent
        payload = json.loads(package_file_final.read_text(encoding="utf-8-sig"))
    except (OSError, ClaudeDiscoveryError, UnicodeError, json.JSONDecodeError):
        return None, "invalid_package"
    if not isinstance(payload, dict) or payload.get("name") != "@anthropic-ai/claude-code":
        return None, "wrong_identity"
    declared = payload.get("bin")
    if isinstance(declared, str):
        bin_value = declared
    elif isinstance(declared, dict) and isinstance(declared.get("claude"), str):
        bin_value = declared["claude"]
    else:
        return None, "invalid_bin"
    if not bin_value or Path(bin_value).is_absolute() or PureWindowsPath(bin_value).is_absolute():
        return None, "invalid_bin"
    declared_suffix = PureWindowsPath(bin_value).suffix.lower() if _is_windows(platform_name) else Path(bin_value).suffix.lower()
    if declared_suffix not in {".exe", ".js", ".cjs", ".mjs"}:
        return None, "unsupported_bin_extension"
    declared_path = (package_root / bin_value).absolute()
    if not _path_within(package_root.absolute(), declared_path) or not declared_path.is_file():
        return None, "invalid_bin"
    final_path, reason = _validate_executable(declared_path, platform_name)
    if reason in {None, "invalid_pe_or_architecture"} and not _path_within(
        package_root.absolute(), final_path.absolute()
    ):
        return None, "invalid_bin"
    suffix = declared_suffix
    if suffix == ".exe":
        if reason is not None:
            return None, reason
        if final_path.suffix.lower() != ".exe":
            return None, "invalid_bin"
        return ClaudeInvocation(command_path, final_path, (), "npm-native", source, final_path), None
    if final_path.suffix.lower() not in {".js", ".cjs", ".mjs"}:
        return None, "invalid_bin"
    if reason not in {None, "invalid_pe_or_architecture"}:
        return None, reason

    node_candidates: list[Path] = []
    sibling = command_path.parent / ("node.exe" if _is_windows(platform_name) else "node")
    node_candidates.append(sibling)
    configured_node = environment.get("MCP_STANDARDS_NODE")
    if configured_node:
        node_candidates.append(Path(configured_node))
    found_node = shutil.which("node.exe" if _is_windows(platform_name) else "node", path=environment.get("PATH"))
    if found_node:
        node_candidates.append(Path(found_node))
    for candidate in node_candidates:
        node_final, node_reason = _validate_executable(candidate, platform_name)
        if node_reason is None:
            return ClaudeInvocation(command_path, node_final, (str(final_path),), "npm-js", source, final_path), None
    return None, "node_not_found_or_invalid"


def _candidate_paths(
    explicit_path: str | Path | None,
    environment: Mapping[str, str],
    platform_name: str,
) -> list[tuple[str, Path]]:
    if explicit_path:
        return [("explicit", _absolute(explicit_path))]
    candidates: list[tuple[str, Path]] = []
    if _is_windows(platform_name):
        system_root = environment.get("SystemRoot") or r"C:\Windows"
        where = Path(system_root) / "System32" / "where.exe"
        if where.is_file():
            try:
                result = subprocess.run(
                    [str(where), "claude"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=5, check=False,
                    env=dict(environment),
                )
                candidates.extend(("where", _absolute(line.strip())) for line in result.stdout.splitlines() if line.strip())
            except (OSError, subprocess.SubprocessError):
                pass
        names = ("claude.exe", "claude.cmd")
        roots = {
            "USERPROFILE": environment.get("USERPROFILE"),
            "APPDATA": environment.get("APPDATA"),
            "LOCALAPPDATA": environment.get("LOCALAPPDATA"),
        }
        known_dirs: list[Path] = []
        if roots["USERPROFILE"]:
            user_profile = Path(roots["USERPROFILE"])
            known_dirs.extend((user_profile / ".local" / "bin", user_profile / ".claude" / "local"))
        if roots["APPDATA"]:
            known_dirs.append(Path(roots["APPDATA"]) / "npm")
        if roots["LOCALAPPDATA"]:
            local_app_data = Path(roots["LOCALAPPDATA"])
            known_dirs.extend((
                local_app_data / "Microsoft" / "WinGet" / "Links",
                local_app_data / "Programs" / "ClaudeCode",
                local_app_data / "Programs" / "ClaudeCode" / "bin",
                local_app_data / "Programs" / "claude",
                local_app_data / "Programs" / "claude" / "bin",
            ))
            winget_packages = local_app_data / "Microsoft" / "WinGet" / "Packages"
            if winget_packages.is_dir():
                try:
                    for package in sorted(winget_packages.glob("Anthropic.ClaudeCode*")):
                        if package.is_dir():
                            known_dirs.extend((package, package / "bin"))
                except OSError:
                    pass
        for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
            program_root = environment.get(environment_name)
            if program_root:
                program_path = Path(program_root)
                known_dirs.extend((
                    program_path / "ClaudeCode",
                    program_path / "ClaudeCode" / "bin",
                    program_path / "Programs" / "ClaudeCode",
                    program_path / "Programs" / "ClaudeCode" / "bin",
                    program_path / "Claude",
                    program_path / "Programs" / "Claude",
                ))
        for environment_name in ("NVM_SYMLINK",):
            link_root = environment.get(environment_name)
            if link_root:
                known_dirs.append(Path(link_root))
        for directory in known_dirs:
            candidates.extend(("known-dir", directory / name) for name in names)
        path_names = names
    else:
        path_names = ("claude",)
    for directory in environment.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.extend(("PATH", Path(directory) / name) for name in path_names)
    if not _is_windows(platform_name):
        found = shutil.which("claude", path=environment.get("PATH"))
        if found:
            candidates.insert(0, ("which", Path(found)))
    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, candidate in candidates:
        key = os.path.normcase(os.path.normpath(str(candidate)))
        if key not in seen:
            seen.add(key)
            unique.append((source, candidate))
    return unique


def invoke_claude(
    invocation: ClaudeInvocation,
    arguments: Sequence[str],
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    environment: Mapping[str, str] | None = None,
) -> ProbeResult:
    command = [str(invocation.executable), *invocation.prefix, *arguments]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, env=dict(environment) if environment is not None else None,
        )
        return ProbeResult(tuple(arguments), result.returncode, _clean_output(result.stdout), _clean_output(result.stderr))
    except subprocess.TimeoutExpired as exc:
        return ProbeResult(tuple(arguments), None, _clean_output(str(exc.stdout or "")), _clean_output(str(exc.stderr or "")), timed_out=True, error="timeout")
    except OSError as exc:
        return ProbeResult(tuple(arguments), None, error=f"launch_failed: {exc}")


def discover_claude(
    *,
    explicit_path: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    probe: bool = True,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> DiscoveryResult:
    env = dict(os.environ if environment is None else environment)
    platform_value = platform_name or sys.platform
    result = DiscoveryResult()
    for source, candidate in _candidate_paths(explicit_path, env, platform_value):
        if not candidate.is_file():
            result.diagnostics.append(CandidateDiagnostic(source, str(candidate), False, "not_found"))
            continue
        suffix = candidate.suffix.lower()
        if _is_windows(platform_value) and suffix not in WINDOWS_CLAUDE_EXTENSIONS:
            result.diagnostics.append(CandidateDiagnostic(source, str(candidate), False, "unsupported_extension"))
            continue
        final_path, reason = _validate_executable(candidate, platform_value)
        if reason is not None and suffix != ".cmd":
            result.diagnostics.append(CandidateDiagnostic(source, str(candidate), False, reason, str(final_path)))
            continue
        if suffix == ".cmd":
            try:
                invocation, npm_reason = _resolve_npm(candidate, source, platform_value, env)
            except (OSError, ValueError) as exc:
                invocation, npm_reason = None, f"discovery_error: {exc}"
            if invocation is None:
                result.diagnostics.append(CandidateDiagnostic(source, str(candidate), False, npm_reason or "invalid_npm_entry", str(final_path)))
                continue
        else:
            kind = "path-native" if not _is_windows(platform_value) else "native"
            invocation = ClaudeInvocation(candidate, final_path, (), kind, source, final_path)
        if probe:
            version = invoke_claude(invocation, ("--version",), timeout=timeout, environment=env)
            capability = invoke_claude(invocation, ("mcp", "--help"), timeout=timeout, environment=env)
            invocation = ClaudeInvocation(invocation.command_path, invocation.executable, invocation.prefix, invocation.kind, invocation.source, invocation.final_path, version, capability)
            if not version.succeeded:
                result.diagnostics.append(CandidateDiagnostic(source, str(candidate), False, "version_probe_failed", str(final_path), invocation.kind))
                continue
            if not capability.succeeded:
                result.diagnostics.append(CandidateDiagnostic(source, str(candidate), False, "capability_unsupported", str(final_path), invocation.kind))
                continue
        result.diagnostics.append(CandidateDiagnostic(source, str(candidate), True, "accepted", str(final_path), invocation.kind))
        result.selected = invocation
        return result
    return result
