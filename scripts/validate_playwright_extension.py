#!/usr/bin/env python3
"""Validate the exact approved Playwright Extension CRX for offline transfer."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import stat
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_APPROVAL = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "playwright-extension-source.json"
)
APPROVAL_KEYS = {
    "schemaVersion",
    "extensionId",
    "version",
    "filename",
    "sizeBytes",
    "sha256",
    "downloadUrl",
    "webStoreUrl",
    "updateUrl",
    "publisher",
    "license",
    "sourceUrl",
    "retrievedAt",
    "manifestKey",
    "requiredPermissions",
    "requiredHostPermissions",
}


class ExtensionValidationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extension_id_from_manifest_key(value: str) -> str:
    try:
        public_key = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ExtensionValidationError("manifest key is not valid base64") from exc
    prefix = hashlib.sha256(public_key).digest()[:16].hex()
    return "".join(chr(ord("a") + int(character, 16)) for character in prefix)


def load_approval(path: Path = DEFAULT_APPROVAL) -> dict[str, Any]:
    try:
        approval = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionValidationError(f"cannot read extension approval: {exc}") from exc
    if not isinstance(approval, dict) or set(approval) != APPROVAL_KEYS:
        raise ExtensionValidationError("extension approval has an invalid root shape")
    if approval.get("schemaVersion") != 1:
        raise ExtensionValidationError("unsupported extension approval schema")
    for name in (
        "extensionId",
        "version",
        "filename",
        "sha256",
        "downloadUrl",
        "webStoreUrl",
        "updateUrl",
        "publisher",
        "license",
        "sourceUrl",
        "retrievedAt",
        "manifestKey",
    ):
        if not isinstance(approval.get(name), str) or not approval[name]:
            raise ExtensionValidationError(f"invalid extension approval field: {name}")
    if not isinstance(approval.get("sizeBytes"), int) or approval["sizeBytes"] <= 0:
        raise ExtensionValidationError("invalid approved extension size")
    if len(approval["sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in approval["sha256"]
    ):
        raise ExtensionValidationError("invalid approved extension SHA-256")
    if extension_id_from_manifest_key(approval["manifestKey"]) != approval["extensionId"]:
        raise ExtensionValidationError("approved manifest key does not derive the extension ID")
    for name in ("requiredPermissions", "requiredHostPermissions"):
        value = approval.get(name)
        if not isinstance(value, list) or not value or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise ExtensionValidationError(f"invalid extension approval field: {name}")
    return approval


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def validate_crx(path: Path, approval: dict[str, Any]) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ExtensionValidationError(f"extension CRX is not a regular file: {path}")
    if path.name != approval["filename"]:
        raise ExtensionValidationError(
            f"extension filename must be {approval['filename']}: {path.name}"
        )
    actual_size = path.stat().st_size
    if actual_size != approval["sizeBytes"]:
        raise ExtensionValidationError(
            f"extension size mismatch: {actual_size} != {approval['sizeBytes']}"
        )
    actual_sha256 = sha256(path)
    if actual_sha256 != approval["sha256"]:
        raise ExtensionValidationError(
            f"extension SHA-256 mismatch: {actual_sha256} != {approval['sha256']}"
        )
    with path.open("rb") as handle:
        prefix = handle.read(12)
    if len(prefix) != 12 or prefix[:4] != b"Cr24":
        raise ExtensionValidationError("extension is not a CRX file")
    version, header_size = struct.unpack("<II", prefix[4:])
    if version != 3:
        raise ExtensionValidationError(f"extension must use CRX3; found CRX{version}")
    if header_size <= 0 or 12 + header_size >= actual_size:
        raise ExtensionValidationError("extension has an invalid CRX3 header size")

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or any(not _safe_archive_name(name) for name in names):
                raise ExtensionValidationError("extension contains an unsafe archive path")
            for item in archive.infolist():
                file_type = (item.external_attr >> 16) & 0o170000
                if file_type == stat.S_IFLNK:
                    raise ExtensionValidationError("extension archive contains a symlink")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if "_metadata/verified_contents.json" not in names:
                raise ExtensionValidationError(
                    "extension is missing Chrome Web Store verified contents"
                )
    except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ExtensionValidationError(f"cannot inspect extension CRX payload: {exc}") from exc

    expected_manifest = {
        "manifest_version": 3,
        "name": "Playwright Extension",
        "version": approval["version"],
        "update_url": approval["updateUrl"],
        "key": approval["manifestKey"],
        "permissions": approval["requiredPermissions"],
        "host_permissions": approval["requiredHostPermissions"],
    }
    for name, expected in expected_manifest.items():
        if manifest.get(name) != expected:
            raise ExtensionValidationError(f"extension manifest field {name} is not approved")
    actual_id = extension_id_from_manifest_key(manifest["key"])
    if actual_id != approval["extensionId"]:
        raise ExtensionValidationError(
            f"extension ID mismatch: {actual_id} != {approval['extensionId']}"
        )
    return {
        "extensionId": actual_id,
        "version": manifest["version"],
        "sha256": actual_sha256,
        "sizeBytes": actual_size,
        "filename": path.name,
    }


def extract_crx_payload(crx: Path, destination: Path) -> None:
    if destination.exists():
        raise ExtensionValidationError(f"refusing to overwrite unpacked extension: {destination}")
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(crx) as archive:
            for item in archive.infolist():
                if not _safe_archive_name(item.filename):
                    raise ExtensionValidationError(
                        f"extension contains an unsafe archive path: {item.filename}"
                    )
                target = destination.joinpath(*PurePosixPath(item.filename).parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def validate_unpacked_against_crx(crx: Path, directory: Path) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise ExtensionValidationError(
            f"unpacked extension is not a regular directory: {directory}"
        )
    try:
        with zipfile.ZipFile(crx) as archive:
            expected = {
                item.filename: hashlib.sha256(archive.read(item)).hexdigest()
                for item in archive.infolist()
                if not item.is_dir()
            }
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExtensionValidationError(f"cannot read CRX payload: {exc}") from exc
    actual: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ExtensionValidationError(
                f"unpacked extension contains a symlink: {path}"
            )
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            if not _safe_archive_name(relative):
                raise ExtensionValidationError(
                    f"unpacked extension contains an unsafe path: {relative}"
                )
            actual[relative] = sha256(path)
    if actual != expected:
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        changed = sorted(
            name
            for name in expected.keys() & actual.keys()
            if expected[name] != actual[name]
        )
        details = []
        if missing:
            details.append("missing=" + ",".join(missing[:5]))
        if extra:
            details.append("extra=" + ",".join(extra[:5]))
        if changed:
            details.append("changed=" + ",".join(changed[:5]))
        raise ExtensionValidationError(
            "unpacked extension does not match approved CRX payload: "
            + "; ".join(details)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("crx", type=Path)
    parser.add_argument("--approval-file", type=Path, default=DEFAULT_APPROVAL)
    parser.add_argument("--unpacked-directory", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        result = validate_crx(args.crx, load_approval(args.approval_file))
        if args.unpacked_directory is not None:
            validate_unpacked_against_crx(args.crx, args.unpacked_directory)
    except ExtensionValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "VALID PLAYWRIGHT EXTENSION "
            f"{result['extensionId']} {result['version']} {result['sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
