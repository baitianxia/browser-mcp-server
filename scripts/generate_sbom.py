#!/usr/bin/env python3
"""Generate a minimal CycloneDX 1.5 component inventory from node_modules."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


def package_directories(node_modules: Path) -> Iterable[Path]:
    seen: set[tuple[str, str]] = set()
    for package_json in sorted(node_modules.rglob("package.json")):
        if package_json.is_symlink() or ".bin" in package_json.parts:
            continue
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("name")
        version = data.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        identity = (name, version)
        if identity in seen:
            continue
        seen.add(identity)
        yield package_json


def license_entries(value: Any) -> list[dict[str, Any]]:
    values: list[str] = []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict) and isinstance(item.get("type"), str):
                values.append(item["type"])
    elif isinstance(value, dict) and isinstance(value.get("type"), str):
        values = [value["type"]]
    result = []
    for item in sorted(set(values)):
        if re_is_spdx(item):
            result.append({"license": {"id": item}})
        else:
            result.append({"license": {"name": item}})
    return result


def re_is_spdx(value: str) -> bool:
    return bool(value) and all(character.isalnum() or character in ".-+" for character in value)


def purl(name: str, version: str) -> str:
    if name.startswith("@") and "/" in name:
        scope, package = name[1:].split("/", 1)
        return f"pkg:npm/{quote('@' + scope, safe='')}/{quote(package, safe='')}@{quote(version, safe='')}"
    return f"pkg:npm/{quote(name, safe='')}@{quote(version, safe='')}"


def generate(node_modules: Path) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    for package_json in package_directories(node_modules):
        data = json.loads(package_json.read_text(encoding="utf-8"))
        raw = package_json.read_bytes()
        name = data["name"]
        version = data["version"]
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": purl(name, version),
            "name": name,
            "version": version,
            "purl": purl(name, version),
            "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(raw).hexdigest()}],
        }
        licenses = license_entries(data.get("license") or data.get("licenses"))
        if licenses:
            component["licenses"] = licenses
        components.append(component)
    components.sort(key=lambda item: (item["name"], item["version"]))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "intranet-browser-agent-runtime",
                "version": "1.0.10",
            }
        },
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-modules", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.node_modules.is_dir():
        parser.error(f"not a directory: {args.node_modules}")
    payload = json.dumps(generate(args.node_modules), indent=2, sort_keys=True) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
