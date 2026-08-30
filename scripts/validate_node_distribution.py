#!/usr/bin/env python3
"""Validate the minimal Windows x64 Node.js distribution accepted by this project."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import struct
from pathlib import Path


ALLOWED_FILES = frozenset({"LICENSE", "SOURCE.json", "VERSION", "node.exe"})
MINIMUM_VERSION = (20, 19, 0)
VERSION_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PE_AMD64 = 0x8664
APPROVAL_FIELDS = {
    "licenseSha256",
    "nodeExeSha256",
    "officialChecksumsSha256",
    "sourceArchiveSha256",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def pe_machine(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError("node.exe is not a PE executable")
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        handle.seek(pe_offset)
        signature_and_machine = handle.read(6)
    if len(signature_and_machine) != 6 or signature_and_machine[:4] != b"PE\0\0":
        raise ValueError("node.exe has an invalid PE signature")
    return struct.unpack_from("<H", signature_and_machine, 4)[0]


def load_approved_sources(path: Path) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read approved Node source manifest: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schemaVersion", "sources"}
        or payload.get("schemaVersion") != 1
        or not isinstance(payload.get("sources"), dict)
    ):
        raise ValueError("approved Node source manifest has an invalid schema")
    result: dict[str, dict[str, str]] = {}
    for version, entry in payload["sources"].items():
        if (
            not isinstance(version, str)
            or parse_version(version) is None
            or not isinstance(entry, dict)
            or set(entry) != APPROVAL_FIELDS
            or any(
                not isinstance(entry[field], str)
                or not SHA256_PATTERN.fullmatch(entry[field])
                for field in APPROVAL_FIELDS
            )
        ):
            raise ValueError(f"invalid approved Node source entry: {version!r}")
        result[version] = dict(entry)
    if not result:
        raise ValueError("approved Node source manifest is empty")
    return result


def validate_distribution(
    root: Path, expected_version: str | None, approval_file: Path
) -> list[str]:
    errors: list[str] = []
    try:
        approved_sources = load_approved_sources(approval_file)
    except ValueError as exc:
        return [str(exc)]
    if is_link_or_reparse(root) or not root.is_dir():
        return [f"distribution is not a regular directory: {root}"]

    try:
        entries = {entry.name: entry for entry in root.iterdir()}
    except OSError as exc:
        return [str(exc)]
    for missing in sorted(ALLOWED_FILES - entries.keys()):
        errors.append(f"missing distribution file: {missing}")
    for unexpected in sorted(entries.keys() - ALLOWED_FILES):
        errors.append(f"unexpected distribution entry: {unexpected}")
    for name in sorted(ALLOWED_FILES & entries.keys()):
        entry = entries[name]
        if is_link_or_reparse(entry) or not entry.is_file():
            errors.append(f"distribution entry is not a regular file: {name}")
    if errors:
        return errors

    try:
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot read VERSION: {exc}"]
    parsed_version = parse_version(version)
    if parsed_version is None:
        errors.append("VERSION must be an exact vMAJOR.MINOR.PATCH value")
    elif parsed_version < MINIMUM_VERSION:
        errors.append(f"Node.js 20.19.0+ is required; distribution contains {version}")
    if expected_version is not None and version != expected_version:
        errors.append(f"distribution version {version} does not match {expected_version}")

    try:
        source = json.loads((root / "SOURCE.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return errors + [f"cannot read SOURCE.json: {exc}"]
    if not isinstance(source, dict):
        return errors + ["SOURCE.json must be an object"]
    if set(source) != {
        "files",
        "officialChecksums",
        "schemaVersion",
        "sourceArchive",
        "target",
        "vendor",
        "version",
    }:
        errors.append("SOURCE.json has unexpected or missing top-level fields")
    if source.get("schemaVersion") != 1 or source.get("vendor") != "Node.js":
        errors.append("SOURCE.json schemaVersion/vendor is invalid")
    if source.get("version") != version:
        errors.append("SOURCE.json version does not match VERSION")
    if source.get("target") != {"arch": "x64", "os": "windows"}:
        errors.append("SOURCE.json target must be windows/x64")

    expected_archive_name = f"node-{version}-win-x64.zip"
    expected_release_root = f"https://nodejs.org/download/release/{version}"
    archive = source.get("sourceArchive")
    if not isinstance(archive, dict) or set(archive) != {"name", "sha256", "url"}:
        errors.append("SOURCE.json sourceArchive is invalid")
    else:
        if archive.get("name") != expected_archive_name:
            errors.append("SOURCE.json source archive name is invalid")
        if archive.get("url") != f"{expected_release_root}/{expected_archive_name}":
            errors.append("SOURCE.json source archive URL is not the official release URL")
        if not isinstance(archive.get("sha256"), str) or not SHA256_PATTERN.fullmatch(
            archive["sha256"]
        ):
            errors.append("SOURCE.json source archive SHA-256 is invalid")

    checksums = source.get("officialChecksums")
    if not isinstance(checksums, dict) or set(checksums) != {"sha256", "url"}:
        errors.append("SOURCE.json officialChecksums is invalid")
    else:
        if checksums.get("url") != f"{expected_release_root}/SHASUMS256.txt":
            errors.append("SOURCE.json checksum URL is not the official release URL")
        if not isinstance(checksums.get("sha256"), str) or not SHA256_PATTERN.fullmatch(
            checksums["sha256"]
        ):
            errors.append("SOURCE.json checksums SHA-256 is invalid")

    files = source.get("files")
    if not isinstance(files, dict) or set(files) != {"LICENSE", "node.exe"}:
        errors.append("SOURCE.json files map is invalid")
    else:
        for name in ("LICENSE", "node.exe"):
            recorded = files.get(name)
            if not isinstance(recorded, str) or not SHA256_PATTERN.fullmatch(recorded):
                errors.append(f"SOURCE.json hash is invalid: {name}")
            elif recorded != sha256(root / name):
                errors.append(f"distribution hash mismatch: {name}")

    approval = approved_sources.get(version)
    if approval is None:
        errors.append(f"Node.js source is not approved by this release: {version}")
    elif isinstance(archive, dict) and isinstance(checksums, dict) and isinstance(files, dict):
        approved_values = {
            "sourceArchiveSha256": archive.get("sha256"),
            "officialChecksumsSha256": checksums.get("sha256"),
            "nodeExeSha256": files.get("node.exe"),
            "licenseSha256": files.get("LICENSE"),
        }
        for field, actual in approved_values.items():
            if actual != approval[field]:
                errors.append(f"Node.js source does not match release approval: {field}")

    try:
        machine = pe_machine(root / "node.exe")
        if machine != PE_AMD64:
            errors.append(f"node.exe PE machine is 0x{machine:04x}, expected AMD64 0x8664")
    except (OSError, ValueError, struct.error) as exc:
        errors.append(str(exc))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--approval-file", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    errors = validate_distribution(
        args.distribution.resolve(), args.expected_version, args.approval_file.resolve()
    )
    if args.as_json:
        print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
    elif errors:
        print("INVALID NODE DISTRIBUTION")
        for error in errors:
            print(f"- {error}")
    else:
        print("VALID NODE DISTRIBUTION")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
