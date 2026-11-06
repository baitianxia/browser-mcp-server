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
                    "bundledNode": False,
                    "nodeRuntime": {"source": "system", "minimumVersion": "20.12.0"},
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
            ("bundledNode", True),
            ("bundledNode", None),
            ("nodeRuntime", None),
            ("nodeRuntime", {"source": "bundled", "minimumVersion": "20.12.0"}),
            ("nodeRuntime", {"source": "system", "minimumVersion": "20.19.0"}),
            ("nodeRuntime", {"source": "system", "minimumVersion": "20.18.3"}),
            ("nodeRuntime", {"source": "system", "minimumVersion": "20.12"}),
            (
                "nodeRuntime",
                {"source": "system", "minimumVersion": "20.12.0", "path": "node.exe"},
            ),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.valid_metadata())
                payload["runtime"]["buildMetadata"][field] = value
                with self.assertRaises(validator.MetadataValidationError):
                    validator.validate_metadata(payload)

    def valid_public_manifest(self) -> dict:
        build = copy.deepcopy(self.valid_metadata()["runtime"]["buildMetadata"])
        return {
            "schemaVersion": 1,
            "product": "browser-mcp-server",
            "displayName": "浏览器助手",
            "mcpServerName": "browser-mcp",
            "version": RUNTIME_VERSION,
            "target": {"system": "windows", "machine": "x64"},
            "package": {
                "name": f"browser-mcp-server-{RUNTIME_VERSION}-windows-x64",
                "format": "zip",
                "publicDelivery": "zip-only",
                "singleTopLevelDirectory": True,
                "entrypoint": "INSTALL.cmd",
                "configureEntrypoint": "CONFIGURE.cmd",
                "openConfigEntrypoint": "OPEN-CONFIG.cmd",
                "uninstallEntrypoint": "UNINSTALL.cmd",
                "checksumManifest": "SHA256SUMS.txt",
            },
            "paths": {
                "installRoot": r"%USERPROFILE%\browser-mcp-server",
                "settings": r"%USERPROFILE%\browser-mcp-server\config\settings.json",
            },
            "traceability": {
                "runtimeArchive": (
                    f"browser-agent-runtime-{RUNTIME_VERSION}-core-windows-x64.tar.gz"
                ),
                "runtimeSha256": "0" * 64,
                "sourceFileCount": 1,
                "sourcePolicy": "reviewed-allowlist",
                "buildMetadata": build,
            },
        }

    def test_public_manifest_requires_existing_system_node(self) -> None:
        validator.validate_public_manifest(self.valid_public_manifest())
        for field, value in (
            ("bundledNode", True),
            ("nodeRuntime", {"source": "bundled", "minimumVersion": "20.12.0"}),
        ):
            with self.subTest(field=field):
                payload = self.valid_public_manifest()
                payload["traceability"]["buildMetadata"][field] = value
                with self.assertRaisesRegex(
                    validator.MetadataValidationError, field
                ):
                    validator.validate_public_manifest(payload)

    def test_system_node_floor_matches_runtime_and_compatibility_layer(self) -> None:
        # One release floor: validator, preflight, package engines, and the
        # MCP entry guard must agree.
        sys.path.insert(0, str(ROOT / "tools"))
        import browser_agent  # noqa: PLC0415

        floor = validator.SYSTEM_NODE_RUNTIME["minimumVersion"]
        self.assertEqual(floor, ".".join(map(str, browser_agent.MIN_NODE_VERSION)))
        package = json.loads(
            (ROOT / "runtime" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(f">={floor}", package["engines"]["node"])
        entry = (ROOT / "runtime" / "bin" / "intranet-browser-agent-mcp.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "var MINIMUM_NODE_VERSION = [" + floor.replace(".", ", ") + "];", entry
        )
        # The transfer-kit text fallback and the CI floor Node.js (which runs
        # the compatibility suite and browser E2E) must name the same floor.
        transfer_kit = (ROOT / "scripts" / "build-transfer-kit.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'else "{floor}"', transfer_kit)
        workflow = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'FLOOR_NODE_VERSION: "{floor}"', workflow)
        self.assertIn("node-version: ${{ env.FLOOR_NODE_VERSION }}", workflow)
        self.assertIn("--node-executable $env:FLOOR_NODE", workflow)
        self.assertIn(
            "$NodeRuntime.minimumVersion -ne $env:FLOOR_NODE_VERSION", workflow
        )

    def test_build_metadata_records_existing_node_and_refuses_windows_bundle(self) -> None:
        script = ROOT / "scripts" / "write_build_metadata.py"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "BUILD-METADATA.json"
            # No --cross-built: on the Windows x64 release runners the target
            # equals the build host, and the script rejects that combination.
            base = [
                sys.executable,
                str(script),
                "--output",
                str(output),
                "--node-version",
                "v24.19.0",
                "--pnpm-version",
                "11.19.0",
                "--target-system",
                "windows",
                "--target-machine",
                "x64",
            ]
            core = subprocess.run(
                base + ["--profile", "core"], check=False, capture_output=True, text=True
            )
            self.assertEqual(0, core.returncode, msg=core.stderr)
            metadata = json.loads(output.read_text(encoding="utf-8"))
            self.assertIs(False, metadata["bundledNode"])
            self.assertEqual(validator.SYSTEM_NODE_RUNTIME, metadata["nodeRuntime"])
            self.assertEqual(RUNTIME_VERSION, metadata["runtimeVersion"])

            diagnostic = subprocess.run(
                base + ["--profile", "diagnostic"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, diagnostic.returncode, msg=diagnostic.stderr)
            self.assertEqual(
                "20.19.0",
                json.loads(output.read_text(encoding="utf-8"))["nodeRuntime"][
                    "minimumVersion"
                ],
            )

            bundled = subprocess.run(
                base + ["--profile", "core", "--bundled-node"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, bundled.returncode)
            self.assertIn("--bundled-node is not allowed", bundled.stderr)

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

    def test_rejects_invalid_extension_compatibility_allowlist(self) -> None:
        payload = self.valid_metadata()
        payload["browserExtension"] = {
            "extensionId": "a" * 32,
            "version": "0.4.0",
            "compatibleVersions": ["0.3.0"],
            "filename": "playwright-extension-0.4.0.crx",
            "sizeBytes": 1,
            "sha256": "0" * 64,
            "path": "browser-extension/playwright-extension-0.4.0.crx",
            "unpackedPath": "browser-extension/unpacked",
            "installation": "offline-user-policy-with-manual-unpacked-fallback",
        }
        with self.assertRaisesRegex(
            validator.MetadataValidationError, "compatibleVersions"
        ):
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
