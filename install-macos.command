#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

APP_NAME="dark-white-mode"
APP_BUNDLE="dark-white-mode.app"
APP_BUNDLE_ID="com.guyhouri.dark-white-mode"
RELEASE_BASE_URL="https://github.com/guyHouri/dark-white-mode/releases/latest/download"
FLUX_DOWNLOAD_URL="https://justgetflux.com/mac/Flux.zip"
FLUX_EULA_URL="https://justgetflux.com/news/pages/eula/"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET_DIRECTORY="$HOME/Applications"
TARGET_APP="$TARGET_DIRECTORY/$APP_BUNDLE"
ASSUME_YES=0
SKIP_FLUX=0
CHECK_ONLY=0
CHECK_FLUX=0
NO_LAUNCH=0
TEMP_DIRECTORY=""
STAGING_PATHS=()

log() {
    printf '%s\n' "[dark-white-mode] $*" >&2
}

fail() {
    printf '%s\n' "[dark-white-mode] ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Install dark-white-mode for the current macOS user.

Usage: install-macos.command [options]
  --yes         Install official f.lux automatically when it is missing.
  --skip-flux   Install dark-white-mode without downloading f.lux.
  --no-launch   Do not launch the applications after installation.
  --check       Validate the packaged app and installer without changing the Mac.
  --check-flux  Also download and validate the official f.lux app without installing it.
  --help        Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --yes)
            ASSUME_YES=1
            ;;
        --skip-flux)
            SKIP_FLUX=1
            ;;
        --no-launch)
            NO_LAUNCH=1
            ;;
        --check)
            CHECK_ONLY=1
            NO_LAUNCH=1
            ;;
        --check-flux)
            CHECK_ONLY=1
            CHECK_FLUX=1
            NO_LAUNCH=1
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1"
            ;;
    esac
    shift
done

cleanup() {
    local path
    for path in "${STAGING_PATHS[@]-}"; do
        if [ -n "$path" ] && [ -e "$path" ]; then
            rm -rf -- "$path"
        fi
    done
    if [ -n "$TEMP_DIRECTORY" ] && [ -d "$TEMP_DIRECTORY" ]; then
        case "$TEMP_DIRECTORY" in
            "${TMPDIR:-/tmp}/dark-white-mode-install."*)
                rm -rf -- "$TEMP_DIRECTORY"
                ;;
        esac
    fi
}
trap cleanup EXIT

ensure_temp_directory() {
    if [ -z "$TEMP_DIRECTORY" ]; then
        TEMP_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/dark-white-mode-install.XXXXXX")"
    fi
}

find_packaged_app() {
    local candidate
    for candidate in \
        "$SCRIPT_DIR/$APP_BUNDLE" \
        "$SCRIPT_DIR/dark-white-mode-macos/$APP_BUNDLE" \
        "$SCRIPT_DIR/dist/$APP_BUNDLE"; do
        if [ -d "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

validate_app_bundle() {
    local app_path="$1"
    local info_plist="$app_path/Contents/Info.plist"
    local executable="$app_path/Contents/MacOS/$APP_NAME"
    local bundle_id

    [ -f "$info_plist" ] || fail "Missing Info.plist in $app_path"
    [ -x "$executable" ] || fail "Missing executable in $app_path"
    bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$info_plist")"
    [ "$bundle_id" = "$APP_BUNDLE_ID" ] || fail "Unexpected bundle identifier: $bundle_id"
    /usr/bin/codesign --verify --deep --strict "$app_path"
}

download_packaged_app() {
    local archive checksum expected actual extracted candidate

    ensure_temp_directory
    archive="$TEMP_DIRECTORY/dark-white-mode-macos.zip"
    checksum="$archive.sha256"
    extracted="$TEMP_DIRECTORY/dark-white-mode-release"
    mkdir -p "$extracted"

    log "Downloading the latest verified macOS release..."
    /usr/bin/curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "$RELEASE_BASE_URL/dark-white-mode-macos.zip" -o "$archive"
    /usr/bin/curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "$RELEASE_BASE_URL/dark-white-mode-macos.zip.sha256" -o "$checksum"

    expected="$(/usr/bin/awk 'NR == 1 { print $1 }' "$checksum" | /usr/bin/tr '[:upper:]' '[:lower:]')"
    actual="$(/usr/bin/shasum -a 256 "$archive" | /usr/bin/awk '{ print $1 }' | /usr/bin/tr '[:upper:]' '[:lower:]')"
    [ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "The macOS release checksum did not match."

    /usr/bin/ditto -x -k "$archive" "$extracted"
    candidate=""
    while IFS= read -r app_candidate; do
        candidate="$app_candidate"
        break
    done < <(/usr/bin/find "$extracted" -type d -name "$APP_BUNDLE" -print)
    [ -n "$candidate" ] || fail "The release archive did not contain $APP_BUNDLE."
    printf '%s\n' "$candidate"
}

find_flux_app() {
    local candidate
    for candidate in \
        "/Applications/Flux.app" \
        "/Applications/f.lux.app" \
        "$HOME/Applications/Flux.app" \
        "$HOME/Applications/f.lux.app"; do
        if [ -d "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

prompt_for_flux() {
    local choice status

    if [ "$SKIP_FLUX" -eq 1 ]; then
        return 1
    fi
    if [ "$ASSUME_YES" -eq 1 ]; then
        return 0
    fi

    set +e
    choice="$(/usr/bin/osascript <<APPLESCRIPT
try
    set answer to display dialog "f.lux is required for the color-temperature profiles but was not found. The installer can download the official Mac build from justgetflux.com. Downloading means you agree to the f.lux EULA: $FLUX_EULA_URL" with title "Install dark-white-mode" buttons {"Cancel", "Install without f.lux", "Install f.lux"} default button "Install f.lux" cancel button "Cancel"
    return button returned of answer
on error number -128
    return "Cancel"
end try
APPLESCRIPT
)"
    status=$?
    set -e

    if [ "$status" -ne 0 ] || [ "$choice" = "Cancel" ]; then
        log "Installation cancelled."
        exit 2
    fi
    [ "$choice" = "Install f.lux" ]
}

validate_zip_paths() {
    local archive="$1"
    local entry
    while IFS= read -r entry; do
        case "$entry" in
            /*|..|../*|*/../*|*/..|*\\*)
                fail "The f.lux archive contained an unsafe path: $entry"
                ;;
        esac
    done < <(/usr/bin/unzip -Z1 "$archive")
}

download_flux_app() {
    local archive extracted candidate

    ensure_temp_directory
    archive="$TEMP_DIRECTORY/Flux.zip"
    extracted="$TEMP_DIRECTORY/flux-release"
    mkdir -p "$extracted"

    log "Downloading f.lux from the official justgetflux.com site..."
    /usr/bin/curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "$FLUX_DOWNLOAD_URL" -o "$archive"
    validate_zip_paths "$archive"
    /usr/bin/ditto -x -k "$archive" "$extracted"

    candidate=""
    while IFS= read -r flux_candidate; do
        candidate="$flux_candidate"
        break
    done < <(/usr/bin/find "$extracted" -type d \( -name 'Flux.app' -o -name 'f.lux.app' \) -print)
    [ -n "$candidate" ] || fail "The official f.lux archive did not contain Flux.app."
    /usr/bin/codesign --verify --deep --strict "$candidate"
    /usr/sbin/spctl --assess --type execute "$candidate"
    printf '%s\n' "$candidate"
}

install_bundle() {
    local source="$1"
    local target="$2"
    local label="$3"
    local stage backup

    mkdir -p "$(dirname "$target")"
    stage="$(dirname "$target")/.$(basename "$target").installing.$$"
    backup="$(dirname "$target")/.$(basename "$target").backup.$$"
    STAGING_PATHS+=("$stage" "$backup")

    /usr/bin/ditto "$source" "$stage"
    /usr/bin/xattr -dr com.apple.quarantine "$stage" 2>/dev/null || true
    if [ -e "$target" ]; then
        mv "$target" "$backup"
    fi
    if mv "$stage" "$target"; then
        if [ -e "$backup" ]; then
            rm -rf -- "$backup"
        fi
    else
        if [ -e "$backup" ]; then
            mv "$backup" "$target"
        fi
        fail "Could not install $label."
    fi
    log "Installed $label at $target"
}

SOURCE_APP="$(find_packaged_app || true)"
if [ -z "$SOURCE_APP" ]; then
    if [ "$CHECK_ONLY" -eq 1 ]; then
        fail "No packaged $APP_BUNDLE was found for validation."
    fi
    SOURCE_APP="$(download_packaged_app)"
fi
validate_app_bundle "$SOURCE_APP"

if [ "$CHECK_FLUX" -eq 1 ]; then
    FLUX_SOURCE="$(download_flux_app)"
    log "Official f.lux download check passed for $FLUX_SOURCE"
    exit 0
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
    log "Installer check passed for $SOURCE_APP"
    if FLUX_PATH="$(find_flux_app)"; then
        log "f.lux detected at $FLUX_PATH"
    else
        log "f.lux is not installed; the interactive installer will offer the official download."
    fi
    exit 0
fi

install_bundle "$SOURCE_APP" "$TARGET_APP" "$APP_NAME"

FLUX_PATH="$(find_flux_app || true)"
if [ -z "$FLUX_PATH" ]; then
    if prompt_for_flux; then
        FLUX_SOURCE="$(download_flux_app)"
        FLUX_TARGET="$TARGET_DIRECTORY/Flux.app"
        install_bundle "$FLUX_SOURCE" "$FLUX_TARGET" "f.lux"
        FLUX_PATH="$FLUX_TARGET"
    else
        log "Continuing without f.lux. You can install it later from https://justgetflux.com/"
    fi
else
    log "Found f.lux at $FLUX_PATH"
fi

if [ "$NO_LAUNCH" -eq 0 ]; then
    if [ -n "$FLUX_PATH" ]; then
        /usr/bin/open "$FLUX_PATH"
    fi
    /usr/bin/open "$TARGET_APP"
fi

if [ "$ASSUME_YES" -eq 0 ]; then
    COMPLETION_MESSAGE="dark-white-mode was installed in your Applications folder."
    if [ -n "$FLUX_PATH" ]; then
        COMPLETION_MESSAGE="$COMPLETION_MESSAGE f.lux is installed too. Complete any first-run prompts it shows."
    else
        COMPLETION_MESSAGE="$COMPLETION_MESSAGE Install f.lux later to use color-temperature profiles."
    fi
    /usr/bin/osascript - "$COMPLETION_MESSAGE" <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run argv
    display dialog (item 1 of argv) with title "Installation complete" buttons {"OK"} default button "OK"
end run
APPLESCRIPT
fi

log "Installation complete."
