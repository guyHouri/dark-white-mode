param(
    [switch]$Launch
)

$ErrorActionPreference = "Stop"

$appName = "dark-white-mode"
$sourcePath = Join-Path $PSScriptRoot "dist\$appName.exe"
if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Build output not found: $sourcePath. Run .\build.ps1 first."
}

$installDirectory = Join-Path $env:LOCALAPPDATA "Programs\$appName"
$installedPath = Join-Path $installDirectory "$appName.exe"
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null

Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -eq $installedPath } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Copy-Item -LiteralPath $sourcePath -Destination $installedPath -Force

$configPath = Join-Path $env:APPDATA "$appName\settings.json"
$startWithWindows = $true
$startMinimized = $false
if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($null -ne $config.start_with_windows) {
        $startWithWindows = [bool]$config.start_with_windows
    }
    if ($null -ne $config.start_minimized_to_tray) {
        $startMinimized = [bool]$config.start_minimized_to_tray
    }
}

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$startupApprovedKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
New-Item -Path $runKey -Force | Out-Null
New-Item -Path $startupApprovedKey -Force | Out-Null
if ($startWithWindows) {
    $runCommand = '"' + $installedPath + '"'
    if ($startMinimized) {
        $runCommand += " --startup"
    }
    New-ItemProperty -Path $runKey -Name $appName -PropertyType String -Value $runCommand -Force | Out-Null
    [byte[]]$startupEnabled = 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
    New-ItemProperty -Path $startupApprovedKey -Name $appName -PropertyType Binary -Value $startupEnabled -Force | Out-Null
} else {
    Remove-ItemProperty -Path $runKey -Name $appName -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $startupApprovedKey -Name $appName -ErrorAction SilentlyContinue
}

# Older releases used Task Scheduler. Windows Application Control can reject an
# unsigned per-user executable when Task Scheduler launches it, so remove that
# legacy task and use the standard per-user Run entry above instead.
Unregister-ScheduledTask -TaskName $appName -Confirm:$false -ErrorAction SilentlyContinue

$startMenuDirectory = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcutPath = Join-Path $startMenuDirectory "$appName.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $installedPath
$shortcut.WorkingDirectory = $installDirectory
$shortcut.IconLocation = "$installedPath,0"
$shortcut.Description = "Switch between Night, Outside, and Work Indoors display profiles"
$shortcut.Save()

Write-Host "Installed: $installedPath"
Write-Host "Startup Run entry: $(if ($startWithWindows) { 'enabled' } else { 'disabled' })"
Write-Host "Legacy startup task: removed"
Write-Host "Start Menu shortcut: $shortcutPath"

if ($Launch) {
    Start-Process -FilePath $installedPath
}
