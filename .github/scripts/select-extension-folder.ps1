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
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class IntranetDesktopInput
{
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr window, int command);

    [DllImport("user32.dll")]
    public static extern bool BringWindowToTop(IntPtr window);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr window);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(
        uint flags,
        uint x,
        uint y,
        uint data,
        UIntPtr extraInfo
    );
}
"@

function Invoke-DesktopElementClick {
    param(
        [Parameter(Mandatory = $true)]
        [System.Windows.Automation.AutomationElement]$Window,
        [Parameter(Mandatory = $true)]
        [System.Windows.Automation.AutomationElement]$Element
    )

    $Bounds = $Element.Current.BoundingRectangle
    if ($Bounds.Width -le 0 -or $Bounds.Height -le 0) {
        throw "Chrome/Edge UI Automation element has no visible screen bounds."
    }
    $ClickX = [int][Math]::Floor($Bounds.Left + ($Bounds.Width / 2))
    $ClickY = [int][Math]::Floor($Bounds.Top + ($Bounds.Height / 2))
    $ChromeHandle = [IntPtr]$Window.Current.NativeWindowHandle
    $null = [IntranetDesktopInput]::ShowWindowAsync($ChromeHandle, 9)
    $null = [IntranetDesktopInput]::BringWindowToTop($ChromeHandle)
    $null = [IntranetDesktopInput]::SetForegroundWindow($ChromeHandle)
    try {
        $Window.SetFocus()
    } catch {
        # Chrome's top-level accessibility object can reject SetFocus even
        # after user32 has successfully restored and foregrounded its HWND.
        Write-Host "UIA_WINDOW_FOCUS action=win32-only"
    }
    Start-Sleep -Milliseconds 400
    if (-not [IntranetDesktopInput]::SetCursorPos($ClickX, $ClickY)) {
        throw "Windows refused to position the pointer over the Chrome/Edge control."
    }
    [IntranetDesktopInput]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 100
    [IntranetDesktopInput]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    return @($ClickX, $ClickY)
}

$Desktop = [System.Windows.Automation.AutomationElement]::RootElement
$ChromeWindow = $null
$LoadButton = $null
$DeveloperButton = $null
$DeveloperModeClicked = $false
$LastButtonNames = @()
$ButtonDeadline = [DateTime]::UtcNow.AddSeconds(20)

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
            $LastButtonNames = @($Buttons | ForEach-Object {
                try { $_.Current.Name } catch { "<stale>" }
            })
            $DeveloperButton = @($Buttons | Where-Object {
                try { $_.Current.Name -eq "Developer mode" } catch { $false }
            } | Select-Object -First 1)
            if ($DeveloperButton.Count -eq 1) {
                $DeveloperButton = $DeveloperButton[0]
            } else {
                $DeveloperButton = $null
            }
            $LoadButton = @($Buttons | Where-Object {
                try {
                    # GitHub's Windows runner and bundled Chrome use English.
                    # Keep this CI-only PS 5.1 script ASCII so a BOM-less source
                    # cannot be mis-decoded by a legacy Windows system code page.
                    $_.Current.Name -eq "Load unpacked"
                } catch {
                    $false
                }
            } | Select-Object -First 1)
            if ($LoadButton.Count -eq 1) {
                $LoadButton = $LoadButton[0]
                $ChromeWindow = $Candidate
                break
            }
            $LoadButton = $null
            if ($DeveloperButton) {
                $ChromeWindow = $Candidate
            }
        } catch {
            # The browser accessibility tree can change while it is scanned.
        }
    }
    if (-not $LoadButton -and -not $DeveloperModeClicked -and
        $DeveloperButton -and $ChromeWindow) {
        $ToggleObject = $null
        $ToggleState = "unknown"
        if ($DeveloperButton.TryGetCurrentPattern(
                [System.Windows.Automation.TogglePattern]::Pattern,
                [ref]$ToggleObject
            )) {
            $ToggleState = [string](
                ([System.Windows.Automation.TogglePattern]$ToggleObject).Current.ToggleState
            )
        }
        if ($ToggleState -eq "On") {
            Write-Host "UIA_DEVELOPER_MODE prior=On action=already-on"
        } elseif ($ToggleObject) {
            try {
                ([System.Windows.Automation.TogglePattern]$ToggleObject).Toggle()
                Start-Sleep -Milliseconds 400
                $ToggleStateAfter = [string](
                    ([System.Windows.Automation.TogglePattern]$ToggleObject).Current.ToggleState
                )
                Write-Host (
                    "UIA_DEVELOPER_MODE prior={0} after={1} action=toggle-pattern" -f `
                        $ToggleState, $ToggleStateAfter
                )
            } catch {
                $DeveloperClick = Invoke-DesktopElementClick `
                    -Window $ChromeWindow -Element $DeveloperButton
                Write-Host (
                    "UIA_DEVELOPER_MODE x={0} y={1} prior={2} " +
                    "action=desktop-fallback" -f `
                        $DeveloperClick[0], $DeveloperClick[1], $ToggleState
                )
            }
        } else {
            $DeveloperClick = Invoke-DesktopElementClick `
                -Window $ChromeWindow -Element $DeveloperButton
            Write-Host (
                "UIA_DEVELOPER_MODE x={0} y={1} prior={2} " +
                "action=desktop-no-toggle-pattern" -f `
                    $DeveloperClick[0], $DeveloperClick[1], $ToggleState
            )
        }
        $DeveloperModeClicked = $true
        Start-Sleep -Milliseconds 800
    }
    if (-not $LoadButton) {
        Start-Sleep -Milliseconds 200
    }
} while (-not $LoadButton -and [DateTime]::UtcNow -lt $ButtonDeadline)

if (-not $LoadButton -or -not $ChromeWindow) {
    throw (
        "Chrome/Edge Load unpacked UI Automation button was not found; buttons=" +
        (@($LastButtonNames) -join "|")
    )
}

$InvokerRunspace = [System.Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace()
$InvokerRunspace.ApartmentState = [System.Threading.ApartmentState]::STA
$InvokerRunspace.ThreadOptions = `
    [System.Management.Automation.Runspaces.PSThreadOptions]::ReuseThread
$InvokerRunspace.Open()
$InvokerRunspace.SessionStateProxy.SetVariable(
    "IntranetLoadButton",
    $LoadButton
)
$LoadInvoker = [System.Management.Automation.PowerShell]::Create()
$LoadInvoker.Runspace = $InvokerRunspace
$null = $LoadInvoker.AddScript(@'
$ErrorActionPreference = "Stop"
$InvokeObject = $null
if (-not $IntranetLoadButton.TryGetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern,
        [ref]$InvokeObject
    )) {
    throw "Chrome/Edge Load unpacked button does not support InvokePattern."
}
"UIA_ASYNC_LOAD action=invoking"
([System.Windows.Automation.InvokePattern]$InvokeObject).Invoke()
"UIA_ASYNC_LOAD action=returned"
'@)
$LoadInvocation = $LoadInvoker.BeginInvoke()
$LoadInvokerCompleted = $false
$LoadInvokerOutput = @()
Write-Host "UIA_LOAD_BUTTON name=Load unpacked action=sta-runspace-invoke"

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
    if (-not $LoadInvokerCompleted -and $LoadInvocation.IsCompleted) {
        try {
            $LoadInvokerOutput = @($LoadInvoker.EndInvoke($LoadInvocation))
            $LoadInvokerCompleted = $true
        } catch {
            $InvokerErrors = @($LoadInvoker.Streams.Error | ForEach-Object {
                $_.ToString()
            }) -join " | "
            throw (
                "STA Load unpacked invoker failed before the dialog appeared: " +
                $InvokerErrors
            )
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

if (-not $LoadInvokerCompleted) {
    if (-not $LoadInvocation.AsyncWaitHandle.WaitOne(20000)) {
        throw "STA Load unpacked invoker did not return after the folder closed."
    }
    try {
        $LoadInvokerOutput = @($LoadInvoker.EndInvoke($LoadInvocation))
        $LoadInvokerCompleted = $true
    } catch {
        $InvokerErrors = @($LoadInvoker.Streams.Error | ForEach-Object {
            $_.ToString()
        }) -join " | "
        throw "STA Load unpacked invoker failed after dialog close: $InvokerErrors"
    }
}
if ($LoadInvoker.Streams.Error.Count -gt 0) {
    $InvokerErrors = @($LoadInvoker.Streams.Error | ForEach-Object {
        $_.ToString()
    }) -join " | "
    throw "STA Load unpacked invoker wrote errors: $InvokerErrors"
}
@($LoadInvokerOutput) | ForEach-Object { Write-Host ([string]$_) }
$LoadInvoker.Dispose()
$InvokerRunspace.Dispose()
