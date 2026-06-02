# dark-white-mode

`dark-white-mode` is a small Windows utility that toggles a computer between a dark/low-light profile and a white/daylight profile.

Credits: Guy Houri

## What the button changes

- Windows app and system theme through the current user's theme registry keys.
- The app window's own dark/white theme, either with the main toggle or with the separate App Theme button.
- Chrome's `chrome://flags` force-dark experiment by editing Chrome's per-user `Local State` file.
- Display brightness through Windows WMI and DDC/CI monitor control where supported.
- f.lux color temperature by updating the current user's f.lux registry values and restarting `flux.exe`.
- A Windows notification-area tray icon with Show, Toggle mode, and Quit actions.

## Notes

- Chrome must be closed before the flag file can be changed reliably. By default, the app closes Chrome automatically, force-closes background Chrome processes if needed, updates the flag, sets each Chrome profile to continue where it left off, suppresses Chrome's crash-restore bubble where supported, and reopens Chrome.
- Closing or minimizing the window keeps the app running in the Windows system tray or hidden-icons overflow menu. Use the tray menu's Quit action to fully exit.
- Brightness control depends on the display. Laptop panels usually work through WMI; many external monitors need DDC/CI enabled in the monitor menu.
- f.lux does not provide a stable public command-line preset API, so this app uses the per-user f.lux preference registry values: `Outdoor`, `Indoor`, and `Late`.
- The app works per Windows user and normally does not need administrator rights.

## Build

From this folder:

```powershell
.\build.ps1
```

The distributable executable is created at:

```text
dist\dark-white-mode.exe
```

## Developer Test

```powershell
python .\dark_white_mode.py --self-test
python -m unittest discover -s tests -v
```
