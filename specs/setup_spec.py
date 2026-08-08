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

import inspect
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import schemas, server, setup  # noqa: E402


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


class ManagedBlock(Fixture):
    """U5.3: the CLAUDE.md block is a MANAGED block -- it is how agents learn
    what this plugin can do, and a capability that never reaches it goes
    undiscovered. So it is rewritten from the template once per version.

    Every claim below is about somebody else's file, which is why they are all
    about restraint: no file or no markers is a no-op (installing the block is
    the user's choice), the bytes outside the markers are preserved exactly,
    and identical content is not written at all.
    """

    def setUp(self):
        super().setUp()
        self.proj = tempfile.mkdtemp()
        self.path = os.path.join(self.proj, "CLAUDE.md")

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def read(self):
        with open(self.path) as f:
            return f.read()

    def installed(self, version="0.0.1"):
        """A CLAUDE.md with the user's own content around an OLD block."""
        block = setup.template_block(version)
        self.write(f"# My project\n\nMy own notes.\n\n{block}\n\nTrailing.\n")
        return block

    def test_the_template_still_carries_both_markers(self):
        block = setup.template_block("9.9.9")
        self.assertIsNotNone(block)
        self.assertIn("supervised-delegation:begin", block.splitlines()[0])
        self.assertIn("supervised-delegation:end", block.splitlines()[-1])

    def test_the_block_is_version_stamped(self):
        self.assertIn("<!-- v: 9.9.9 -->", setup.template_block("9.9.9"))
        self.assertNotIn("{version}", setup.template_block("9.9.9"))

    def test_a_version_bump_rewrites_the_installed_block(self):
        old = self.installed("0.0.1")
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "updated")
        self.assertIn("<!-- v: 9.9.9 -->", self.read())
        self.assertNotIn("<!-- v: 0.0.1 -->", self.read())
        self.assertNotEqual(old, setup.template_block("9.9.9"))

    def test_running_twice_is_byte_identical_and_leaves_one_block(self):
        self.installed("0.0.1")
        setup.update_managed_block(self.proj, version="9.9.9")
        first = self.read()
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "current")
        self.assertEqual(self.read(), first)
        self.assertEqual(first.count("supervised-delegation:begin"), 1)
        self.assertEqual(first.count("supervised-delegation:end"), 1)

    def test_an_unchanged_version_does_not_touch_the_file(self):
        self.installed("9.9.9")
        before = os.stat(self.path).st_mtime_ns
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "current")
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)

    def test_the_users_own_content_is_preserved_byte_for_byte(self):
        self.installed("0.0.1")
        setup.update_managed_block(self.proj, version="9.9.9")
        text = self.read()
        self.assertTrue(text.startswith("# My project\n\nMy own notes.\n\n"))
        self.assertTrue(text.endswith("\n\nTrailing.\n"))

    def test_a_file_without_the_markers_is_left_alone(self):
        # Not installing the block is a decision; installing one uninvited is
        # not this hook's to make.
        self.write("# Just my notes\n")
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "absent")
        self.assertEqual(self.read(), "# Just my notes\n")

    def test_a_half_marked_file_is_left_alone(self):
        self.write("# Notes\n<!-- supervised-delegation:begin -->\nhalf a block\n")
        before = self.read()
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "absent")
        self.assertEqual(self.read(), before)

    def test_no_claude_md_at_all_is_a_no_op(self):
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "absent")
        self.assertFalse(os.path.exists(self.path))

    def test_a_block_at_the_very_end_without_a_newline_still_updates(self):
        self.write("# Notes\n\n" + setup.template_block("0.0.1"))
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "updated")
        self.assertTrue(self.read().startswith("# Notes\n\n"))
        self.assertIn("<!-- v: 9.9.9 -->", self.read())

    def test_an_unwritable_project_is_survivable(self):
        # Rule 3 of this hook: it never raises. A session that cannot start
        # because a directory is read-only is a far worse bug than a stale
        # capability list.
        self.installed("0.0.1")
        os.chmod(self.proj, 0o555)
        self.addCleanup(os.chmod, self.proj, 0o755)
        self.assertEqual(
            setup.update_managed_block(self.proj, version="9.9.9"), "absent")
        self.assertIn("<!-- v: 0.0.1 -->", self.read())

    def test_the_run_hook_refreshes_the_block_once_per_version(self):
        self.installed("0.0.1")
        cwd = os.getcwd()
        os.chdir(self.proj)
        self.addCleanup(os.chdir, cwd)
        self.write(self.read())          # same content, new mtime baseline
        setup.run()
        self.assertIn(f"<!-- v: {setup.plugin_version()} -->", self.read())

    def test_every_capability_is_discoverable_without_long_form_docs(self):
        # U2.7 RESTATED for v0.6. The rule is unchanged -- a capability must
        # never live only in long-form docs -- but the surface that satisfies
        # it is the TOOL SCHEMA, which is in context whenever the tools are,
        # and the submit response, which the caller reads on every run.
        #
        # It used to be this block, which is resident in EVERY session whether
        # or not that session delegates. Cataloguing capabilities in both
        # places cost ~520 resident tokens to repeat what the schema already
        # said, and then the `delegation` skill loaded and said it a third
        # time. Discovery does not require residency.
        surface = (json.dumps(schemas.TOOL) + json.dumps(schemas.QUERY_TOOL)
                   + inspect.getsource(server._submit_text))
        for capability in ("wait", "retry_of", "result_schema",
                           "brief_file", "touch_scope", "WATCH"):
            self.assertIn(capability, surface)

    def test_result_schema_description_says_an_out_of_subset_keyword_refuses(self):
        # House pattern: a refusal is documented in the FIELD's own
        # description, not left for the caller to discover by triggering it
        # -- see trust :102, verify_timeout_sec :143, preflight_expect :147.
        # result_schema now refuses a keyword qd.jsonschema.schema_refusal()
        # cannot enforce (U5.1 accept-time check), on BOTH tools -- query
        # never enters engine.py, so its own field needs its own sentence,
        # not a cross-reference the caller cannot see from the schema alone.
        for tool in (schemas.TOOL, schemas.QUERY_TOOL):
            desc = tool["inputSchema"]["properties"]["result_schema"]["description"]
            self.assertIn("refus", desc.lower())

    def test_the_block_carries_the_trigger_and_the_pre_skill_rules(self):
        # What the block is FOR, now that it is not a catalogue: fire before
        # Claude has decided to look at the tools, name the skill that has
        # the rest, and carry the two rules that must hold BEFORE that skill
        # loads (an uncommitted tree has no rollback; a hand-re-run green gate
        # spends the context this plugin exists to save).
        block = setup.template_block("9.9.9")
        for required in ("delegate", "query", "delegation",
                         "Commit first", "STATUS"):
            self.assertIn(required, block)

    def test_the_block_stays_small(self):
        # A budget on something resident in every session. The block reached
        # ~520 tokens by absorbing the skill one useful line at a time, and
        # nothing failed while it did. This fails.
        block = setup.template_block("9.9.9")
        self.assertLess(
            len(block), 1200,
            f"managed block is {len(block)} chars (~{len(block)//4} tokens) "
            f"and resident in every session -- if a new capability needs "
            f"explaining, the skill or the tool schema is where it goes")


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
