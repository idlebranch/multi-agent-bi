[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectRoot "dist\MultiAgentBI-Launcher.exe"

if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "Launcher EXE not found. Run build_launcher.cmd first: $Launcher"
}

$Desktop = [Environment]::GetFolderPath("Desktop")
if (-not $Desktop) {
    $Desktop = (Get-ItemProperty -LiteralPath `
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" `
        -Name Desktop).Desktop
    $Desktop = [Environment]::ExpandEnvironmentVariables($Desktop)
}
if (-not $Desktop -or -not (Test-Path -LiteralPath $Desktop -PathType Container)) {
    throw "Windows Desktop directory not found."
}
$ShortcutPath = Join-Path $Desktop "Multi-Agent BI.lnk"

if ($PSCmdlet.ShouldProcess($ShortcutPath, "Create desktop shortcut")) {
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $Launcher
    $Shortcut.Arguments = ""
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.IconLocation = "$Launcher,0"
    $Shortcut.WindowStyle = 1
    $Shortcut.Description = "Launch Multi-Agent BI with Docker Compose"
    $Shortcut.Save()
}

Write-Host "Desktop shortcut: $ShortcutPath"
