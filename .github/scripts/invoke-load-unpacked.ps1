[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$Desktop = [System.Windows.Automation.AutomationElement]::RootElement
$LoadButton = $null
$Deadline = [DateTime]::UtcNow.AddSeconds(20)

do {
    $TopWindows = $Desktop.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    foreach ($Candidate in $TopWindows) {
        try {
            if ($Candidate.Current.ClassName -ne "Chrome_WidgetWin_1") {
                continue
            }
            $Owner = Get-Process -Id $Candidate.Current.ProcessId -ErrorAction Stop
            if ($Owner.ProcessName -notin @("chrome", "msedge")) {
                continue
            }
            $ButtonCondition = New-Object -TypeName `
                System.Windows.Automation.PropertyCondition -ArgumentList `
                (
                    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                    [System.Windows.Automation.ControlType]::Button
                )
            $Buttons = $Candidate.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                $ButtonCondition
            )
            $LoadButton = @($Buttons | Where-Object {
                try { $_.Current.Name -eq "Load unpacked" } catch { $false }
            } | Select-Object -First 1)
            if ($LoadButton.Count -eq 1) {
                $LoadButton = $LoadButton[0]
                break
            }
            $LoadButton = $null
        } catch {
            # The browser accessibility tree can change while it is scanned.
        }
    }
    if (-not $LoadButton) {
        Start-Sleep -Milliseconds 200
    }
} while (-not $LoadButton -and [DateTime]::UtcNow -lt $Deadline)

if (-not $LoadButton) {
    throw "Chrome/Edge Load unpacked button was not found by the async invoker."
}

$InvokeObject = $null
if (-not $LoadButton.TryGetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern,
        [ref]$InvokeObject
    )) {
    throw "Chrome/Edge Load unpacked button does not support InvokePattern."
}

Write-Host "UIA_ASYNC_LOAD action=invoking"
([System.Windows.Automation.InvokePattern]$InvokeObject).Invoke()
Write-Host "UIA_ASYNC_LOAD action=returned"

