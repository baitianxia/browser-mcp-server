#!/usr/bin/env python3
"""Find the approved Playwright Extension in a Chrome/Edge Windows profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path


PLAYWRIGHT_EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
REQUIRED_API_PERMISSIONS = {"activeTab", "debugger", "tabs", "tabGroups"}
REQUIRED_HOST_PERMISSION = "<all_urls>"
PROFILE_PATTERN = re.compile(r"^Profile (\d+)$")


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


def _path_is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _directory_inventory(root: Path) -> dict[str, tuple[str, int, str]] | None:
    if not root.is_dir() or _path_is_link_like(root):
        return None
    inventory: dict[str, tuple[str, int, str]] = {}
    try:
        for item in sorted(root.rglob("*"), key=lambda entry: entry.as_posix()):
            relative = item.relative_to(root).as_posix()
            if _path_is_link_like(item):
                return None
            if item.is_dir():
                inventory[relative] = ("directory", 0, "")
            elif item.is_file():
                digest = hashlib.sha256()
                with item.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                inventory[relative] = ("file", item.stat().st_size, digest.hexdigest())
            else:
                return None
    except OSError:
        return None
    return inventory


def _is_verified_profile_copy(
    profile: Path, candidate: Path, approved_unpacked_path: Path
) -> bool:
    expected = profile / "Unpacked Extensions" / approved_unpacked_path.name
    if os.path.normcase(str(candidate.resolve())) != os.path.normcase(
        str(expected.resolve())
    ):
        return False
    approved_inventory = _directory_inventory(approved_unpacked_path)
    return approved_inventory is not None and _directory_inventory(candidate) == approved_inventory


def _resolved_extension_path(
    profile: Path, record: dict, approved_unpacked_path: Path
) -> Path | None:
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        if os.path.normcase(str(candidate.resolve())) == os.path.normcase(
            str(approved_unpacked_path.resolve())
        ):
            return candidate
        # Chrome's developerPrivate.loadDirectory imports non-native browser
        # filesystem entries into this fixed Profile-owned directory. CI uses
        # that persistent implementation because hosted runners cannot provide
        # trusted human input to the native folder picker. Accept only the exact
        # last-used Profile location and only when every file is byte-identical
        # to the already approved package directory.
        return (
            candidate
            if _is_verified_profile_copy(profile, candidate, approved_unpacked_path)
            else None
        )
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


def _record_is_enabled(
    profile: Path,
    record: dict,
    expected_version: str,
    approved_unpacked_path: Path,
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
        profile, record, approved_unpacked_path
    )
    if extension_path is None:
        return False
    try:
        manifest = json.loads(
            (extension_path / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("version") == expected_version


def extension_installed_in_profile(
    profile: Path, expected_version: str, approved_unpacked_path: Path
) -> bool:
    for filename in ("Secure Preferences", "Preferences"):
        record = _extension_record(profile / filename)
        if record is not None:
            return _record_is_enabled(
                profile, record, expected_version, approved_unpacked_path
            )
    return False


def find_extension_profile(
    user_data_dir: Path,
    expected_version: str,
    approved_unpacked_path: Path,
) -> str | None:
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
        ):
            return profile
    return None


def check(args: argparse.Namespace) -> int:
    user_data_dir = browser_user_data_dir(args.local_app_data, args.browser_channel)
    profile = find_extension_profile(
        user_data_dir,
        args.expected_version,
        args.approved_unpacked_path,
    )
    if profile is None:
        if not args.quiet:
            print(
                "NOT INSTALLED: Playwright Extension "
                f"{PLAYWRIGHT_EXTENSION_ID} in {user_data_dir}"
            )
        return 3
    if not args.quiet:
        print(f"INSTALLED: {user_data_dir / profile}")
    return 0


def wait_for_extension(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout_seconds
    while True:
        result = check(args)
        if result == 0:
            return 0
        if time.monotonic() >= deadline:
            if args.quiet:
                user_data_dir = browser_user_data_dir(
                    args.local_app_data, args.browser_channel
                )
                print(
                    "NOT INSTALLED: Playwright Extension "
                    f"{PLAYWRIGHT_EXTENSION_ID} in {user_data_dir}"
                )
            return 3
        time.sleep(args.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "wait"):
        command = subparsers.add_parser(name)
        command.add_argument("--local-app-data", required=True, type=Path)
        command.add_argument(
            "--browser-channel", required=True, choices=("chrome", "msedge")
        )
        command.add_argument("--expected-version", required=True)
        command.add_argument("--approved-unpacked-path", required=True, type=Path)
        command.add_argument("--quiet", action="store_true")
        if name == "wait":
            command.add_argument("--timeout-seconds", type=int, default=180)
            command.add_argument("--poll-seconds", type=float, default=2.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", args.expected_version):
        print("ERROR: invalid expected extension version", file=sys.stderr)
        return 2
    if args.command == "check":
        return check(args)
    if args.timeout_seconds < 1 or not 0.1 <= args.poll_seconds <= 30:
        print("ERROR: invalid wait interval", file=sys.stderr)
        return 2
    return wait_for_extension(args)


if __name__ == "__main__":
    raise SystemExit(main())
