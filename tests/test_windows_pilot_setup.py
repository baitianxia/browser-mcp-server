from __future__ import annotations

import argparse
import importlib.util
import json
import os
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
        self.assertIn('Invoke-Python @($McpRegistrar, "self-test")', installer)
        self.assertIn('"--claude-executable", $ClaudeExecutable', installer)
        self.assertIn('"--node-executable", $NodeExe', installer)
        self.assertIn('"--playwright-cli", $PlaywrightCliPath', installer)
        self.assertIn('"--browser-channel", $BrowserChannel', installer)
        self.assertIn('"--browser-executable", $BrowserExecutable', installer)
        self.assertNotIn('"--wrapper"', installer)
        self.assertNotIn('Get-Command "claude.cmd"', installer)
        self.assertIn('Get-Command "claude.exe"', installer)
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
        self.assertIn('-LogPath "%INSTALL_LOG%" %*', launcher)
        self.assertIn("toolkit\\scripts\\verify-windows-release.ps1", launcher)
        self.assertIn('set "TRANSFER_ROOT=%%~fI"', launcher)
        self.assertIn('-TransferPath "%TRANSFER_ROOT%"', launcher)
        self.assertIn('-LogPath "%GATE_LOG%"', launcher)
        self.assertIn("Gate log:", launcher)
        self.assertIn("Detailed gate error log:", launcher)
        self.assertIn("Installation was not started", launcher)
        self.assertLess(
            launcher.index("verify-windows-release.ps1"),
            launcher.index('powershell.exe -NoProfile -File "%~dp0INSTALL-WINDOWS-PILOT.ps1"'),
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

    def test_windows_release_gate_requires_powershell_51_and_registration_self_test(
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
        self.assertIn('"--claude-executable", $ClaudeExecutable', gate)
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
        self.assertIn('send("Browser.close")', loader)
        self.assertIn("SESSION_COOKIE_VALUE", loader)
        self.assertIn("seedExtensionAuthToken", loader)
        self.assertIn("process.exit(1)", loader)

        self.assertIn("exercise-extension-mcp.py", workflow)
        self.assertIn('"CHROME_EXE=$ChromeExe"', workflow)
        self.assertIn('$ChromeExe = [string]$env:CHROME_EXE', workflow)
        self.assertIn("--browser-executable $ChromeExe", workflow)
        self.assertIn("Installed Playwright MCP could not reuse", workflow)
        self.assertIn("playwright-extension-ci-ready.json", workflow)
        self.assertIn("playwright-extension-ci-stop.txt", workflow)
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
        self.assertIn("session_cookie=reused", exercise)
        self.assertNotIn('"browser_close"', exercise)

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
            "Move-Item -LiteralPath $StagedRuntimeRoot -Destination $RuntimeRoot"
        )
        self.assertLess(staged_runtime_verify, runtime_publish)
        self.assertLess(staged_runtime_verify, staged_node_verify)
        self.assertLess(staged_node_verify, runtime_publish)
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
            "Move-Item -LiteralPath $ConfigRoot -Destination $ConfigBackupPath"
        )
        backup_complete = installer.index("$ConfigBackupComplete = $true")
        config_publish = installer.index(
            "Move-Item -LiteralPath $StageDeploy -Destination $ConfigRoot"
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
        self.assertIn(
            "Move-Item -LiteralPath $ConfigBackupPath -Destination $ConfigRoot",
            installer,
        )
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
