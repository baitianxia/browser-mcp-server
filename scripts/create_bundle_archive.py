#!/usr/bin/env python3
"""Create a safe tar.gz archive with regular files materialized independently."""

from __future__ import annotations

import argparse
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def archive_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return str(PurePosixPath(root.name, *relative.parts))


def create_archive(root: Path, output: Path, reject_links: bool) -> None:
    root = root.resolve()
    output = output.resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    if output.exists():
        raise ValueError(f"refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    entries = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT, dereference=False) as bundle:
        for path in entries:
            linked = is_link_or_reparse(path)
            if linked and reject_links:
                raise ValueError(f"link/reparse point is forbidden: {path}")
            info = bundle.gettarinfo(str(path), arcname=archive_name(root, path))
            if linked:
                if not info.issym():
                    raise ValueError(f"unsupported reparse point: {path}")
                bundle.addfile(info)
            elif path.is_dir():
                bundle.addfile(info)
            elif path.is_file():
                info.type = tarfile.REGTYPE
                info.linkname = ""
                with path.open("rb") as handle:
                    bundle.addfile(info, handle)
            else:
                raise ValueError(f"unsupported filesystem entry: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reject-links", action="store_true")
    args = parser.parse_args()
    try:
        create_archive(args.root, args.output, args.reject_links)
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
