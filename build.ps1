$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([scriptblock]$Command)

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Command"
    }
}

Invoke-Checked { python -m pip install --user --upgrade pyinstaller pystray pillow==12.1.0 }
Invoke-Checked { python -m unittest discover -s tests -v }

$iconPath = Join-Path $PWD "assets\dark-white-mode.ico"
Invoke-Checked { python .\dark_white_mode.py --make-icon $iconPath }
Invoke-Checked {
    python -m PyInstaller --noconfirm --clean --onefile --windowed --icon $iconPath --name dark-white-mode dark_white_mode.py
}

Write-Host ""
Write-Host "Built: $PWD\dist\dark-white-mode.exe"
