from __future__ import annotations

import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("browser_agent", ROOT / "tools" / "browser_agent.py")
assert SPEC and SPEC.loader
browser_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(browser_agent)


class BrowserAgentManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.demo_path = ROOT / "config" / "deployment.local-demo.json"
        self.demo = json.loads(self.demo_path.read_text(encoding="utf-8"))

    def test_local_demo_is_valid(self) -> None:
        self.assertEqual([], browser_agent.validate_manifest(self.demo))

    def test_production_template_fails_closed(self) -> None:
        template = json.loads(
            (ROOT / "config" / "deployment.production.json.template").read_text(encoding="utf-8")
        )
        errors = browser_agent.validate_manifest(template)
        self.assertTrue(any("approved must be true" in error for error in errors))
        self.assertTrue(any("REPLACE-ME" in error for error in errors))

    def test_pilot_template_fails_closed(self) -> None:
        template = json.loads(
            (ROOT / "config" / "deployment.pilot.json.template").read_text(encoding="utf-8")
        )
        errors = browser_agent.validate_manifest(template)
        self.assertTrue(any("approved must be true" in error for error in errors))
        self.assertTrue(any("REPLACE-ME" in error for error in errors))
        self.assertFalse(any("loopback-only-demo" in error for error in errors))

    def test_windows_pilot_template_fails_closed(self) -> None:
        template = json.loads(
            (ROOT / "config" / "deployment.windows-pilot.json.template").read_text(
                encoding="utf-8"
            )
        )
        errors = browser_agent.validate_manifest(template)
        self.assertTrue(any("REPLACE-ME" in error for error in errors))
        self.assertFalse(any("dataBoundary" in error for error in errors))
        self.assertFalse(any("absolute local Windows" in error for error in errors))

    def test_windows_production_template_fails_closed(self) -> None:
        template = json.loads(
            (ROOT / "config" / "deployment.windows-production.json.template").read_text(
                encoding="utf-8"
            )
        )
        errors = browser_agent.validate_manifest(template)
        self.assertTrue(any("approved must be true" in error for error in errors))
        self.assertTrue(any("REPLACE-ME" in error for error in errors))
        self.assertFalse(any("absolute local Windows" in error for error in errors))

    def valid_windows_manifest(self) -> dict:
        manifest = json.loads(
            (ROOT / "config" / "deployment.windows-pilot.json.template").read_text(
                encoding="utf-8"
            )
        )
        user_root = r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent"
        manifest["installRoot"] = user_root + r"\releases\runtime-1"
        manifest["nodeExecutable"] = (
            user_root + r"\releases\runtime-1\node\node.exe"
        )
        manifest["configRoot"] = user_root + r"\config\pilot"
        manifest["output"]["directory"] = user_root + r"\output\pilot"
        manifest["browser"]["executablePath"] = (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        )
        manifest["browser"]["profileOwner"] = "CORP\\browser-agent-pilot"
        return manifest

    def test_pilot_omits_production_governance_but_production_requires_it(self) -> None:
        pilot = self.valid_windows_manifest()
        self.assertEqual([], browser_agent.validate_manifest(pilot))
        self.assertNotIn("network", pilot)
        self.assertNotIn("dataBoundary", pilot)

        production = copy.deepcopy(pilot)
        production["environment"] = "production"
        errors = browser_agent.validate_manifest(production)
        self.assertTrue(any("dataBoundary" in error for error in errors))
        self.assertTrue(any("network" in error for error in errors))

        production["network"] = {"allowedOrigins": ["https://portal.corp.example"]}
        errors = browser_agent.validate_manifest(production)
        self.assertTrue(any("enforcement" in error for error in errors))

    def test_windows_paths_render_direct_node_command(self) -> None:
        manifest = self.valid_windows_manifest()
        self.assertEqual([], browser_agent.validate_manifest(manifest))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "windows.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            browser_agent.render(manifest_path, root / "rendered", force=False)
            mcp = json.loads((root / "rendered" / ".mcp.json").read_text(encoding="utf-8"))
            server = mcp["mcpServers"]["intranet-browser-agent"]
            self.assertEqual(
                r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent\releases\runtime-1\node\node.exe",
                server["command"],
            )
            self.assertEqual(
                r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent\releases\runtime-1\bin\intranet-browser-agent-mcp.js",
                server["args"][0],
            )
            self.assertEqual("--browser=chrome", server["args"][1])
            self.assertEqual(
                r"--executable-path=C:\Program Files\Google\Chrome\Application\chrome.exe",
                server["args"][2],
            )
            self.assertEqual("--config", server["args"][3])
            self.assertEqual(
                r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent\config\pilot\playwright.config.json",
                server["args"][4],
            )
            self.assertEqual(
                browser_agent._load_windows_mcp_environment(), server["env"]
            )
            self.assertEqual("", server["env"]["PLAYWRIGHT_MCP_ALLOWED_ORIGINS"])
            self.assertEqual("", server["env"]["NODE_OPTIONS"])
            self.assertNotIn("PLAYWRIGHT_MCP_EXTENSION_TOKEN", server["env"])

    def test_windows_paths_reject_posix_and_unc_roots(self) -> None:
        manifest = self.valid_windows_manifest()
        manifest.pop("nodeExecutable")
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("nodeExecutable is required" in error for error in errors))

        manifest = self.valid_windows_manifest()
        manifest["installRoot"] = "/opt/intranet-browser-agent"
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("absolute local Windows" in error for error in errors))

    def test_manifest_strings_reject_surrounding_whitespace(self) -> None:
        manifest = self.valid_windows_manifest()
        manifest["nodeExecutable"] = " " + manifest["nodeExecutable"]
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(
            any("leading or trailing whitespace" in error for error in errors)
        )

        for unsafe in (
            r"C:\Agent\CON",
            r"C:\Agent\config:stream",
            r"C:\Agent\trailing.",
            r"C:\Agent\wild*card",
        ):
            with self.subTest(unsafe=unsafe):
                manifest = self.valid_windows_manifest()
                manifest["configRoot"] = unsafe
                errors = browser_agent.validate_manifest(manifest)
                self.assertTrue(any("safe absolute local Windows" in error for error in errors))

        manifest = self.valid_windows_manifest()
        manifest["target"]["arch"] = "arm64"
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("x64 for Windows" in error for error in errors))

        manifest = self.valid_windows_manifest()
        manifest["installRoot"] = r"\\server\share\agent"
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("absolute local Windows" in error for error in errors))

    def test_wildcard_hostname_is_rejected(self) -> None:
        manifest = copy.deepcopy(self.demo)
        manifest["network"]["allowedOrigins"] = ["https://*.corp.example"]
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("explicit HTTP(S) origin" in error for error in errors))

    def test_remote_cdp_endpoint_is_rejected(self) -> None:
        manifest = copy.deepcopy(self.demo)
        manifest["mode"] = "cdp"
        manifest["browser"].pop("userDataDir")
        manifest["browser"]["cdpEndpoint"] = "http://10.1.2.3:9222"
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("loopback" in error for error in errors))

    def test_default_personal_profile_is_rejected(self) -> None:
        manifest = copy.deepcopy(self.demo)
        manifest["browser"]["userDataDir"] = (
            "/Users/example/Library/Application Support/Google/Chrome/Default"
        )
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("personal/default" in error for error in errors))

    def test_extension_production_requires_managed_distribution(self) -> None:
        manifest = copy.deepcopy(self.demo)
        manifest["environment"] = "production"
        manifest["mode"] = "extension"
        manifest["browser"].pop("userDataDir")
        manifest["browser"].update(
            {
                "extensionDistribution": "manual-pilot",
                "manualConnectionApproval": True,
            }
        )
        manifest["network"].update(
            {
                "allowedOrigins": ["https://portal.corp.example"],
                "enforcement": "egress-proxy",
            }
        )
        manifest["dataBoundary"]["classification"] = "internal"
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("manual-pilot" in error for error in errors))

    def test_stored_extension_authorization_is_limited_to_windows_user_pilot(self) -> None:
        pilot = self.valid_windows_manifest()
        pilot["browser"]["manualConnectionApproval"] = False
        self.assertEqual([], browser_agent.validate_manifest(pilot))

        production = copy.deepcopy(pilot)
        production["environment"] = "production"
        errors = browser_agent.validate_manifest(production)
        self.assertTrue(any("stored extension authorization" in error for error in errors))

    def test_all_risky_actions_are_required(self) -> None:
        manifest = copy.deepcopy(self.demo)
        manifest["controls"]["confirmationActions"].remove("delete")
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("missing: delete" in error for error in errors))

    def test_interaction_modes_are_explicit_and_bounded(self) -> None:
        manifest = copy.deepcopy(self.demo)
        manifest["interaction"]["snapshotStrategy"] = "automatic"
        manifest["interaction"]["compatibilityMode"] = "force"
        manifest["interaction"]["defaultSnapshotDepth"] = 0
        errors = browser_agent.validate_manifest(manifest)
        self.assertTrue(any("snapshotStrategy" in error for error in errors))
        self.assertTrue(any("compatibilityMode" in error for error in errors))
        self.assertTrue(any("defaultSnapshotDepth" in error for error in errors))

    def test_render_uses_absolute_offline_commands_and_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rendered"
            written = browser_agent.render(self.demo_path, output, force=False)
            self.assertEqual(6, len(written))
            mcp = json.loads((output / ".mcp.json").read_text(encoding="utf-8"))
            server = mcp["mcpServers"]["intranet-browser-agent"]
            self.assertTrue(PurePosixPath(server["command"]).is_absolute())
            self.assertNotIn("npx", json.dumps(mcp))
            self.assertNotIn("@latest", json.dumps(mcp))
            self.assertNotIn("TOKEN", json.dumps(mcp).upper())
            self.assertEqual(
                "/tmp/intranet-browser-agent-demo/config/playwright.config.json",
                server["args"][1],
            )
            playwright = json.loads(
                (output / "playwright.config.json").read_text(encoding="utf-8")
            )
            self.assertFalse(playwright["allowUnrestrictedFileAccess"])
            self.assertEqual(["core", "vision"], playwright["capabilities"])
            self.assertEqual("none", playwright["snapshot"]["mode"])
            interaction = json.loads(
                (output / "interaction.config.json").read_text(encoding="utf-8")
            )
            self.assertEqual("compact", interaction["snapshotStrategy"])
            self.assertEqual("robust", interaction["compatibilityMode"])

    def test_render_refuses_implicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rendered"
            browser_agent.render(self.demo_path, output, force=False)
            with self.assertRaises(browser_agent.ManifestError):
                browser_agent.render(self.demo_path, output, force=False)

    def test_devtools_is_explicit_and_hardened(self) -> None:
        manifest = copy.deepcopy(self.demo)
        manifest["controls"]["devtools"] = True
        manifest["browser"]["devtoolsEndpoint"] = "http://127.0.0.1:9222"
        self.assertEqual([], browser_agent.validate_manifest(manifest))
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = Path(temporary) / "rendered"
            browser_agent.render(manifest_path, output, force=False)
            mcp = json.loads((output / ".mcp.json").read_text(encoding="utf-8"))
            devtools = mcp["mcpServers"]["chrome-devtools"]
            self.assertIn("--no-usage-statistics", devtools["args"])
            self.assertIn("--no-performance-crux", devtools["args"])
            self.assertIn("--redact-network-headers", devtools["args"])
            self.assertEqual("1", devtools["env"]["CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS"])

    def test_preflight_detects_config_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            package_dir = runtime / "node_modules" / "@playwright" / "mcp"
            package_dir.mkdir(parents=True)
            (package_dir / "package.json").write_text(
                json.dumps({"version": browser_agent.PLAYWRIGHT_MCP_VERSION}),
                encoding="utf-8",
            )
            rendered = root / "rendered"
            browser_agent.render(self.demo_path, rendered, force=False)
            with mock.patch.object(
                browser_agent, "_host_system", return_value="darwin"
            ), mock.patch.object(browser_agent, "_host_machine", return_value="arm64"):
                checks = browser_agent.preflight(
                    self.demo_path,
                    runtime,
                    rendered,
                    run_cli_help=False,
                )
            config_check = next(check for check in checks if check["name"] == "playwright-config")
            self.assertEqual("pass", config_check["status"])
            interaction_check = next(
                check for check in checks if check["name"] == "interaction-config"
            )
            self.assertEqual("pass", interaction_check["status"])
            mcp_check = next(check for check in checks if check["name"] == "mcp-config")
            self.assertEqual("pass", mcp_check["status"])

            config_path = rendered / "playwright.config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["allowUnrestrictedFileAccess"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch.object(
                browser_agent, "_host_system", return_value="darwin"
            ), mock.patch.object(browser_agent, "_host_machine", return_value="arm64"):
                checks = browser_agent.preflight(
                    self.demo_path,
                    runtime,
                    rendered,
                    run_cli_help=False,
                )
            config_check = next(check for check in checks if check["name"] == "playwright-config")
            self.assertEqual("fail", config_check["status"])

            interaction_path = rendered / "interaction.config.json"
            interaction = json.loads(interaction_path.read_text(encoding="utf-8"))
            interaction["compatibilityMode"] = "standard"
            interaction_path.write_text(json.dumps(interaction), encoding="utf-8")
            with mock.patch.object(
                browser_agent, "_host_system", return_value="darwin"
            ), mock.patch.object(browser_agent, "_host_machine", return_value="arm64"):
                checks = browser_agent.preflight(
                    self.demo_path,
                    runtime,
                    rendered,
                    run_cli_help=False,
                )
            interaction_check = next(
                check for check in checks if check["name"] == "interaction-config"
            )
            self.assertEqual("fail", interaction_check["status"])

            mcp_path = rendered / ".mcp.json"
            mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
            mcp["mcpServers"]["intranet-browser-agent"]["command"] = "/tmp/wrong"
            mcp_path.write_text(json.dumps(mcp), encoding="utf-8")
            with mock.patch.object(
                browser_agent, "_host_system", return_value="darwin"
            ), mock.patch.object(browser_agent, "_host_machine", return_value="arm64"):
                checks = browser_agent.preflight(
                    self.demo_path,
                    runtime,
                    rendered,
                    run_cli_help=False,
                )
            mcp_check = next(check for check in checks if check["name"] == "mcp-config")
            self.assertEqual("fail", mcp_check["status"])

    def test_runtime_versions_match_package_manifest(self) -> None:
        package = json.loads((ROOT / "runtime" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            browser_agent.PLAYWRIGHT_MCP_VERSION,
            package["dependencies"]["@playwright/mcp"],
        )
        self.assertEqual(
            browser_agent.CHROME_DEVTOOLS_MCP_VERSION,
            package["optionalDependencies"]["chrome-devtools-mcp"],
        )

    def test_node_version_parser_requires_exact_output(self) -> None:
        self.assertEqual((20, 19, 0), browser_agent._version_tuple("v20.19.0\n"))
        self.assertIsNone(browser_agent._version_tuple("v20.19.0 unexpected"))

    def test_release_version_is_consistent_across_build_entrypoints(self) -> None:
        version = json.loads(
            (ROOT / "runtime" / "package.json").read_text(encoding="utf-8")
        )["version"]
        self.assertEqual(version, browser_agent.TOOL_VERSION)
        expected_fragments = {
            ROOT / "scripts" / "build-offline-bundle.sh": (
                f'ARTIFACT_NAME="browser-agent-runtime-{version}-'
            ),
            ROOT / "scripts" / "build-offline-bundle.ps1": (
                f'$ArtifactName = "browser-agent-runtime-{version}-'
            ),
            ROOT / "scripts" / "build-transfer-kit.py": (
                f'TOOLKIT_VERSION = "{version}"'
            ),
            ROOT / "scripts" / "generate_sbom.py": f'"version": "{version}"',
            ROOT / "scripts" / "write_build_metadata.py": (
                f'"runtimeVersion": "{version}"'
            ),
        }
        for path, fragment in expected_fragments.items():
            with self.subTest(path=path.name):
                self.assertIn(fragment, path.read_text(encoding="utf-8"))

        workflow = ROOT / ".github" / "workflows" / "windows-release.yml"
        if workflow.is_file():
            workflow_text = workflow.read_text(encoding="utf-8")
            workflow_release_versions = set(
                re.findall(
                    r"(?:browser-agent-runtime|intranet-browser-agent-transfer)-"
                    r"(\d+\.\d+\.\d+)",
                    workflow_text,
                )
            )
            self.assertEqual({version}, workflow_release_versions)

    def test_runtime_uses_link_free_pnpm_layout(self) -> None:
        settings = (ROOT / "runtime" / "pnpm-workspace.yaml").read_text(encoding="utf-8")
        self.assertIn("nodeLinker: hoisted", settings)
        self.assertIn("packageImportMethod: copy", settings)
        self.assertFalse((ROOT / "runtime" / ".npmrc").exists())


if __name__ == "__main__":
    unittest.main()
