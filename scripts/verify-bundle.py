#!/usr/bin/env python3
"""Verify an extracted browser-mcp-server bundle or archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO


WINDOWS_RESERVED_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)


def digest_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def digest_path(path: Path) -> str:
    with path.open("rb") as handle:
        return digest_stream(handle)


def parse_sums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        if len(line) < 67 or line[64:66] != "  ":
            raise ValueError(f"invalid SHA256SUMS line {line_number}")
        digest, path = line[:64], line[66:]
        if not all(character in "0123456789abcdef" for character in digest):
            raise ValueError(f"invalid SHA-256 on line {line_number}")
        if not safe_relative(path):
            raise ValueError(f"unsafe checksum path on line {line_number}: {path}")
        if path in result:
            raise ValueError(f"duplicate checksum path: {path}")
        result[path] = digest
    if not result:
        raise ValueError("SHA256SUMS is empty")
    return result


def safe_relative(path: str) -> bool:
    pure = PurePosixPath(path)
    if (
        not path
        or path == "."
        or "\x00" in path
        or "\\" in path
        or ":" in path
        or any(ord(character) < 32 or character in '<>"|?*' for character in path)
        or pure.is_absolute()
        or ".." in pure.parts
        or posixpath.normpath(path) != path
    ):
        return False
    return all(
        part
        and not part.endswith((" ", "."))
        and not WINDOWS_RESERVED_NAME.fullmatch(part)
        for part in pure.parts
    )


def link_stays_inside(link_path: str, target: str) -> bool:
    if (
        not target
        or target.startswith("/")
        or "\\" in target
        or ":" in target
        or "\x00" in target
    ):
        return False
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(link_path), target))
    return safe_relative(normalized)


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def load_link_manifest(text: str) -> dict[str, str]:
    data = json.loads(text)
    if data.get("schemaVersion") != 1 or not isinstance(data.get("links"), list):
        raise ValueError("invalid SYMLINKS.json")
    result: dict[str, str] = {}
    for item in data["links"]:
        if not isinstance(item, dict) or set(item) != {"path", "target"}:
            raise ValueError("invalid symlink entry")
        path, target = item["path"], item["target"]
        if not isinstance(path, str) or not isinstance(target, str):
            raise ValueError("invalid symlink path or target")
        if not safe_relative(path) or not link_stays_inside(path, target):
            raise ValueError(f"unsafe symlink: {path} -> {target}")
        if path in result:
            raise ValueError(f"duplicate symlink: {path}")
        result[path] = target
    return result


def verify_directory(root: Path) -> list[str]:
    if is_link_or_reparse(root) or not root.is_dir():
        return [f"bundle root is not a regular directory: {root}"]
    errors: list[str] = []
    try:
        sums_candidates = ("SHA256SUMS.txt", "SHA256SUMS")
        sums_name = next(
            (
                name
                for name in sums_candidates
                if (root / name).is_file() and not is_link_or_reparse(root / name)
            ),
            None,
        )
        if sums_name is None:
            raise ValueError("missing regular SHA256SUMS.txt or SHA256SUMS")
        expected = parse_sums((root / sums_name).read_text(encoding="utf-8"))
        links_path = root / "SYMLINKS.json"
        if links_path.exists() and is_link_or_reparse(links_path):
            raise ValueError("SYMLINKS.json must be a regular file")
        links = load_link_manifest(links_path.read_text(encoding="utf-8")) if links_path.exists() else {}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]

    seen_files: set[str] = set()
    seen_links: dict[str, str] = {}
    seen_portable_names: dict[str, str] = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(dirnames + filenames):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if not safe_relative(relative):
                errors.append(f"unsafe filesystem path: {relative}")
            portable_name = relative.casefold()
            previous_name = seen_portable_names.get(portable_name)
            if previous_name is not None and previous_name != relative:
                errors.append(
                    f"case-colliding filesystem paths: {previous_name} / {relative}"
                )
            seen_portable_names[portable_name] = relative
            if is_link_or_reparse(path):
                try:
                    target = os.readlink(path)
                except OSError as exc:
                    errors.append(f"cannot read link/reparse point {relative}: {exc}")
                    continue
                seen_links[relative] = target
                if not link_stays_inside(relative, target):
                    errors.append(f"unsafe symlink: {relative} -> {target}")
            elif path.is_file() and relative != sums_name:
                seen_files.add(relative)
                if relative not in expected:
                    errors.append(f"unlisted file: {relative}")
                elif digest_path(path) != expected[relative]:
                    errors.append(f"hash mismatch: {relative}")
            elif path.is_dir():
                # Directories are structural entries and are covered by the
                # integrity manifest through their regular-file descendants.
                continue
            elif relative not in {sums_name}:
                errors.append(f"unsupported filesystem entry type: {relative}")

    for missing in sorted(expected.keys() - seen_files):
        errors.append(f"missing file: {missing}")
    if seen_links != links:
        for missing in sorted(links.keys() - seen_links.keys()):
            errors.append(f"missing symlink: {missing}")
        for extra in sorted(seen_links.keys() - links.keys()):
            errors.append(f"unlisted symlink: {extra}")
        for common in sorted(links.keys() & seen_links.keys()):
            if links[common] != seen_links[common]:
                errors.append(f"symlink target mismatch: {common}")
    return errors


def verify_sidecar(archive: Path) -> list[str]:
    if archive.is_symlink() or not archive.is_file():
        return [f"archive is not a regular file: {archive}"]
    sidecar = Path(f"{archive}.sha256")
    if sidecar.is_symlink() or not sidecar.is_file():
        return [f"missing archive checksum sidecar: {sidecar}"]
    try:
        raw = sidecar.read_bytes()
    except OSError as exc:
        return [str(exc)]
    actual = digest_path(archive)
    expected = f"{actual}  {archive.name}\n".encode("utf-8")
    return [] if raw == expected else ["invalid or non-canonical archive checksum sidecar"]


def verify_archive(archive: Path) -> list[str]:
    if archive.suffix.lower() == ".zip":
        return verify_zip_archive(archive)
    errors = verify_sidecar(archive)
    if errors:
        return errors
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if not members:
                return ["archive is empty"]
            top_levels = {PurePosixPath(member.name).parts[0] for member in members if member.name}
            if len(top_levels) != 1:
                return ["archive must contain exactly one top-level directory"]
            top_level = next(iter(top_levels))
            if not safe_relative(top_level):
                return [f"unsafe archive top-level directory: {top_level}"]
            prefix = top_level + "/"
            by_relative: dict[str, tarfile.TarInfo] = {}
            by_portable_name: dict[str, str] = {}
            for member in members:
                if member.name == prefix[:-1]:
                    if not member.isdir():
                        errors.append("archive top-level entry must be a directory")
                    continue
                if not member.name.startswith(prefix):
                    errors.append(f"member escapes bundle root: {member.name}")
                    continue
                relative = member.name[len(prefix):]
                if not safe_relative(relative):
                    errors.append(f"unsafe archive member: {member.name}")
                    continue
                if member.isdev() or member.isfifo() or member.islnk():
                    errors.append(f"unsupported archive member type: {member.name}")
                    continue
                if relative in by_relative:
                    errors.append(f"duplicate archive member: {relative}")
                portable_name = relative.casefold()
                previous_name = by_portable_name.get(portable_name)
                if previous_name is not None and previous_name != relative:
                    errors.append(
                        f"case-colliding archive members: {previous_name} / {relative}"
                    )
                by_portable_name[portable_name] = relative
                by_relative[relative] = member

            sums_member = by_relative.get("SHA256SUMS")
            links_member = by_relative.get("SYMLINKS.json")
            if sums_member is None or links_member is None:
                return errors + ["archive is missing SHA256SUMS or SYMLINKS.json"]
            if not sums_member.isfile() or not links_member.isfile():
                return errors + [
                    "archive integrity manifests must be regular files"
                ]
            sums_file = tar.extractfile(sums_member)
            links_file = tar.extractfile(links_member)
            if sums_file is None or links_file is None:
                return errors + ["integrity manifests are not regular files"]
            expected = parse_sums(sums_file.read().decode("utf-8"))
            links = load_link_manifest(links_file.read().decode("utf-8"))

            seen_files: set[str] = set()
            seen_links: dict[str, str] = {}
            for relative, member in by_relative.items():
                if member.isdir() or relative == "SHA256SUMS":
                    continue
                if member.issym():
                    seen_links[relative] = member.linkname
                    if not link_stays_inside(relative, member.linkname):
                        errors.append(f"unsafe symlink: {relative} -> {member.linkname}")
                    continue
                if not member.isfile():
                    errors.append(f"unsupported archive member type: {relative}")
                    continue
                seen_files.add(relative)
                if relative not in expected:
                    errors.append(f"unlisted file: {relative}")
                    continue
                handle = tar.extractfile(member)
                if handle is None or digest_stream(handle) != expected[relative]:
                    errors.append(f"hash mismatch: {relative}")

            for missing in sorted(expected.keys() - seen_files):
                errors.append(f"missing file: {missing}")
            if seen_links != links:
                for missing in sorted(links.keys() - seen_links.keys()):
                    errors.append(f"missing symlink: {missing}")
                for extra in sorted(seen_links.keys() - links.keys()):
                    errors.append(f"unlisted symlink: {extra}")
                for common in sorted(links.keys() & seen_links.keys()):
                    if links[common] != seen_links[common]:
                        errors.append(f"symlink target mismatch: {common}")
    except (OSError, tarfile.TarError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return sorted(set(errors))


def _zip_member_is_symlink(member: zipfile.ZipInfo) -> bool:
    mode = (member.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _zip_member_is_special(member: zipfile.ZipInfo) -> bool:
    """Return true for device, FIFO, or other non-regular POSIX members."""
    mode = (member.external_attr >> 16) & 0o170000
    return mode not in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}


def verify_zip_archive(archive: Path) -> list[str]:
    """Verify a single-top-level-directory ZIP with an in-archive checksum list.

    The in-archive manifest is sufficient for an extracted package.  When an
    adjacent ``.sha256`` exists, it is checked as the canonical UTF-8/LF
    sidecar; the public release gate requires that sidecar to be present.
    """
    errors: list[str] = []
    if archive.is_symlink() or not archive.is_file():
        return [f"archive is not a regular file: {archive}"]
    sidecar = Path(f"{archive}.sha256")
    if sidecar.is_symlink() or sidecar.exists():
        errors.extend(verify_sidecar(archive))
        if errors:
            return sorted(set(errors))
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            if not infos:
                return ["archive is empty"]
            top_levels = {
                PurePosixPath(info.filename).parts[0]
                for info in infos
                if info.filename
            }
            if len(top_levels) != 1:
                return ["archive must contain exactly one top-level directory"]
            top_level = next(iter(top_levels))
            if not safe_relative(top_level):
                return [f"unsafe archive top-level directory: {top_level}"]
            prefix = top_level + "/"
            by_relative: dict[str, zipfile.ZipInfo] = {}
            by_portable_name: dict[str, str] = {}
            for info in infos:
                # ZIP member names are always POSIX paths.  Silently converting
                # backslashes would make a package verify on Unix but resolve to
                # a different path after extraction on Windows.
                if "\\" in info.filename or "\x00" in info.filename:
                    errors.append(f"unsafe archive member: {info.filename}")
                    continue
                name = info.filename
                if name == top_level or name == prefix:
                    if not info.is_dir():
                        errors.append("archive top-level entry must be a directory")
                    continue
                if not name.startswith(prefix):
                    errors.append(f"member escapes bundle root: {info.filename}")
                    continue
                relative = name[len(prefix):]
                if info.is_dir() and relative.endswith("/"):
                    relative = relative.rstrip("/")
                if not safe_relative(relative):
                    errors.append(f"unsafe archive member: {info.filename}")
                    continue
                if _zip_member_is_symlink(info):
                    errors.append(f"unsupported archive member type: {info.filename}")
                    continue
                if _zip_member_is_special(info):
                    errors.append(f"unsupported archive member type: {info.filename}")
                    continue
                if relative in by_relative:
                    errors.append(f"duplicate archive member: {relative}")
                portable_name = relative.casefold()
                previous_name = by_portable_name.get(portable_name)
                if previous_name is not None and previous_name != relative:
                    errors.append(
                        f"case-colliding archive members: {previous_name} / {relative}"
                    )
                by_portable_name[portable_name] = relative
                by_relative[relative] = info

            sums_member = by_relative.get("SHA256SUMS.txt") or by_relative.get("SHA256SUMS")
            if sums_member is None:
                return errors + ["archive is missing SHA256SUMS.txt"]
            if sums_member.is_dir() or _zip_member_is_symlink(sums_member):
                return errors + ["archive checksum manifest must be a regular file"]
            expected = parse_sums(bundle.read(sums_member).decode("utf-8"))
            links_member = by_relative.get("SYMLINKS.json")
            links = {}
            if links_member is not None:
                if links_member.is_dir() or _zip_member_is_symlink(links_member):
                    errors.append("archive symlink manifest must be a regular file")
                else:
                    links = load_link_manifest(bundle.read(links_member).decode("utf-8"))

            seen_files: set[str] = set()
            for relative, info in by_relative.items():
                if info.is_dir() or relative in {"SHA256SUMS.txt", "SHA256SUMS"}:
                    continue
                if _zip_member_is_symlink(info):
                    continue
                if info.is_dir():
                    errors.append(f"unsupported archive member type: {relative}")
                    continue
                seen_files.add(relative)
                if relative not in expected:
                    errors.append(f"unlisted file: {relative}")
                    continue
                if hashlib.sha256(bundle.read(info)).hexdigest() != expected[relative]:
                    errors.append(f"hash mismatch: {relative}")

            for missing in sorted(expected.keys() - seen_files):
                errors.append(f"missing file: {missing}")
            if links:
                errors.append("archive contains unsupported symlink manifest")
    except (OSError, RuntimeError, zipfile.BadZipFile, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    if args.bundle.is_dir():
        errors = verify_directory(args.bundle)
    elif args.bundle.is_file():
        errors = verify_archive(args.bundle)
    else:
        errors = [f"not found: {args.bundle}"]
    if args.as_json:
        print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
    elif errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
    else:
        print("VALID")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
