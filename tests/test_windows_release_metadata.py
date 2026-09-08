from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_VERSION = json.loads(
    (ROOT / "runtime" / "package.json").read_text(encoding="utf-8")
)["version"]
SPEC = importlib.util.spec_from_file_location(
    "validate_windows_release_metadata",
    ROOT / "scripts" / "validate_windows_release_metadata.py",
)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class WindowsReleaseMetadataTests(unittest.TestCase):
    def valid_metadata(self) -> dict:
        return {
            "schemaVersion": 1,
            "toolkitVersion": RUNTIME_VERSION,
            "product": "browser-mcp-server",
            "displayName": "浏览器助手",
            "mcpServerName": "browser-mcp",
            "sourcePolicy": "reviewed-allowlist",
            "sourceFileCount": 69,
            "runtime": {
                "archive": (
                    f"browser-agent-runtime-{RUNTIME_VERSION}-core-windows-x64.tar.gz"
                ),
                "sha256": "0" * 64,
                "sizeBytes": 1,
                "buildMetadata": {
                    "schemaVersion": 1,
                    "runtimeVersion": RUNTIME_VERSION,
                    "profile": "core",
                    "buildHost": {"system": "windows", "machine": "x64"},
                    "target": {"system": "windows", "machine": "x64"},
                    "crossBuilt": False,
                    "targetCliSmokeTested": True,
                    "bundledNode": True,
                    "tools": {"node": "v24.19.0", "pnpm": "11.19.0"},
                },
            },
        }

    def test_accepts_exact_windows_native_release_metadata(self) -> None:
        validator.validate_metadata(self.valid_metadata())

    def test_rejects_every_non_release_build_state(self) -> None:
        mutations = (
            ("buildHost", {"system": "linux", "machine": "x64"}),
            ("target", {"system": "windows", "machine": "arm64"}),
            ("crossBuilt", True),
            ("crossBuilt", "false"),
            ("targetCliSmokeTested", False),
            ("targetCliSmokeTested", "true"),
            ("bundledNode", False),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.valid_metadata())
                payload["runtime"]["buildMetadata"][field] = value
                with self.assertRaises(validator.MetadataValidationError):
                    validator.validate_metadata(payload)

    def test_rejects_version_archive_and_tool_drift(self) -> None:
        mutations = (
            ("toolkitVersion", "999.0.0"),
            ("runtimeVersion", "1.0.11"),
            ("archive", "browser-agent-runtime-1.0.12-core-linux-x64.tar.gz"),
            ("node", "v20.18.3"),
            ("pnpm", "11.18.0"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.valid_metadata())
                if field == "toolkitVersion":
                    payload[field] = value
                elif field == "archive":
                    payload["runtime"][field] = value
                elif field in {"node", "pnpm"}:
                    payload["runtime"]["buildMetadata"]["tools"][field] = value
                else:
                    payload["runtime"]["buildMetadata"][field] = value
                with self.assertRaises(validator.MetadataValidationError):
                    validator.validate_metadata(payload)

    def test_cli_accepts_release_and_rejects_cross_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path = Path(temporary) / "KIT-METADATA.json"
            metadata_path.write_text(
                json.dumps(self.valid_metadata()), encoding="utf-8"
            )
            valid = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "validate_windows_release_metadata.py"),
                    str(metadata_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, valid.returncode, msg=valid.stderr)
            self.assertIn("WINDOWS RELEASE METADATA: VALID", valid.stdout)

            payload = self.valid_metadata()
            payload["runtime"]["buildMetadata"]["crossBuilt"] = True
            metadata_path.write_text(json.dumps(payload), encoding="utf-8")
            invalid = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "validate_windows_release_metadata.py"),
                    str(metadata_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, invalid.returncode)
            self.assertIn("crossBuilt must be false", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
