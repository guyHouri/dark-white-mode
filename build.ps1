$ErrorActionPreference = "Stop"

python -m pip install --user --upgrade pyinstaller pystray pillow==12.1.0
python -m PyInstaller --noconfirm --clean --onefile --windowed --name dark-white-mode dark_white_mode.py

Write-Host ""
Write-Host "Built: $PWD\dist\dark-white-mode.exe"
