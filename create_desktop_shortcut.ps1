[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $ProjectRoot "dist\MultiAgentBI-Launcher.exe"

if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "Launcher EXE not found. Run build_launcher.cmd first: $Launcher"
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Multi-Agent BI.lnk"

if ($PSCmdlet.ShouldProcess($ShortcutPath, "Create desktop shortcut")) {
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $Launcher
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.IconLocation = "$Launcher,0"
    $Shortcut.Description = "Launch the local Multi-Agent BI interview demo"
    $Shortcut.Save()
}

Write-Host "Desktop shortcut: $ShortcutPath"
