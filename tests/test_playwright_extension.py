from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import threading
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_module(
    "validate_playwright_extension",
    ROOT / "scripts" / "validate_playwright_extension.py",
)
checker = load_module(
    "check_playwright_extension",
    ROOT / "scripts" / "check_playwright_extension.py",
)


class PlaywrightExtensionValidationTests(unittest.TestCase):
    def approval(self) -> dict:
        return json.loads(
            (ROOT / "config" / "playwright-extension-source.json").read_text(
                encoding="utf-8"
            )
        )

    def make_crx(self, root: Path) -> tuple[Path, dict]:
        approval = self.approval()
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
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("_metadata/verified_contents.json", "[]")
        header = b"test-crx3-header"
        content = b"Cr24" + struct.pack("<II", 3, len(header)) + header + payload.getvalue()
        path = root / approval["filename"]
        path.write_bytes(content)
        approval["sizeBytes"] = len(content)
        approval["sha256"] = hashlib.sha256(content).hexdigest()
        return path, approval

    def test_approved_manifest_key_derives_official_extension_id(self) -> None:
        approval = validator.load_approval()
        self.assertEqual(
            approval["extensionId"],
            validator.extension_id_from_manifest_key(approval["manifestKey"]),
        )

    def test_rejects_invalid_compatible_version_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "approval.json"
            for compatible_versions in (
                None,
                [],
                "0.4.0",
                ["0.3.0"],
                ["0.4.0", "0.4.0"],
                ["0.4.0", {"version": "0.3.0"}],
                ["0.4.0", "0.3.0.1"],
                ["0.4.0", "../0.3.0"],
            ):
                approval = self.approval()
                approval["compatibleVersions"] = compatible_versions
                path.write_text(json.dumps(approval), encoding="utf-8")
                with self.subTest(compatible_versions=compatible_versions):
                    with self.assertRaisesRegex(
                        validator.ExtensionValidationError,
                        "invalid compatible extension versions",
                    ):
                        validator.load_approval(path)

    def test_validates_crx3_payload_and_exact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, approval = self.make_crx(Path(temporary))
            result = validator.validate_crx(path, approval)
            self.assertEqual(approval["extensionId"], result["extensionId"])
            self.assertEqual(approval["version"], result["version"])
            self.assertEqual(
                approval["compatibleVersions"], result["compatibleVersions"]
            )

            changed = copy.deepcopy(approval)
            changed["sha256"] = "0" * 64
            with self.assertRaisesRegex(
                validator.ExtensionValidationError, "SHA-256 mismatch"
            ):
                validator.validate_crx(path, changed)

    def test_rejects_unapproved_manifest_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, approval = self.make_crx(Path(temporary))
            approval["requiredPermissions"] = ["debugger"]
            with self.assertRaisesRegex(
                validator.ExtensionValidationError, "permissions is not approved"
            ):
                validator.validate_crx(path, approval)

    def test_extracts_and_verifies_unpacked_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, approval = self.make_crx(root)
            validator.validate_crx(path, approval)
            unpacked = root / "unpacked"

            validator.extract_crx_payload(path, unpacked)
            validator.validate_unpacked_against_crx(path, unpacked)

            self.assertTrue((unpacked / "manifest.json").is_file())
            self.assertTrue(
                (unpacked / "_metadata" / "verified_contents.json").is_file()
            )

    def test_rejects_modified_unpacked_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, _ = self.make_crx(root)
            unpacked = root / "unpacked"
            validator.extract_crx_payload(path, unpacked)
            (unpacked / "manifest.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                validator.ExtensionValidationError, "changed=manifest.json"
            ):
                validator.validate_unpacked_against_crx(path, unpacked)


class PlaywrightExtensionDetectionTests(unittest.TestCase):
    VERSION = "0.3.0"

    def record(
        self,
        extension_path: str,
        *,
        state: int = 1,
        disable_reasons: int | list[int] = 0,
        api_permissions: list[str] | None = None,
    ) -> dict:
        return {
            "state": state,
            "disable_reasons": disable_reasons,
            "path": extension_path,
            "active_permissions": {
                "api": api_permissions
                if api_permissions is not None
                else ["activeTab", "debugger", "tabs", "tabGroups"],
                "explicit_host": ["<all_urls>"],
            },
        }

    def write_manifest(self, directory: Path, version: str = VERSION) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )

    def test_finds_last_used_profile_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            user_data = checker.browser_user_data_dir(local, "chrome")
            default = user_data / "Default"
            profile = user_data / "Profile 2"
            default.mkdir(parents=True)
            profile.mkdir()
            (user_data / "Local State").write_text(
                json.dumps({"profile": {"last_used": "Profile 2"}}),
                encoding="utf-8",
            )
            relative_extension = (
                Path("Extensions")
                / checker.PLAYWRIGHT_EXTENSION_ID
                / f"{self.VERSION}_0"
            )
            self.write_manifest(profile / relative_extension)
            (profile / "Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: self.record(
                                    str(relative_extension)
                                )
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                "Profile 2",
                checker.find_extension_profile(
                    user_data,
                    self.VERSION,
                    local / "approved-unpacked",
                ),
            )

    def test_finds_enabled_secure_preference_and_supports_edge_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            user_data = checker.browser_user_data_dir(local, "msedge")
            profile = user_data / "Default"
            profile.mkdir(parents=True)
            relative_extension = Path("playwright") / self.VERSION
            self.write_manifest(profile / "Extensions" / relative_extension)
            enabled_record = self.record(
                str(relative_extension), disable_reasons=[]
            )
            enabled_record.pop("state")
            (profile / "Secure Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: enabled_record
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                "Default",
                checker.find_extension_profile(
                    user_data,
                    self.VERSION,
                    local / "approved-unpacked",
                ),
            )
            self.assertIn("Microsoft", str(user_data))

    def test_last_used_profile_does_not_false_pass_from_dormant_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            user_data = checker.browser_user_data_dir(local, "chrome")
            default = user_data / "Default"
            dormant = user_data / "Profile 2"
            default.mkdir(parents=True)
            dormant.mkdir()
            (user_data / "Local State").write_text(
                json.dumps({"profile": {"last_used": "Default"}}),
                encoding="utf-8",
            )
            relative_extension = (
                Path("Extensions")
                / checker.PLAYWRIGHT_EXTENSION_ID
                / f"{self.VERSION}_0"
            )
            self.write_manifest(dormant / relative_extension)
            (dormant / "Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: self.record(
                                    str(relative_extension)
                                )
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(
                checker.find_extension_profile(
                    user_data,
                    self.VERSION,
                    local / "approved-unpacked",
                )
            )

    def test_missing_or_unrelated_profiles_do_not_false_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(
                checker.find_extension_profile(
                    root / "missing",
                    self.VERSION,
                    root / "approved-unpacked",
                )
            )
            default = root / "Default"
            default.mkdir()
            (default / "Preferences").write_text(
                '{"extensions":{"settings":{"not-playwright":{}}}}',
                encoding="utf-8",
            )
            self.assertIsNone(
                checker.find_extension_profile(
                    root,
                    self.VERSION,
                    root / "approved-unpacked",
                )
            )

    def test_disabled_or_wrong_version_does_not_false_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "Default"
            profile.mkdir()
            relative_extension = Path("Extensions") / "playwright" / self.VERSION
            for record, manifest_version in (
                (self.record(str(relative_extension), state=0), self.VERSION),
                (
                    self.record(str(relative_extension), disable_reasons=[1]),
                    self.VERSION,
                ),
                (self.record(str(relative_extension)), "0.2.0"),
                (
                    self.record(str(relative_extension), api_permissions=["tabs"]),
                    self.VERSION,
                ),
            ):
                self.write_manifest(profile / relative_extension, manifest_version)
                (profile / "Secure Preferences").write_text(
                    json.dumps(
                        {
                            "extensions": {
                                "settings": {checker.PLAYWRIGHT_EXTENSION_ID: record}
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertIsNone(
                    checker.find_extension_profile(
                        root,
                        self.VERSION,
                        root / "approved-unpacked",
                    )
                )

    def test_secure_preferences_cannot_be_overridden_by_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "Default"
            profile.mkdir()
            relative_extension = Path("Extensions") / "playwright" / self.VERSION
            self.write_manifest(profile / relative_extension, self.VERSION)
            record = self.record(str(relative_extension), state=0)
            payload = {"extensions": {"settings": {checker.PLAYWRIGHT_EXTENSION_ID: record}}}
            (profile / "Secure Preferences").write_text(json.dumps(payload), encoding="utf-8")
            enabled = self.record(str(relative_extension))
            (profile / "Preferences").write_text(
                json.dumps({"extensions": {"settings": {checker.PLAYWRIGHT_EXTENSION_ID: enabled}}}),
                encoding="utf-8",
            )
            self.assertEqual(
                checker.STATUS_VERSION_MISMATCH,
                checker.extension_status_in_profile(
                    profile, self.VERSION, root / "approved-unpacked"
                )[0],
            )

    def test_reports_installed_extension_when_version_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            user_data = checker.browser_user_data_dir(local, "chrome")
            profile = user_data / "Default"
            profile.mkdir(parents=True)
            relative_extension = Path("Extensions") / "playwright" / "0.2.0"
            self.write_manifest(profile / relative_extension, "0.2.0")
            (profile / "Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: self.record(
                                    str(relative_extension)
                                )
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            status, found_profile, version = checker.find_extension_status(
                user_data,
                self.VERSION,
                local / "approved-unpacked",
            )
            self.assertEqual(checker.STATUS_VERSION_MISMATCH, status)
            self.assertEqual("Default", found_profile)
            self.assertEqual("0.2.0", version)

            output = io.StringIO()
            with redirect_stdout(output):
                result = checker.check(
                    SimpleNamespace(
                        local_app_data=local,
                        browser_channel="chrome",
                        expected_version=self.VERSION,
                        approved_unpacked_path=local / "approved-unpacked",
                        quiet=False,
                    )
                )
            self.assertEqual(checker.EXIT_VERSION_MISMATCH, result)
            self.assertIn("INCOMPATIBLE", output.getvalue())
            self.assertIn("0.2.0", output.getvalue())
            self.assertNotIn("NOT INSTALLED", output.getvalue())
            status_output = io.StringIO()
            with redirect_stdout(status_output):
                self.assertEqual(
                    0,
                    checker.report_status(
                        SimpleNamespace(
                            local_app_data=local,
                            browser_channel="chrome",
                            expected_version=self.VERSION,
                            approved_unpacked_path=local / "approved-unpacked",
                            quiet=False,
                        )
                    ),
                )
            self.assertEqual("INCOMPATIBLE|0.2.0\n", status_output.getvalue())

    def test_reuses_compatible_extension_but_reports_upgrade_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            user_data = checker.browser_user_data_dir(local, "chrome")
            profile = user_data / "Default"
            profile.mkdir(parents=True)
            installed_version = "0.2.0"
            relative_extension = Path("Extensions") / "playwright" / installed_version
            self.write_manifest(profile / relative_extension, installed_version)
            (profile / "Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: self.record(
                                    str(relative_extension)
                                )
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            compatible_versions = [self.VERSION, installed_version]
            status, found_profile, version = checker.find_extension_status(
                user_data,
                self.VERSION,
                local / "approved-unpacked",
                compatible_versions,
            )
            self.assertEqual(checker.STATUS_COMPATIBLE, status)
            self.assertEqual("Default", found_profile)
            self.assertEqual(installed_version, version)

            output = io.StringIO()
            with redirect_stdout(output):
                result = checker.check(
                    SimpleNamespace(
                        local_app_data=local,
                        browser_channel="chrome",
                        expected_version=self.VERSION,
                        compatible_versions=compatible_versions,
                        approved_unpacked_path=local / "approved-unpacked",
                        quiet=False,
                    )
                )
            self.assertEqual(0, result)
            self.assertIn("COMPATIBLE", output.getvalue())
            self.assertIn(installed_version, output.getvalue())
            status_output = io.StringIO()
            with redirect_stdout(status_output):
                self.assertEqual(
                    0,
                    checker.report_status(
                        SimpleNamespace(
                            local_app_data=local,
                            browser_channel="chrome",
                            expected_version=self.VERSION,
                            compatible_versions=compatible_versions,
                            approved_unpacked_path=local / "approved-unpacked",
                            quiet=False,
                        )
                    ),
                )
            self.assertEqual("COMPATIBLE|0.2.0\n", status_output.getvalue())

    def test_recognizes_compatible_version_in_previous_approved_unpack_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            user_data = checker.browser_user_data_dir(local, "chrome")
            profile = user_data / "Default"
            profile.mkdir(parents=True)
            extension_root = local / "browser-mcp-server" / "browser-extension"
            current_path = extension_root / self.VERSION / "unpacked"
            installed_version = "0.2.0"
            previous_path = extension_root / installed_version / "unpacked"
            self.write_manifest(previous_path, installed_version)
            (profile / "Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: self.record(
                                    str(previous_path)
                                )
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            status, found_profile, version = checker.find_extension_status(
                user_data,
                self.VERSION,
                current_path,
                [self.VERSION, installed_version],
            )
            self.assertEqual(checker.STATUS_COMPATIBLE, status)
            self.assertEqual("Default", found_profile)
            self.assertEqual(installed_version, version)

    def test_recognizes_incompatible_version_in_previous_approved_unpack_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            user_data = checker.browser_user_data_dir(local, "chrome")
            profile = user_data / "Default"
            profile.mkdir(parents=True)
            extension_root = local / "IntranetBrowserAgent" / "browser-extension"
            current_path = extension_root / self.VERSION / "unpacked"
            installed_version = "0.1.0"
            previous_path = extension_root / installed_version / "unpacked"
            self.write_manifest(previous_path, installed_version)
            (profile / "Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: self.record(
                                    str(previous_path)
                                )
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            status, found_profile, version = checker.find_extension_status(
                user_data,
                self.VERSION,
                current_path,
                [self.VERSION, "0.2.0"],
            )
            self.assertEqual(checker.STATUS_VERSION_MISMATCH, status)
            self.assertEqual("Default", found_profile)
            self.assertEqual(installed_version, version)

    def test_current_version_remains_valid_when_allowlist_only_adds_old_version(self) -> None:
        self.assertTrue(
            checker._is_compatible_version(
                self.VERSION,
                self.VERSION,
                ["0.2.0"],
            )
        )

    def test_unreadable_or_invalid_version_is_reported_without_raw_manifest_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            profile = checker.browser_user_data_dir(local, "chrome") / "Default"
            profile.mkdir(parents=True)
            approved = local / "browser-mcp-server" / "browser-extension" / self.VERSION / "unpacked"
            (profile / "Preferences").write_text(
                json.dumps({"extensions": {"settings": {
                    checker.PLAYWRIGHT_EXTENSION_ID: self.record(str(approved))
                }}}), encoding="utf-8",
            )
            for version in (None, "0.3.0|unexpected", "0.3.0\nCURRENT|0.3.0"):
                with self.subTest(version=version):
                    self.write_manifest(approved, version)
                    output = io.StringIO()
                    with redirect_stdout(output):
                        checker.report_status(SimpleNamespace(
                            local_app_data=local, browser_channel="chrome",
                            expected_version=self.VERSION, approved_unpacked_path=approved,
                        ))
                    self.assertEqual("INCOMPATIBLE|\n", output.getvalue())

    def test_rejects_unapproved_absolute_unpacked_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "Default"
            profile.mkdir()
            wrong = root / "wrong-unpacked"
            approved = root / "approved-unpacked"
            self.write_manifest(wrong)
            (profile / "Secure Preferences").write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                checker.PLAYWRIGHT_EXTENSION_ID: self.record(str(wrong))
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(
                checker.find_extension_profile(root, self.VERSION, approved)
            )

            status, found_profile, version = checker.find_extension_status(
                root,
                self.VERSION,
                approved,
            )
            self.assertEqual(checker.STATUS_VERSION_MISMATCH, status)
            self.assertEqual("Default", found_profile)
            self.assertIsNone(version)

    def test_wait_continues_when_manual_load_appears(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            profile = checker.browser_user_data_dir(local, "chrome") / "Default"
            approved = local / "browser-mcp-server" / "browser-extension" / "unpacked"
            self.write_manifest(approved)

            def simulate_manual_load() -> None:
                profile.mkdir(parents=True)
                (profile / "Preferences").write_text(
                    json.dumps(
                        {
                            "extensions": {
                                "settings": {
                                    checker.PLAYWRIGHT_EXTENSION_ID: self.record(
                                        str(approved)
                                    )
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )

            timer = threading.Timer(0.1, simulate_manual_load)
            timer.start()
            try:
                result = checker.wait_for_extension(
                    SimpleNamespace(
                        local_app_data=local,
                        browser_channel="chrome",
                        expected_version=self.VERSION,
                        approved_unpacked_path=approved,
                        quiet=True,
                        timeout_seconds=2,
                        poll_seconds=0.05,
                    )
                )
            finally:
                timer.cancel()
                timer.join()
            self.assertEqual(0, result)


if __name__ == "__main__":
    unittest.main()
