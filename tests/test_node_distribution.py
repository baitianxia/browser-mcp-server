from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_node_distribution", ROOT / "scripts" / "validate_node_distribution.py"
)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
validator = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(validator)


def minimal_pe(machine: int = 0x8664) -> bytes:
    payload = bytearray(512)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, machine)
    return bytes(payload)


class NodeDistributionTests(unittest.TestCase):
    def source_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        archive = root / "node-v24.19.0-win-x64.zip"
        node_bytes = minimal_pe()
        license_bytes = b"fixture license\n"
        with zipfile.ZipFile(archive, "w") as bundle:
            prefix = "node-v24.19.0-win-x64"
            bundle.writestr(f"{prefix}/node.exe", node_bytes)
            bundle.writestr(f"{prefix}/LICENSE", license_bytes)
            bundle.writestr(f"{prefix}/npm.cmd", b"must not be copied\n")
        archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        node_hash = hashlib.sha256(node_bytes).hexdigest()
        shasums = root / "SHASUMS256.txt"
        shasums.write_text(
            f"{archive_hash}  {archive.name}\n{node_hash}  win-x64/node.exe\n",
            encoding="utf-8",
        )
        approval = root / "windows-node-sources.json"
        approval.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "sources": {
                        "v24.19.0": {
                            "licenseSha256": hashlib.sha256(license_bytes).hexdigest(),
                            "nodeExeSha256": node_hash,
                            "officialChecksumsSha256": hashlib.sha256(
                                shasums.read_bytes()
                            ).hexdigest(),
                            "sourceArchiveSha256": archive_hash,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return archive, shasums, approval

    def prepare(self, root: Path) -> Path:
        archive, shasums, approval = self.source_fixture(root)
        output = root / "portable-node"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "prepare_windows_node_distribution.py"),
                "--archive",
                str(archive),
                "--shasums",
                str(shasums),
                "--output",
                str(output),
                "--approval-file",
                str(approval),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return output

    def test_prepares_exact_minimal_official_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self.prepare(Path(temporary))
            self.assertEqual(
                {"LICENSE", "SOURCE.json", "VERSION", "node.exe"},
                {entry.name for entry in output.iterdir()},
            )
            self.assertFalse((output / "npm.cmd").exists())
            approval = Path(temporary) / "windows-node-sources.json"
            self.assertEqual(
                [],
                validator.validate_distribution(output, "v24.19.0", approval),
            )
            source = json.loads((output / "SOURCE.json").read_text(encoding="utf-8"))
            self.assertEqual(
                "https://nodejs.org/download/release/v24.19.0/node-v24.19.0-win-x64.zip",
                source["sourceArchive"]["url"],
            )

    def test_rejects_tampering_and_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self.prepare(Path(temporary))
            (output / "node.exe").write_bytes(b"tampered")
            (output / "npm.cmd").write_text("forbidden\n", encoding="utf-8")
            errors = validator.validate_distribution(
                output, "v24.19.0", Path(temporary) / "windows-node-sources.json"
            )
            self.assertTrue(any("unexpected distribution entry: npm.cmd" in error for error in errors))

    def test_rejects_non_amd64_pe_even_with_matching_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self.prepare(Path(temporary))
            wrong_node = minimal_pe(machine=0xAA64)
            (output / "node.exe").write_bytes(wrong_node)
            source_path = output / "SOURCE.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["files"]["node.exe"] = hashlib.sha256(wrong_node).hexdigest()
            source_path.write_text(json.dumps(source), encoding="utf-8")
            errors = validator.validate_distribution(
                output, "v24.19.0", Path(temporary) / "windows-node-sources.json"
            )
            self.assertTrue(any("expected AMD64" in error for error in errors))

    def test_rejects_self_consistent_but_unapproved_node_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self.prepare(root)
            replacement = minimal_pe() + b"unapproved"
            (output / "node.exe").write_bytes(replacement)
            source_path = output / "SOURCE.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["files"]["node.exe"] = hashlib.sha256(replacement).hexdigest()
            source_path.write_text(json.dumps(source), encoding="utf-8")
            errors = validator.validate_distribution(
                output, "v24.19.0", root / "windows-node-sources.json"
            )
            self.assertTrue(
                any("does not match release approval: nodeExeSha256" in error for error in errors)
            )

    def test_rejects_archive_with_wrong_official_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, shasums, approval = self.source_fixture(root)
            text = shasums.read_text(encoding="utf-8")
            shasums.write_text("0" * 64 + text[64:], encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "prepare_windows_node_distribution.py"),
                    "--archive",
                    str(archive),
                    "--shasums",
                    str(shasums),
                    "--output",
                    str(root / "portable-node"),
                    "--approval-file",
                    str(approval),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("source archive SHA-256 mismatch", result.stdout)


if __name__ == "__main__":
    unittest.main()
