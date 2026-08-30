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
    ) -> Path:
        runtime_name = (
            f"browser-agent-runtime-{RUNTIME_VERSION}-core-{target_system}-{target_machine}"
        )
        runtime_root = root / runtime_name
        runtime_root.mkdir()
        (runtime_root / "BUILD-METADATA.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "runtimeVersion": RUNTIME_VERSION,
                    "profile": "core",
                    "target": {"system": target_system, "machine": target_machine},
                    "crossBuilt": target_system.lower() == "windows",
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
            runtime = self.make_runtime(root, target_system="windows", target_machine="x64")
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
            archive = output / (
                f"intranet-browser-agent-transfer-{RUNTIME_VERSION}-core-windows-x64.tar.gz"
            )
            self.assertTrue(archive.is_file())
            self.assertTrue(Path(f"{archive}.sha256").is_file())
            self.assertEqual([], verify_bundle.verify_archive(archive))

            prefix = (
                f"intranet-browser-agent-transfer-{RUNTIME_VERSION}-core-windows-x64"
            )
            with tarfile.open(archive, "r:gz") as bundle:
                names = {member.name for member in bundle.getmembers()}
                start_handle = bundle.extractfile(f"{prefix}/START-HERE.md")
                self.assertIsNotNone(start_handle)
                start_here = start_handle.read().decode("utf-8")
                metadata_handle = bundle.extractfile(f"{prefix}/KIT-METADATA.json")
                self.assertIsNotNone(metadata_handle)
                kit_metadata = json.loads(metadata_handle.read().decode("utf-8"))
            self.assertIn("INSTALL-WINDOWS-PILOT.cmd", start_here)
            self.assertIn("双击安装", start_here)
            self.assertIn(
                "演练首次安装、升级、条目/环境错写和 `remove/add/get` 失败回滚",
                start_here,
            )
            self.assertIn("首次安装明确没有旧 MCP 条目时会直接继续", start_here)
            self.assertIn("双击本目录的 `INSTALL-WINDOWS-PILOT.cmd` 一次", start_here)
            self.assertIn("真实 Claude CLI 隔离探针", start_here)
            self.assertIn("真实 MCP stdio", start_here)
            self.assertIn("全部通过后才开始持久化安装", start_here)
            self.assertIn(f"{prefix}/START-HERE.md", names)
            self.assertIn(f"{prefix}/INSTALL-WINDOWS-PILOT.cmd", names)
            self.assertIn(f"{prefix}/INSTALL-WINDOWS-PILOT.ps1", names)
            self.assertIn(
                f"{prefix}/browser-extension/{extension.name}", names
            )
            self.assertIn(
                f"{prefix}/browser-extension/unpacked/manifest.json", names
            )
            self.assertIn(
                f"{prefix}/browser-extension/unpacked/_metadata/verified_contents.json",
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
            self.assertIn(f"{prefix}/toolkit/config/deployment.pilot.json.template", names)
            self.assertIn(f"{prefix}/toolkit/config/windows-mcp-environment.json", names)
            self.assertIn(f"{prefix}/toolkit/config/windows-node-sources.json", names)
            self.assertIn(f"{prefix}/toolkit/docs/windows-quickstart.md", names)
            self.assertIn(
                f"{prefix}/toolkit/scripts/configure_windows_pilot.py", names
            )
            self.assertIn(
                f"{prefix}/toolkit/scripts/check_playwright_extension.py", names
            )
            self.assertIn(
                f"{prefix}/toolkit/scripts/validate_playwright_extension.py", names
            )
            self.assertIn(
                f"{prefix}/toolkit/scripts/register_claude_user_mcp.py", names
            )
            self.assertIn(
                f"{prefix}/toolkit/scripts/smoke_playwright_mcp.py", names
            )
            self.assertIn(
                f"{prefix}/toolkit/scripts/verify-windows-release.ps1", names
            )
            self.assertIn(
                f"{prefix}/toolkit/tests/test_claude_mcp_registration.py", names
            )
            self.assertIn(f"{prefix}/toolkit/scripts/verify-bundle.py", names)
            self.assertIn(
                f"{prefix}/toolkit/scripts/validate_node_distribution.py", names
            )
            self.assertIn(f"{prefix}/runtime/{runtime.name}", names)
            self.assertFalse(any("/.git/" in name for name in names))
            self.assertFalse(any("/__pycache__/" in name for name in names))
            self.assertNotIn(f"{prefix}/toolkit/config/deployment.production.json", names)

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


if __name__ == "__main__":
    unittest.main()
