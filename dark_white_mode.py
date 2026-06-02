import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None


APP_NAME = "dark-white-mode"
CREDIT_TEXT = "Credits: Guy Houri"
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
    base = Path(os.environ.get("APPDATA", str(Path.home())))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "settings.json"


DEFAULT_CONFIG = {
    "windows_theme": True,
    "chrome_force_dark": True,
    "brightness": True,
    "flux": True,
    "prompt_before_closing_chrome": True,
    "reopen_chrome_after_flag": True,
    "dark_brightness": 1,
    "white_brightness": 70,
    "dark_flux_kelvin": 1200,
    "white_flux_kelvin": 6500,
    "last_mode": "white",
}


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    return merged


def save_config(config: dict) -> None:
    config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")


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


def run_hidden(command, timeout=12):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def is_process_running(image_name: str) -> bool:
    try:
        completed = run_hidden(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
            timeout=6,
        )
    except Exception:
        return False
    return image_name.lower() in completed.stdout.lower()


def wait_for_process_exit(image_name: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_process_running(image_name):
            return True
        time.sleep(0.4)
    return not is_process_running(image_name)


def close_chrome_for_flag() -> StepResult:
    if not is_process_running("chrome.exe"):
        return StepResult("Chrome", True, "Chrome was already closed.")

    messages = []
    try:
        run_hidden(["taskkill", "/IM", "chrome.exe", "/T"], timeout=8)
        messages.append("Asked Chrome to close.")
    except Exception as exc:
        messages.append(f"Graceful close failed: {exc}")
    if wait_for_process_exit("chrome.exe", 10):
        return StepResult("Chrome", True, "Chrome closed.")

    try:
        run_hidden(["taskkill", "/IM", "chrome.exe", "/T", "/F"], timeout=8)
        messages.append("Forced remaining Chrome processes to close.")
    except Exception as exc:
        messages.append(f"Forced close failed: {exc}")
    if wait_for_process_exit("chrome.exe", 8):
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

    if wait_for_process_exit("chrome.exe", 5):
        return StepResult("Chrome", True, "Chrome closed after fallback.")
    return StepResult("Chrome", False, "Chrome is still running. " + " ".join(messages))


def find_chrome_exe() -> Path | None:
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


def reopen_chrome() -> StepResult:
    chrome_exe = find_chrome_exe()
    if not chrome_exe:
        return StepResult("Chrome", False, "Chrome flag changed, but chrome.exe was not found to reopen it.")
    try:
        subprocess.Popen(
            [str(chrome_exe)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return StepResult("Chrome", True, "Chrome reopened.")
    except Exception as exc:
        return StepResult("Chrome", False, f"Chrome flag changed, but reopen failed: {exc}")


def create_tray_image():
    if Image is None or ImageDraw is None:
        return None

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(26, 28, 33), outline=(230, 230, 230), width=2)
    draw.pieslice((12, 12, 52, 52), 90, 270, fill=(245, 245, 245))
    draw.pieslice((12, 12, 52, 52), 270, 90, fill=(22, 22, 24))
    draw.ellipse((12, 12, 52, 52), outline=(250, 250, 250), width=2)
    return image


def chrome_local_state_path() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    path = Path(local_app_data) / "Google" / "Chrome" / "User Data" / "Local State"
    return path if path.exists() else None


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


def set_chrome_force_dark(enable: bool, reopen_after: bool = False) -> StepResult:
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
    if reopen_after:
        reopen_result = reopen_chrome()
        if reopen_result.ok:
            message += " Chrome reopened."
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


def parse_flux_run_value(value: str) -> Path | None:
    quoted = re.search(r'"([^"]*flux\.exe)"', value, flags=re.IGNORECASE)
    if quoted:
        return Path(quoted.group(1))
    match = re.search(r"([^\s]+flux\.exe)", value, flags=re.IGNORECASE)
    return Path(match.group(1)) if match else None


def find_flux_exe() -> Path | None:
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
        subprocess.Popen(
            [str(flux_exe), "/noshow"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return StepResult("f.lux", True, f"f.lux set to {kelvin}K and restarted.")
    except Exception as exc:
        return StepResult("f.lux", False, f"f.lux set to {kelvin}K, but restart failed: {exc}")


class DarkWhiteModeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("520x650")
        self.minsize(500, 590)
        self.config_data = load_config()
        self.current_mode = read_windows_mode()
        self.vars = {}
        self.running = False
        self.quitting = False
        self.tray_icon = None
        self.tray_started = False
        self.tray_notice_shown = False
        self.ui_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Unmap>", self._on_unmap)
        self._refresh_button()
        self.after(100, self._drain_ui_queue)
        self.after(500, self.start_tray_icon)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Mode.TLabel", font=("Segoe UI", 11))
        style.configure("Big.TButton", font=("Segoe UI", 14, "bold"), padding=(18, 14))

        root = ttk.Frame(self, padding=18)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)

        ttk.Label(root, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.mode_label = ttk.Label(root, text="", style="Mode.TLabel")
        self.mode_label.grid(row=1, column=0, sticky="w", pady=(4, 16))

        self.toggle_button = ttk.Button(root, text="", style="Big.TButton", command=self.toggle)
        self.toggle_button.grid(row=2, column=0, sticky="ew", pady=(0, 16))

        targets = ttk.LabelFrame(root, text="Targets", padding=12)
        targets.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        targets.columnconfigure(0, weight=1)
        for index, (key, label) in enumerate(
            [
                ("windows_theme", "Windows theme"),
                ("chrome_force_dark", "Chrome force-dark flag"),
                ("brightness", "Display brightness"),
                ("flux", "f.lux color temperature"),
            ]
        ):
            var = tk.BooleanVar(value=bool(self.config_data[key]))
            self.vars[key] = var
            ttk.Checkbutton(targets, text=label, variable=var).grid(row=index, column=0, sticky="w", pady=2)

        values = ttk.LabelFrame(root, text="Values", padding=12)
        values.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        values.columnconfigure(1, weight=1)

        self._add_spinbox(values, 0, "Dark brightness", "dark_brightness", 0, 100, "%")
        self._add_spinbox(values, 1, "White brightness", "white_brightness", 0, 100, "%")
        self._add_spinbox(values, 2, "Dark f.lux", "dark_flux_kelvin", 800, 10000, "K")
        self._add_spinbox(values, 3, "White f.lux", "white_flux_kelvin", 800, 10000, "K")

        chrome_var = tk.BooleanVar(value=bool(self.config_data["prompt_before_closing_chrome"]))
        self.vars["prompt_before_closing_chrome"] = chrome_var
        ttk.Checkbutton(
            root,
            text="Prompt before closing Chrome",
            variable=chrome_var,
        ).grid(row=5, column=0, sticky="w", pady=(0, 12))

        reopen_chrome_var = tk.BooleanVar(value=bool(self.config_data["reopen_chrome_after_flag"]))
        self.vars["reopen_chrome_after_flag"] = reopen_chrome_var
        ttk.Checkbutton(
            root,
            text="Reopen Chrome after updating flag",
            variable=reopen_chrome_var,
        ).grid(row=6, column=0, sticky="w", pady=(0, 12))

        status_frame = ttk.LabelFrame(root, text="Status", padding=8)
        status_frame.grid(row=7, column=0, sticky="nsew")
        root.rowconfigure(7, weight=1)
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)

        self.status_text = tk.Text(
            status_frame,
            height=10,
            wrap="word",
            state="disabled",
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.status_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.configure(yscrollcommand=scrollbar.set)
        ttk.Label(root, text=CREDIT_TEXT).grid(row=8, column=0, sticky="w", pady=(10, 0))
        self.log("Ready.")

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
            pystray.MenuItem("Toggle mode", self._tray_toggle, enabled=lambda _item: not self.running),
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

    def _add_spinbox(self, parent, row, label, key, minimum, maximum, suffix):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        value = tk.StringVar(value=str(self.config_data[key]))
        self.vars[key] = value
        spin = ttk.Spinbox(parent, from_=minimum, to=maximum, textvariable=value, width=8)
        spin.grid(row=row, column=1, sticky="w", padx=(12, 6), pady=4)
        ttk.Label(parent, text=suffix).grid(row=row, column=2, sticky="w", pady=4)

    def _refresh_button(self):
        mode_name = "Dark" if self.current_mode == "dark" else "White"
        target = "White" if self.current_mode == "dark" else "Dark"
        self.mode_label.configure(text=f"Current mode: {mode_name}")
        self.toggle_button.configure(text=f"Switch to {target} Mode")

    def log(self, message: str):
        self.status_text.configure(state="normal")
        self.status_text.insert("end", message + "\n")
        self.status_text.see("end")
        self.status_text.configure(state="disabled")

    def collect_settings(self) -> dict:
        settings = dict(self.config_data)
        for key in (
            "windows_theme",
            "chrome_force_dark",
            "brightness",
            "flux",
            "prompt_before_closing_chrome",
            "reopen_chrome_after_flag",
        ):
            settings[key] = bool(self.vars[key].get())
        settings["dark_brightness"] = clamp_int(self.vars["dark_brightness"].get(), 0, 100, 1)
        settings["white_brightness"] = clamp_int(self.vars["white_brightness"].get(), 0, 100, 70)
        settings["dark_flux_kelvin"] = clamp_int(self.vars["dark_flux_kelvin"].get(), 800, 10000, 1200)
        settings["white_flux_kelvin"] = clamp_int(self.vars["white_flux_kelvin"].get(), 800, 10000, 6500)
        return settings

    def toggle(self):
        if self.running:
            return
        target_dark = self.current_mode != "dark"
        settings = self.collect_settings()
        settings["last_mode"] = "dark" if target_dark else "white"
        self.config_data = settings
        save_config(settings)

        if settings["chrome_force_dark"] and is_process_running("chrome.exe"):
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
                self.log("Closing Chrome...")
                close_result = close_chrome_for_flag()
                self.log(("OK" if close_result.ok else "WARN") + f" - Chrome: {close_result.message}")
                if close_result.ok:
                    settings = dict(settings)
                    settings["_reopen_chrome_after_flag"] = settings["reopen_chrome_after_flag"]
                else:
                    self.log("Chrome flag will be skipped because Chrome did not close.")
                    settings = dict(settings)
                    settings["chrome_force_dark"] = False

        self.running = True
        self.toggle_button.configure(state="disabled")
        self.log("")
        self.log("Applying dark mode..." if target_dark else "Applying white mode...")
        thread = threading.Thread(target=self._apply_in_thread, args=(target_dark, settings), daemon=True)
        thread.start()
        self.after(100, self._check_result_queue)

    def _apply_in_thread(self, target_dark: bool, settings: dict):
        results = []
        if settings["windows_theme"]:
            results.append(set_windows_mode(target_dark))
        if settings["brightness"]:
            level = settings["dark_brightness"] if target_dark else settings["white_brightness"]
            results.append(set_brightness(level))
        if settings["chrome_force_dark"]:
            results.append(set_chrome_force_dark(target_dark, bool(settings.get("_reopen_chrome_after_flag"))))
        if settings["flux"]:
            kelvin = settings["dark_flux_kelvin"] if target_dark else settings["white_flux_kelvin"]
            results.append(set_flux_kelvin(kelvin))
        self.result_queue.put((target_dark, results))

    def _check_result_queue(self):
        try:
            target_dark, results = self.result_queue.get_nowait()
        except queue.Empty:
            if self.running:
                self.after(100, self._check_result_queue)
            return
        self._finish_apply(target_dark, results)

    def _finish_apply(self, target_dark: bool, results: list[StepResult]):
        for result in results:
            prefix = "OK" if result.ok else "WARN"
            self.log(f"{prefix} - {result.name}: {result.message}")
        if results and all(result.ok for result in results):
            self.log("Done.")
        else:
            self.log("Done with warnings.")
        self.current_mode = "dark" if target_dark else "white"
        self.running = False
        self.toggle_button.configure(state="normal")
        self._refresh_button()
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
    image = create_tray_image()
    assert image is not None and image.size == (64, 64)
    print("self-test ok")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    app = DarkWhiteModeApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
