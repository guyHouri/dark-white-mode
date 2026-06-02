import json
import tempfile
import unittest
from pathlib import Path

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


class MiscTests(unittest.TestCase):
    def test_clamp_int(self):
        self.assertEqual(app.clamp_int("200", 0, 100, 1), 100)
        self.assertEqual(app.clamp_int("bad", 0, 100, 7), 7)

    def test_flux_run_value_parses_quoted_path(self):
        parsed = app.parse_flux_run_value(r'"C:\Users\me\AppData\Local\FluxSoftware\Flux\flux.exe" /noshow')

        self.assertEqual(str(parsed), r"C:\Users\me\AppData\Local\FluxSoftware\Flux\flux.exe")


if __name__ == "__main__":
    unittest.main()
