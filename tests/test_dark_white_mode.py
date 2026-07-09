import json
import plistlib
import queue
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dark_white_mode as app


class ChromeFlagTests(unittest.TestCase):
    def test_enable_force_dark_replaces_existing_force_dark_entries(self):
        state = {"browser": {"enabled_labs_experiments": ["abc@1", "enable-force-dark@2"]}}

        updated = app.update_chrome_state_data(state, True)

        self.assertEqual(updated["browser"]["enabled_labs_experiments"], ["abc@1", "enable-force-dark@1"])

    def test_disable_force_dark_removes_only_force_dark_entries(self):
        state = {"browser": {"enabled_labs_experiments": ["abc@1", "enable-force-dark@1"]}}

        updated = app.update_chrome_state_data(state, False)

        self.assertEqual(updated["browser"]["enabled_labs_experiments"], ["abc@1"])


class ChromeRestoreTests(unittest.TestCase):
    def test_reopen_command_requests_restore_and_hides_crash_bubble(self):
        command = app.build_chrome_reopen_command(Path(r"C:\Chrome\chrome.exe"), True)

        self.assertEqual(command[0], r"C:\Chrome\chrome.exe")
        self.assertIn("--restore-last-session", command)
        self.assertIn("--hide-crash-restore-bubble", command)

    def test_preferences_data_enables_continue_where_left_off(self):
        preferences = {"session": {"restore_on_startup": 4}, "profile": {"exit_type": "Crashed"}}

        updated = app.update_chrome_preferences_data(preferences, True)

        self.assertEqual(updated["session"]["restore_on_startup"], 1)
        self.assertTrue(updated["profile"]["exited_cleanly"])
        self.assertEqual(updated["profile"]["exit_type"], "Normal")

    def test_profile_preferences_paths_skips_system_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("Default", "Profile 1", "System Profile"):
                profile = base / name
                profile.mkdir()
                (profile / "Preferences").write_text("{}", encoding="utf-8")

            paths = app.chrome_profile_preferences_paths(base)

        self.assertEqual([path.parent.name for path in paths], ["Default", "Profile 1"])

    def test_set_restore_preferences_updates_all_profiles_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            default = base / "Default"
            profile = base / "Profile 1"
            default.mkdir()
            profile.mkdir()
            (default / "Preferences").write_text(json.dumps({"session": {}}), encoding="utf-8")
            (profile / "Preferences").write_text(json.dumps({"profile": {"exit_type": "Crashed"}}), encoding="utf-8")

            result = app.set_chrome_restore_preferences(True, base)

            self.assertTrue(result.ok)
            for preferences_path in (default / "Preferences", profile / "Preferences"):
                data = json.loads(preferences_path.read_text(encoding="utf-8"))
                self.assertEqual(data["session"]["restore_on_startup"], 1)
                self.assertTrue(data["profile"]["exited_cleanly"])
                self.assertEqual(data["profile"]["exit_type"], "Normal")
                self.assertTrue((preferences_path.parent / "Preferences.dark-white-mode.bak").exists())


class ConfigTests(unittest.TestCase):
    def test_old_config_migrates_to_automatic_chrome_restore(self):
        migrated = app.merge_config_data(
            {
                "config_version": 1,
                "prompt_before_closing_chrome": True,
                "reopen_chrome_after_flag": False,
                "restore_chrome_pages": False,
            }
        )

        self.assertEqual(migrated["config_version"], app.DEFAULT_CONFIG["config_version"])
        self.assertFalse(migrated["prompt_before_closing_chrome"])
        self.assertTrue(migrated["reopen_chrome_after_flag"])
        self.assertTrue(migrated["restore_chrome_pages"])
        self.assertTrue(migrated["app_window_theme"])
        self.assertEqual(migrated["app_theme"], "white")
        self.assertEqual(migrated["current_profile"], "outside")
        self.assertTrue(migrated["start_with_windows"])
        self.assertTrue(migrated["start_minimized_to_tray"])
        self.assertFalse(migrated["software_dimming"])
        self.assertEqual(migrated["night_software_dimming"], app.DEFAULT_CONFIG["night_software_dimming"])
        self.assertEqual(migrated["outside_software_dimming"], app.DEFAULT_CONFIG["outside_software_dimming"])
        self.assertEqual(migrated["indoor_software_dimming"], app.DEFAULT_CONFIG["indoor_software_dimming"])

    def test_v6_dimming_config_migrates_to_profile_values(self):
        migrated = app.merge_config_data(
            {
                "config_version": 6,
                "last_mode": "dark",
                "dark_brightness": 3,
                "white_brightness": 88,
                "dark_software_dimming": 20,
                "white_software_dimming": 95,
                "dark_flux_kelvin": 1100,
                "white_flux_kelvin": 6200,
            }
        )

        self.assertEqual(migrated["current_profile"], "night")
        self.assertEqual(migrated["night_brightness"], 3)
        self.assertEqual(migrated["outside_brightness"], 88)
        self.assertEqual(migrated["night_software_dimming"], 20)
        self.assertEqual(migrated["outside_software_dimming"], 95)
        self.assertEqual(migrated["night_flux_kelvin"], 1100)
        self.assertEqual(migrated["outside_flux_kelvin"], 6200)

    def test_old_dark_config_migrates_to_night_profile(self):
        migrated = app.merge_config_data({"config_version": 5, "last_mode": "dark"})

        self.assertEqual(migrated["current_profile"], "night")
        self.assertEqual(migrated["app_theme"], "dark")

    def test_migration_preserves_manual_app_theme_when_profile_theming_is_disabled(self):
        migrated = app.merge_config_data(
            {
                "config_version": 5,
                "last_mode": "dark",
                "app_window_theme": False,
                "app_theme": "white",
            }
        )

        self.assertEqual(migrated["current_profile"], "night")
        self.assertFalse(migrated["app_window_theme"])
        self.assertEqual(migrated["app_theme"], "white")

    def test_app_theme_is_normalized(self):
        migrated = app.merge_config_data({"config_version": app.DEFAULT_CONFIG["config_version"], "app_theme": "purple"})

        self.assertEqual(migrated["app_theme"], "white")

    def test_profile_id_is_normalized(self):
        migrated = app.merge_config_data({"config_version": app.DEFAULT_CONFIG["config_version"], "current_profile": "indoors"})

        self.assertEqual(migrated["current_profile"], "work_indoors")

    def test_opposite_app_theme(self):
        self.assertEqual(app.opposite_app_theme("white"), "dark")
        self.assertEqual(app.opposite_app_theme("dark"), "white")


class ApplyWorkflowTests(unittest.TestCase):
    def test_failed_chrome_close_skips_chrome_flag_but_continues_remaining_steps(self):
        runner = type("Runner", (), {"result_queue": queue.Queue()})()
        settings = {
            "windows_theme": False,
            "brightness": False,
            "software_dimming": False,
            "chrome_force_dark": True,
            "flux": True,
            "_close_chrome_for_flag": True,
            "_reopen_chrome_after_flag": True,
            "_restore_chrome_pages": True,
            "night_flux_kelvin": 1200,
            "outside_flux_kelvin": 6500,
            "indoor_flux_kelvin": 2700,
        }

        with (
            mock.patch.object(
                app,
                "close_chrome_for_flag",
                return_value=app.StepResult("Chrome", False, "Chrome is still running."),
            ),
            mock.patch.object(app, "set_chrome_force_dark") as set_chrome_force_dark,
            mock.patch.object(
                app,
                "set_flux_kelvin",
                return_value=app.StepResult("f.lux", True, "f.lux updated."),
            ) as set_flux_kelvin,
        ):
            app.DarkWhiteModeApp._apply_in_thread(runner, "night", settings)

        target_profile, results = runner.result_queue.get_nowait()
        self.assertEqual(target_profile, "night")
        set_chrome_force_dark.assert_not_called()
        set_flux_kelvin.assert_called_once_with(1200)
        self.assertEqual([result.name for result in results], ["Chrome", "Chrome", "f.lux"])

    def test_software_dimming_is_applied_after_flux(self):
        runner = type("Runner", (), {"result_queue": queue.Queue()})()
        settings = {
            "windows_theme": False,
            "brightness": False,
            "software_dimming": True,
            "chrome_force_dark": False,
            "flux": True,
            "night_software_dimming": 20,
            "outside_software_dimming": 100,
            "indoor_software_dimming": 50,
            "night_flux_kelvin": 1200,
            "outside_flux_kelvin": 6500,
            "indoor_flux_kelvin": 2700,
        }
        calls = []

        def fake_flux(kelvin):
            calls.append(("flux", kelvin))
            return app.StepResult("f.lux", True, "f.lux updated.")

        def fake_dimming(percent):
            calls.append(("software", percent))
            return app.StepResult("Software dimming", True, "Software dimming updated.")

        with (
            mock.patch.object(app.software_gamma_dimmer, "is_active", return_value=False),
            mock.patch.object(app, "set_flux_kelvin", side_effect=fake_flux),
            mock.patch.object(app, "set_software_dimming", side_effect=fake_dimming),
        ):
            app.DarkWhiteModeApp._apply_in_thread(runner, "night", settings)

        target_profile, results = runner.result_queue.get_nowait()
        self.assertEqual(target_profile, "night")
        self.assertEqual(calls, [("flux", 1200), ("software", 20)])
        self.assertEqual([result.name for result in results], ["f.lux", "Software dimming"])

    def test_software_dimming_skips_hardware_brightness(self):
        runner = type("Runner", (), {"result_queue": queue.Queue()})()
        settings = {
            "windows_theme": False,
            "brightness": True,
            "software_dimming": True,
            "chrome_force_dark": False,
            "flux": False,
            "night_brightness": 1,
            "outside_brightness": 70,
            "indoor_brightness": 50,
            "night_software_dimming": 20,
            "outside_software_dimming": 100,
            "indoor_software_dimming": 50,
        }

        with (
            mock.patch.object(app.software_gamma_dimmer, "is_active", return_value=False),
            mock.patch.object(app, "set_brightness") as set_brightness,
            mock.patch.object(
                app,
                "set_software_dimming",
                return_value=app.StepResult("Software dimming", True, "Software dimming updated."),
            ),
        ):
            app.DarkWhiteModeApp._apply_in_thread(runner, "night", settings)

        _target_profile, results = runner.result_queue.get_nowait()
        set_brightness.assert_not_called()
        self.assertEqual([result.name for result in results], ["Brightness", "Software dimming"])
        self.assertIn("Hardware brightness skipped", results[0].message)

    def test_active_software_dimming_is_restored_before_mode_changes(self):
        runner = type("Runner", (), {"result_queue": queue.Queue()})()
        settings = {
            "windows_theme": False,
            "brightness": False,
            "software_dimming": True,
            "chrome_force_dark": False,
            "flux": True,
            "night_software_dimming": 20,
            "outside_software_dimming": 100,
            "indoor_software_dimming": 50,
            "night_flux_kelvin": 1200,
            "outside_flux_kelvin": 6500,
            "indoor_flux_kelvin": 2700,
        }
        calls = []

        def fake_restore():
            calls.append(("restore", None))
            return app.StepResult("Software dimming", True, "Software dimming restored.")

        def fake_flux(kelvin):
            calls.append(("flux", kelvin))
            return app.StepResult("f.lux", True, "f.lux updated.")

        def fake_dimming(percent):
            calls.append(("software", percent))
            return app.StepResult("Software dimming", True, "Software dimming updated.")

        with (
            mock.patch.object(app.software_gamma_dimmer, "is_active", return_value=True),
            mock.patch.object(app, "restore_software_dimming", side_effect=fake_restore),
            mock.patch.object(app, "set_flux_kelvin", side_effect=fake_flux),
            mock.patch.object(app, "set_software_dimming", side_effect=fake_dimming),
        ):
            app.DarkWhiteModeApp._apply_in_thread(runner, "outside", settings)

        target_profile, results = runner.result_queue.get_nowait()
        self.assertEqual(target_profile, "outside")
        self.assertEqual(calls, [("restore", None), ("flux", 6500), ("software", 100)])
        self.assertEqual([result.name for result in results], ["Software dimming", "f.lux", "Software dimming"])


class MiscTests(unittest.TestCase):
    def test_mode_change_loading_text_names_target_mode(self):
        self.assertEqual(app.mode_change_status_text(True), "Changing to Dark mode. Please wait...")
        self.assertEqual(app.mode_change_status_text(False), "Changing to White mode. Please wait...")
        self.assertEqual(app.mode_change_button_text(True), "Changing to Dark Mode...")
        self.assertEqual(app.mode_change_button_text(False), "Changing to White Mode...")
        self.assertEqual(app.profile_change_status_text("work_indoors"), "Changing to Work Indoors mode. Please wait...")
        self.assertEqual(app.profile_change_button_text("outside"), "Changing to Outside...")

    def test_profile_defaults(self):
        settings = dict(app.DEFAULT_CONFIG)

        self.assertEqual(app.profile_brightness("night", settings), 0)
        self.assertEqual(app.profile_flux_kelvin("night", settings), 1200)
        self.assertEqual(app.profile_software_dimming("night", settings), 25)
        self.assertEqual(app.profile_brightness("outside", settings), 100)
        self.assertEqual(app.profile_flux_kelvin("outside", settings), 6500)
        self.assertEqual(app.profile_software_dimming("outside", settings), 100)
        self.assertEqual(app.profile_brightness("work_indoors", settings), 50)
        self.assertEqual(app.profile_flux_kelvin("work_indoors", settings), 2700)
        self.assertEqual(app.profile_software_dimming("work_indoors", settings), 50)
        self.assertTrue(app.profile_is_dark("work_indoors"))
        self.assertEqual(app.next_profile_id("night"), "outside")
        self.assertEqual(app.next_profile_id("outside"), "work_indoors")
        self.assertEqual(app.next_profile_id("work_indoors"), "night")

    def test_clamp_int(self):
        self.assertEqual(app.clamp_int("200", 0, 100, 1), 100)
        self.assertEqual(app.clamp_int("bad", 0, 100, 7), 7)

    def test_normalize_software_dimming_percent(self):
        self.assertEqual(app.normalize_software_dimming_percent("2"), 5)
        self.assertEqual(app.normalize_software_dimming_percent("40"), 40)
        self.assertEqual(app.normalize_software_dimming_percent("200"), 100)
        self.assertEqual(app.normalize_software_dimming_percent("bad"), 100)

    def test_build_dimmed_gamma_ramp_values(self):
        values = [0, 1000, 65535]

        self.assertEqual(app.build_dimmed_gamma_ramp_values(values, 25), [0, 250, 16384])

    def test_build_dimmed_gamma_table_values(self):
        values = [0.0, 0.5, 1.0]

        self.assertEqual(app.build_dimmed_gamma_table_values(values, 25), [0.0, 0.125, 0.25])

    def test_flux_run_value_parses_quoted_path(self):
        parsed = app.parse_flux_run_value(r'"C:\Users\me\AppData\Local\FluxSoftware\Flux\flux.exe" /noshow')

        self.assertEqual(str(parsed), r"C:\Users\me\AppData\Local\FluxSoftware\Flux\flux.exe")

    def test_startup_command_quotes_path_and_uses_startup_arg(self):
        command = app.build_startup_command([r"C:\Program Files\dark-white-mode\dark-white-mode.exe"], True)

        self.assertEqual(command, r'"C:\Program Files\dark-white-mode\dark-white-mode.exe" --startup')

    def test_startup_command_can_launch_visible(self):
        command = app.build_startup_command([r"C:\Program Files\dark-white-mode\dark-white-mode.exe"], False)

        self.assertEqual(command, r'"C:\Program Files\dark-white-mode\dark-white-mode.exe"')

    def test_start_menu_shortcut_script_uses_exe_icon(self):
        script = app.build_windows_shortcut_script(
            Path(r"C:\Users\me\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\dark-white-mode.lnk"),
            [r"C:\Program Files\dark-white-mode\dark-white-mode.exe"],
        )

        self.assertIn("dark-white-mode.exe,0", script)
        self.assertIn("IconLocation", script)

    def test_app_icon_can_be_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            icon_path = app.save_app_icon(Path(tmp) / "dark-white-mode.ico")

            self.assertTrue(icon_path.exists())
            self.assertGreater(icon_path.stat().st_size, 0)

    def test_macos_launch_agent_arguments_include_startup(self):
        arguments = app.macos_launch_agent_program_arguments(["/Applications/dark-white-mode.app/Contents/MacOS/dark-white-mode"], True)

        self.assertEqual(arguments[-1], "--startup")

    def test_macos_launch_agent_plist_is_valid(self):
        payload = app.build_macos_launch_agent_plist(["/Applications/dark-white-mode.app/Contents/MacOS/dark-white-mode"], True)
        decoded = plistlib.loads(payload)

        self.assertEqual(decoded["Label"], "com.guyhouri.dark-white-mode")
        self.assertTrue(decoded["RunAtLoad"])
        self.assertIn("--startup", decoded["ProgramArguments"])


if __name__ == "__main__":
    unittest.main()
