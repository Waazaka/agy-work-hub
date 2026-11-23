#!/usr/bin/env python3
"""Zero-dependency subprocess unit tests for configure_models.py."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLI_PATH = os.path.join(SCRIPT_DIR, "configure_models.py")
DEFAULT_MODELS_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "models.json")


def run_cli(
    args: list[str], env_override: Optional[dict[str, str]] = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, CLI_PATH, *args],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


class ConfigureModelsCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name
        self.cfg_path = os.path.join(self.tmp_dir, "models.json")
        shutil.copyfile(DEFAULT_MODELS_PATH, self.cfg_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_01_default_models_json_validate_show_and_list_presets(self):
        res_val = run_cli(["--config", self.cfg_path, "--validate", "--json"])
        self.assertEqual(res_val.returncode, 0, res_val.stderr)
        val_data = json.loads(res_val.stdout)
        self.assertTrue(val_data["valid"])
        self.assertEqual(val_data["preset"], "balanced")

        res_show = run_cli(["--config", self.cfg_path, "--show", "--json"])
        self.assertEqual(res_show.returncode, 0, res_show.stderr)
        show_data = json.loads(res_show.stdout)
        self.assertEqual(show_data["preset"], "balanced")
        self.assertEqual(show_data["roles"]["coordinator"], "gemini-4-argon-medium")
        self.assertEqual(show_data["roles"]["planner"], "gemini-4-carbon-high")
        self.assertEqual(show_data["roles"]["research"], "inherit")
        self.assertEqual(len(show_data["roles"]), 8)

        # Default action with no flags behaves like --show
        res_default = run_cli(["--config", self.cfg_path])
        self.assertEqual(res_default.returncode, 0, res_default.stderr)
        self.assertIn("Active Preset: balanced", res_default.stdout)

        res_presets = run_cli(["--config", self.cfg_path, "--list-presets", "--json"])
        self.assertEqual(res_presets.returncode, 0, res_presets.stderr)
        presets_data = json.loads(res_presets.stdout)
        self.assertEqual(
            set(presets_data["presets"].keys()),
            {"balanced", "economy", "max-quality", "inherit-all"},
        )

    def test_02_switch_presets_and_inherit_all(self):
        res_eco = run_cli(["--config", self.cfg_path, "--preset", "economy", "--json"])
        self.assertEqual(res_eco.returncode, 0, res_eco.stderr)
        eco_data = json.loads(res_eco.stdout)
        self.assertTrue(eco_data["updated"])
        self.assertEqual(eco_data["preset"], "economy")
        self.assertEqual(eco_data["roles"]["coordinator"], "inherit")
        self.assertEqual(eco_data["roles"]["impl_g2_g3"], "gemini-4-hydrogen-high")

        # Persisted on disk
        with open(self.cfg_path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["preset"], "economy")

        res_inh = run_cli(["--config", self.cfg_path, "--preset", "inherit-all", "--json"])
        self.assertEqual(res_inh.returncode, 0, res_inh.stderr)
        inh_data = json.loads(res_inh.stdout)
        self.assertEqual(set(inh_data["roles"].values()), {"inherit"})

    def test_03_set_roles_marks_custom_and_supports_multiple_flags(self):
        res_set = run_cli(
            [
                "--config",
                self.cfg_path,
                "--set",
                "planner=custom-architect-v1",
                "--set",
                "impl_g1=inherit",
                "--json",
            ]
        )
        self.assertEqual(res_set.returncode, 0, res_set.stderr)
        data = json.loads(res_set.stdout)
        self.assertEqual(data["preset"], "custom")
        self.assertEqual(data["roles"]["planner"], "custom-architect-v1")
        self.assertEqual(data["roles"]["impl_g1"], "inherit")

        # Unchanged --set on a matching preset keeps preset name
        run_cli(["--config", self.cfg_path, "--preset", "balanced"])
        res_noop = run_cli(
            ["--config", self.cfg_path, "--set", "planner=gemini-4-carbon-high", "--json"]
        )
        self.assertEqual(res_noop.returncode, 0, res_noop.stderr)
        self.assertEqual(json.loads(res_noop.stdout)["preset"], "balanced")

    def test_04_block_and_unblock_and_conflict_rejection(self):
        res_blk = run_cli(["--config", self.cfg_path, "--block", "gemini-3.8-pro", "--json"])
        self.assertEqual(res_blk.returncode, 0, res_blk.stderr)
        self.assertIn("gemini-3.8-pro", json.loads(res_blk.stdout)["blocked_models"])

        res_unblk = run_cli(["--config", self.cfg_path, "--unblock", "gemini-3.8-pro", "--json"])
        self.assertEqual(res_unblk.returncode, 0, res_unblk.stderr)
        self.assertNotIn("gemini-3.8-pro", json.loads(res_unblk.stdout)["blocked_models"])

        # Setting a blocked model (wildcard or exact) is rejected cleanly
        for bad_model in ("gemini-3.8-flash", "flash", "flash_lite"):
            res_bad = run_cli(["--config", self.cfg_path, "--set", f"impl_g1={bad_model}"])
            self.assertNotEqual(res_bad.returncode, 0)
            self.assertIn("Error:", res_bad.stderr)
            self.assertNotIn("Traceback", res_bad.stderr)

        # Blocking an actively used model is rejected cleanly
        res_conflict = run_cli(["--config", self.cfg_path, "--block", "gemini-4-carbon-high"])
        self.assertNotEqual(res_conflict.returncode, 0)
        self.assertIn("Error:", res_conflict.stderr)
        self.assertNotIn("Traceback", res_conflict.stderr)

        # Blocking 'inherit' or '*' is rejected cleanly
        for bad_pat in ("inherit", "*"):
            res_inh = run_cli(["--config", self.cfg_path, "--block", bad_pat])
            self.assertNotEqual(res_inh.returncode, 0)
            self.assertIn("Error:", res_inh.stderr)
            self.assertNotIn("Traceback", res_inh.stderr)

        # Switching to a preset whose role hits blocked_models is rejected
        run_cli(["--config", self.cfg_path, "--preset", "inherit-all"])
        res_blk_argon = run_cli(["--config", self.cfg_path, "--block", "gemini-4-argon-*"])
        self.assertEqual(res_blk_argon.returncode, 0, res_blk_argon.stderr)
        res_switch = run_cli(["--config", self.cfg_path, "--preset", "balanced"])
        self.assertNotEqual(res_switch.returncode, 0)
        self.assertIn("Error:", res_switch.stderr)
        self.assertNotIn("Traceback", res_switch.stderr)

    def test_05_env_var_config_path_and_bad_inputs_no_traceback(self):
        # WORK_HUB_MODELS_FILE env var works when --config omitted
        res_env = run_cli(
            ["--validate", "--json"],
            env_override={"WORK_HUB_MODELS_FILE": self.cfg_path},
        )
        self.assertEqual(res_env.returncode, 0, res_env.stderr)
        self.assertEqual(json.loads(res_env.stdout)["config_path"], os.path.abspath(self.cfg_path))

        # Unknown role, malformed --set, unknown preset, missing config, corrupt JSON, invalid schema
        bad_cases: list[list[str]] = [
            ["--config", self.cfg_path, "--set", "unknown_role=gpt-x"],
            ["--config", self.cfg_path, "--set", "planner_no_equals"],
            ["--config", self.cfg_path, "--set", "planner="],
            ["--config", self.cfg_path, "--set", "planner=bad model"],
            ["--config", self.cfg_path, "--preset", "nonexistent-preset"],
            ["--config", os.path.join(self.tmp_dir, "missing.json"), "--show"],
            ["--config", "", "--show"],
        ]
        for argv in bad_cases:
            res = run_cli(argv)
            self.assertNotEqual(res.returncode, 0, f"Expected failure for {argv}")
            self.assertIn("Error:", res.stderr)
            self.assertNotIn("Traceback", res.stderr)

        # Corrupt JSON
        corrupt_path = os.path.join(self.tmp_dir, "corrupt.json")
        with open(corrupt_path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        res_corrupt = run_cli(["--config", corrupt_path, "--validate"])
        self.assertNotEqual(res_corrupt.returncode, 0)
        self.assertIn("Error:", res_corrupt.stderr)
        self.assertNotIn("Traceback", res_corrupt.stderr)

        # Missing required role in JSON
        with open(self.cfg_path, "r", encoding="utf-8") as fh:
            broken = json.load(fh)
        del broken["roles"]["monitor"]
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            json.dump(broken, fh)
        res_missing_role = run_cli(["--config", self.cfg_path, "--validate"])
        self.assertNotEqual(res_missing_role.returncode, 0)
        self.assertIn("monitor", res_missing_role.stderr)
        self.assertNotIn("Traceback", res_missing_role.stderr)


if __name__ == "__main__":
    unittest.main()
