# dark-white-mode

`dark-white-mode` is a small desktop utility that switches a computer between Night, Outside, and Work Indoors profiles.

Credits: Guy Houri

## Easiest installation

### Mac

1. Open the [latest release](https://github.com/guyHouri/dark-white-mode/releases/latest).
2. Download `Install-dark-white-mode.zip` and unzip it if macOS does not unzip it automatically.
3. In Finder, right-click `Install-dark-white-mode.command`, choose **Open**, and confirm **Open**.
4. If f.lux is missing, the installer offers to download the official Mac version from `justgetflux.com`, validates its Apple signature, installs both apps under `~/Applications`, and launches them.

No Terminal commands, Python installation, Homebrew, or Apple Developer account are required for the normal human installation. The separate `dark-white-mode-macos.zip` download contains the same installer beside the app for an offline app install.

### Give the GitHub link to an AI

Send the AI this repository URL and say: **“Install the latest release and follow `AGENTS.md`. Install official f.lux too if it is missing.”** The repository's [AGENTS.md](AGENTS.md) gives an agent checksum verification, installation, f.lux, and post-install test instructions.

## What the button changes

- Windows/macOS system dark and light appearance.
- The app window's own dark/white theme, either with the mode profile or with the separate App Theme button.
- Windows/macOS startup registration, so the app can launch automatically after restart. It opens visibly by default and can optionally start minimized to the tray/menu bar.
- A per-user Windows installation under `%LOCALAPPDATA%\Programs\dark-white-mode`, plus a Start Menu shortcut that uses the packaged app icon.
- Windows Outside battery optimization, using Power saver and a 99% CPU maximum processor state while Outside mode is active.
- Chrome's `chrome://flags` force-dark experiment by editing Chrome's per-user `Local State` file.
- Display brightness through Windows WMI and DDC/CI monitor control where supported.
- Experimental PWM-safe software dimming on Windows and macOS through display gamma/LUT tables, so the dark profile can dim pixels without lowering monitor hardware brightness.
- f.lux color temperature by updating the current user's f.lux registry values and restarting `flux.exe`.
- A Windows notification-area tray icon with Show, profile choices, Next mode, and Quit actions.

## Profiles

- Night: dark appearance, Chrome force-dark enabled, 0% brightness, and 1200K f.lux.
- Outside: light appearance, Chrome force-dark disabled, 100% brightness, 6500K f.lux, Windows Power saver, and 99% CPU maximum processor state.
- Work Indoors: dark appearance, Chrome force-dark enabled, 50% brightness, and 2700K f.lux.

## Notes

- Chrome must be closed before the flag file can be changed reliably. By default, the app closes Chrome automatically, force-closes background Chrome processes if needed, updates the flag, sets each Chrome profile to continue where it left off, suppresses Chrome's crash-restore bubble where supported, and reopens Chrome.
- Closing or minimizing the window keeps the app running in the Windows system tray or hidden-icons overflow menu. Use the tray menu's Quit action to fully exit.
- Startup is enabled by default. On Windows the app registers a per-user Task Scheduler logon task, which is more reliable than the basic Startup Apps Run key. On macOS it writes a per-user LaunchAgent. The app opens visibly by default; enable "Start minimized to tray" to launch it hidden with `--startup`.
- Brightness control depends on the display. Windows uses WMI and DDC/CI where supported. macOS uses the built-in DisplayServices interface first and can fall back to the optional Homebrew `brightness` tool for displays that need it.
- Outside battery optimization is Windows-only. Switching to Night or Work Indoors restores the Balanced power scheme and a 100% CPU maximum processor state. Lenovo battery charge thresholds, Conservation Mode, and Rapid Charge still need to be managed in Lenovo Commercial Vantage.
- PWM-safe software dimming is off by default. It stores the current gamma/LUT tables before dimming, restores them before each mode change, and restores them again when the app quits or the Python process exits. Because f.lux, HDR, Night light, True Tone, graphics drivers, and calibration tools can also own the gamma/LUT pipeline, the app reapplies software dimming after f.lux changes and may show a warning if the OS or driver rejects gamma changes.
- f.lux does not provide a stable public command-line preset API. Windows uses the per-user f.lux preference registry values: `Outdoor`, `Indoor`, and `Late`. macOS f.lux Kelvin switching is not supported yet.
- The app works per desktop user and normally does not need administrator rights.

## macOS

The macOS build is produced and runtime-tested by GitHub Actions on a real Apple Silicon macOS runner and contains a native `.icns` application icon. The packaged `.app` is launched twice in CI: once for native integration checks and once as a real GUI window with its menu-bar icon. The native checks exercise Cocoa, CoreGraphics, display enumeration, no-change gamma and hardware-brightness round trips when the runner display supports them, appearance detection, LaunchAgent generation, the bundle identifier, and the bundled icon.

The project does not require an Apple Developer account. Builds are therefore ad-hoc signed by the macOS build tools rather than notarized by Apple. On first launch, Gatekeeper may require right-clicking the app and choosing Open. If quarantine still blocks a build you created or downloaded from this repository, it can be removed with:

```bash
xattr -dr com.apple.quarantine dark-white-mode.app
```

## Build

From this folder:

```powershell
.\build.ps1
```

To build, install the packaged executable for the current Windows user, repair the startup task and Start Menu shortcut, and launch it:

```powershell
.\build.ps1 -Install
```

The distributable executable is created at:

```text
dist\dark-white-mode.exe
```

GitHub Actions also builds a one-file macOS installer and an app zip:

```text
Install-dark-white-mode.command
Install-dark-white-mode.zip
dark-white-mode-macos.zip
```

The workflow grants read-only repository access to build jobs. Write access is isolated to the release-publishing job, and third-party actions are pinned to immutable commits.

## Safety boundaries

The app runs per user and does not contain network, analytics, or telemetry code. It does intentionally change local display and appearance settings, register itself for login, edit Chrome profile files after creating backups, and optionally close and reopen Chrome. PWM-safe software dimming changes display gamma tables and restores the captured tables when switching modes or exiting normally.

## Developer Test

```powershell
python .\dark_white_mode.py --self-test
python -m unittest discover -s tests -v
```

## License

MIT License. See [LICENSE](LICENSE).
