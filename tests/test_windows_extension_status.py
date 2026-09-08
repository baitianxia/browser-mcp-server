from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe" if os.name == "nt" else "pwsh")


@unittest.skipUnless(POWERSHELL, "requires PowerShell for installer helper tests")
class WindowsExtensionStatusTests(unittest.TestCase):
    def test_probe_repair_update_and_approval_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "checker.py"
            checker.write_text(
                'import os\nprint(os.environ["BROWSER_MCP_TEST_EXTENSION_STATUS"])\n',
                encoding="utf-8",
            )
            script = root / "test-status.ps1"
            script.write_text(
                r'''param([string]$Installer, [string]$Python, [string]$Checker)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Tokens = $null
$ParseErrors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $Installer, [ref]$Tokens, [ref]$ParseErrors
)
if (@($ParseErrors).Count -gt 0) { throw ($ParseErrors | Out-String) }
$Functions = $Ast.FindAll({ param($Node)
    $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $Node.Name -in @("Get-PlaywrightExtensionStatus", "Test-PlaywrightExtension",
        "Assert-PlaywrightExtensionMetadata")
}, $false)
if (@($Functions).Count -ne 3) { throw "Missing installer helpers" }
foreach ($Function in $Functions) {
    . ([scriptblock]::Create($Function.Extent.Text))
}
function Write-InstallLog { param([string]$Message) }
$script:PythonExe = $Python
$script:PythonPrefix = @()
$Arguments = @{
    Checker = $Checker; LocalAppData = $PSScriptRoot; Channel = "chrome"
    ExpectedVersion = "0.4.0"; ApprovedUnpackedPath = $PSScriptRoot
    CompatibleVersions = @("0.3.0", "0.4.0")
}
foreach ($Case in @(
    @{ Marker = "CURRENT|0.4.0"; Status = "current"; Ready = $true },
    @{ Marker = "COMPATIBLE|0.3.0"; Status = "compatible"; Ready = $false },
    @{ Marker = "INCOMPATIBLE|0.2.0"; Status = "incompatible"; Ready = $false },
    @{ Marker = "INCOMPATIBLE|"; Status = "incompatible"; Ready = $false },
    @{ Marker = "NOT_INSTALLED|"; Status = "not-installed"; Ready = $false }
)) {
    $env:BROWSER_MCP_TEST_EXTENSION_STATUS = $Case.Marker
    $Result = Get-PlaywrightExtensionStatus @Arguments
    if ($Result.Status -cne $Case.Status) { throw "Wrong status: $($Case.Marker)" }
    if ((Test-PlaywrightExtension @Arguments) -ne $Case.Ready) {
        throw "Update accepted without the current version: $($Case.Marker)"
    }
}
foreach ($Marker in @("CURRENT|0.3.0", "COMPATIBLE|0.2.0", "CURRENT",
    "COMPATIBLE|", "NOT_INSTALLED|0.4.0", "CURRENT|0.4.0|extra")) {
    $env:BROWSER_MCP_TEST_EXTENSION_STATUS = $Marker
    $Rejected = $false
    try { Get-PlaywrightExtensionStatus @Arguments | Out-Null } catch { $Rejected = $true }
    if (-not $Rejected) { throw "Invalid status accepted: $Marker" }
}
$Approval = [pscustomobject]@{
    extensionId = "mmlmfjhmonkocbjadbfplnigmagldckm"
    version = "0.4.0"; compatibleVersions = @("0.4.0")
}
Assert-PlaywrightExtensionMetadata $Approval $Approval $Approval
foreach ($Field in @("extensionId", "version", "compatibleVersions")) {
    foreach ($Location in @("kit", "public")) {
        $Changed = $Approval | ConvertTo-Json | ConvertFrom-Json
        if ($Field -eq "compatibleVersions") {
            $Changed.$Field = @("0.3.0", "0.4.0")
        } else { $Changed.$Field = "different" }
        $Rejected = $false
        try {
            if ($Location -eq "kit") {
                Assert-PlaywrightExtensionMetadata $Changed $Approval $Approval
            } else {
                Assert-PlaywrightExtensionMetadata $Approval $Changed $Approval
            }
        } catch { $Rejected = $true }
        if (-not $Rejected) { throw "Metadata drift accepted: $Location.$Field" }
    }
}
Write-Output "EXTENSION STATUS AND APPROVAL CHECKS PASSED"
''',
                encoding="utf-8-sig",
            )
            result = subprocess.run(
                [
                    POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                    str(script), str(ROOT / "scripts" / "INSTALL-WINDOWS-PILOT.ps1"),
                    sys.executable, str(checker),
                ],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(0, result.returncode, msg=result.stdout + result.stderr)
            self.assertIn("EXTENSION STATUS AND APPROVAL CHECKS PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
