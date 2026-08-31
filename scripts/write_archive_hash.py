#!/usr/bin/env python3
"""Write a SHA-256 sidecar for an archive."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    digest = hashlib.sha256()
    with args.archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    Path(f"{args.archive}.sha256").write_bytes(
        f"{digest.hexdigest()}  {args.archive.name}\n".encode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
