[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExtensionDirectory
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ExtensionDirectory -PathType Container)) {
    throw "The approved unpacked extension directory is missing: $ExtensionDirectory"
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$Desktop = [System.Windows.Automation.AutomationElement]::RootElement
$Dialog = $null
$TopWindows = @()
$Deadline = [DateTime]::UtcNow.AddSeconds(20)

do {
    $TopWindows = $Desktop.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    foreach ($Candidate in $TopWindows) {
        try {
            if ($Candidate.Current.ClassName -ne "#32770") {
                continue
            }
            $Owner = Get-Process -Id $Candidate.Current.ProcessId -ErrorAction Stop
            if ($Owner.ProcessName -in @("chrome", "msedge")) {
                $Dialog = $Candidate
                break
            }
        } catch {
            # UI Automation elements can disappear while the desktop is scanned.
        }
    }
    if (-not $Dialog) {
        Start-Sleep -Milliseconds 200
    }
} while (-not $Dialog -and [DateTime]::UtcNow -lt $Deadline)

if (-not $Dialog) {
    $Visible = @($TopWindows | ForEach-Object {
        try {
            "{0}|{1}|{2}" -f `
                $_.Current.ProcessId, $_.Current.ClassName, $_.Current.Name
        } catch {
            "<stale>"
        }
    }) -join "; "
    throw "Chrome/Edge folder dialog was not found; top-level windows=$Visible"
}

Write-Host (
    "UIA_DIALOG pid={0} class={1} name={2}" -f `
        $Dialog.Current.ProcessId, $Dialog.Current.ClassName, $Dialog.Current.Name
)

$WindowObject = $null
if ($Dialog.TryGetCurrentPattern(
        [System.Windows.Automation.WindowPattern]::Pattern,
        [ref]$WindowObject
    )) {
    ([System.Windows.Automation.WindowPattern]$WindowObject).SetWindowVisualState(
        [System.Windows.Automation.WindowVisualState]::Normal
    )
}
$Dialog.SetFocus()
[System.Windows.Forms.SendKeys]::SendWait("^l")
Start-Sleep -Milliseconds 400

$Address = [System.Windows.Automation.AutomationElement]::FocusedElement
$ValueObject = $null
if ($Address -and $Address.TryGetCurrentPattern(
        [System.Windows.Automation.ValuePattern]::Pattern,
        [ref]$ValueObject
    )) {
    $ValuePattern = [System.Windows.Automation.ValuePattern]$ValueObject
    if ($ValuePattern.Current.IsReadOnly) {
        throw "Chrome/Edge folder dialog address field is read-only."
    }
    $ValuePattern.SetValue([IO.Path]::GetFullPath($ExtensionDirectory))
    Write-Host (
        "UIA_ADDRESS id={0} name={1}" -f `
            $Address.Current.AutomationId, $Address.Current.Name
    )
} else {
    [System.Windows.Forms.Clipboard]::SetText(
        [IO.Path]::GetFullPath($ExtensionDirectory)
    )
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Write-Host "UIA_ADDRESS clipboard-fallback"
}

[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
Start-Sleep -Milliseconds 1200

$AcceptCondition = New-Object -TypeName `
    System.Windows.Automation.PropertyCondition -ArgumentList `
    ([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "1")
$Accept = $Dialog.FindFirst(
    [System.Windows.Automation.TreeScope]::Descendants,
    $AcceptCondition
)
if (-not $Accept) {
    throw "Chrome/Edge folder dialog accept button (AutomationId=1) was not found."
}

$InvokeObject = $null
if (-not $Accept.TryGetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern,
        [ref]$InvokeObject
    )) {
    throw (
        "Chrome/Edge folder dialog accept control is not invokable: " +
        $Accept.Current.Name
    )
}
Write-Host (
    "UIA_ACCEPT id={0} name={1}" -f `
        $Accept.Current.AutomationId, $Accept.Current.Name
)
([System.Windows.Automation.InvokePattern]$InvokeObject).Invoke()

