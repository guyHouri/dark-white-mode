import ctypes
import json
import os
import plistlib
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

if sys.platform == "win32":
    import winreg
else:
    winreg = None

try:
    import pystray
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None
    ImageTk = None


APP_NAME = "dark-white-mode"
CREDIT_TEXT = "Credits: Guy Houri"
APP_USER_MODEL_ID = "GuyHouri.dark-white-mode"
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
CREATE_NO_WINDOW = 0x08000000
WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002


@dataclass
class StepResult:
    name: str
    ok: bool
    message: str


def app_data_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "settings.json"


PROFILE_ORDER = ("night", "outside", "work_indoors")
DEFAULT_PROFILE = "outside"
PROFILE_DEFINITIONS = {
    "night": {
        "label": "Night",
        "theme": "dark",
        "brightness_key": "night_brightness",
        "flux_key": "night_flux_kelvin",
        "default_brightness": 0,
        "default_flux_kelvin": 1200,
    },
    "outside": {
        "label": "Outside",
        "theme": "white",
        "brightness_key": "outside_brightness",
        "flux_key": "outside_flux_kelvin",
        "default_brightness": 100,
        "default_flux_kelvin": 6500,
    },
    "work_indoors": {
        "label": "Work Indoors",
        "theme": "white",
        "brightness_key": "indoor_brightness",
        "flux_key": "indoor_flux_kelvin",
        "default_brightness": 50,
        "default_flux_kelvin": 2700,
    },
}
PROFILE_ALIASES = {
    "dark": "night",
    "night": "night",
    "white": "outside",
    "light": "outside",
    "outside": "outside",
    "work": "work_indoors",
    "indoor": "work_indoors",
    "indoors": "work_indoors",
    "work_indoors": "work_indoors",
}


DEFAULT_CONFIG = {
    "config_version": 6,
    "start_with_windows": True,
    "start_minimized_to_tray": True,
    "app_window_theme": True,
    "app_theme": "white",
    "current_profile": DEFAULT_PROFILE,
    "windows_theme": True,
    "chrome_force_dark": True,
    "brightness": True,
    "flux": True,
    "prompt_before_closing_chrome": False,
    "reopen_chrome_after_flag": True,
    "restore_chrome_pages": True,
    "night_brightness": 0,
    "outside_brightness": 100,
    "indoor_brightness": 50,
    "night_flux_kelvin": 1200,
    "outside_flux_kelvin": 6500,
    "indoor_flux_kelvin": 2700,
}


APP_THEME_COLORS = {
    "white": {
        "bg": "#f5f6f8",
        "surface": "#ffffff",
        "text": "#1f2328",
        "muted": "#5d6470",
        "border": "#d0d7de",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "button_fg": "#ffffff",
        "field": "#ffffff",
        "status_bg": "#ffffff",
        "status_fg": "#1f2328",
        "select_bg": "#dbeafe",
    },
    "dark": {
        "bg": "#111318",
        "surface": "#1b1f27",
        "text": "#f3f4f6",
        "muted": "#a7b0be",
        "border": "#303744",
        "accent": "#75a7ff",
        "accent_hover": "#9bbcff",
        "button_fg": "#101318",
        "field": "#101318",
        "status_bg": "#101318",
        "status_fg": "#e5e7eb",
        "select_bg": "#26324a",
    },
}


def normalize_app_theme(value) -> str:
    return "dark" if str(value).lower() == "dark" else "white"


def opposite_app_theme(value) -> str:
    return "white" if normalize_app_theme(value) == "dark" else "dark"


def normalize_profile_id(value) -> str:
    return PROFILE_ALIASES.get(str(value).strip().lower().replace("-", "_").replace(" ", "_"), DEFAULT_PROFILE)


def profile_label(profile_id: str) -> str:
    return PROFILE_DEFINITIONS[normalize_profile_id(profile_id)]["label"]


def profile_theme(profile_id: str) -> str:
    return PROFILE_DEFINITIONS[normalize_profile_id(profile_id)]["theme"]


def profile_is_dark(profile_id: str) -> bool:
    return profile_theme(profile_id) == "dark"


def next_profile_id(profile_id: str) -> str:
    normalized = normalize_profile_id(profile_id)
    index = PROFILE_ORDER.index(normalized)
    return PROFILE_ORDER[(index + 1) % len(PROFILE_ORDER)]


def profile_brightness(profile_id: str, settings: dict) -> int:
    profile = PROFILE_DEFINITIONS[normalize_profile_id(profile_id)]
    return clamp_int(settings.get(profile["brightness_key"]), 0, 100, int(profile["default_brightness"]))


def profile_flux_kelvin(profile_id: str, settings: dict) -> int:
    profile = PROFILE_DEFINITIONS[normalize_profile_id(profile_id)]
    return clamp_int(settings.get(profile["flux_key"]), 800, 10000, int(profile["default_flux_kelvin"]))


def profile_from_legacy_mode(value) -> str:
    return "night" if str(value).lower() == "dark" else DEFAULT_PROFILE


def mode_display_name(dark: bool) -> str:
    return "Dark" if dark else "White"


def mode_change_status_text(target_dark: bool) -> str:
    return f"Changing to {mode_display_name(target_dark)} mode. Please wait..."


def mode_change_button_text(target_dark: bool) -> str:
    return f"Changing to {mode_display_name(target_dark)} Mode..."


def profile_change_status_text(profile_id: str) -> str:
    return f"Changing to {profile_label(profile_id)} mode. Please wait..."


def profile_change_button_text(profile_id: str) -> str:
    return f"Changing to {profile_label(profile_id)}..."


def merge_config_data(data: dict) -> dict:
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    if int(data.get("config_version", 1)) < 2:
        merged["prompt_before_closing_chrome"] = False
        merged["reopen_chrome_after_flag"] = True
        merged["restore_chrome_pages"] = True
    if int(data.get("config_version", 1)) < 3:
        merged["restore_chrome_pages"] = True
    if int(data.get("config_version", 1)) < 4:
        merged["app_window_theme"] = True
        merged["app_theme"] = normalize_app_theme(data.get("last_mode", DEFAULT_CONFIG["app_theme"]))
    if int(data.get("config_version", 1)) < 5:
        merged["start_with_windows"] = True
        merged["start_minimized_to_tray"] = True
    if int(data.get("config_version", 1)) < 6:
        merged["current_profile"] = profile_from_legacy_mode(data.get("last_mode", data.get("app_theme", "white")))
        if bool(merged.get("app_window_theme", True)):
            merged["app_theme"] = profile_theme(merged["current_profile"])
    merged["app_theme"] = normalize_app_theme(merged.get("app_theme"))
    merged["current_profile"] = normalize_profile_id(merged.get("current_profile"))
    merged["config_version"] = DEFAULT_CONFIG["config_version"]
    return merged


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    return merge_config_data(data)


def save_config(config: dict) -> None:
    config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")


def platform_name() -> str:
    if IS_WINDOWS:
        return "Windows"
    if IS_MAC:
        return "macOS"
    return sys.platform


def startup_label() -> str:
    return "Start with macOS" if IS_MAC else "Start with Windows"


def quote_command_part(value: str | Path) -> str:
    return '"' + str(value).replace('"', r'\"') + '"'


def app_launch_command_parts() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


def build_startup_command(command_parts: list[str], minimized_to_tray: bool = True) -> str:
    parts = [quote_command_part(part) for part in command_parts]
    if minimized_to_tray:
        parts.append("--startup")
    return " ".join(parts)


def startup_registry_command(minimized_to_tray: bool = True) -> str:
    return build_startup_command(app_launch_command_parts(), minimized_to_tray)


def macos_launch_agent_label() -> str:
    return "com.guyhouri.dark-white-mode"


def macos_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{macos_launch_agent_label()}.plist"


def macos_launch_agent_program_arguments(command_parts: list[str], minimized_to_tray: bool = True) -> list[str]:
    parts = [str(part) for part in command_parts]
    if minimized_to_tray:
        parts.append("--startup")
    return parts


def build_macos_launch_agent_plist(command_parts: list[str], minimized_to_tray: bool = True) -> bytes:
    payload = {
        "Label": macos_launch_agent_label(),
        "ProgramArguments": macos_launch_agent_program_arguments(command_parts, minimized_to_tray),
        "RunAtLoad": True,
    }
    return plistlib.dumps(payload, sort_keys=True)


def set_windows_startup(enabled: bool, minimized_to_tray: bool = True) -> StepResult:
    if not IS_WINDOWS or winreg is None:
        return StepResult("Startup", False, "Windows startup is only available on Windows.")
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_registry_command(minimized_to_tray))
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        message = "Enabled Windows startup." if enabled else "Disabled Windows startup."
        return StepResult("Startup", True, message)
    except Exception as exc:
        return StepResult("Startup", False, f"Windows startup update failed: {exc}")


def set_macos_startup(enabled: bool, minimized_to_tray: bool = True) -> StepResult:
    if not IS_MAC:
        return StepResult("Startup", False, "macOS startup is only available on macOS.")
    try:
        path = macos_launch_agent_path()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_macos_launch_agent_plist(app_launch_command_parts(), minimized_to_tray))
            return StepResult("Startup", True, "Enabled macOS startup LaunchAgent.")
        if path.exists():
            path.unlink()
        return StepResult("Startup", True, "Disabled macOS startup LaunchAgent.")
    except Exception as exc:
        return StepResult("Startup", False, f"macOS startup update failed: {exc}")


def set_platform_startup(enabled: bool, minimized_to_tray: bool = True) -> StepResult:
    if IS_WINDOWS:
        return set_windows_startup(enabled, minimized_to_tray)
    if IS_MAC:
        return set_macos_startup(enabled, minimized_to_tray)
    return StepResult("Startup", False, f"Startup is not supported on {platform_name()}.")


def set_windows_app_user_model_id() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        return True
    except Exception:
        return False


def windows_start_menu_shortcut_path() -> Path:
    app_data = Path(os.environ.get("APPDATA", str(Path.home())))
    return app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def shortcut_working_directory(command_parts: list[str]) -> str:
    if getattr(sys, "frozen", False):
        return str(Path(command_parts[0]).resolve().parent)
    if len(command_parts) > 1:
        return str(Path(command_parts[1]).resolve().parent)
    return str(Path(command_parts[0]).resolve().parent)


def build_windows_shortcut_script(shortcut_path: Path, command_parts: list[str]) -> str:
    target_path = str(Path(command_parts[0]).resolve())
    arguments = " ".join(quote_command_part(part) for part in command_parts[1:])
    payload = {
        "shortcut_path": str(shortcut_path),
        "target_path": target_path,
        "arguments": arguments,
        "working_directory": shortcut_working_directory(command_parts),
        "description": f"{APP_NAME} by Guy Houri",
        "icon_location": f"{target_path},0",
    }
    payload_json = json.dumps(payload)
    return f"""
$data = @'
{payload_json}
'@ | ConvertFrom-Json
$parent = Split-Path -Parent $data.shortcut_path
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($data.shortcut_path)
$shortcut.TargetPath = $data.target_path
$shortcut.Arguments = $data.arguments
$shortcut.WorkingDirectory = $data.working_directory
$shortcut.Description = $data.description
$shortcut.IconLocation = $data.icon_location
$shortcut.Save()
"""


def ensure_windows_start_menu_shortcut() -> StepResult:
    if not IS_WINDOWS:
        return StepResult("Shortcut", False, "Start Menu shortcut is only available on Windows.")
    try:
        script = build_windows_shortcut_script(windows_start_menu_shortcut_path(), app_launch_command_parts())
        completed = run_hidden(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            timeout=10,
        )
        if completed.returncode == 0:
            return StepResult("Shortcut", True, "Windows Start Menu shortcut is ready.")
        message = (completed.stderr or completed.stdout or "").strip()
        return StepResult("Shortcut", False, "Windows Start Menu shortcut failed. " + message)
    except Exception as exc:
        return StepResult("Shortcut", False, f"Windows Start Menu shortcut failed: {exc}")


def clamp_int(value, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


def broadcast_windows_theme_change() -> None:
    user32 = ctypes.windll.user32
    for area in ("ImmersiveColorSet", "WindowsThemeElement"):
        result = ctypes.c_ulong()
        user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            area,
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )


def read_windows_mode() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "white" if int(value) else "dark"
    except OSError:
        return "white"


def set_windows_mode(dark: bool) -> StepResult:
    if not IS_WINDOWS or winreg is None:
        return StepResult("System", False, "Windows theme is only available on Windows.")
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            light_value = 0 if dark else 1
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, light_value)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, light_value)
        broadcast_windows_theme_change()
        return StepResult("Windows", True, "Windows theme set to dark." if dark else "Windows theme set to light.")
    except Exception as exc:
        return StepResult("Windows", False, f"Windows theme failed: {exc}")


def read_macos_mode() -> str:
    try:
        completed = run_hidden(["defaults", "read", "-g", "AppleInterfaceStyle"], timeout=6)
    except Exception:
        return "white"
    return "dark" if completed.returncode == 0 and "Dark" in completed.stdout else "white"


def set_macos_mode(dark: bool) -> StepResult:
    value = "true" if dark else "false"
    script = f'tell application "System Events" to tell appearance preferences to set dark mode to {value}'
    try:
        completed = run_hidden(["osascript", "-e", script], timeout=12)
        if completed.returncode == 0:
            return StepResult("System", True, "macOS appearance set to dark." if dark else "macOS appearance set to light.")
        message = (completed.stderr or completed.stdout or "").strip()
        return StepResult("System", False, "macOS appearance update failed. " + message)
    except Exception as exc:
        return StepResult("System", False, f"macOS appearance update failed: {exc}")


def read_system_mode() -> str:
    if IS_MAC:
        return read_macos_mode()
    return read_windows_mode()


def set_system_mode(dark: bool) -> StepResult:
    if IS_MAC:
        return set_macos_mode(dark)
    return set_windows_mode(dark)


def run_hidden(command, timeout=12):
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kwargs,
    )


def popen_hidden(command):
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.Popen(command, **kwargs)


def is_process_running(image_name: str) -> bool:
    if IS_WINDOWS:
        try:
            completed = run_hidden(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
                timeout=6,
            )
        except Exception:
            return False
        return image_name.lower() in completed.stdout.lower()

    try:
        completed = run_hidden(["pgrep", "-x", image_name], timeout=6)
    except Exception:
        return False
    return completed.returncode == 0


def chrome_process_name() -> str:
    return "chrome.exe" if IS_WINDOWS else "Google Chrome"


def is_chrome_running() -> bool:
    return is_process_running(chrome_process_name())


def wait_for_process_exit(image_name: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_process_running(image_name):
            return True
        time.sleep(0.4)
    return not is_process_running(image_name)


def close_chrome_for_flag() -> StepResult:
    if not is_chrome_running():
        return StepResult("Chrome", True, "Chrome was already closed.")

    if IS_MAC:
        messages = []
        try:
            run_hidden(["osascript", "-e", 'tell application "Google Chrome" to quit'], timeout=8)
            messages.append("Asked Chrome to quit.")
        except Exception as exc:
            messages.append(f"AppleScript quit failed: {exc}")
        if wait_for_process_exit(chrome_process_name(), 10):
            return StepResult("Chrome", True, "Chrome closed.")

        try:
            run_hidden(["pkill", "-x", "Google Chrome"], timeout=8)
            messages.append("Forced remaining Chrome processes to close.")
        except Exception as exc:
            messages.append(f"Forced close failed: {exc}")
        if wait_for_process_exit(chrome_process_name(), 8):
            return StepResult("Chrome", True, "Chrome closed after forcing remaining processes.")
        return StepResult("Chrome", False, "Chrome is still running. " + " ".join(messages))

    messages = []
    try:
        run_hidden(["taskkill", "/IM", "chrome.exe", "/T"], timeout=8)
        messages.append("Asked Chrome to close.")
    except Exception as exc:
        messages.append(f"Graceful close failed: {exc}")
    if wait_for_process_exit(chrome_process_name(), 10):
        return StepResult("Chrome", True, "Chrome closed.")

    try:
        run_hidden(["taskkill", "/IM", "chrome.exe", "/T", "/F"], timeout=8)
        messages.append("Forced remaining Chrome processes to close.")
    except Exception as exc:
        messages.append(f"Forced close failed: {exc}")
    if wait_for_process_exit(chrome_process_name(), 8):
        return StepResult("Chrome", True, "Chrome closed after forcing remaining processes.")

    script = "Get-Process chrome -ErrorAction SilentlyContinue | Stop-Process -Force"
    try:
        run_hidden(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            timeout=8,
        )
        messages.append("Tried PowerShell Stop-Process fallback.")
    except Exception as exc:
        messages.append(f"PowerShell fallback failed: {exc}")

    if wait_for_process_exit(chrome_process_name(), 5):
        return StepResult("Chrome", True, "Chrome closed after fallback.")
    return StepResult("Chrome", False, "Chrome is still running. " + " ".join(messages))


def find_chrome_exe() -> Path | None:
    if IS_MAC:
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("PROGRAMFILES")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")

    if local_app_data:
        candidates.append(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe")
    if program_files:
        candidates.append(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe")

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ) as key:
            value, _ = winreg.QueryValueEx(key, None)
            candidates.insert(0, Path(str(value).strip('"')))
    except OSError:
        pass

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ) as key:
            value, _ = winreg.QueryValueEx(key, None)
            candidates.insert(0, Path(str(value).strip('"')))
    except OSError:
        pass

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def build_chrome_reopen_command(chrome_exe: Path, restore_pages: bool = True) -> list[str]:
    command = [str(chrome_exe)]
    if restore_pages:
        command.extend(["--restore-last-session", "--hide-crash-restore-bubble"])
    return command


def reopen_chrome(restore_pages: bool = True) -> StepResult:
    chrome_exe = find_chrome_exe()
    if not chrome_exe:
        return StepResult("Chrome", False, "Chrome flag changed, but chrome.exe was not found to reopen it.")
    try:
        popen_hidden(build_chrome_reopen_command(chrome_exe, restore_pages))
        message = "Chrome reopened with session restore requested." if restore_pages else "Chrome reopened."
        return StepResult("Chrome", True, message)
    except Exception as exc:
        return StepResult("Chrome", False, f"Chrome flag changed, but reopen failed: {exc}")


def create_icon_image(size: int = 64):
    if Image is None or ImageDraw is None:
        return None

    scale = size / 64

    def scaled(value: int) -> int:
        return int(round(value * scale))

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    border_width = max(1, scaled(2))
    draw.rounded_rectangle(
        (scaled(4), scaled(4), scaled(60), scaled(60)),
        radius=scaled(12),
        fill=(26, 28, 33),
        outline=(230, 230, 230),
        width=border_width,
    )
    draw.pieslice((scaled(12), scaled(12), scaled(52), scaled(52)), 90, 270, fill=(245, 245, 245))
    draw.pieslice((scaled(12), scaled(12), scaled(52), scaled(52)), 270, 90, fill=(22, 22, 24))
    draw.ellipse(
        (scaled(12), scaled(12), scaled(52), scaled(52)),
        outline=(250, 250, 250),
        width=border_width,
    )
    return image


def create_tray_image():
    return create_icon_image(64)


def save_app_icon(path: Path) -> Path:
    image = create_icon_image(256)
    if image is None:
        raise RuntimeError("Pillow is required to generate the app icon.")

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return path


def runtime_icon_path() -> Path:
    return app_data_dir() / f"{APP_NAME}.ico"


def chrome_local_state_path() -> Path | None:
    if IS_MAC:
        path = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Local State"
        return path if path.exists() else None

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    path = Path(local_app_data) / "Google" / "Chrome" / "User Data" / "Local State"
    return path if path.exists() else None


def chrome_user_data_dir() -> Path | None:
    if IS_MAC:
        path = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        return path if path.exists() else None

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    path = Path(local_app_data) / "Google" / "Chrome" / "User Data"
    return path if path.exists() else None


def chrome_profile_preferences_paths(user_data_dir: Path | None = None) -> list[Path]:
    base = user_data_dir if user_data_dir is not None else chrome_user_data_dir()
    if not base or not base.exists():
        return []

    paths = []
    for child in base.iterdir():
        if not child.is_dir() or child.name == "System Profile":
            continue
        preferences_path = child / "Preferences"
        if preferences_path.exists():
            paths.append(preferences_path)
    return sorted(paths)


def update_chrome_state_data(state: dict, enable: bool) -> dict:
    browser = state.setdefault("browser", {})
    experiments = browser.get("enabled_labs_experiments")
    if not isinstance(experiments, list):
        experiments = []

    filtered = [
        experiment
        for experiment in experiments
        if not (isinstance(experiment, str) and experiment.startswith("enable-force-dark"))
    ]
    if enable:
        filtered.append("enable-force-dark@1")

    browser["enabled_labs_experiments"] = filtered
    return state


def update_chrome_preferences_data(preferences: dict, restore_pages: bool = True) -> dict:
    if restore_pages:
        session = preferences.setdefault("session", {})
        session["restore_on_startup"] = 1

    profile = preferences.setdefault("profile", {})
    profile["exited_cleanly"] = True
    profile["exit_type"] = "Normal"
    return preferences


def set_chrome_restore_preferences(restore_pages: bool, user_data_dir: Path | None = None) -> StepResult:
    paths = chrome_profile_preferences_paths(user_data_dir)
    if not paths:
        return StepResult("Chrome", False, "No Chrome profile Preferences files were found for session restore.")

    updated = 0
    errors = []
    for path in paths:
        try:
            preferences = json.loads(path.read_text(encoding="utf-8"))
            update_chrome_preferences_data(preferences, restore_pages)
            backup_path = path.with_name("Preferences.dark-white-mode.bak")
            if not backup_path.exists():
                shutil.copy2(path, backup_path)
            path.write_text(json.dumps(preferences, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
            updated += 1
        except Exception as exc:
            errors.append(f"{path.parent.name}: {exc}")

    if updated:
        message = f"Chrome restore preferences updated for {updated} profile(s)."
        if errors:
            message += " Some profiles failed: " + "; ".join(errors)
        return StepResult("Chrome", True, message)
    return StepResult("Chrome", False, "Chrome restore preferences were not changed. " + "; ".join(errors))


def set_chrome_force_dark(enable: bool, reopen_after: bool = False, restore_pages: bool = True) -> StepResult:
    path = chrome_local_state_path()
    if not path:
        return StepResult("Chrome", False, "Chrome Local State file was not found.")

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        update_chrome_state_data(state, enable)
        backup_path = path.with_name("Local State.dark-white-mode.bak")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
        path.write_text(json.dumps(state, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        return StepResult("Chrome", False, f"Chrome flag update failed: {exc}")

    action = "enabled" if enable else "removed"
    message = f"Chrome force-dark flag {action}."
    if restore_pages:
        restore_result = set_chrome_restore_preferences(restore_pages)
        message += " " + restore_result.message
    if reopen_after:
        reopen_result = reopen_chrome(restore_pages)
        if reopen_result.ok:
            message += " " + reopen_result.message
        else:
            message += " " + reopen_result.message
    else:
        message += " Restart Chrome to apply."
    return StepResult("Chrome", True, message)


def set_brightness_wmi(level: int) -> tuple[bool, str]:
    script = f"""
$level = {level}
$methods = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue
if ($methods) {{
    foreach ($method in $methods) {{
        Invoke-CimMethod -InputObject $method -MethodName WmiSetBrightness -Arguments @{{ Timeout = 1; Brightness = $level }} | Out-Null
    }}
    exit 0
}}
$legacy = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue
if ($legacy) {{
    foreach ($method in $legacy) {{
        $method.WmiSetBrightness(1, $level) | Out-Null
    }}
    exit 0
}}
exit 2
"""
    try:
        completed = run_hidden(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            timeout=15,
        )
    except Exception as exc:
        return False, f"WMI brightness failed: {exc}"

    if completed.returncode == 0:
        return True, "WMI brightness updated."
    stderr = (completed.stderr or "").strip()
    return False, stderr or "No WMI brightness-capable display was found."


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class PhysicalMonitor(ctypes.Structure):
    _fields_ = [
        ("hPhysicalMonitor", ctypes.c_void_p),
        ("szPhysicalMonitorDescription", ctypes.c_wchar * 128),
    ]


def set_brightness_ddc(level: int) -> tuple[bool, str]:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)
    except OSError as exc:
        return False, f"DDC/CI unavailable: {exc}"

    monitors = []
    lparam_type = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    enum_proc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(Rect),
        lparam_type,
    )

    def on_monitor(hmonitor, _hdc, _rect, _data):
        monitors.append(hmonitor)
        return True

    callback = enum_proc(on_monitor)
    if not user32.EnumDisplayMonitors(None, None, callback, 0):
        return False, "No display monitors were enumerated."

    dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = ctypes.c_bool
    dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(PhysicalMonitor),
    ]
    dxva2.GetPhysicalMonitorsFromHMONITOR.restype = ctypes.c_bool
    dxva2.SetVCPFeature.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ulong]
    dxva2.SetVCPFeature.restype = ctypes.c_bool
    dxva2.DestroyPhysicalMonitors.argtypes = [
        ctypes.c_ulong,
        ctypes.POINTER(PhysicalMonitor),
    ]
    dxva2.DestroyPhysicalMonitors.restype = ctypes.c_bool

    changed = 0
    physical_count = 0
    for hmonitor in monitors:
        count = ctypes.c_ulong()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)):
            continue
        if count.value == 0:
            continue
        array_type = PhysicalMonitor * count.value
        physical_monitors = array_type()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count, physical_monitors):
            continue
        try:
            for monitor in physical_monitors:
                physical_count += 1
                if dxva2.SetVCPFeature(monitor.hPhysicalMonitor, 0x10, level):
                    changed += 1
        finally:
            dxva2.DestroyPhysicalMonitors(count, physical_monitors)

    if changed:
        return True, f"DDC/CI brightness updated on {changed} display(s)."
    if physical_count:
        return False, "Displays were found, but none accepted DDC/CI brightness control."
    return False, "No DDC/CI-capable physical display was found."


def set_brightness(level: int) -> StepResult:
    if IS_MAC:
        return set_brightness_macos(level)

    level = clamp_int(level, 0, 100, 1)
    messages = []
    ok = False

    wmi_ok, wmi_message = set_brightness_wmi(level)
    messages.append(wmi_message)
    ok = ok or wmi_ok

    ddc_ok, ddc_message = set_brightness_ddc(level)
    messages.append(ddc_message)
    ok = ok or ddc_ok

    if ok:
        return StepResult("Brightness", True, f"Brightness set to {level}%. " + " ".join(messages))
    return StepResult("Brightness", False, "Brightness was not changed. " + " ".join(messages))


def find_command(candidates: list[str]) -> Path | None:
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return Path(path)
    return None


def set_brightness_macos(level: int) -> StepResult:
    level = clamp_int(level, 0, 100, 1)
    brightness_tool = find_command(["brightness"])
    if not brightness_tool:
        return StepResult(
            "Brightness",
            False,
            "macOS brightness requires the optional 'brightness' command-line tool. Install it with Homebrew: brew install brightness.",
        )
    try:
        completed = run_hidden([str(brightness_tool), str(level / 100)], timeout=10)
        if completed.returncode == 0:
            return StepResult("Brightness", True, f"Brightness set to {level}%.")
        message = (completed.stderr or completed.stdout or "").strip()
        return StepResult("Brightness", False, "macOS brightness update failed. " + message)
    except Exception as exc:
        return StepResult("Brightness", False, f"macOS brightness update failed: {exc}")


def parse_flux_run_value(value: str) -> Path | None:
    quoted = re.search(r'"([^"]*flux\.exe)"', value, flags=re.IGNORECASE)
    if quoted:
        return Path(quoted.group(1))
    match = re.search(r"([^\s]+flux\.exe)", value, flags=re.IGNORECASE)
    return Path(match.group(1)) if match else None


def find_flux_exe() -> Path | None:
    if IS_MAC:
        candidates = [
            Path("/Applications/Flux.app"),
            Path("/Applications/f.lux.app"),
            Path.home() / "Applications" / "Flux.app",
            Path.home() / "Applications" / "f.lux.app",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.extend(
            [
                Path(local_app_data) / "FluxSoftware" / "Flux" / "flux.exe",
                Path(local_app_data) / "Apps" / "F.lux" / "flux.exe",
            ]
        )

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        ) as key:
            run_value, _ = winreg.QueryValueEx(key, "f.lux")
            parsed = parse_flux_run_value(str(run_value))
            if parsed:
                candidates.insert(0, parsed)
    except OSError:
        pass

    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def set_flux_kelvin(kelvin: int) -> StepResult:
    if IS_MAC:
        flux_app = find_flux_exe()
        if flux_app:
            try:
                run_hidden(["open", str(flux_app)], timeout=8)
            except Exception:
                pass
        return StepResult(
            "f.lux",
            False,
            "macOS f.lux Kelvin switching is not supported yet because f.lux does not expose a stable public preset API.",
        )

    kelvin = clamp_int(kelvin, 800, 10000, 6500)
    key_path = r"Software\Michael Herf\flux\Preferences"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            for value_name in ("Outdoor", "Indoor", "Late"):
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, kelvin)
    except Exception as exc:
        return StepResult("f.lux", False, f"f.lux registry update failed: {exc}")

    flux_exe = find_flux_exe()
    if not flux_exe:
        return StepResult("f.lux", True, f"f.lux set to {kelvin}K, but flux.exe was not found to restart it.")

    try:
        if is_process_running("flux.exe"):
            run_hidden(["taskkill", "/IM", "flux.exe", "/F"], timeout=8)
            time.sleep(0.4)
        popen_hidden([str(flux_exe), "/noshow"])
        return StepResult("f.lux", True, f"f.lux set to {kelvin}K and restarted.")
    except Exception as exc:
        return StepResult("f.lux", False, f"f.lux set to {kelvin}K, but restart failed: {exc}")


class DarkWhiteModeApp(tk.Tk):
    def __init__(self, start_hidden: bool = False):
        set_windows_app_user_model_id()
        super().__init__()
        self.app_icon_photo = None
        self.apply_window_icon()
        self.title(APP_NAME)
        self.geometry("540x860")
        self.minsize(520, 780)
        self.config_data = load_config()
        self.start_hidden = start_hidden
        self.current_profile = normalize_profile_id(self.config_data.get("current_profile"))
        self.vars = {}
        self.profile_buttons = {}
        self.running = False
        self.busy_target_profile = None
        self.quitting = False
        self.tray_icon = None
        self.tray_started = False
        self.tray_notice_shown = False
        self.ui_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.style = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Unmap>", self._on_unmap)
        self.sync_startup_setting(log_result=False)
        self.sync_start_menu_shortcut(log_result=False)
        self._refresh_button()
        self.after(100, self._drain_ui_queue)
        self.after(500, self.start_tray_icon)
        self.after(900, self._maybe_hide_startup_window)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        self.style.configure("Mode.TLabel", font=("Segoe UI", 11))
        self.style.configure("Busy.TLabel", font=("Segoe UI", 10, "bold"))
        self.style.configure("Big.TButton", font=("Segoe UI", 14, "bold"), padding=(18, 14))
        self.style.configure("Profile.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 10))
        self.style.configure("ActiveProfile.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 10))

        root = ttk.Frame(self, padding=18)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)

        ttk.Label(root, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.mode_label = ttk.Label(root, text="", style="Mode.TLabel")
        self.mode_label.grid(row=1, column=0, sticky="w", pady=(4, 16))

        self.app_theme_button = ttk.Button(root, text="", command=self.toggle_app_theme)
        self.app_theme_button.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        profile_frame = ttk.Frame(root)
        profile_frame.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        for column, profile_id in enumerate(PROFILE_ORDER):
            profile_frame.columnconfigure(column, weight=1)
            button = ttk.Button(
                profile_frame,
                text=profile_label(profile_id),
                style="Profile.TButton",
                command=lambda selected=profile_id: self.apply_profile(selected),
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0 if column == len(PROFILE_ORDER) - 1 else 4))
            self.profile_buttons[profile_id] = button

        self.busy_frame = ttk.Frame(root)
        self.busy_frame.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        self.busy_frame.columnconfigure(0, weight=1)
        self.busy_label = ttk.Label(self.busy_frame, text="", style="Busy.TLabel")
        self.busy_label.grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.busy_progress = ttk.Progressbar(
            self.busy_frame,
            mode="indeterminate",
            style="Busy.Horizontal.TProgressbar",
        )
        self.busy_progress.grid(row=1, column=0, sticky="ew")
        self.busy_frame.grid_remove()

        targets = ttk.LabelFrame(root, text="Targets", padding=12)
        targets.grid(row=5, column=0, sticky="ew", pady=(0, 12))
        targets.columnconfigure(0, weight=1)
        for index, (key, label) in enumerate(
            [
                ("app_window_theme", "App window theme"),
                ("windows_theme", "System theme"),
                ("chrome_force_dark", "Chrome force-dark flag"),
                ("brightness", "Display brightness"),
                ("flux", "f.lux color temperature"),
            ]
        ):
            var = tk.BooleanVar(value=bool(self.config_data[key]))
            self.vars[key] = var
            ttk.Checkbutton(targets, text=label, variable=var).grid(row=index, column=0, sticky="w", pady=2)

        values = ttk.LabelFrame(root, text="Values", padding=12)
        values.grid(row=6, column=0, sticky="ew", pady=(0, 12))
        values.columnconfigure(1, weight=1)

        self._add_spinbox(values, 0, "Night brightness", "night_brightness", 0, 100, "%")
        self._add_spinbox(values, 1, "Outside brightness", "outside_brightness", 0, 100, "%")
        self._add_spinbox(values, 2, "Indoor brightness", "indoor_brightness", 0, 100, "%")
        self._add_spinbox(values, 3, "Night f.lux", "night_flux_kelvin", 800, 10000, "K")
        self._add_spinbox(values, 4, "Outside f.lux", "outside_flux_kelvin", 800, 10000, "K")
        self._add_spinbox(values, 5, "Indoor f.lux", "indoor_flux_kelvin", 800, 10000, "K")

        chrome_var = tk.BooleanVar(value=bool(self.config_data["prompt_before_closing_chrome"]))
        self.vars["prompt_before_closing_chrome"] = chrome_var

        startup_var = tk.BooleanVar(value=bool(self.config_data["start_with_windows"]))
        self.vars["start_with_windows"] = startup_var
        ttk.Checkbutton(
            root,
            text=startup_label(),
            variable=startup_var,
            command=self.sync_startup_setting,
        ).grid(row=7, column=0, sticky="w", pady=(0, 12))

        startup_minimized_var = tk.BooleanVar(value=bool(self.config_data["start_minimized_to_tray"]))
        self.vars["start_minimized_to_tray"] = startup_minimized_var
        ttk.Checkbutton(
            root,
            text="Start minimized to tray",
            variable=startup_minimized_var,
            command=self.sync_startup_setting,
        ).grid(row=8, column=0, sticky="w", pady=(0, 12))

        ttk.Checkbutton(
            root,
            text="Ask before closing Chrome",
            variable=chrome_var,
        ).grid(row=9, column=0, sticky="w", pady=(0, 12))

        reopen_chrome_var = tk.BooleanVar(value=bool(self.config_data["reopen_chrome_after_flag"]))
        self.vars["reopen_chrome_after_flag"] = reopen_chrome_var
        ttk.Checkbutton(
            root,
            text="Reopen Chrome after updating flag",
            variable=reopen_chrome_var,
        ).grid(row=10, column=0, sticky="w", pady=(0, 12))

        restore_chrome_var = tk.BooleanVar(value=bool(self.config_data["restore_chrome_pages"]))
        self.vars["restore_chrome_pages"] = restore_chrome_var
        ttk.Checkbutton(
            root,
            text="Restore Chrome pages automatically",
            variable=restore_chrome_var,
        ).grid(row=11, column=0, sticky="w", pady=(0, 12))

        status_frame = ttk.LabelFrame(root, text="Status", padding=8)
        status_frame.grid(row=12, column=0, sticky="nsew")
        root.rowconfigure(12, weight=1)
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)

        self.status_text = tk.Text(
            status_frame,
            height=8,
            wrap="word",
            state="disabled",
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.status_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.configure(yscrollcommand=scrollbar.set)
        ttk.Label(root, text=CREDIT_TEXT).grid(row=13, column=0, sticky="w", pady=(10, 0))
        self.apply_app_theme()
        self.log("Ready.")

    def theme_colors(self) -> dict:
        return APP_THEME_COLORS[normalize_app_theme(self.config_data.get("app_theme"))]

    def apply_app_theme(self):
        colors = self.theme_colors()
        self.configure(background=colors["bg"])
        if self.style is not None:
            self.style.configure(".", background=colors["bg"], foreground=colors["text"])
            self.style.configure("TFrame", background=colors["bg"])
            self.style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
            self.style.configure("Title.TLabel", background=colors["bg"], foreground=colors["text"])
            self.style.configure("Mode.TLabel", background=colors["bg"], foreground=colors["muted"])
            self.style.configure("Busy.TLabel", background=colors["bg"], foreground=colors["accent"])
            self.style.configure(
                "TLabelframe",
                background=colors["bg"],
                foreground=colors["text"],
                bordercolor=colors["border"],
                relief="solid",
            )
            self.style.configure(
                "TLabelframe.Label",
                background=colors["bg"],
                foreground=colors["text"],
            )
            self.style.configure(
                "TCheckbutton",
                background=colors["bg"],
                foreground=colors["text"],
                focuscolor=colors["border"],
            )
            self.style.map(
                "TCheckbutton",
                background=[("active", colors["bg"])],
                foreground=[("disabled", colors["muted"]), ("active", colors["text"])],
            )
            self.style.configure(
                "TButton",
                background=colors["surface"],
                foreground=colors["text"],
                bordercolor=colors["border"],
                focuscolor=colors["border"],
            )
            self.style.map(
                "TButton",
                background=[("active", colors["select_bg"]), ("disabled", colors["surface"])],
                foreground=[("disabled", colors["muted"]), ("active", colors["text"])],
            )
            self.style.configure(
                "Big.TButton",
                background=colors["accent"],
                foreground=colors["button_fg"],
                bordercolor=colors["accent"],
                focuscolor=colors["accent"],
            )
            self.style.map(
                "Big.TButton",
                background=[("active", colors["accent_hover"]), ("disabled", colors["surface"])],
                foreground=[("disabled", colors["muted"]), ("active", colors["button_fg"])],
            )
            self.style.configure(
                "Profile.TButton",
                background=colors["surface"],
                foreground=colors["text"],
                bordercolor=colors["border"],
                focuscolor=colors["border"],
            )
            self.style.map(
                "Profile.TButton",
                background=[("active", colors["select_bg"]), ("disabled", colors["surface"])],
                foreground=[("disabled", colors["muted"]), ("active", colors["text"])],
            )
            self.style.configure(
                "ActiveProfile.TButton",
                background=colors["accent"],
                foreground=colors["button_fg"],
                bordercolor=colors["accent"],
                focuscolor=colors["accent"],
            )
            self.style.map(
                "ActiveProfile.TButton",
                background=[("active", colors["accent_hover"]), ("disabled", colors["surface"])],
                foreground=[("disabled", colors["muted"]), ("active", colors["button_fg"])],
            )
            self.style.configure(
                "TSpinbox",
                fieldbackground=colors["field"],
                background=colors["surface"],
                foreground=colors["text"],
                bordercolor=colors["border"],
                arrowcolor=colors["text"],
            )
            self.style.configure(
                "Busy.Horizontal.TProgressbar",
                background=colors["accent"],
                troughcolor=colors["surface"],
                bordercolor=colors["border"],
                lightcolor=colors["accent"],
                darkcolor=colors["accent"],
            )

        if hasattr(self, "status_text"):
            self.status_text.configure(
                background=colors["status_bg"],
                foreground=colors["status_fg"],
                insertbackground=colors["text"],
                selectbackground=colors["select_bg"],
                selectforeground=colors["text"],
            )
        self._refresh_button()

    def set_app_theme(self, theme: str, persist: bool = True):
        self.config_data["app_theme"] = normalize_app_theme(theme)
        if persist:
            save_config(self.config_data)
        self.apply_app_theme()

    def toggle_app_theme(self):
        next_theme = opposite_app_theme(self.config_data.get("app_theme"))
        self.set_app_theme(next_theme)
        self.log(f"OK - App: App window theme set to {next_theme}.")

    def start_tray_icon(self):
        if self.tray_started:
            return
        if pystray is None:
            self.log("WARN - Tray: pystray is not installed, so the tray icon is unavailable.")
            return

        image = create_tray_image()
        if image is None:
            self.log("WARN - Tray: tray icon image could not be created.")
            return

        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem(
                "Night mode",
                self._tray_apply_profile("night"),
                checked=lambda _item: self.current_profile == "night",
                enabled=lambda _item: not self.running,
            ),
            pystray.MenuItem(
                "Outside mode",
                self._tray_apply_profile("outside"),
                checked=lambda _item: self.current_profile == "outside",
                enabled=lambda _item: not self.running,
            ),
            pystray.MenuItem(
                "Work Indoors",
                self._tray_apply_profile("work_indoors"),
                checked=lambda _item: self.current_profile == "work_indoors",
                enabled=lambda _item: not self.running,
            ),
            pystray.MenuItem("Next mode", self._tray_toggle, enabled=lambda _item: not self.running),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self.tray_icon = pystray.Icon(APP_NAME, image, f"{APP_NAME} - Guy Houri", menu)
        thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        thread.start()
        self.tray_started = True
        self.log("OK - Tray: icon is running in the Windows notification area.")

    def _tray_show(self, _icon=None, _item=None):
        self.ui_queue.put(self.show_window)

    def _tray_toggle(self, _icon=None, _item=None):
        self.ui_queue.put(self.toggle)

    def _tray_apply_profile(self, profile_id: str):
        def callback(_icon=None, _item=None):
            self.ui_queue.put(lambda: self.apply_profile(profile_id))

        return callback

    def _tray_quit(self, _icon=None, _item=None):
        self.ui_queue.put(self.quit_app)

    def _drain_ui_queue(self):
        while True:
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            callback()
        if not self.quitting:
            self.after(100, self._drain_ui_queue)

    def _on_unmap(self, event):
        if event.widget is self and self.state() == "iconic" and not self.quitting:
            self.after(0, self.hide_to_tray)

    def hide_to_tray(self):
        self.withdraw()
        if self.tray_icon is not None and not self.tray_notice_shown:
            self.tray_notice_shown = True
            try:
                self.tray_icon.notify("Still running in the system tray.", APP_NAME)
            except Exception:
                pass

    def show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def quit_app(self):
        self.quitting = True
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    def _maybe_hide_startup_window(self):
        if not self.start_hidden or self.quitting:
            return
        if not self.config_data.get("start_minimized_to_tray"):
            return
        if self.tray_icon is None:
            self.log("WARN - Startup: Could not hide to tray because the tray icon is unavailable.")
            return
        self.hide_to_tray()

    def sync_startup_setting(self, log_result: bool = True):
        if "start_with_windows" in self.vars:
            self.config_data["start_with_windows"] = bool(self.vars["start_with_windows"].get())
        if "start_minimized_to_tray" in self.vars:
            self.config_data["start_minimized_to_tray"] = bool(self.vars["start_minimized_to_tray"].get())
        save_config(self.config_data)

        result = set_platform_startup(
            bool(self.config_data.get("start_with_windows")),
            bool(self.config_data.get("start_minimized_to_tray")),
        )
        if log_result:
            prefix = "OK" if result.ok else "WARN"
            self.log(f"{prefix} - {result.name}: {result.message}")

    def sync_start_menu_shortcut(self, log_result: bool = True):
        if not IS_WINDOWS:
            return
        result = ensure_windows_start_menu_shortcut()
        if log_result or not result.ok:
            prefix = "OK" if result.ok else "WARN"
            self.log(f"{prefix} - {result.name}: {result.message}")

    def apply_window_icon(self):
        if ImageTk is None:
            return

        image = create_icon_image(256)
        if image is None:
            return

        try:
            self.app_icon_photo = ImageTk.PhotoImage(image)
            self.iconphoto(True, self.app_icon_photo)
        except Exception:
            pass

        if IS_WINDOWS:
            try:
                icon_path = save_app_icon(runtime_icon_path())
                self.iconbitmap(default=str(icon_path))
            except Exception:
                pass

    def _add_spinbox(self, parent, row, label, key, minimum, maximum, suffix):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        value = tk.StringVar(value=str(self.config_data[key]))
        self.vars[key] = value
        spin = ttk.Spinbox(parent, from_=minimum, to=maximum, textvariable=value, width=8)
        spin.grid(row=row, column=1, sticky="w", padx=(12, 6), pady=4)
        ttk.Label(parent, text=suffix).grid(row=row, column=2, sticky="w", pady=4)

    def _refresh_button(self):
        if self.running and self.busy_target_profile is not None:
            self.mode_label.configure(text=profile_change_status_text(self.busy_target_profile))
            for profile_id, button in self.profile_buttons.items():
                button.configure(
                    text=profile_change_button_text(profile_id) if profile_id == self.busy_target_profile else profile_label(profile_id),
                    state="disabled",
                    style="ActiveProfile.TButton" if profile_id == self.busy_target_profile else "Profile.TButton",
                )
            return
        self.mode_label.configure(text=f"Current mode: {profile_label(self.current_profile)}")
        for profile_id, button in self.profile_buttons.items():
            button.configure(
                text=profile_label(profile_id),
                state="normal",
                style="ActiveProfile.TButton" if profile_id == self.current_profile else "Profile.TButton",
            )
        if hasattr(self, "app_theme_button"):
            app_theme = normalize_app_theme(self.config_data.get("app_theme"))
            next_theme = opposite_app_theme(app_theme).title()
            self.app_theme_button.configure(text=f"App Theme: {app_theme.title()}  |  Switch to {next_theme}")

    def _set_busy_state(self, active: bool, target_profile: str | None = None):
        self.running = active
        self.busy_target_profile = normalize_profile_id(target_profile) if active and target_profile else None
        if active:
            for button in self.profile_buttons.values():
                button.configure(state="disabled")
            self.app_theme_button.configure(state="disabled")
            self.busy_label.configure(text=profile_change_status_text(self.busy_target_profile or self.current_profile))
            self.busy_frame.grid()
            self.busy_progress.start(12)
            self.configure(cursor="watch")
            self._refresh_button()
            self.update_idletasks()
            return

        self.busy_progress.stop()
        self.busy_frame.grid_remove()
        self.configure(cursor="")
        for button in self.profile_buttons.values():
            button.configure(state="normal")
        self.app_theme_button.configure(state="normal")
        self._refresh_button()

    def log(self, message: str):
        self.status_text.configure(state="normal")
        self.status_text.insert("end", message + "\n")
        self.status_text.see("end")
        self.status_text.configure(state="disabled")

    def collect_settings(self) -> dict:
        settings = dict(self.config_data)
        for key in (
            "start_with_windows",
            "start_minimized_to_tray",
            "app_window_theme",
            "windows_theme",
            "chrome_force_dark",
            "brightness",
            "flux",
            "prompt_before_closing_chrome",
            "reopen_chrome_after_flag",
            "restore_chrome_pages",
        ):
            settings[key] = bool(self.vars[key].get())
        for profile_id, profile in PROFILE_DEFINITIONS.items():
            settings[profile["brightness_key"]] = clamp_int(
                self.vars[profile["brightness_key"]].get(),
                0,
                100,
                int(profile["default_brightness"]),
            )
            settings[profile["flux_key"]] = clamp_int(
                self.vars[profile["flux_key"]].get(),
                800,
                10000,
                int(profile["default_flux_kelvin"]),
            )
        return settings

    def toggle(self):
        self.apply_profile(next_profile_id(self.current_profile))

    def apply_profile(self, profile_id: str):
        if self.running:
            return
        target_profile = normalize_profile_id(profile_id)
        target_dark = profile_is_dark(target_profile)
        settings = self.collect_settings()
        settings["current_profile"] = target_profile
        if settings["app_window_theme"]:
            settings["app_theme"] = profile_theme(target_profile)
        self.config_data = settings
        save_config(settings)
        if settings["app_window_theme"]:
            self.apply_app_theme()
            self.log(f"OK - App: App window theme set to {settings['app_theme']}.")

        if settings["chrome_force_dark"] and is_chrome_running():
            close_now = True
            if settings["prompt_before_closing_chrome"]:
                close_now = messagebox.askyesno(
                    "Chrome is open",
                    "Chrome must close before its flags file can be changed reliably.\n\n"
                    "Close Chrome now? The app will reopen it after the flag is updated.",
                    parent=self,
                )
            if not close_now:
                self.log("Chrome flag skipped because Chrome is open.")
                settings = dict(settings)
                settings["chrome_force_dark"] = False
            else:
                self.log("Chrome will close while the mode changes.")
                settings = dict(settings)
                settings["_close_chrome_for_flag"] = True
                settings["_reopen_chrome_after_flag"] = settings["reopen_chrome_after_flag"]
                settings["_restore_chrome_pages"] = settings["restore_chrome_pages"]

        self._set_busy_state(True, target_profile)
        self.log("")
        self.log(profile_change_status_text(target_profile))
        thread = threading.Thread(target=self._apply_in_thread, args=(target_profile, settings), daemon=True)
        thread.start()
        self.after(100, self._check_result_queue)

    def _apply_in_thread(self, target_profile: str, settings: dict):
        target_profile = normalize_profile_id(target_profile)
        target_dark = profile_is_dark(target_profile)
        results = []
        if settings["windows_theme"]:
            results.append(set_system_mode(target_dark))
        if settings["brightness"]:
            level = profile_brightness(target_profile, settings)
            results.append(set_brightness(level))
        if settings["chrome_force_dark"]:
            chrome_can_update = True
            if settings.get("_close_chrome_for_flag"):
                close_result = close_chrome_for_flag()
                results.append(close_result)
                if not close_result.ok:
                    results.append(StepResult("Chrome", False, "Chrome flag skipped because Chrome did not close."))
                    chrome_can_update = False
            if chrome_can_update:
                results.append(
                    set_chrome_force_dark(
                        target_dark,
                        bool(settings.get("_reopen_chrome_after_flag")),
                        bool(settings.get("_restore_chrome_pages", settings.get("restore_chrome_pages", True))),
                    )
                )
        if settings["flux"]:
            kelvin = profile_flux_kelvin(target_profile, settings)
            results.append(set_flux_kelvin(kelvin))
        self.result_queue.put((target_profile, results))

    def _check_result_queue(self):
        try:
            target_profile, results = self.result_queue.get_nowait()
        except queue.Empty:
            if self.running:
                self.after(100, self._check_result_queue)
            return
        self._finish_apply(target_profile, results)

    def _finish_apply(self, target_profile: str, results: list[StepResult]):
        for result in results:
            prefix = "OK" if result.ok else "WARN"
            self.log(f"{prefix} - {result.name}: {result.message}")
        if results and all(result.ok for result in results):
            self.log("Done.")
        else:
            self.log("Done with warnings.")
        self.current_profile = normalize_profile_id(target_profile)
        self._set_busy_state(False)
        if self.tray_icon is not None:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass


def self_test() -> int:
    sample = {"browser": {"enabled_labs_experiments": ["abc@1", "enable-force-dark@2"]}}
    enabled = update_chrome_state_data(json.loads(json.dumps(sample)), True)
    assert enabled["browser"]["enabled_labs_experiments"] == ["abc@1", "enable-force-dark@1"]
    disabled = update_chrome_state_data(json.loads(json.dumps(enabled)), False)
    assert disabled["browser"]["enabled_labs_experiments"] == ["abc@1"]
    assert clamp_int("200", 0, 100, 1) == 100
    assert clamp_int("bad", 0, 100, 7) == 7
    assert parse_flux_run_value(r'"C:\Users\me\AppData\Local\FluxSoftware\Flux\flux.exe" /noshow')
    assert "reopen_chrome_after_flag" in load_config()
    assert "restore_chrome_pages" in load_config()
    assert normalize_app_theme("dark") == "dark"
    assert normalize_app_theme("bad") == "white"
    assert opposite_app_theme("white") == "dark"
    assert mode_change_status_text(True) == "Changing to Dark mode. Please wait..."
    assert mode_change_button_text(False) == "Changing to White Mode..."
    assert profile_change_status_text("work_indoors") == "Changing to Work Indoors mode. Please wait..."
    assert profile_brightness("night", DEFAULT_CONFIG) == 0
    assert profile_brightness("outside", DEFAULT_CONFIG) == 100
    assert profile_flux_kelvin("work_indoors", DEFAULT_CONFIG) == 2700
    assert merge_config_data({"config_version": 5, "last_mode": "dark"})["current_profile"] == "night"
    assert "app_window_theme" in load_config()
    assert "start_with_windows" in load_config()
    assert build_startup_command([r"C:\Program Files\App\dark-white-mode.exe"], True).endswith('" --startup')
    shortcut_script = build_windows_shortcut_script(
        Path(r"C:\Users\me\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\dark-white-mode.lnk"),
        [r"C:\Program Files\App\dark-white-mode.exe"],
    )
    assert "IconLocation" in shortcut_script
    launch_agent = plistlib.loads(build_macos_launch_agent_plist(["/Applications/dark-white-mode.app/Contents/MacOS/dark-white-mode"], True))
    assert launch_agent["Label"] == macos_launch_agent_label()
    assert "--startup" in launch_agent["ProgramArguments"]
    image = create_tray_image()
    assert image is not None and image.size == (64, 64)
    print("self-test ok")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--make-icon" in sys.argv:
        index = sys.argv.index("--make-icon")
        if index + 1 >= len(sys.argv):
            print("Missing icon path after --make-icon", file=sys.stderr)
            return 2
        icon_path = save_app_icon(Path(sys.argv[index + 1]))
        print(f"Icon written: {icon_path}")
        return 0
    app = DarkWhiteModeApp(start_hidden="--startup" in sys.argv)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
