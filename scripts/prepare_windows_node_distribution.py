#!/usr/bin/env python3
"""Prepare a minimal, provenance-recorded Node.js Windows x64 distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from validate_node_distribution import sha256, validate_distribution


ARCHIVE_PATTERN = re.compile(r"^node-(v\d+\.\d+\.\d+)-win-x64\.zip$")


class PreparationError(RuntimeError):
    pass


def parse_shasums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        if len(line) < 67 or line[64:66] != "  ":
            raise PreparationError(f"invalid SHASUMS256 line {number}")
        digest, name = line[:64], line[66:]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PreparationError(f"invalid SHA-256 on SHASUMS256 line {number}")
        if name in result:
            raise PreparationError(f"duplicate SHASUMS256 path: {name}")
        result[name] = digest
    if not result:
        raise PreparationError("SHASUMS256 is empty")
    return result


def zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def read_exact_member(bundle: zipfile.ZipFile, name: str) -> bytes:
    matches = [info for info in bundle.infolist() if info.filename == name]
    if len(matches) != 1:
        raise PreparationError(f"archive must contain exactly one {name}")
    info = matches[0]
    if info.is_dir() or zip_entry_is_symlink(info) or (info.flag_bits & 0x1):
        raise PreparationError(f"archive member is not an unencrypted regular file: {name}")
    return bundle.read(info)


def prepare_distribution(
    archive: Path, shasums_path: Path, output: Path, approval_file: Path
) -> Path:
    archive = archive.resolve()
    shasums_path = shasums_path.resolve()
    output = output.resolve()
    match = ARCHIVE_PATTERN.fullmatch(archive.name)
    if not match:
        raise PreparationError("archive name must match node-vMAJOR.MINOR.PATCH-win-x64.zip")
    version = match.group(1)
    if output.exists() or output.is_symlink():
        raise PreparationError(f"refusing to overwrite output: {output}")
    try:
        shasums_text = shasums_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PreparationError(f"cannot read SHASUMS256: {exc}") from exc
    sums = parse_shasums(shasums_text)
    expected_archive_hash = sums.get(archive.name)
    if expected_archive_hash is None:
        raise PreparationError(f"SHASUMS256 does not list {archive.name}")
    actual_archive_hash = sha256(archive)
    if actual_archive_hash != expected_archive_hash:
        raise PreparationError("Node.js source archive SHA-256 mismatch")
    expected_node_hash = sums.get("win-x64/node.exe")
    if expected_node_hash is None:
        raise PreparationError("SHASUMS256 does not list win-x64/node.exe")

    root_name = archive.name.removesuffix(".zip")
    try:
        with zipfile.ZipFile(archive) as bundle:
            node_bytes = read_exact_member(bundle, f"{root_name}/node.exe")
            license_bytes = read_exact_member(bundle, f"{root_name}/LICENSE")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PreparationError(f"cannot read Node.js source archive: {exc}") from exc
    actual_node_hash = hashlib.sha256(node_bytes).hexdigest()
    if actual_node_hash != expected_node_hash:
        raise PreparationError("node.exe does not match the official standalone checksum")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (temporary / "node.exe").write_bytes(node_bytes)
        (temporary / "LICENSE").write_bytes(license_bytes)
        (temporary / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        release_root = f"https://nodejs.org/download/release/{version}"
        source = {
            "schemaVersion": 1,
            "vendor": "Node.js",
            "version": version,
            "target": {"os": "windows", "arch": "x64"},
            "sourceArchive": {
                "name": archive.name,
                "url": f"{release_root}/{archive.name}",
                "sha256": actual_archive_hash,
            },
            "officialChecksums": {
                "url": f"{release_root}/SHASUMS256.txt",
                "sha256": sha256(shasums_path),
            },
            "files": {
                "LICENSE": sha256(temporary / "LICENSE"),
                "node.exe": actual_node_hash,
            },
        }
        (temporary / "SOURCE.json").write_text(
            json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        errors = validate_distribution(
            temporary, expected_version=version, approval_file=approval_file
        )
        if errors:
            raise PreparationError("prepared distribution is invalid: " + "; ".join(errors))
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--shasums", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approval-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        output = prepare_distribution(
            args.archive, args.shasums, args.output, args.approval_file
        )
    except PreparationError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Prepared {output}")
    print("Included only node.exe, LICENSE, VERSION, and SOURCE.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
