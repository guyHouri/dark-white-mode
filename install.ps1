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

if ($startWithWindows) {
    $actionParameters = @{
        Execute = $installedPath
        WorkingDirectory = $installDirectory
    }
    if ($startMinimized) {
        $actionParameters.Argument = "--startup"
    }
    $action = New-ScheduledTaskAction @actionParameters
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $appName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
} else {
    Unregister-ScheduledTask -TaskName $appName -Confirm:$false -ErrorAction SilentlyContinue
}

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $runKey -Name $appName -ErrorAction SilentlyContinue

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
Write-Host "Startup task: $(if ($startWithWindows) { 'enabled' } else { 'disabled' })"
Write-Host "Start Menu shortcut: $shortcutPath"

if ($Launch) {
    Start-Process -FilePath $installedPath
}
