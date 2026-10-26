# Agent installation instructions

When a user gives you this repository and asks to install `dark-white-mode`, use the latest GitHub Release rather than running the Python source directly. Running the source directly causes the generic Python icon and bypasses the packaged startup integration.

## macOS — preferred one-file install

1. Confirm the Mac is Apple Silicon (`uname -m` returns `arm64`). The published macOS build is not currently an Intel build.
2. Download `Install-dark-white-mode.command` and its `.sha256` file from the latest release.
3. Verify the SHA-256 checksum before running it.
4. Run the installer from Terminal:

   ```bash
   bash Install-dark-white-mode.command --yes
   ```

   Use `--yes` only when the user explicitly asked you to install f.lux if it is missing. Otherwise omit `--yes`; the installer will ask the user before downloading f.lux.
5. The installer downloads release files only from this repository and downloads f.lux only from the official `https://justgetflux.com/mac/Flux.zip` URL. It verifies the dark-white-mode release checksum, validates both app bundles, installs per-user under `~/Applications`, and launches the applications.
6. Verify that `~/Applications/dark-white-mode.app` exists and that its bundle identifier is `com.guyhouri.dark-white-mode`. Confirm the process remains running and the menu-bar icon appears.

If the user already downloaded `dark-white-mode-macos.zip`, unzip it and run the included `Install-dark-white-mode.command`; it will use the bundled app instead of downloading it again.

## Windows

Download `dark-white-mode.exe` and its `.sha256` file from the latest release, verify the checksum, copy the executable to `%LOCALAPPDATA%\Programs\dark-white-mode\dark-white-mode.exe`, and launch that executable. The repository's `install.ps1` contains the Task Scheduler and Start Menu integration used for a source checkout.

## Important behavior

- No Apple Developer account is required. The Mac app is ad-hoc signed rather than Apple-notarized.
- f.lux is a third-party prerequisite for color-temperature profiles. Do not download it from mirrors and do not bundle it into this repository. The installer fetches the official copy only after user consent.
- On macOS, f.lux may show its own first-run permission or location prompts. Do not bypass those prompts; let the user complete them.
- The app contains no analytics or telemetry. It does intentionally change display and appearance settings, register itself at login, edit Chrome profile files after creating backups, and optionally close and reopen Chrome.
