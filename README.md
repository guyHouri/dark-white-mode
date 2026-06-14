# dark-white-mode

`dark-white-mode` is a small desktop utility that toggles a computer between a dark/low-light profile and a white/daylight profile.

Credits: Guy Houri

## What the button changes

- Windows/macOS system dark and light appearance.
- The app window's own dark/white theme, either with the main toggle or with the separate App Theme button.
- Windows/macOS startup registration, so the app can launch automatically after restart and start minimized to the tray/menu bar.
- A Windows Start Menu shortcut, so Windows Search can find the app with the same icon used by the executable.
- Chrome's `chrome://flags` force-dark experiment by editing Chrome's per-user `Local State` file.
- Display brightness through Windows WMI. External monitor DDC/CI brightness is a separate opt-in test option.
- f.lux color temperature by updating the current user's f.lux registry values and restarting `flux.exe`.
- A Windows notification-area tray icon with Show, Toggle mode, and Quit actions.

## Notes

- Chrome must be closed before the flag file can be changed reliably. By default, the app closes Chrome automatically, force-closes background Chrome processes if needed, updates the flag, sets each Chrome profile to continue where it left off, suppresses Chrome's crash-restore bubble where supported, and reopens Chrome.
- Closing or minimizing the window keeps the app running in the Windows system tray or hidden-icons overflow menu. Use the tray menu's Quit action to fully exit.
- Startup is enabled by default. On Windows the app registers itself under the current user's startup apps. On macOS it writes a per-user LaunchAgent. Both launch with `--startup`, which starts minimized to the tray/menu bar.
- Brightness control depends on the display. Windows uses WMI for the normal brightness toggle. External monitors may expose DDC/CI/VCP brightness, but support is inconsistent and some displays, including some ViewSonic units, may block software brightness changes. The DDC/CI path is off by default and should only be enabled for monitors you have tested. macOS requires the optional Homebrew `brightness` tool.
- f.lux does not provide a stable public command-line preset API. Windows uses the per-user f.lux preference registry values: `Outdoor`, `Indoor`, and `Late`. macOS f.lux Kelvin switching is not supported yet.
- The app works per desktop user and normally does not need administrator rights.

## macOS

The macOS build is produced and tested by GitHub Actions on a real macOS runner. The app is currently unsigned, so macOS Gatekeeper may require right-clicking the app and choosing Open, or removing quarantine with:

```bash
xattr -dr com.apple.quarantine dark-white-mode.app
```

## Build

From this folder:

```powershell
.\build.ps1
```

The distributable executable is created at:

```text
dist\dark-white-mode.exe
```

GitHub Actions also builds a macOS app zip:

```text
dark-white-mode-macos.zip
```

## Developer Test

```powershell
python .\dark_white_mode.py --self-test
python -m unittest discover -s tests -v
```

## License

MIT License. See [LICENSE](LICENSE).
