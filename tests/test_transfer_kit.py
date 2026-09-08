from __future__ import annotations

import importlib.util
import io
import hashlib
import json
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_VERSION = json.loads(
    (ROOT / "runtime" / "package.json").read_text(encoding="utf-8")
)["version"]
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verify_bundle_for_transfer", ROOT / "scripts" / "verify-bundle.py"
)
assert VERIFY_SPEC and VERIFY_SPEC.loader
verify_bundle = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(verify_bundle)
BUILD_SPEC = importlib.util.spec_from_file_location(
    "build_transfer_kit_for_tests", ROOT / "scripts" / "build-transfer-kit.py"
)
assert BUILD_SPEC and BUILD_SPEC.loader
build_transfer = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(build_transfer)


class TransferKitTests(unittest.TestCase):
    def make_extension(self, root: Path) -> tuple[Path, dict]:
        approval = json.loads(
            (ROOT / "config" / "playwright-extension-source.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = {
            "manifest_version": 3,
            "name": "Playwright Extension",
            "version": approval["version"],
            "update_url": approval["updateUrl"],
            "key": approval["manifestKey"],
            "permissions": approval["requiredPermissions"],
            "host_permissions": approval["requiredHostPermissions"],
        }
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest))
            bundle.writestr("_metadata/verified_contents.json", "[]")
        header = b"transfer-test-crx3"
        content = b"Cr24" + struct.pack("<II", 3, len(header)) + header + payload.getvalue()
        extension = root / approval["filename"]
        extension.write_bytes(content)
        approval["sizeBytes"] = len(content)
        approval["sha256"] = hashlib.sha256(content).hexdigest()
        return extension, approval

    def make_runtime(
        self,
        root: Path,
        target_system: str = "linux",
        target_machine: str = "x64",
        bundled_node: bool | None = None,
        cross_built: bool | None = None,
    ) -> Path:
        runtime_name = (
            f"browser-agent-runtime-{RUNTIME_VERSION}-core-{target_system}-{target_machine}"
        )
        runtime_root = root / runtime_name
        runtime_root.mkdir()
        is_cross_built = (
            target_system.lower() == "windows"
            if cross_built is None
            else cross_built
        )
        build_host = (
            {"system": "linux", "machine": "x64"}
            if is_cross_built
            else {"system": target_system, "machine": target_machine}
        )
        (runtime_root / "BUILD-METADATA.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "runtimeVersion": RUNTIME_VERSION,
                    "profile": "core",
                    "buildHost": build_host,
                    "target": {"system": target_system, "machine": target_machine},
                    "crossBuilt": is_cross_built,
                    "targetCliSmokeTested": not is_cross_built,
                    "tools": {"node": "v24.0.0", "pnpm": "11.19.0"},
                    "bundledNode": (
                        target_system.lower() == "windows"
                        if bundled_node is None
                        else bundled_node
                    ),
                    "sourceDateEpoch": None,
                }
            ),
            encoding="utf-8",
        )
        (runtime_root / "payload.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "write_integrity.py"),
                "--root",
                str(runtime_root),
            ],
            check=True,
        )
        archive = root / f"{runtime_name}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(runtime_root, arcname=runtime_name)
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "write_archive_hash.py"), str(archive)],
            check=True,
        )
        return archive

    def test_builds_verified_allowlisted_transfer_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(
                root,
                target_system="windows",
                target_machine="x64",
                cross_built=False,
            )
            extension, extension_approval = self.make_extension(root)
            output = root / "output"
            build_transfer.build_transfer_kit(
                ROOT,
                runtime,
                output,
                False,
                extension,
                extension_approval=extension_approval,
            )
            archive = output / f"browser-mcp-server-{RUNTIME_VERSION}-windows-x64.zip"
            self.assertTrue(archive.is_file())
            self.assertTrue(Path(f"{archive}.sha256").is_file())
            self.assertEqual([], verify_bundle.verify_archive(archive))

            prefix = f"browser-mcp-server-{RUNTIME_VERSION}-windows-x64"
            with zipfile.ZipFile(archive, "r") as bundle:
                names = set(bundle.namelist())
                start_here = bundle.read(f"{prefix}/START-HERE.html").decode("utf-8")
                kit_metadata = json.loads(bundle.read(f"{prefix}/payload/KIT-METADATA.json"))
                release_manifest = json.loads(bundle.read(f"{prefix}/release-manifest.json"))
            self.assertIn("INSTALL.cmd", start_here)
            self.assertIn("双击下面", start_here)
            self.assertIn("CONFIGURE.cmd", start_here)
            self.assertIn(f"{prefix}/START-HERE.html", names)
            self.assertIn(f"{prefix}/INSTALL.cmd", names)
            self.assertIn(f"{prefix}/NOTICE.md", names)
            self.assertIn(f"{prefix}/CONFIGURE.cmd", names)
            self.assertIn(f"{prefix}/OPEN-CONFIG.cmd", names)
            self.assertIn(f"{prefix}/UNINSTALL.cmd", names)
            self.assertIn(f"{prefix}/release-manifest.json", names)
            self.assertIn(f"{prefix}/SHA256SUMS.txt", names)
            self.assertIn(f"{prefix}/payload/INSTALL-WINDOWS-PILOT.cmd", names)
            self.assertIn(f"{prefix}/payload/INSTALL-WINDOWS-PILOT.ps1", names)
            self.assertIn(
                f"{prefix}/payload/browser-extension/{extension.name}", names
            )
            self.assertIn(
                f"{prefix}/payload/browser-extension/unpacked/manifest.json", names
            )
            self.assertIn(
                f"{prefix}/payload/browser-extension/unpacked/_metadata/verified_contents.json",
                names,
            )
            self.assertEqual(
                "browser-extension/unpacked",
                kit_metadata["browserExtension"]["unpackedPath"],
            )
            self.assertEqual(
                "offline-user-policy-with-manual-unpacked-fallback",
                kit_metadata["browserExtension"]["installation"],
            )
            self.assertIn(f"{prefix}/payload/toolkit/config/windows-mcp-environment.json", names)
            self.assertIn(f"{prefix}/payload/toolkit/config/windows-node-sources.json", names)
            self.assertIn(f"{prefix}/payload/toolkit/docs/windows-quickstart.md", names)
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/configure_windows_pilot.py", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/CONFIGURE.cmd", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/BROWSER-AGENT-SETTINGS.ps1", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/check_playwright_extension.py", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/validate_playwright_extension.py", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/register_claude_user_mcp.py", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/smoke_playwright_mcp.py", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/windows-tool-discovery.ps1", names
            )
            self.assertIn(f"{prefix}/payload/toolkit/scripts/verify-bundle.py", names)
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/validate_node_distribution.py", names
            )
            self.assertIn(
                f"{prefix}/payload/toolkit/scripts/validate_windows_release_metadata.py",
                names,
            )
            self.assertIn(f"{prefix}/payload/runtime/{runtime.name}", names)
            self.assertEqual("browser-mcp-server", release_manifest["product"])
            self.assertEqual("browser-mcp", release_manifest["mcpServerName"])
            self.assertFalse(any("/.git/" in name for name in names))
            self.assertFalse(any("/__pycache__/" in name for name in names))
            self.assertNotIn(f"{prefix}/payload/toolkit/config/deployment.production.json", names)

    def test_cross_built_windows_start_here_refuses_target_install(self) -> None:
        start_here = build_transfer.start_here(
            "candidate",
            f"browser-agent-runtime-{RUNTIME_VERSION}-core-windows-x64.tar.gz",
            {
                "target": {"system": "windows", "machine": "x64"},
                "crossBuilt": True,
                "targetCliSmokeTested": False,
                "bundledNode": True,
            },
        )
        self.assertIn("仅供发布侧结构审查", start_here)
        self.assertIn("不要在目标机双击", start_here)
        self.assertIn("目标安装器会拒绝", start_here)
        self.assertNotIn("双击本目录的 `INSTALL-WINDOWS-PILOT.cmd` 一次", start_here)

    def test_windows_transfer_requires_approved_extension_crx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(
                root, target_system="windows", target_machine="x64"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build-transfer-kit.py"),
                    "--runtime-archive",
                    str(runtime),
                    "--output-dir",
                    str(root / "output"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("require --extension-crx", result.stderr)

    def test_refuses_to_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(root)
            output = root / "output"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "build-transfer-kit.py"),
                "--runtime-archive",
                str(runtime),
                "--output-dir",
                str(output),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(2, result.returncode)
            self.assertIn("refusing to overwrite", result.stderr)

    def test_rejects_windows_transfer_without_bundled_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(
                root,
                target_system="windows",
                target_machine="x64",
                bundled_node=False,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build-transfer-kit.py"),
                    "--runtime-archive",
                    str(runtime),
                    "--output-dir",
                    str(root / "output"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("bundledNode=true", result.stderr)

    def test_rejects_cross_built_windows_public_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(
                root,
                target_system="windows",
                target_machine="x64",
                cross_built=True,
            )
            extension, extension_approval = self.make_extension(root)
            with self.assertRaises(build_transfer.TransferKitError) as raised:
                build_transfer.build_transfer_kit(
                    ROOT,
                    runtime,
                    root / "output",
                    False,
                    extension,
                    extension_approval=extension_approval,
                )
            self.assertIn("crossBuilt=false", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
