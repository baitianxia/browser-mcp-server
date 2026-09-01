from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "configure_windows_pilot", ROOT / "scripts" / "configure_windows_pilot.py"
)
assert SPEC and SPEC.loader
configure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure)


class WindowsPilotSetupTests(unittest.TestCase):
    def template(self) -> dict:
        return json.loads(
            (ROOT / "config" / "deployment.windows-pilot.json.template").read_text(
                encoding="utf-8"
            )
        )

    def manifest(self) -> dict:
        user_root = r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent"
        return configure.build_manifest(
            self.template(),
            runtime_root=user_root + r"\releases\runtime-1",
            config_root=user_root + r"\config\pilot",
            output_directory=user_root + r"\output\pilot",
            profile_owner=r"CORP\pilot-user",
            browser_channel="chrome",
            browser_executable=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            node_executable=user_root + r"\releases\runtime-1\node\node.exe",
        )

    def test_builds_valid_user_scoped_windows_pilot_manifest(self) -> None:
        manifest = self.manifest()
        self.assertEqual([], configure.browser_agent.validate_manifest(manifest))
        self.assertEqual("user", manifest["mcpScope"])
        self.assertEqual([], manifest["workspaceRoots"])
        self.assertNotIn("$schema", manifest)
        self.assertNotIn("network", manifest)
        self.assertNotIn("dataBoundary", manifest)
        self.assertEqual("extension", manifest["mode"])
        self.assertNotIn("userDataDir", manifest["browser"])
        self.assertEqual(
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            manifest["browser"]["executablePath"],
        )
        self.assertEqual("manual-pilot", manifest["browser"]["extensionDistribution"])
        self.assertTrue(manifest["browser"]["manualConnectionApproval"])
        self.assertIn(r"\AppData\Local\IntranetBrowserAgent", manifest["installRoot"])

    def test_user_scope_rejects_project_roots(self) -> None:
        manifest = self.manifest()
        manifest["workspaceRoots"] = [r"C:\BrowserAgent\Workspace"]
        errors = configure.browser_agent.validate_manifest(manifest)
        self.assertTrue(any("must be empty for user-scoped MCP" in item for item in errors))

    def test_post_install_browser_preferences_cover_supported_combinations(self) -> None:
        user_root = r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent"
        dedicated = configure.build_manifest(
            self.template(),
            runtime_root=user_root + r"\releases\runtime-1",
            config_root=user_root + r"\config\pilot",
            output_directory=user_root + r"\output\pilot",
            profile_owner=r"CORP\pilot-user",
            browser_channel="chrome",
            browser_executable=(
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            ),
            node_executable=user_root + r"\releases\runtime-1\node\node.exe",
            browser_mode="dedicated",
            headless=True,
            user_data_dir=user_root + r"\browser-profile\pilot",
        )
        self.assertEqual([], configure.browser_agent.validate_manifest(dedicated))
        self.assertEqual("persistent", dedicated["mode"])
        self.assertTrue(dedicated["browser"]["headless"])
        self.assertEqual(
            user_root + r"\browser-profile\pilot",
            dedicated["browser"]["userDataDir"],
        )
        self.assertNotIn("manualConnectionApproval", dedicated["browser"])
        playwright = configure.browser_agent._render_playwright(dedicated)
        self.assertNotIn("extension", playwright)
        self.assertTrue(playwright["browser"]["launchOptions"]["headless"])
        self.assertEqual(
            dedicated["browser"]["executablePath"],
            playwright["browser"]["launchOptions"]["executablePath"],
        )

        remembered = self.manifest()
        configure.apply_browser_preferences(
            remembered,
            browser_mode="extension",
            headless=False,
            extension_authorization="user",
            user_data_dir=None,
        )
        self.assertEqual([], configure.browser_agent.validate_manifest(remembered))
        self.assertFalse(remembered["browser"]["manualConnectionApproval"])

        with self.assertRaisesRegex(
            configure.ConfiguratorError, "headless mode cannot be combined"
        ):
            configure.apply_browser_preferences(
                self.manifest(),
                browser_mode="extension",
                headless=True,
                extension_authorization="session",
                user_data_dir=None,
            )

    def test_reconfigure_writes_a_complete_rendered_dedicated_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed_manifest = root / "installed.json"
            manifest_out = root / "staged" / "deployment.json"
            render_out = root / "rendered"
            installed_manifest.write_text(
                json.dumps(self.manifest()), encoding="utf-8"
            )
            dedicated_profile = (
                r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent"
                r"\browser-profile\pilot"
            )
            configure.reconfigure(
                argparse.Namespace(
                    manifest=installed_manifest,
                    manifest_out=manifest_out,
                    render_out=render_out,
                    browser_mode="dedicated",
                    headless="true",
                    extension_authorization="session",
                    user_data_dir=dedicated_profile,
                    force=False,
                )
            )
            staged = json.loads(manifest_out.read_text(encoding="utf-8"))
            rendered = json.loads(
                (render_out / "playwright.config.json").read_text(encoding="utf-8")
            )
            mcp = json.loads((render_out / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual("persistent", staged["mode"])
            self.assertEqual(dedicated_profile, staged["browser"]["userDataDir"])
            self.assertTrue(rendered["browser"]["launchOptions"]["headless"])
            self.assertEqual(
                staged["browser"]["executablePath"],
                rendered["browser"]["launchOptions"]["executablePath"],
            )
            arguments = mcp["mcpServers"]["intranet-browser-agent"]["args"]
            self.assertIn("--browser=chrome", arguments)
            self.assertIn(
                f"--executable-path={staged['browser']['executablePath']}",
                arguments,
            )
            self.assertNotIn("extension", rendered)

    def test_settings_tool_is_installed_for_later_user_changes(self) -> None:
        installer = (ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.ps1").read_text(
            encoding="utf-8"
        )
        settings = (ROOT / "scripts" / "BROWSER-AGENT-SETTINGS.ps1").read_text(
            encoding="utf-8"
        )
        launcher = (ROOT / "scripts" / "BROWSER-AGENT-SETTINGS.cmd").read_text(
            encoding="utf-8"
        )
        self.assertIn("Install-BrowserAgentSettingsTool", installer)
        self.assertIn('"BROWSER-AGENT-SETTINGS.cmd"', installer)
        self.assertIn('"current-version.txt"', installer)
        self.assertIn('"templates\\CLAUDE.browser.md"', installer)
        self.assertIn("SETTINGS TOOL RESTORE", installer)
        self.assertIn("[IO.Directory]::Move", settings)
        self.assertNotIn("Move-Item", settings)
        self.assertIn('"extension", "dedicated"', settings)
        self.assertIn('"headed", "headless"', settings)
        self.assertIn('"session", "user"', settings)
        self.assertIn("Read-Host \"扩展令牌\" -AsSecureString", settings)
        self.assertIn("绕过扩展批准页", settings)
        self.assertIn("chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/status.html", settings)
        self.assertIn('"--extension-token-stdin"', settings)
        self.assertIn("-StandardInput $ExtensionToken", settings)
        self.assertNotIn('"extension-token.txt"', settings)
        self.assertIn("$ConfigBackedUp = $true", settings)
        self.assertIn("Resolve-ClaudeCodeInvocation", settings)
        self.assertNotIn("npm.cmd", settings + launcher)
        self.assertNotIn("npx", settings + launcher)
        self.assertIn("maintenance\\%SETTINGS_VERSION%", launcher)
        settings_install_call = installer.rindex("Install-BrowserAgentSettingsTool `")
        registration_call = installer.index("Invoke-Python $RegistrarArguments")
        commit_marker = installer.index("$UserConfigCommitted = $true")
        self.assertLess(registration_call, settings_install_call)
        self.assertLess(settings_install_call, commit_marker)

    def test_powershell_launcher_is_per_user_and_project_independent(self) -> None:
        installer = (ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.ps1").read_text(
            encoding="utf-8"
        )
        launcher = (ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.cmd").read_text(
            encoding="utf-8"
        )
        registrar = (
            ROOT / "scripts" / "register_claude_user_mcp.py"
        ).read_text(encoding="utf-8")
        discovery = (
            ROOT / "scripts" / "windows-tool-discovery.ps1"
        ).read_text(encoding="utf-8")
        metadata_validator = (
            ROOT / "scripts" / "validate_windows_release_metadata.py"
        ).read_text(encoding="utf-8")
        target_automation = (
            installer + launcher + registrar + discovery + metadata_validator
        )
        self.assertNotIn("ExecutionPolicy", installer + launcher)
        self.assertNotIn("ProjectRoot", installer)
        self.assertNotIn("--project-root", installer)
        self.assertNotIn("install-project", installer)
        self.assertNotIn("Test-Administrator", installer)
        self.assertNotIn("-Verb RunAs", installer)
        self.assertNotIn("C:\\ProgramData", installer)
        self.assertNotIn("icacls.exe", installer)
        self.assertNotIn("Read-Host", installer)
        self.assertNotIn("ProfileDirectory", installer)
        self.assertNotIn("--profile-directory", installer)
        self.assertIn("$env:LOCALAPPDATA", installer)
        self.assertIn("USERPROFILE 和 LOCALAPPDATA 必须是本机盘符绝对路径", installer)
        self.assertNotIn("Invoke-ExternalOptional", installer)
        self.assertNotIn('"mcp", "remove"', installer)
        self.assertNotIn('"mcp", "add"', installer)
        self.assertIn("register_claude_user_mcp.py", installer)
        self.assertIn("smoke_playwright_mcp.py", installer)
        self.assertIn("check_playwright_extension.py", installer)
        self.assertIn("validate_playwright_extension.py", installer)
        self.assertIn("validate_windows_release_metadata.py", installer)
        self.assertNotIn('Invoke-Python @($McpRegistrar, "self-test")', installer)
        self.assertIn(
            '"--claude-executable", [string]$ClaudeInvocation.Executable', installer
        )
        self.assertIn('"--claude-prefix", [string]$ClaudePrefixArgument', installer)
        self.assertIn('"--node-executable", $NodeExe', installer)
        self.assertIn('"--playwright-cli", $PlaywrightCliPath', installer)
        self.assertIn('"--browser-channel", $BrowserChannel', installer)
        self.assertIn('"--browser-executable", $BrowserExecutable', installer)
        self.assertNotIn('"--wrapper"', installer)
        self.assertNotIn('Get-Command "claude.cmd"', installer)
        self.assertNotIn("function Get-ClaudeExecutable", installer)
        self.assertIn("Resolve-ClaudeCodeInvocation", installer)
        self.assertIn('"claude.exe", "claude.cmd"', discovery)
        self.assertIn('".local\\bin\\claude.exe"', discovery)
        self.assertIn("Resolve-ClaudeCodeInvocation", discovery)
        self.assertIn("Resolve-NpmClaudeInvocation", discovery)
        self.assertIn('"node_modules\\@anthropic-ai\\claude-code"', discovery)
        self.assertIn('Package.bin.PSObject.Properties["claude"]', discovery)
        self.assertIn('"--user-config", $ClaudeUserConfigPath', installer)
        self.assertIn("[IO.Path]::IsPathRooted($ClaudeConfigDirectory)", installer)
        self.assertIn("$ClaudeConfigDirectory -notmatch '^[A-Za-z]:[\\\\/]'", installer)
        self.assertIn("CLAUDE_CONFIG_DIR 必须是本机盘符绝对路径", installer)
        self.assertIn('("mcp", "remove", server_name, "--scope", "user")', registrar)
        self.assertIn('"--transport",', registrar)
        self.assertIn('"--scope",', registrar)
        self.assertIn('("mcp", "get", server_name)', registrar)
        self.assertIn("claude-user-config.json.bak", installer)
        self.assertIn("ROLLBACK: restored Claude Code user configuration", installer)
        self.assertIn('if ($BrowserChannel -eq "auto")', installer)
        self.assertIn('Test-BrowserInstalled "chrome"', installer)
        self.assertIn('Test-BrowserInstalled "msedge"', installer)
        self.assertIn("ExtensionInstallForcelist", installer)
        self.assertIn("$CrxUri = ([Uri]$InstalledExtensionCrx).AbsoluteUri", installer)
        self.assertIn('codebase="$EscapedCrxUri"', installer)
        self.assertIn("Start-Process -FilePath $BrowserExecutable", installer)
        self.assertIn("Test-PlaywrightExtension", installer)
        self.assertIn('"unpackedPath"', installer)
        self.assertIn('"--unpacked-directory", $InstalledExtensionUnpacked', installer)
        self.assertIn('"--expected-version", $ExtensionVersion', installer)
        self.assertIn(
            '"--approved-unpacked-path", $InstalledExtensionUnpacked', installer
        )
        self.assertIn('"chrome://extensions"', installer)
        self.assertIn('"edge://extensions"', installer)
        self.assertIn("开发者模式", installer)
        self.assertIn("加载已解压的扩展程序", installer)
        self.assertIn("无需重新运行安装器", installer)
        self.assertIn("Restore-ExtensionPolicyChange", installer)
        self.assertIn("[int]$ManualExtensionWaitSeconds = 0", installer)
        self.assertNotIn("chromewebstore.google.com", installer)
        self.assertNotIn("Invoke-WebRequest", installer)
        self.assertNotIn("clients2.google.com", installer)
        self.assertNotIn("AllowedOrigins", installer)
        self.assertNotIn("批准单号", installer)
        self.assertIn('Invoke-Python @($Verifier, $PSScriptRoot)', installer)
        self.assertNotIn("BROWSER_AGENT_NODE", installer)
        self.assertIn("未修改系统 Node.js", installer)
        self.assertIn("Detailed error log:", launcher)
        self.assertNotIn("npm.cmd", target_automation)
        self.assertNotIn("pnpm.cmd", target_automation)
        self.assertNotIn("npx.cmd", target_automation)
        self.assertIn("不会安装、升级或修复 Claude Code", installer)
        self.assertIn('-LogPath "%INSTALL_LOG%" %*', launcher)
        self.assertNotIn("verify-windows-release.ps1", launcher)
        self.assertNotIn("TRANSFER_ROOT", launcher)
        self.assertNotIn("GATE_LOG", launcher)
        self.assertNotIn("Gate log:", launcher)
        self.assertNotIn('"unittest"', installer + launcher)
        self.assertNotIn('"self-test"', installer + launcher)
        self.assertEqual(1, launcher.count("powershell.exe -NoProfile -File"))
        self.assertIn(
            'powershell.exe -NoProfile -File "%~dp0INSTALL-WINDOWS-PILOT.ps1"',
            launcher,
        )
        self.assertIn("targetCliSmokeTested", metadata_validator)
        self.assertIn("crossBuilt must be false", metadata_validator)
        self.assertIn("buildHost must be exactly windows/x64", metadata_validator)
        self.assertLess(
            installer.index("Invoke-Python @($ReleaseMetadataVerifier, $KitMetadataPath)"),
            installer.index('"--approval-file", $ExtensionApproval'),
        )

        function_ranges = (
            ("Invoke-External", "Invoke-Python"),
            ("Invoke-Python", "Get-NodeInfo"),
            ("Get-NodeInfo", "Test-BrowserInstalled"),
        )
        for function_name, next_function_name in function_ranges:
            start = installer.index(f"function {function_name}")
            end = installer.index(f"function {next_function_name}", start)
            function_body = installer[start:end]
            self.assertIn('$ErrorActionPreference = "Continue"', function_body)
            self.assertIn(
                "$ErrorActionPreference = $PreviousErrorActionPreference", function_body
            )

    def test_publisher_windows_release_gate_is_full_and_not_a_target_launcher_step(
        self,
    ) -> None:
        gate = (ROOT / "scripts" / "verify-windows-release.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('$PSVersionTable.PSEdition -ne "Desktop"', gate)
        self.assertIn('[Management.Automation.Language.Parser]::ParseFile', gate)
        self.assertIn("Get-ChildItem -LiteralPath $PSScriptRoot", gate)
        self.assertIn('"unittest", "discover"', gate)
        self.assertIn('"register_claude_user_mcp.py"', gate)
        self.assertIn('"self-test"', gate)
        self.assertIn("intranet-browser-agent-release-probe", gate)
        self.assertIn("$env:CLAUDE_CONFIG_DIR = $ProbeClaudeConfig", gate)
        self.assertIn(
            '"--claude-executable", [string]$ClaudeInvocation.Executable', gate
        )
        self.assertIn('"--claude-prefix", [string]$ClaudePrefixArgument', gate)
        self.assertIn('"--node-executable", $ProbeNode', gate)
        self.assertIn('"--playwright-cli", $ProbePlaywrightCli', gate)
        self.assertIn('"validate_node_distribution.py"', gate)
        self.assertIn("Get-NativeOutput $ProbeNode", gate)
        self.assertIn('"smoke_playwright_mcp.py"', gate)
        self.assertIn('[string]$TransferPath = ""', gate)
        self.assertIn("Resolve-Path -LiteralPath $TransferPath", gate)
        self.assertIn("TransferPath must be the extracted transfer directory", gate)
        self.assertIn(
            "Release gate must run from the same extracted transfer directory", gate
        )
        self.assertNotIn("TransferArchive", gate)
        self.assertIn('[string]$LogPath = ""', gate)
        self.assertIn("Start-Transcript -LiteralPath $LogPath", gate)
        self.assertIn("Stop-Transcript", gate)
        self.assertIn('$env:PYTHONDONTWRITEBYTECODE = "1"', gate)
        self.assertIn("Remove-Item Env:PYTHONDONTWRITEBYTECODE", gate)
        self.assertIn("WINDOWS POWERSHELL 5.1 RELEASE GATE PASSED", gate)
        self.assertIn('Join-Path $PSScriptRoot "windows-tool-discovery.ps1"', gate)
        self.assertIn("Resolve-ClaudeCodeInvocation", gate)
        self.assertIn('"validate_windows_release_metadata.py"', gate)
        self.assertLess(
            gate.index('"validate_windows_release_metadata.py"'),
            gate.index('"unittest", "discover"'),
        )
        launcher = (ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.cmd").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.ps1").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("verify-windows-release.ps1", launcher)
        self.assertNotIn('Invoke-Python @($McpRegistrar, "self-test")', installer)

        verify_call = '(Join-Path $PSScriptRoot "verify-bundle.py")'
        verify_positions: list[int] = []
        offset = 0
        while True:
            position = gate.find(verify_call, offset)
            if position < 0:
                break
            verify_positions.append(position)
            offset = position + 1
        self.assertEqual(4, len(verify_positions))
        tests = gate.index('"unittest", "discover"')
        probe = gate.index(
            '"--server-name", "intranet-browser-agent-release-probe"'
        )
        self.assertLess(verify_positions[0], tests)
        self.assertLess(tests, verify_positions[1])
        self.assertLess(verify_positions[1], verify_positions[2])
        self.assertLess(verify_positions[2], probe)
        self.assertLess(probe, verify_positions[3])

    def test_optional_browser_automation_failures_use_manual_fallback(self) -> None:
        installer = (ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.ps1").read_text(
            encoding="utf-8"
        )

        policy_start = installer.index("$PolicyAttemptAvailable = $false")
        policy_end = installer.index("if ($PolicyAttemptAvailable)", policy_start)
        policy_block = installer[policy_start:policy_end]
        policy_write = policy_block.index("New-ItemProperty")
        change_recorded = policy_block.index(
            "$ExtensionPolicyChangeStarted = $true"
        )
        self.assertLess(policy_write, change_recorded)
        self.assertIn("} catch {", policy_block)
        self.assertIn("OFFLINE EXTENSION POLICY UNAVAILABLE", policy_block)
        self.assertIn("Ensure-CurrentUserRegistryKey", policy_block)
        self.assertIn("浏览器扩展策略键创建结果不一致", policy_block)
        self.assertNotIn("CreateSubKey", policy_block)
        self.assertIn("Restore-ExtensionPolicyChange", policy_block)
        self.assertIn("不会中止或要求重新运行", policy_block)
        self.assertNotIn("exit ", policy_block)

        manual_start = installer.index(
            "Start-Process -FilePath $BrowserExecutable",
            policy_end,
        )
        manual_marker = installer.index(
            '"MANUAL EXTENSION LOAD REQUIRED:', manual_start
        )
        manual_block = installer[manual_start:manual_marker]
        self.assertIn("} catch {", manual_block)
        self.assertIn("WARNING: could not open browser extension page", manual_block)
        self.assertIn("请在浏览器地址栏手动打开", manual_block)
        self.assertIn("WARNING: clip.exe returned exit code", manual_block)
        self.assertIn("WARNING: clip.exe is unavailable", manual_block)
        self.assertIn("安装器正在等待", manual_block)
        self.assertNotIn("throw ", manual_block)

        restore_start = installer.index("function Restore-ExtensionPolicyChange")
        restore_end = installer.index("\ntry {", restore_start)
        restore_block = installer[restore_start:restore_end]
        self.assertIn("$script:ExtensionPolicyKeyCreated", restore_block)
        self.assertIn("$script:ExtensionPolicyCreatedPaths", restore_block)
        self.assertIn("$CreatedPolicyIndex -= 1", restore_block)
        self.assertIn("$script:ExtensionPolicyPreviousValueKind", restore_block)
        self.assertIn("浏览器扩展策略原值恢复后核对不一致", restore_block)
        self.assertIn("浏览器扩展策略新增值删除后仍然存在", restore_block)
        self.assertIn("Remove-ItemProperty", restore_block)
        self.assertIn(
            "WARNING: could not remove empty browser extension policy key",
            restore_block,
        )

    def test_windows_ci_separates_manual_boundary_and_live_session_e2e(self) -> None:
        if not (ROOT / ".github").is_dir():
            self.skipTest("CI-only workflow files are not part of the transfer kit")
        workflow = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(
            encoding="utf-8"
        )
        loader = (
            ROOT / ".github" / "scripts" / "load-unpacked-extension-cdp.js"
        ).read_text(encoding="utf-8")
        exercise = (
            ROOT / ".github" / "scripts" / "exercise-extension-mcp.py"
        ).read_text(encoding="utf-8")
        access_denied_injector = (
            ROOT
            / ".github"
            / "scripts"
            / "inject-runtime-publish-access-denied.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("CI-only session surrogate", loader)
        self.assertIn("not evidence that the manual load survives a restart", loader)
        self.assertIn('send("Extensions.loadUnpacked"', loader)
        self.assertIn("sessionOnly: loadedForSession", loader)
        self.assertIn('source: loadedForSession ? "session-surrogate"', loader)
        self.assertIn('"ready-file"', loader)
        self.assertIn('"stop-file"', loader)
        self.assertIn("waitForStop", loader)
        self.assertNotIn('"Input.dispatchDragEvent"', loader)
        self.assertNotIn("chrome.developerPrivate.loadDirectory", loader)
        self.assertNotIn("webkitRequestFileSystem", loader)
        self.assertIn("windowsHide: true", loader)
        self.assertNotIn('"--enable-automation"', loader)
        self.assertIn('send("Browser.close")', loader)
        self.assertIn("SESSION_COOKIE_VALUE", loader)
        self.assertIn("readExtensionAuthToken", loader)
        self.assertIn('document.querySelector(".auth-token-code")', loader)
        self.assertIn("PLAYWRIGHT_MCP_EXTENSION_TOKEN=", loader)
        self.assertNotIn('localStorage.setItem("auth-token"', loader)
        self.assertIn("process.exit(1)", loader)

        self.assertIn("exercise-extension-mcp.py", workflow)
        self.assertIn(
            "Exercise post-install authorization, headless, and Profile settings",
            workflow,
        )
        self.assertIn("CI USER EXTENSION AUTHORIZATION PASSED", workflow)
        self.assertIn("CI DEDICATED HEADLESS PROFILE ISOLATION PASSED", workflow)
        self.assertIn("CI DEDICATED HEADED CONFIGURATION PASSED", workflow)
        self.assertIn("CI SETTINGS FAILURE ROLLBACK PASSED", workflow)
        self.assertIn("WINDOWS POST-INSTALL SETTINGS PASSED", workflow)
        self.assertIn("CI EXISTING NPM CLAUDE READY", workflow)
        self.assertIn("CI failed to hide native claude.exe from PATH", workflow)
        self.assertIn("The official per-user Claude Code fallback path is missing", workflow)
        self.assertIn("Prepare exact native and npm Claude Code CI fixtures", workflow)
        self.assertIn("CI-only native Claude Code", workflow)
        self.assertIn("CI-only npm Claude Code", workflow)
        self.assertIn("Prove native Claude compatibility", workflow)
        self.assertIn("PUBLISHER_GATE_LOG", workflow)
        self.assertIn("WINDOWS-PUBLISHER-RELEASE-GATE.log", workflow)
        self.assertIn("publisher release gate did not run the registration self-test", workflow)
        self.assertIn("target installer unexpectedly ran the publisher-only self-test", workflow)
        self.assertIn("publisher release gate did not run the complete Python suite", workflow)
        self.assertIn("target installer unexpectedly ran the publisher-only unit suite", workflow)
        self.assertIn("single-pass one-click install both passed", workflow)
        self.assertIn("Resolve-ClaudeCodeInvocation", workflow)
        self.assertIn("The shared resolver did not select the existing npm", workflow)
        self.assertIn("NPM_CLAUDE_COMMAND", workflow)
        self.assertIn("NPM_CLAUDE_NODE", workflow)
        fixture_step = workflow.split(
            "- name: Prepare exact native and npm Claude Code CI fixtures", 1
        )[1].split("- name: Prove native Claude compatibility", 1)[0]
        self.assertIn("Select-Object -First 1", fixture_step)
        self.assertIn("$ActualNpmNode", fixture_step)
        self.assertIn("$env:CLAUDE_NPM_NODE_VERSION", fixture_step)
        self.assertIn("$ClaudeFirewallRule", workflow)
        self.assertIn('"CHROME_EXE=$ChromeExe"', workflow)
        self.assertIn('$ChromeExe = [string]$env:CHROME_EXE', workflow)
        self.assertIn("--browser-executable $ChromeExe", workflow)
        self.assertIn("Installed Playwright MCP could not reuse", workflow)
        self.assertIn("playwright-extension-ci-ready.json", workflow)
        self.assertIn("playwright-extension-ci-stop.txt", workflow)
        self.assertIn("one-click-launcher.exit.txt", workflow)
        self.assertIn("playwright-extension-ci.exit.txt", workflow)
        self.assertIn("runtime-publish-access-denied.exit.txt", workflow)
        self.assertIn("inject-runtime-publish-access-denied.ps1", workflow)
        self.assertIn("RUNTIME PUBLISH RETRY", workflow)
        self.assertIn("RUNTIME PUBLISH RECOVERED", workflow)
        self.assertIn("CI EXTENSION POLICY ACCESS DENIED ARMED", workflow)
        self.assertIn("CI EXTENSION POLICY ACCESS DENIED RESTORED", workflow)
        self.assertIn("OFFLINE EXTENSION POLICY UNAVAILABLE", workflow)
        self.assertIn("RegistryAccessRule", workflow)
        self.assertIn("RegistryRights]::SetValue", workflow)
        self.assertIn("$script:CiExtensionPolicyCreatedPaths", workflow)
        self.assertIn("$CiCreatedPolicyIndex -= 1", workflow)
        self.assertIn("$CiExtensionPolicySubKey -split '\\\\'", workflow)
        self.assertIn("[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey", workflow)
        self.assertIn(".GetAccessControl(", workflow)
        self.assertIn(".SetAccessControl(", workflow)
        self.assertIn("[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey", workflow)
        self.assertNotIn("Get-Acl -LiteralPath $CiExtensionPolicyPath", workflow)
        self.assertNotIn("Set-Acl -LiteralPath $CiExtensionPolicyPath", workflow)
        self.assertIn("CiExtensionPolicyOriginalAccessSddl", workflow)
        self.assertIn("RestoredPolicyAccessSddl", workflow)
        self.assertIn("did not restore the exact extension-policy access ACL", workflow)
        self.assertIn("did not restore the absent extension-policy path", workflow)
        self.assertIn("ci-invalid-extension-leftover.txt", workflow)
        self.assertIn("EXTENSION INVALID DIRECTORY QUARANTINED", workflow)
        self.assertIn("was not preserved exactly once", workflow)
        self.assertIn("exactly one installer process", workflow)
        self.assertIn("Read-CiExitCode", workflow)
        self.assertNotIn("$LauncherProcess.ExitCode", workflow)
        self.assertNotIn("$CdpProcess.ExitCode", workflow)
        self.assertNotIn("$RuntimeLockProcess.ExitCode", workflow)
        self.assertIn("-RemoteAddress Internet", workflow)
        self.assertIn("native folder picker", workflow)
        self.assertIn("claiming that CI performed or persisted", workflow)
        self.assertIn("One-click Windows launcher did not continue", workflow)
        self.assertIn("Get-Content -LiteralPath $LauncherStdout", workflow)
        self.assertIn("taskkill.exe", workflow)
        self.assertIn("/PID $LauncherProcess.Id /T /F", workflow)
        self.assertLess(
            workflow.index("$CdpReady = Get-Content"),
            workflow.index("$LauncherProcess.WaitForExit(300000)"),
        )
        self.assertLess(
            workflow.index("python $ExtensionExercise"),
            workflow.index("Remove-NetFirewallRule -DisplayName $FirewallRule"),
        )

        self.assertIn('"browser_navigate"', exercise)
        self.assertIn('"browser_snapshot"', exercise)
        self.assertIn("OFFLINE-INTRANET-SESSION-REUSED", exercise)
        self.assertIn("session_cookie=isolated", exercise)
        self.assertIn("unexpectedly reused the existing browser session", exercise)
        self.assertIn("session_cookie=reused", exercise)
        self.assertNotIn('"browser_close"', exercise)

        self.assertIn("Set-Acl", access_denied_injector)
        self.assertIn("FileSystemRights]::Delete", access_denied_injector)
        self.assertIn(
            "FileSystemRights]::DeleteSubdirectoriesAndFiles",
            access_denied_injector,
        )
        self.assertIn("AccessControlType]::Deny", access_denied_injector)
        self.assertIn("OriginalRuntimeAccessSddl", access_denied_injector)
        self.assertIn("OriginalParentAccessSddl", access_denied_injector)
        self.assertIn("pnpm-workspace.yaml", access_denied_injector)
        extraction_marker = access_denied_injector.index(
            '$ExtractionCompletionMarker = Join-Path $DeniedPath "pnpm-workspace.yaml"'
        )
        self.assertLess(
            access_denied_injector.index(
                "Set-Acl -LiteralPath $DeniedParentPath -AclObject $ParentAcl"
            ),
            extraction_marker,
        )
        self.assertLess(
            access_denied_injector.index(
                "Set-Acl -LiteralPath $DeniedPath -AclObject $RuntimeAcl"
            ),
            extraction_marker,
        )
        self.assertIn(
            "Set-Acl -LiteralPath $DeniedPath -AclObject $RestoreRuntimeAcl",
            access_denied_injector,
        )
        self.assertIn(
            "Set-Acl -LiteralPath $DeniedParentPath -AclObject $RestoreParentAcl",
            access_denied_injector,
        )
        self.assertIn("InstallLogRoot", access_denied_injector)
        self.assertIn("RUNTIME PUBLISH RETRY", access_denied_injector)
        self.assertIn("CI RUNTIME PUBLISH RETRY OBSERVED", workflow)
        self.assertNotIn("-HoldSeconds", workflow)
        self.assertIn("CI RUNTIME PUBLISH ACCESS DENIED ARMED", access_denied_injector)
        self.assertIn("CI RUNTIME PUBLISH ACCESS DENIED RESTORED", access_denied_injector)

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell 5.1")
    def test_native_claude_resolver_finds_official_path_when_path_is_stale(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            user_profile = Path(temporary) / "profile"
            claude = user_profile / ".local" / "bin" / "claude.exe"
            claude.parent.mkdir(parents=True)
            claude.write_bytes(b"MZ")
            resolver = ROOT / "scripts" / "windows-tool-discovery.ps1"

            def ps_literal(value: Path) -> str:
                return "'" + str(value).replace("'", "''") + "'"

            command = (
                "$ErrorActionPreference = 'Stop'; "
                f"$env:USERPROFILE = {ps_literal(user_profile)}; "
                "$env:PATH = ''; "
                f". {ps_literal(resolver)}; "
                "$Resolved = Resolve-ClaudeCodeInvocation; "
                f"if ($Resolved.CommandPath -ne {ps_literal(claude)} -or "
                "$Resolved.Executable -ne $Resolved.CommandPath -or "
                "$Resolved.Kind -ne 'native' -or @($Resolved.Prefix).Count -ne 0) { "
                "throw ('Unexpected Claude invocation: ' + ($Resolved | Out-String)) }; "
                f"$Explicit = Resolve-ClaudeCodeInvocation -ExplicitPath "
                f"{ps_literal(user_profile / 'missing.exe')}; "
                "if ($Explicit) { throw 'An invalid explicit path must not fall back' }"
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                0,
                result.returncode,
                msg=f"stdout={result.stdout}\nstderr={result.stderr}",
            )

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell 5.1")
    def test_npm_claude_resolver_uses_installed_package_without_running_npm(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_profile = root / "profile"
            native_fallback = user_profile / ".local" / "bin" / "claude.exe"
            native_fallback.parent.mkdir(parents=True)
            native_fallback.write_bytes(b"MZNATIVE")

            npm_root = root / "npm prefix with spaces"
            npm_root.mkdir(parents=True)
            command = npm_root / "claude.cmd"
            command.write_bytes(b"@echo off\r\nrem npm Claude fixture\r\n")
            node = npm_root / "node.exe"
            node.write_bytes(b"MZNODE")
            package_root = (
                npm_root / "node_modules" / "@anthropic-ai" / "claude-code"
            )
            package_root.mkdir(parents=True)
            cli = package_root / "cli.js"
            cli.write_bytes(b"// existing npm Claude Code entry\n")
            (package_root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@anthropic-ai/claude-code",
                        "bin": {"claude": "cli.js"},
                    }
                ),
                encoding="utf-8",
            )
            resolver = ROOT / "scripts" / "windows-tool-discovery.ps1"

            def ps_literal(value: Path) -> str:
                return "'" + str(value).replace("'", "''") + "'"

            powershell = (
                "$ErrorActionPreference = 'Stop'; "
                f"$env:USERPROFILE = {ps_literal(user_profile)}; "
                f"$env:PATH = {ps_literal(npm_root)}; "
                f". {ps_literal(resolver)}; "
                "$Resolved = Resolve-ClaudeCodeInvocation; "
                "$ResolvedPrefix = @($Resolved.Prefix); "
                "function Get-TestFileHash { param([string]$Path) "
                "if (-not $Path -or -not "
                "(Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }; "
                "return (Get-FileHash -LiteralPath $Path "
                "-Algorithm SHA256).Hash }; "
                "$ResolvedCommandHash = Get-TestFileHash "
                "([string]$Resolved.CommandPath); "
                "$ResolvedNodeHash = Get-TestFileHash "
                "([string]$Resolved.Executable); "
                "$ResolvedCliHash = if ($ResolvedPrefix.Count -eq 1) { "
                "Get-TestFileHash $ResolvedPrefix[0] } else { '' }; "
                f"$ExpectedCommandHash = Get-TestFileHash {ps_literal(command)}; "
                f"$ExpectedNodeHash = Get-TestFileHash {ps_literal(node)}; "
                f"$ExpectedCliHash = Get-TestFileHash {ps_literal(cli)}; "
                "if ($ResolvedCommandHash -ne $ExpectedCommandHash -or "
                "$ResolvedNodeHash -ne $ExpectedNodeHash -or "
                "$Resolved.Kind -ne 'npm' -or $ResolvedPrefix.Count -ne 1 -or "
                "$ResolvedCliHash -ne $ExpectedCliHash) { "
                "throw ('Unexpected npm Claude invocation: ' + "
                "([ordered]@{ CommandPath = $Resolved.CommandPath; "
                "Executable = $Resolved.Executable; Kind = $Resolved.Kind; "
                "Prefix = $ResolvedPrefix } | "
                "ConvertTo-Json -Compress)) }; "
                f"$Explicit = Resolve-ClaudeCodeInvocation -ExplicitPath "
                f"{ps_literal(command)}; "
                "if ($Explicit.Kind -ne 'npm') { "
                "throw 'Explicit npm Claude command was not accepted' }"
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    powershell,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                0,
                result.returncode,
                msg=f"stdout={result.stdout}\nstderr={result.stderr}",
            )

    def test_windows_installer_stages_and_rolls_back_runtime_and_config(self) -> None:
        installer = (ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '("r-" + [guid]::NewGuid().ToString("N").Substring(0, 12))',
            installer,
        )
        self.assertIn('$StagingRoot = Join-Path $AgentRoot "staging"', installer)
        path_guard = installer.index('"assert-user-paths"')
        first_install_write = installer.index(
            "New-Item -ItemType Directory -Path $AgentRoot -Force"
        )
        self.assertLess(path_guard, first_install_write)
        staged_runtime_verify = installer.index(
            "Invoke-Python @($Verifier, $StagedRuntimeRoot)"
        )
        staged_node_verify = installer.index("$StagedBundledNodeRoot =")
        runtime_publish = installer.index(
            "Publish-StagedRuntime -Source $StagedRuntimeRoot -Destination $RuntimeRoot"
        )
        self.assertLess(staged_runtime_verify, runtime_publish)
        self.assertLess(staged_runtime_verify, staged_node_verify)
        self.assertLess(staged_node_verify, runtime_publish)
        self.assertIn("function Test-RetryableDirectoryMoveError", installer)
        self.assertIn("function Move-DirectoryAtomicallyWithRetry", installer)
        self.assertIn("function Publish-StagedRuntime", installer)
        publish_start = installer.index("function Move-DirectoryAtomicallyWithRetry")
        publish_end = installer.index("function Publish-StagedRuntime", publish_start)
        publish_function = installer[publish_start:publish_end]
        self.assertIn("[IO.Directory]::Move($Source, $Destination)", publish_function)
        self.assertNotIn("Move-Item", publish_function)
        self.assertIn('-LogPrefix "RUNTIME PUBLISH"', installer)
        self.assertIn('"{0} RETRY {1}/{2}: {3}"', installer)
        self.assertIn('"{0} RECOVERED: attempts={1}; destination={2}"', installer)
        self.assertIn("[Math]::Min($DelayMilliseconds * 2, 5000)", installer)
        self.assertIn("无需重新运行", installer)
        self.assertIn(
            "Remove-Item -LiteralPath $RuntimeExtractionRoot -Recurse -Force -ErrorAction SilentlyContinue",
            installer,
        )

        self.assertIn(
            '$StageRoot = Join-Path $StagingRoot', installer
        )
        self.assertNotIn("[IO.Path]::GetTempPath()", installer)
        extension_verify = installer.index(
            'Write-Host "Playwright Extension 已从迁移包离线安装并验证。"'
        )
        policy_attempt = installer.index("$PolicyInstallDeadline =")
        manual_instructions = installer.index("加载已解压的扩展程序")
        manual_detected = installer.index('Write-InstallLog "MANUAL EXTENSION LOAD DETECTED"')
        stage_preflight = installer.index('"--config-root", $StageDeploy')
        old_config_backup = installer.index(
            '"CONFIG BACKUP"'
        )
        backup_complete = installer.index("$ConfigBackupComplete = $true")
        config_publish = installer.index(
            '"CONFIG PUBLISH"'
        )
        publish_complete = installer.index("$ConfigPublished = $true")
        installed_preflight = installer.index(
            '"--config-root", $ConfigRoot', config_publish
        )
        direct_cli_smoke = installer.index("$McpSmoke,", installed_preflight)
        registration = installer.index('"--server-name", $McpServerName')
        commit = installer.index("$ConfigCommitted = $true")
        self.assertLess(policy_attempt, manual_instructions)
        self.assertLess(manual_instructions, manual_detected)
        self.assertLess(manual_detected, extension_verify)
        self.assertLess(extension_verify, stage_preflight)
        self.assertLess(stage_preflight, old_config_backup)
        self.assertLess(old_config_backup, backup_complete)
        self.assertLess(backup_complete, config_publish)
        self.assertLess(config_publish, publish_complete)
        self.assertLess(old_config_backup, config_publish)
        self.assertLess(config_publish, installed_preflight)
        self.assertLess(installed_preflight, direct_cli_smoke)
        self.assertLess(direct_cli_smoke, registration)
        self.assertLess(registration, commit)
        self.assertNotIn("Move-Item", installer)
        self.assertIn('"EXTENSION PUBLISH"', installer)
        self.assertIn('"EXTENSION QUARANTINE"', installer)
        self.assertIn("EXTENSION INVALID DIRECTORY QUARANTINED", installer)
        self.assertIn("发现上次遗留的不完整扩展目录", installer)
        self.assertIn('"CONFIG ROLLBACK QUARANTINE"', installer)
        self.assertIn('"CONFIG ROLLBACK RESTORE"', installer)
        self.assertIn(
            "ROLLBACK: restored previous pilot configuration directory", installer
        )
        self.assertIn(
            "ROLLBACK: restored previous browser extension policy", installer
        )
        self.assertIn(
            "ROLLBACK: removed newly added browser extension policy", installer
        )
        self.assertIn(
            "配置发布状态不明确；已保留当前目录和备份，未执行破坏性回滚。",
            installer,
        )
        self.assertIn('$env:PYTHONDONTWRITEBYTECODE = "1"', installer)
        self.assertIn('[IO.FileShare]::None', installer)
        self.assertIn('$InstallLockStream.Dispose()', installer)

    def test_windows_build_script_handles_py_launcher_and_native_stderr(self) -> None:
        build = (ROOT / "scripts" / "build-offline-bundle.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('$script:PythonPrefix = @("-3")', build)
        self.assertIn("function Invoke-PythonChecked", build)
        self.assertIn('$env:PYTHONDONTWRITEBYTECODE = "1"', build)
        self.assertIn("Remove-Item Env:PYTHONDONTWRITEBYTECODE", build)
        self.assertNotIn("Invoke-Checked $PythonBin", build)
        self.assertIn('$ErrorActionPreference = "Continue"', build)
        self.assertIn("$ExitCode = $LASTEXITCODE", build)
        self.assertIn("Invoke-Checked $RuntimeNode", build)

    def test_windows_user_scope_path_check_is_case_insensitive_and_contained(self) -> None:
        local_app_data = r"C:\Users\Pilot\AppData\Local"
        self.assertTrue(
            configure.browser_agent._windows_path_is_within(
                r"c:\users\pilot\appdata\local\IntranetBrowserAgent\config",
                local_app_data,
            )
        )
        self.assertFalse(
            configure.browser_agent._windows_path_is_within(
                r"C:\Users\Pilot\AppData\Local-Evil\config", local_app_data
            )
        )
        self.assertFalse(
            configure.browser_agent._windows_path_is_within(
                r"C:\Users\Pilot\AppData\Local\..\Roaming\config",
                local_app_data,
            )
        )

    def test_installer_user_path_guard_rejects_resolved_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local_app_data = root / "Local"
            local_app_data.mkdir()
            self.assertEqual(
                [],
                configure.user_path_errors(
                    local_app_data,
                    [local_app_data / "IntranetBrowserAgent" / "config"],
                ),
            )
            errors = configure.user_path_errors(
                local_app_data, [root / "Roaming" / "IntranetBrowserAgent"]
            )
            self.assertTrue(any("outside LOCALAPPDATA" in error for error in errors))
            real_profile = local_app_data / "real-profile"
            real_profile.mkdir()
            linked_profile = local_app_data / "linked-profile"
            try:
                os.symlink(real_profile, linked_profile, target_is_directory=True)
            except OSError:
                return
            errors = configure.user_path_errors(local_app_data, [linked_profile])
            self.assertTrue(any("traverses a link/junction" in error for error in errors))

    def test_generate_renders_user_scope_stdio_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = root / "template.json"
            template.write_text(json.dumps(self.template()), encoding="utf-8")
            user_root = r"C:\Users\pilot\AppData\Local\IntranetBrowserAgent"
            arguments = argparse.Namespace(
                template=template,
                manifest_out=root / "pilot.json",
                render_out=root / "rendered",
                runtime_root=user_root + r"\releases\runtime-1",
                config_root=user_root + r"\config\pilot",
                output_directory=user_root + r"\output\pilot",
                profile_owner=r"CORP\pilot-user",
                browser_channel="chrome",
                browser_executable=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                node_executable=user_root
                + r"\releases\runtime-1\node\node.exe",
                force=False,
            )
            configure.generate(arguments)
            mcp = json.loads(
                (root / "rendered" / ".mcp.json").read_text(encoding="utf-8")
            )
            server = mcp["mcpServers"]["intranet-browser-agent"]
            self.assertEqual("stdio", server["type"])
            self.assertTrue(server["command"].endswith(r"node\node.exe"))
            self.assertTrue(
                server["args"][0].endswith(r"node_modules\@playwright\mcp\cli.js")
            )
            self.assertEqual("--browser=chrome", server["args"][1])
            self.assertEqual(
                r"--executable-path=C:\Program Files\Google\Chrome\Application\chrome.exe",
                server["args"][2],
            )
            self.assertEqual("--config", server["args"][3])
            playwright = json.loads(
                (root / "rendered" / "playwright.config.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("network", playwright)
            self.assertTrue(playwright["extension"])
            self.assertNotIn("browser", playwright)


if __name__ == "__main__":
    unittest.main()
