#!/usr/bin/env python3
"""Find the approved Playwright Extension in a Chrome/Edge Windows profile."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Iterable
from pathlib import Path


PLAYWRIGHT_EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
REQUIRED_API_PERMISSIONS = {"activeTab", "debugger", "tabs", "tabGroups"}
REQUIRED_HOST_PERMISSION = "<all_urls>"
PROFILE_PATTERN = re.compile(r"^Profile (\d+)$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

STATUS_CURRENT = "current"
STATUS_COMPATIBLE = "compatible"
STATUS_INCOMPATIBLE = "incompatible"
# Keep the old name as a source-compatible alias for callers that used the
# exact-version checker before compatibility was introduced.
STATUS_VERSION_MISMATCH = STATUS_INCOMPATIBLE
STATUS_NOT_INSTALLED = "not-installed"
EXIT_NOT_INSTALLED = 3
EXIT_INCOMPATIBLE = 4
EXIT_VERSION_MISMATCH = EXIT_INCOMPATIBLE


def browser_user_data_dir(local_app_data: Path, browser_channel: str) -> Path:
    if browser_channel == "chrome":
        return local_app_data / "Google" / "Chrome" / "User Data"
    if browser_channel == "msedge":
        return local_app_data / "Microsoft" / "Edge" / "User Data"
    raise ValueError(f"unsupported browser channel: {browser_channel}")


def profile_directories(user_data_dir: Path) -> list[str]:
    try:
        names = [item.name for item in user_data_dir.iterdir() if item.is_dir()]
    except OSError:
        return []
    profiles = [
        name for name in names if name == "Default" or PROFILE_PATTERN.fullmatch(name)
    ]
    profiles.sort(
        key=lambda name: -1
        if name == "Default"
        else int(PROFILE_PATTERN.fullmatch(name).group(1))  # type: ignore[union-attr]
    )
    return profiles


def last_used_profile(user_data_dir: Path) -> str | None:
    try:
        payload = json.loads((user_data_dir / "Local State").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    profile = payload.get("profile") if isinstance(payload, dict) else None
    value = profile.get("last_used") if isinstance(profile, dict) else None
    return value if isinstance(value, str) else None


def _extension_record(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    extensions = payload.get("extensions") if isinstance(payload, dict) else None
    settings = extensions.get("settings") if isinstance(extensions, dict) else None
    record = settings.get(PLAYWRIGHT_EXTENSION_ID) if isinstance(settings, dict) else None
    return record if isinstance(record, dict) else None


def _approved_unpacked_paths(
    approved_unpacked_path: Path,
    expected_version: str | None = None,
    compatible_versions: Iterable[str] | None = None,
) -> tuple[Path, ...]:
    """Return the exact unpacked directories approved for this package.

    Windows upgrades keep the unpacked extension under a versioned directory,
    so a compatible extension from a previous package may still point at a
    sibling such as ``browser-extension/0.3.0/unpacked``.  Derive only those
    sibling paths from the installer-controlled current path; never broaden
    approval to an arbitrary directory or glob.
    """
    paths = [approved_unpacked_path]
    if (
        expected_version
        and approved_unpacked_path.name == "unpacked"
        and approved_unpacked_path.parent.name == expected_version
    ):
        root = approved_unpacked_path.parent.parent
        for version in compatible_versions or ():
            if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
                continue
            candidate = root / version / "unpacked"
            if candidate not in paths:
                paths.append(candidate)
    return tuple(paths)


def _resolved_extension_path(
    profile: Path,
    record: dict,
    approved_unpacked_path: Path,
    expected_version: str | None = None,
    compatible_versions: Iterable[str] | None = None,
) -> Path | None:
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        candidate_resolved = candidate.resolve()
        for approved_path in _approved_unpacked_paths(
            approved_unpacked_path, expected_version, compatible_versions
        ):
            if os.path.normcase(str(candidate_resolved)) != os.path.normcase(
                str(approved_path.resolve())
            ):
                continue
            return (
                candidate
                if (candidate / "manifest.json").is_file()
                else None
            )
        return None
    profile_root = profile.resolve()
    for base in (profile, profile / "Extensions"):
        resolved = (base / candidate).resolve()
        try:
            resolved.relative_to(profile_root)
        except ValueError:
            continue
        if (resolved / "manifest.json").is_file():
            return resolved
    return None


def _is_compatible_version(
    version: object,
    expected_version: str,
    compatible_versions: Iterable[str] | None,
) -> bool:
    # The current package is always valid. The optional list only adds older
    # versions that the package has explicitly declared safe to keep using.
    allowed = {expected_version}
    if compatible_versions:
        allowed.update(compatible_versions)
    return isinstance(version, str) and version in allowed


def _record_is_enabled(
    profile: Path,
    record: dict,
    expected_version: str,
    approved_unpacked_path: Path,
    compatible_versions: Iterable[str] | None = None,
) -> bool:
    if record.get("state") not in (None, 1):
        return False
    disable_reasons = record.get("disable_reasons", 0)
    if isinstance(disable_reasons, list):
        if disable_reasons:
            return False
    elif disable_reasons != 0:
        return False
    permissions = record.get("active_permissions")
    if not isinstance(permissions, dict):
        return False
    api_permissions = permissions.get("api")
    host_permissions = permissions.get("explicit_host")
    if not isinstance(api_permissions, list) or not REQUIRED_API_PERMISSIONS.issubset(
        item for item in api_permissions if isinstance(item, str)
    ):
        return False
    if not isinstance(host_permissions, list) or REQUIRED_HOST_PERMISSION not in host_permissions:
        return False
    extension_path = _resolved_extension_path(
        profile,
        record,
        approved_unpacked_path,
        expected_version,
        compatible_versions,
    )
    if extension_path is None:
        return False
    try:
        manifest = json.loads(
            (extension_path / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and _is_compatible_version(
        manifest.get("version"), expected_version, compatible_versions
    )


def _record_manifest_version(
    profile: Path,
    record: dict,
    approved_unpacked_path: Path,
    expected_version: str | None = None,
    compatible_versions: Iterable[str] | None = None,
) -> str | None:
    extension_path = _resolved_extension_path(
        profile,
        record,
        approved_unpacked_path,
        expected_version,
        compatible_versions,
    )
    if extension_path is None:
        # An incompatible old package may live under the same controlled
        # versioned extension root but is intentionally not in the allowlist.
        # Read it only for status reporting; _record_is_enabled still accepts
        # exact current/compatible paths exclusively.
        raw_path = record.get("path")
        if isinstance(raw_path, str) and Path(raw_path).is_absolute():
            candidate = Path(raw_path).resolve()
            for approved_path in _approved_unpacked_paths(
                approved_unpacked_path, expected_version, compatible_versions
            ):
                approved_resolved = approved_path.resolve()
                if approved_resolved.name != "unpacked":
                    continue
                root = approved_resolved.parent.parent
                try:
                    relative = candidate.relative_to(root)
                except ValueError:
                    continue
                if (
                    len(relative.parts) == 2
                    and relative.parts[1] == "unpacked"
                    and VERSION_PATTERN.fullmatch(relative.parts[0])
                    and (candidate / "manifest.json").is_file()
                ):
                    extension_path = candidate
                    break
    if extension_path is None:
        return None
    try:
        manifest = json.loads(
            (extension_path / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return (
        version
        if isinstance(version, str) and VERSION_PATTERN.fullmatch(version)
        else None
    )


def extension_installed_in_profile(
    profile: Path,
    expected_version: str,
    approved_unpacked_path: Path,
    compatible_versions: Iterable[str] | None = None,
) -> bool:
    compatible_versions = tuple(compatible_versions or ())
    for filename in ("Secure Preferences", "Preferences"):
        record = _extension_record(profile / filename)
        if record is not None:
            return _record_is_enabled(
                profile,
                record,
                expected_version,
                approved_unpacked_path,
                compatible_versions,
            )
    return False


def extension_status_in_profile(
    profile: Path,
    expected_version: str,
    approved_unpacked_path: Path,
    compatible_versions: Iterable[str] | None = None,
) -> tuple[str, str | None]:
    """Return the usable status and observed version for one browser profile."""
    compatible_versions = tuple(compatible_versions or ())
    incompatible_version: str | None = None
    record_seen = False
    for filename in ("Secure Preferences", "Preferences"):
        record = _extension_record(profile / filename)
        if record is None:
            continue
        record_seen = True
        version = _record_manifest_version(
            profile,
            record,
            approved_unpacked_path,
            expected_version,
            compatible_versions,
        )
        if _record_is_enabled(
            profile,
            record,
            expected_version,
            approved_unpacked_path,
            compatible_versions,
        ):
            if version == expected_version:
                return STATUS_CURRENT, version
            return STATUS_COMPATIBLE, version
        if version is not None and not _is_compatible_version(
            version, expected_version, compatible_versions
        ):
            incompatible_version = version
        # Chrome treats Secure Preferences as authoritative for extension
        # state. Never let a stale ordinary Preferences file override a
        # disabled or otherwise invalid secure record.
        if filename == "Secure Preferences":
            return STATUS_VERSION_MISMATCH, version
    if incompatible_version is not None:
        return STATUS_VERSION_MISMATCH, incompatible_version
    # A record for this extension exists but cannot be safely resolved to an
    # approved directory/version. Treat it as incompatible so the installer
    # repairs it instead of claiming that the extension is absent.
    if record_seen:
        return STATUS_VERSION_MISMATCH, None
    return STATUS_NOT_INSTALLED, None


def find_extension_profile(
    user_data_dir: Path,
    expected_version: str,
    approved_unpacked_path: Path,
    compatible_versions: Iterable[str] | None = None,
) -> str | None:
    compatible_versions = tuple(compatible_versions or ())
    profiles = profile_directories(user_data_dir)
    last_used = last_used_profile(user_data_dir)
    if last_used in profiles:
        # The installer opens the browser without --profile-directory, and the
        # production MCP executable-path fallback does the same. Only the
        # browser's last-used Profile can therefore satisfy the manual flow.
        # Accepting an extension from some other dormant Profile would make the
        # installer pass and the first real MCP call fail.
        profiles = [last_used]
    for profile in profiles:
        if extension_installed_in_profile(
            user_data_dir / profile,
            expected_version,
            approved_unpacked_path,
            compatible_versions,
        ):
            return profile
    return None


def find_extension_status(
    user_data_dir: Path,
    expected_version: str,
    approved_unpacked_path: Path,
    compatible_versions: Iterable[str] | None = None,
) -> tuple[str, str | None, str | None]:
    """Return status, matching profile, and observed version for last-used Profile."""
    compatible_versions = tuple(compatible_versions or ())
    profiles = profile_directories(user_data_dir)
    last_used = last_used_profile(user_data_dir)
    if last_used in profiles:
        profiles = [last_used]
    mismatched_version: str | None = None
    mismatched_profile: str | None = None
    mismatched_found = False
    for profile in profiles:
        status, version = extension_status_in_profile(
            user_data_dir / profile,
            expected_version,
            approved_unpacked_path,
            compatible_versions,
        )
        if status in (STATUS_CURRENT, STATUS_COMPATIBLE):
            return status, profile, version
        if status == STATUS_VERSION_MISMATCH and not mismatched_found:
            mismatched_version = version
            mismatched_profile = profile
            mismatched_found = True
    if mismatched_found:
        return STATUS_VERSION_MISMATCH, mismatched_profile, mismatched_version
    return STATUS_NOT_INSTALLED, None, None


def _status_message(
    status: str,
    user_data_dir: Path,
    expected_version: str,
    profile: str | None,
    installed_version: str | None,
) -> str:
    if status == STATUS_VERSION_MISMATCH:
        location = user_data_dir / profile if profile else user_data_dir
        return (
            "INCOMPATIBLE: Playwright Extension "
            f"{PLAYWRIGHT_EXTENSION_ID} installed version "
            f"{installed_version or 'unknown'}, expected {expected_version} in {location}"
        )
    return f"NOT INSTALLED: Playwright Extension {PLAYWRIGHT_EXTENSION_ID} in {user_data_dir}"


def _status_for_args(args: argparse.Namespace) -> tuple[str, str | None, str | None, Path]:
    user_data_dir = browser_user_data_dir(args.local_app_data, args.browser_channel)
    status, profile, installed_version = find_extension_status(
        user_data_dir,
        args.expected_version,
        args.approved_unpacked_path,
        getattr(args, "compatible_versions", None),
    )
    return status, profile, installed_version, user_data_dir


def check(args: argparse.Namespace) -> int:
    status, profile, installed_version, user_data_dir = _status_for_args(args)
    if status == STATUS_NOT_INSTALLED:
        if not args.quiet:
            print(
                _status_message(
                    status,
                    user_data_dir,
                    args.expected_version,
                    profile,
                    installed_version,
                )
            )
        return EXIT_NOT_INSTALLED
    if status == STATUS_VERSION_MISMATCH:
        if not args.quiet:
            print(
                _status_message(
                    status,
                    user_data_dir,
                    args.expected_version,
                    profile,
                    installed_version,
                )
            )
        return EXIT_VERSION_MISMATCH
    if status == STATUS_COMPATIBLE:
        if not args.quiet:
            location = user_data_dir / profile if profile else user_data_dir
            print(
                "COMPATIBLE: Playwright Extension "
                f"{PLAYWRIGHT_EXTENSION_ID} version {installed_version} in {location}"
            )
        return 0
    if not args.quiet:
        print(f"INSTALLED: {user_data_dir / profile}")
    return 0


def report_status(args: argparse.Namespace) -> int:
    status, _profile, version, _user_data_dir = _status_for_args(args)
    marker = {
        STATUS_CURRENT: "CURRENT",
        STATUS_COMPATIBLE: "COMPATIBLE",
        STATUS_VERSION_MISMATCH: "INCOMPATIBLE",
        STATUS_NOT_INSTALLED: "NOT_INSTALLED",
    }[status]
    print(f"{marker}|{version or ''}")
    return 0


def wait_for_extension(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout_seconds
    while True:
        result = check(args)
        if result == 0:
            return 0
        if time.monotonic() >= deadline:
            status, profile, installed_version, user_data_dir = _status_for_args(args)
            if args.quiet:
                print(
                    _status_message(
                        status,
                        user_data_dir,
                        args.expected_version,
                        profile,
                        installed_version,
                    )
                )
            return (
                EXIT_VERSION_MISMATCH
                if status == STATUS_VERSION_MISMATCH
                else EXIT_NOT_INSTALLED
            )
        time.sleep(args.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "wait", "status"):
        command = subparsers.add_parser(name)
        command.add_argument("--local-app-data", required=True, type=Path)
        command.add_argument(
            "--browser-channel", required=True, choices=("chrome", "msedge")
        )
        command.add_argument("--expected-version", required=True)
        command.add_argument(
            "--compatible-version",
            dest="compatible_versions",
            action="append",
            default=[],
            help="additional installed extension version allowed for reuse",
        )
        command.add_argument("--approved-unpacked-path", required=True, type=Path)
        command.add_argument("--quiet", action="store_true")
        if name == "wait":
            command.add_argument("--timeout-seconds", type=int, default=180)
            command.add_argument("--poll-seconds", type=float, default=2.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not VERSION_PATTERN.fullmatch(args.expected_version) or any(
        not VERSION_PATTERN.fullmatch(version)
        for version in args.compatible_versions
    ):
        print("ERROR: invalid extension version", file=sys.stderr)
        return 2
    if args.command == "check":
        return check(args)
    if args.command == "status":
        return report_status(args)
    if args.timeout_seconds < 1 or not 0.1 <= args.poll_seconds <= 30:
        print("ERROR: invalid wait interval", file=sys.stderr)
        return 2
    return wait_for_extension(args)


if __name__ == "__main__":
    raise SystemExit(main())
