from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_bundle", ROOT / "scripts" / "verify-bundle.py")
assert SPEC and SPEC.loader
verify_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_bundle)
SBOM_SPEC = importlib.util.spec_from_file_location(
    "generate_sbom", ROOT / "scripts" / "generate_sbom.py"
)
assert SBOM_SPEC and SBOM_SPEC.loader
generate_sbom = importlib.util.module_from_spec(SBOM_SPEC)
SBOM_SPEC.loader.exec_module(generate_sbom)
ARCHIVE_SPEC = importlib.util.spec_from_file_location(
    "create_bundle_archive", ROOT / "scripts" / "create_bundle_archive.py"
)
assert ARCHIVE_SPEC and ARCHIVE_SPEC.loader
create_bundle_archive = importlib.util.module_from_spec(ARCHIVE_SPEC)
ARCHIVE_SPEC.loader.exec_module(create_bundle_archive)
PORTABILITY_SPEC = importlib.util.spec_from_file_location(
    "check_runtime_portability", ROOT / "scripts" / "check_runtime_portability.py"
)
assert PORTABILITY_SPEC and PORTABILITY_SPEC.loader
check_runtime_portability = importlib.util.module_from_spec(PORTABILITY_SPEC)
PORTABILITY_SPEC.loader.exec_module(check_runtime_portability)
PREPARE_SPEC = importlib.util.spec_from_file_location(
    "prepare_runtime_tree", ROOT / "scripts" / "prepare_runtime_tree.py"
)
assert PREPARE_SPEC and PREPARE_SPEC.loader
prepare_runtime_tree = importlib.util.module_from_spec(PREPARE_SPEC)
PREPARE_SPEC.loader.exec_module(prepare_runtime_tree)


class ArtifactIntegrityTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "creating symlinks may require Windows privileges")
    def test_directory_integrity_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            (root / "lib").mkdir(parents=True)
            (root / "lib" / "payload.txt").write_text("known content\n", encoding="utf-8")
            os.symlink("lib/payload.txt", root / "payload-link")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "write_integrity.py"),
                    "--root",
                    str(root),
                ],
                check=True,
            )
            self.assertEqual([], verify_bundle.verify_directory(root))
            (root / "lib" / "payload.txt").write_text("tampered\n", encoding="utf-8")
            errors = verify_bundle.verify_directory(root)
            self.assertTrue(any("hash mismatch" in error for error in errors))

    def test_unsafe_symlink_is_rejected(self) -> None:
        self.assertFalse(verify_bundle.link_stays_inside("bin/tool", "../../../etc/passwd"))
        self.assertFalse(verify_bundle.link_stays_inside("bin/tool", "/etc/passwd"))
        self.assertFalse(verify_bundle.link_stays_inside("bin/tool", r"C:\Windows"))
        self.assertFalse(verify_bundle.link_stays_inside("bin/tool", r"..\outside"))
        self.assertTrue(verify_bundle.link_stays_inside("bin/tool", "../lib/tool.js"))

    def test_archive_paths_reject_windows_unsafe_names(self) -> None:
        for path in (
            r"bin\tool.cmd",
            "C:/tool.cmd",
            "bin/tool:stream",
            "bin/CON",
            "bin/trailing.",
            "bin/trailing ",
            "bin/./tool",
            "bin/wild*card",
            "bin/question?mark",
            "bin/new\nline",
        ):
            with self.subTest(path=path):
                self.assertFalse(verify_bundle.safe_relative(path))
        self.assertTrue(verify_bundle.safe_relative("bin/tool.cmd"))

    def test_checksum_parser_rejects_duplicates(self) -> None:
        digest = "0" * 64
        with self.assertRaises(ValueError):
            verify_bundle.parse_sums(f"{digest}  file\n{digest}  file\n")

    def test_directory_rejects_windows_unsafe_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            root.mkdir()
            (root / "bad:name").write_bytes(b"fixture")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "write_integrity.py"),
                    "--root",
                    str(root),
                ],
                check=True,
            )
            errors = verify_bundle.verify_directory(root)
            self.assertTrue(any("unsafe checksum path" in error for error in errors))

    def test_scoped_npm_purl_encodes_at_sign(self) -> None:
        self.assertEqual(
            "pkg:npm/%40playwright/mcp@0.0.79",
            generate_sbom.purl("@playwright/mcp", "0.0.79"),
        )

    def test_python_archiver_produces_verifiable_link_free_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            root = temporary_path / "runtime"
            root.mkdir()
            (root / "payload.txt").write_text("known\n", encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "write_integrity.py"),
                    "--root",
                    str(root),
                    "--reject-links",
                ],
                check=True,
            )
            archive = temporary_path / "runtime.tar.gz"
            create_bundle_archive.create_archive(root, archive, reject_links=True)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "write_archive_hash.py"), str(archive)],
                check=True,
            )
            self.assertEqual([], verify_bundle.verify_archive(archive))

    def test_archive_rejects_symlinked_integrity_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.tar.gz"
            payload = b'{"schemaVersion":1,"links":[]}\n'
            with tarfile.open(archive, "w:gz") as bundle:
                top = tarfile.TarInfo("runtime")
                top.type = tarfile.DIRTYPE
                bundle.addfile(top)

                sums = tarfile.TarInfo("runtime/SHA256SUMS")
                sums.type = tarfile.SYMTYPE
                sums.linkname = "SYMLINKS.json"
                bundle.addfile(sums)

                links = tarfile.TarInfo("runtime/SYMLINKS.json")
                links.size = len(payload)
                bundle.addfile(links, io.BytesIO(payload))
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "write_archive_hash.py"),
                    str(archive),
                ],
                check=True,
            )
            errors = verify_bundle.verify_archive(archive)
            self.assertTrue(any("must be regular files" in error for error in errors))

    def test_portability_rejects_native_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "node_modules"
            package = root / "example"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                '{"name":"example","version":"1.0.0"}\n', encoding="utf-8"
            )
            self.assertEqual(
                [], check_runtime_portability.portability_errors(root, "windows", "x64")
            )
            (package / "addon.node").write_bytes(b"fixture")
            errors = check_runtime_portability.portability_errors(root, "windows", "x64")
            self.assertTrue(any("native binary" in error for error in errors))

    def test_core_runtime_prunes_optional_packages_and_pnpm_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            node_modules = Path(temporary) / "node_modules"
            for package in (
                node_modules / "@playwright" / "mcp",
                node_modules / "playwright",
                node_modules / "playwright-core",
                node_modules / "chrome-devtools-mcp",
                node_modules / "fsevents",
            ):
                package.mkdir(parents=True)
                (package / "package.json").write_text("{}\n", encoding="utf-8")
            (node_modules / ".bin").mkdir()
            (node_modules / ".cache").mkdir()
            (node_modules / ".modules.yaml").write_text("fixture\n", encoding="utf-8")
            prepare_runtime_tree.prepare(node_modules, "core")
            self.assertTrue((node_modules / "@playwright" / "mcp").is_dir())
            self.assertFalse((node_modules / "chrome-devtools-mcp").exists())
            self.assertFalse((node_modules / "fsevents").exists())
            self.assertFalse((node_modules / ".bin").exists())
            self.assertFalse((node_modules / ".cache").exists())
            self.assertFalse((node_modules / ".modules.yaml").exists())


if __name__ == "__main__":
    unittest.main()
