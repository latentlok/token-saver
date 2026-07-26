#!/usr/bin/env python3
"""
Spec for qd/setup.py -- the install/update hook.

Claude-authored gate (never delegate this file -- it defines what correct means).

This runs from SessionStart on every session, before the user has said anything,
so the failure modes are not "wrong answer" but "wrecked the session":

  1. Once per VERSION. Stamped => silent; a version bump un-stamps, which is the
     whole mechanism for "on install and on update, and never otherwise".
  2. Silent unless something is HIGH. Stdout from a SessionStart hook is injected
     into the context this plugin exists to conserve, so an INFO-only machine
     must cost zero tokens.
  3. Never raises, never exits non-zero -- not on a missing settings file, not on
     a corrupt one, not on an unwritable stamp dir. A plugin that breaks session
     start looks broken in a way its actual findings never would.
  4. No qwen config yet => no stamp. Mid-install is a legitimate state, and
     stamping it would burn the one chance to report on the machine that needs
     it most.

Run:  python3 specs/setup_spec.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import setup  # noqa: E402


CLEAN = {
    "model": {"name": "qwen3.6-27b"},
    "modelProviders": {"openai": [
        {"id": "qwen3.6-27b", "generationConfig": {"maxTokens": 8192}}]},
}
DIRTY = {
    "model": {"name": "qwen3.6:27b-agent"},
    "modelProviders": {"openai": [
        {"id": "qwen3.6:27b-agent", "generationConfig": {}}]},
}


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.stamps = tempfile.mkdtemp()
        setup.STAMP_DIR = self.stamps
        self.settings = os.path.join(tempfile.mkdtemp(), "settings.json")
        os.environ["QWEN_SETTINGS"] = self.settings
        from qd import doctor
        doctor.SETTINGS_PATH = self.settings

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def write(self, obj):
        with open(self.settings, "w") as f:
            if isinstance(obj, str):
                f.write(obj)
            else:
                json.dump(obj, f)


class OncePerVersion(Fixture):
    def test_first_run_stamps_and_reports(self):
        self.write(DIRTY)
        notice, ran = setup.run()
        self.assertTrue(ran)
        self.assertIn("unrecognised-model-name", notice)
        self.assertTrue(setup.already_ran(setup.plugin_version()))

    def test_second_run_is_silent_and_does_nothing(self):
        self.write(DIRTY)
        setup.run()
        notice, ran = setup.run()
        self.assertFalse(ran)
        self.assertIsNone(notice)

    def test_a_version_bump_runs_again(self):
        self.write(DIRTY)
        setup.run()
        setup.write_stamp("0.3.0", [])
        # A different version has no stamp of its own.
        self.assertFalse(setup.already_ran("9.9.9"))

    def test_force_reruns_a_stamped_version(self):
        self.write(DIRTY)
        setup.run()
        notice, ran = setup.run(force=True)
        self.assertTrue(ran)
        self.assertIsNotNone(notice)


class Silence(Fixture):
    def test_clean_machine_says_nothing_but_still_stamps(self):
        self.write(CLEAN)
        notice, ran = setup.run()
        self.assertTrue(ran)                       # so it won't re-check
        self.assertIsNone(notice)                  # ...and costs zero context

    def test_a_dotted_model_name_does_not_truncate_the_finding(self):
        # "qwen3.6:27b-agent" -- splitting the summary on "." ended the sentence
        # inside the model name and shipped "Model 'qwen3." as the whole report.
        self.write(DIRTY)
        notice, _ = setup.run()
        self.assertIn("qwen3.6:27b-agent", notice)
        self.assertIn("27b-agent'", notice)        # the normalized form too

    def test_notice_names_the_file_and_denies_a_repo_bug(self):
        self.write(DIRTY)
        notice, _ = setup.run()
        self.assertIn("~/.qwen/settings.json", notice)
        self.assertIn("not repo bugs", notice)
        self.assertIn("doctor", notice)


class NeverBreaksTheSession(Fixture):
    def test_missing_settings_is_silent_and_unstamped(self):
        notice, ran = setup.run()                  # no settings file at all
        self.assertIsNone(notice)
        self.assertFalse(ran)
        self.assertFalse(setup.already_ran(setup.plugin_version()))

    def test_corrupt_settings_does_not_raise(self):
        self.write("{not json")
        notice, ran = setup.run()
        self.assertTrue(ran)                       # degrades to "no findings"
        self.assertIsNone(notice)

    def test_unwritable_stamp_dir_is_survivable(self):
        setup.STAMP_DIR = "/proc/nonexistent/stamps"
        self.write(DIRTY)
        notice, ran = setup.run()
        self.assertTrue(ran)
        self.assertIsNotNone(notice)               # still reports, just can't stamp

    def test_main_exits_zero_on_every_path(self):
        self.assertEqual(setup.main([]), 0)
        self.write("{not json")
        self.assertEqual(setup.main([]), 0)
        self.write(DIRTY)
        self.assertEqual(setup.main(["--force"]), 0)

    def test_plugin_version_never_raises_on_a_bad_root(self):
        self.assertEqual(setup.plugin_version("/nonexistent"), "unknown")


class HookWiring(unittest.TestCase):
    """The hook file is the delivery mechanism; a typo in it means none of the
    above ever runs."""

    def test_hooks_json_registers_setup_on_session_start(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "hooks", "hooks.json")) as f:
            cfg = json.load(f)
        entries = cfg["hooks"]["SessionStart"]
        cmds = [h["command"] for e in entries for h in e["hooks"]]
        self.assertTrue(any("qd/setup.py" in c for c in cmds), cmds)
        self.assertTrue(all("${CLAUDE_PLUGIN_ROOT}" in c for c in cmds), cmds)

    def test_the_script_the_hook_names_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertTrue(os.path.isfile(os.path.join(root, "qd", "setup.py")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
