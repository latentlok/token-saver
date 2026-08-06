#!/usr/bin/env python3
"""
Spec for qd/bootstrap.py -- self-configuration (LLD "qd/bootstrap.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

Port gate, crane-equality style: for identical inputs qd.bootstrap must agree
with the running server byte-for-byte (both read the SAME templates/QWEN.md, so
the v2 rule additions -- refs pinning, the *_qwen.* self-test convention -- are
made in the TEMPLATE, upgrading both sides identically). Plus the v2 seam: the
rules FILENAME is profile-driven (C1 rules_file), defaulting to QWEN.md.

Load-bearing:
  1. detect_test_cmd's table must match the crane exactly -- a wrong detected
     command becomes a wrong standing rule in every future session.
  1b. ...and the command it returns must actually RUN, and must actually
     COLLECT (class DetectedCommandActuallyRuns). Asserting the TEXT of a
     command-builder and never executing the command is how the last-resort
     branch shipped a gate command that crashes on this repo's own layout.
  2. render_worker_rules must never emit a placeholder ("TEMPLATE" banner or
     the un-substituted testing line) -- measured rule: never write a
     placeholder.
  3. The template must now carry the refs rule (.qwen-delegate/refs/) and the
     self-test convention (*_qwen.*) -- the v2 additions land in the template.
  4. bootstrap_worker_rules is atomic, backs up pre-existing files, returns
     (test_cmd, dest) or (None, None), never raises.
  5. rules_file="AGENTS.md" must change the DESTINATION name only, not the
     content.

Public surface pinned here:
    detect_test_cmd(cwd), render_worker_rules(test_cmd),
    bootstrap_worker_rules(cwd, rules_file="QWEN.md"),
    worker_rules_path(cwd, rules_file="QWEN.md"),
    worker_rules_status(cwd, rules_file="QWEN.md"),
    bootstrap_notice(test_cmd, path), unconfigured_notice(cwd, state, path),
    unconfigured_reason(cwd, state, path), nongit_refusal(cwd),
    bootstrap_failed_refusal(cwd, reason)

Run:  python3 specs/bootstrap_spec.py
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import bootstrap  # noqa: E402


def mkrepo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", d], check=True)
    return d


def put(d, rel, content=""):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(rel) else None
    with open(p, "w") as f:
        f.write(content)
    return p


def spec_src(tag, passing=True):
    """A test file that PRINTS when it runs.

    The sentinel is what makes a green exit code mean something: a discovery
    that matches NO file also exits 0 ("Ran 0 tests ... OK"), so returncode
    alone cannot tell a suite that passed from a suite that was never
    collected -- the vacuous-pass class this repo exists to catch.
    """
    body = "self.assertTrue(True)" if passing else "self.fail('deliberate')"
    return ("import unittest\n"
            "class Case(unittest.TestCase):\n"
            "    def test_case(self):\n"
            f"        print('RAN:{tag}')\n"
            f"        {body}\n")


class DetectTable(unittest.TestCase):
    """Case 1: detection returns the expected command for each marker file."""

    def check(self, setup, expected):
        d = tempfile.mkdtemp()
        setup(d)
        self.assertEqual(bootstrap.detect_test_cmd(d), expected)

    def test_npm(self):
        self.check(lambda d: put(d, "package.json",
                                 '{"scripts": {"test": "jest"}}'), "npm test")

    def test_npm_without_test_script_falls_through(self):
        self.check(lambda d: put(d, "package.json", '{"name": "x"}'), "")

    def test_cargo(self):
        self.check(lambda d: put(d, "Cargo.toml", "[package]"), "cargo test")

    def test_go(self):
        self.check(lambda d: put(d, "go.mod", "module x"), "go test ./...")

    def test_gemfile(self):
        self.check(lambda d: put(d, "Gemfile", ""), "bundle exec rspec")

    def test_venv_pytest_needs_exec_bit(self):
        def setup(d):
            p = put(d, "venv/bin/pytest", "#!/bin/sh\n")
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        self.check(setup,
                   'venv/bin/pytest -q -o "python_files=test_*.py *_test.py *_spec.py"')

    def test_pyproject(self):
        self.check(lambda d: put(d, "pyproject.toml", ""),
                   'python -m pytest -q -o "python_files=test_*.py *_test.py *_spec.py"')

    def test_nothing(self):
        self.check(lambda d: None, "")


class RenderRules(unittest.TestCase):
    def test_detected_command_reaches_the_rules_file(self):
        # Was a crane-equality test against the frozen v1. The contract that
        # actually matters: whatever was detected is what the worker is told.
        self.assertIn("npm test", bootstrap.render_worker_rules("npm test"))

    def test_no_placeholder_ever(self):
        for cmd in ("cargo test", ""):
            s = bootstrap.render_worker_rules(cmd)
            first_para = s.split("\n\n")[0]
            self.assertNotIn("TEMPLATE", first_para)
            self.assertNotIn("<-- EDIT", s)

    def test_v2_template_additions_present(self):
        # Case 3: the refs rule and the self-test convention live in the
        # template now, so every rendered rules file carries them.
        s = bootstrap.render_worker_rules("npm test")
        self.assertIn(".qwen-delegate/refs/", s)
        self.assertIn("_qwen.", s)

    def test_no_test_cmd_is_an_instruction_not_a_blank(self):
        s = bootstrap.render_worker_rules("")
        self.assertIn("Do NOT guess", s)


class Bootstrap(unittest.TestCase):
    def test_creates_rules_file_and_returns_cmd_dest(self):
        d = mkrepo()
        put(d, "go.mod", "module x")
        cmd, dest = bootstrap.bootstrap_worker_rules(d)
        self.assertEqual(cmd, "go test ./...")
        self.assertEqual(os.path.basename(dest), "QWEN.md")
        with open(dest) as f:
            body = f.read()
        self.assertIn("go test ./...", body)
        self.assertNotIn(".tmp", os.listdir(d))  # atomic: no leftovers

    def test_backs_up_existing(self):
        d = mkrepo()
        put(d, "QWEN.md", "old contents")
        cmd, dest = bootstrap.bootstrap_worker_rules(d)
        with open(dest + ".bak") as f:
            self.assertEqual(f.read(), "old contents")

    def test_rules_file_param_changes_destination_only(self):
        d = mkrepo()
        cmd, dest = bootstrap.bootstrap_worker_rules(d, rules_file="AGENTS.md")
        self.assertEqual(os.path.basename(dest), "AGENTS.md")
        with open(dest) as f:
            agents = f.read()
        d2 = mkrepo()
        _, dest2 = bootstrap.bootstrap_worker_rules(d2)
        with open(dest2) as f:
            qwen = f.read()
        self.assertEqual(agents, qwen)  # same content, different name

    def test_never_raises_on_unwritable(self):
        d = mkrepo()
        os.chmod(d, 0o555)
        try:
            cmd, dest = bootstrap.bootstrap_worker_rules(d)
        finally:
            os.chmod(d, 0o755)
        self.assertEqual((cmd, dest), (None, None))


class StatusAndNotices(unittest.TestCase):
    """Was crane equality against the frozen v1. Pinned to the contracts the
    engine and the receipts actually read: the STATE, and the load-bearing
    facts each notice must carry. Deliberately not byte-equality on prose --
    a wording change is not a regression, a missing path is."""

    def test_status_across_states(self):
        # missing / ok / subdirectory-of-configured
        d = mkrepo()
        self.assertEqual(bootstrap.worker_rules_status(d), ("missing", None))
        bootstrap.bootstrap_worker_rules(d)
        state, path = bootstrap.worker_rules_status(d)
        self.assertEqual(state, "ok")
        self.assertEqual(os.path.basename(path), "QWEN.md")
        # A deep subdirectory walks up to the SAME configured file: the worker
        # is governed by the repo's rules wherever in the tree it is invoked.
        sub = os.path.join(d, "src", "deep")
        os.makedirs(sub)
        self.assertEqual(bootstrap.worker_rules_status(sub), (state, path))

    def test_bootstrap_notice_names_the_file_and_the_command(self):
        with_cmd = bootstrap.bootstrap_notice("npm test", "/p/QWEN.md")
        self.assertIn("/p/QWEN.md", with_cmd)
        self.assertIn("npm test", with_cmd)
        self.assertIn("uncommitted", with_cmd)
        # No command detected: the notice must ASK rather than leave a blank,
        # because a silently empty test command is a gate that cannot fail.
        without = bootstrap.bootstrap_notice("", "/p/QWEN.md")
        self.assertIn("/p/QWEN.md", without)
        self.assertIn("could not detect a test command", without)

    def test_unconfigured_notice_embeds_the_cwd(self):
        # The message names the directory it is talking about; a notice that
        # says "no QWEN.md governs" without saying where is unactionable.
        d = mkrepo()
        state, path = bootstrap.worker_rules_status(d)
        notice = bootstrap.unconfigured_notice(d, state, path)
        self.assertIn(d, notice)
        self.assertIn("QWEN.md", notice)

    def test_nongit_refusal_is_an_error_receipt_naming_the_path(self):
        nong = tempfile.mkdtemp()
        refusal = bootstrap.nongit_refusal(nong)
        self.assertTrue(refusal.startswith("STATUS: error"))
        self.assertIn(nong, refusal)
        self.assertIn("git init", refusal)   # the fix, not just the diagnosis


class TestLocation(unittest.TestCase):
    """Where a project keeps its tests is a project's own business. Every
    detector here is a guess, so a project only has to be slightly unusual to
    get the wrong command or none at all -- and 'none at all' means the
    self-graded gate falls back to a folder that may not exist and can never
    go green."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def cfg(self, **keys):
        with open(os.path.join(self.d, ".qwen-delegate.json"), "w") as f:
            json.dump(keys, f)

    def test_an_explicit_command_wins_over_every_detector(self):
        os.makedirs(os.path.join(self.d, "tests"))
        with open(os.path.join(self.d, "Cargo.toml"), "w") as f:
            f.write("[package]\n")
        self.cfg(test_command="make check")
        self.assertEqual(bootstrap.detect_test_cmd(self.d), "make check")

    def test_a_named_test_dir_is_used_for_discovery(self):
        os.makedirs(os.path.join(self.d, "t"))
        self.cfg(test_dir="t")
        self.assertEqual(bootstrap.test_dir(self.d), "t")
        self.assertIn("-s t ", bootstrap.detect_test_cmd(self.d))

    def test_a_plain_tests_folder_is_found_without_any_config(self):
        # The common case: stdlib unittest in tests/, no packaging metadata.
        os.makedirs(os.path.join(self.d, "tests"))
        self.assertEqual(bootstrap.test_dir(self.d), "tests")
        self.assertIn("unittest discover -s tests", bootstrap.detect_test_cmd(self.d))

    def test_other_conventional_names_are_recognised(self):
        os.makedirs(os.path.join(self.d, "spec"))
        self.assertEqual(bootstrap.test_dir(self.d), "spec")

    def test_packaging_metadata_still_wins_over_the_folder_guess(self):
        os.makedirs(os.path.join(self.d, "tests"))
        with open(os.path.join(self.d, "pyproject.toml"), "w") as f:
            f.write("[project]\n")
        self.assertIn("pytest", bootstrap.detect_test_cmd(self.d))

    def test_nothing_detectable_still_returns_empty(self):
        self.assertEqual(bootstrap.detect_test_cmd(self.d), "")
        self.assertIsNone(bootstrap.test_dir(self.d))

    def test_a_corrupt_config_does_not_break_detection(self):
        with open(os.path.join(self.d, ".qwen-delegate.json"), "w") as f:
            f.write("{not json")
        os.makedirs(os.path.join(self.d, "tests"))
        self.assertIn("tests", bootstrap.detect_test_cmd(self.d))


class DetectedCommandActuallyRuns(unittest.TestCase):
    """The command detect_test_cmd RETURNS must RUN, and must COLLECT.

    Every other test of the detector in this file asserts the TEXT of the
    returned command and not one of them executes it. That is precisely how the
    last-resort branch (qd/bootstrap.py:99-101) shipped a command that CRASHES
    on this repo's own layout. Reproduced 2026-08-06, python3.14, in a fixture
    tree shaped like this repo (specs/*_spec.py, no __init__.py):

        $ python3 -m unittest discover -s specs -t . -v
        ImportError: Start directory is not importable: '<tree>/specs'

    `-t .` makes unittest require the START DIRECTORY to be an importable
    package. specs/ is a plain folder, so discovery dies before the pattern
    mismatch (*_spec.py against unittest's default test*.py) even matters --
    two independent reasons the same command can never grade this project, and
    the returned string satisfied every assertion we had.

    It is not an idle string either: it is embedded in the trust="self" gate
    script (qd/engine.py:244-245) and reused for the C8 *_qwen.* prefilter
    (qd/engine.py:1819, line re-derived 2026-08-06). A gate command that
    crashes is a gate that can never go green.

    WHY THIS IS PINNED AS A DETECTOR FIX, not as a per-project declaration.
    The alternative on offer was to declare a tier map in this repo's own
    .qwen-delegate.json ("tests": {"unit": ...}). It was rejected as the gate:
    qd/core/tiers.gate_for() has ZERO callers outside its own spec (verified
    2026-08-06), so declaring a map changes nothing that runs today -- and even
    once wired, it would fix exactly one repo while detect_test_cmd kept
    handing the same crashing command to every other stdlib-layout project.
    tiers.py agrees: its docstring (:16-18) commits to detect_test_cmd
    remaining the standing fallback, and gate_for returns (None, None, None)
    for ordinary work. Declaring the map is still worth doing; it is not what
    closes this hole.

    Each test below builds a throwaway tree, takes whatever the detector
    returns FOR THAT TREE, and executes it with the tree as cwd -- exactly what
    the self-gate script does.
    """

    def detected_run(self, d):
        cmd = bootstrap.detect_test_cmd(d)
        self.assertTrue(cmd, f"no command detected for {os.listdir(d)}")
        p = subprocess.run(cmd, shell=True, cwd=d, capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL)
        return cmd, p, ((p.stdout or "") + (p.stderr or "")).strip()

    def assertRan(self, cmd, p, out, *tags):
        self.assertNotIn(
            "Start directory is not importable", out,
            f"{cmd!r} crashed on discovery -- the gate built from it can never "
            f"go green:\n{out[-600:]}")
        self.assertEqual(p.returncode, 0,
                         f"{cmd!r} exited {p.returncode}:\n{out[-600:]}")
        for tag in tags:
            self.assertIn(f"RAN:{tag}", out,
                          f"{cmd!r} exited 0 without collecting {tag} -- a "
                          f"suite that matches nothing passes vacuously:"
                          f"\n{out[-600:]}")

    def test_the_command_for_a_spec_folder_runs_instead_of_crashing(self):
        # This repo's own shape: *_spec.py, no __init__.py, no packaging
        # metadata at all -- the case the last-resort branch exists for.
        d = tempfile.mkdtemp()
        put(d, "specs/alpha_spec.py", spec_src("alpha"))
        self.assertRan(*self.detected_run(d), "alpha")

    def test_the_command_goes_RED_when_a_test_fails(self):
        # The other half of "it runs": a command that collects nothing also
        # exits 0. Only a suite that can FAIL is a gate. Without this, an
        # implementation that "fixed" the crash by discovering nothing would
        # look identical to a working one.
        d = tempfile.mkdtemp()
        put(d, "specs/alpha_spec.py", spec_src("alpha", passing=False))
        cmd, p, out = self.detected_run(d)
        self.assertIn("RAN:alpha", out, f"{cmd!r} never collected the failing "
                                        f"test:\n{out[-600:]}")
        self.assertNotEqual(p.returncode, 0,
                            f"{cmd!r} reported success over a failing test:"
                            f"\n{out[-600:]}")

    def test_it_collects_every_filename_convention_the_pytest_branch_does(self):
        # Parity with the branch directly above it in the source: the pytest
        # detectors force `python_files=test_*.py *_test.py *_spec.py`
        # (qd/bootstrap.py:85-95) precisely "so a gate can't be silently
        # skipped". The unittest fallback never got that parity and keeps
        # unittest's default test*.py -- so in a mixed folder it grades a
        # SUBSET and reports OK, which is the same silent skip the comment
        # three lines earlier says must not happen.
        d = tempfile.mkdtemp()
        put(d, "specs/alpha_spec.py", spec_src("alpha"))
        put(d, "specs/beta_test.py", spec_src("beta"))
        put(d, "specs/test_gamma.py", spec_src("gamma"))
        self.assertRan(*self.detected_run(d), "alpha", "beta", "gamma")

    def test_the_plain_tests_folder_case_still_runs(self):
        # Regression guard on the shape that works today: whatever fixes the
        # spec folder must not cost the conventional tests/test_*.py layout,
        # which is the majority of projects this branch is reached for.
        d = tempfile.mkdtemp()
        put(d, "tests/test_thing.py", spec_src("thing"))
        self.assertRan(*self.detected_run(d), "thing")

    def test_a_folder_that_is_a_package_still_runs(self):
        # The one shape where `-t .` was survivable: an __init__.py makes the
        # start directory importable. It must keep working, so the fix cannot
        # simply be "assume it is never a package".
        d = tempfile.mkdtemp()
        put(d, "tests/__init__.py", "")
        put(d, "tests/test_thing.py", spec_src("thing"))
        self.assertRan(*self.detected_run(d), "thing")

    def test_a_configured_test_dir_produces_a_runnable_command_too(self):
        # test_a_named_test_dir_is_used_for_discovery (above) asserts `-s t `
        # appears in the string. Same gap, same fix: the configured branch has
        # to run as well, or a project that took our advice and declared
        # test_dir gets a gate that crashes.
        d = tempfile.mkdtemp()
        put(d, ".qwen-delegate.json", json.dumps({"test_dir": "t"}))
        put(d, "t/alpha_spec.py", spec_src("alpha"))
        self.assertRan(*self.detected_run(d), "alpha")

    def test_an_ordinary_helper_module_beside_the_tests_is_harmless(self):
        # The widened `-p "*.py"` imports NON-test modules too, which the
        # default `test*.py` never did. The ordinary case must survive that, or
        # the parity fix above costs every suite that keeps a helper next to
        # its tests -- which is most of them.
        d = tempfile.mkdtemp()
        put(d, "tests/__init__.py", "")
        put(d, "tests/helpers.py", "VALUE = 7\n")
        put(d, "tests/test_thing.py",
            "import unittest\n"
            "from .helpers import VALUE\n"
            "class Case(unittest.TestCase):\n"
            "    def test_case(self):\n"
            "        print('RAN:thing')\n"
            "        self.assertEqual(VALUE, 7)\n")
        # Also pins the relative import, and so the `-t .` half of the fix: the
        # start directory has to stay a PACKAGE for `from .helpers` to resolve.
        # Dropping `-t .` unconditionally passes every other test in this class
        # and breaks exactly this (measured 2026-08-06: "attempted relative
        # import with no known parent package").
        self.assertRan(*self.detected_run(d), "thing")

    def test_a_helper_that_cannot_import_now_fails_LOUDLY_not_silently(self):
        # The accepted COST of the widened pattern, pinned so it stays a
        # decision rather than a surprise. No single glob spans test_*.py /
        # *_test.py / *_spec.py, so the pattern has to widen, and a widened
        # pattern imports the broken helper too.
        #
        # Measured 2026-08-06 on this exact tree: the old `-t . -v` reported
        # "Ran 1 test ... OK", exit 0 -- unittest's default test*.py never
        # looked at helpers.py, so the breakage sat there wearing a green
        # receipt. It is a red gate now.
        #
        # That is the right way round for a GATE -- a false red gets read, a
        # green that graded a subset does not -- but it IS a behaviour change,
        # so it is asserted rather than left to be rediscovered as a
        # regression. The test still gets collected: this is a loud failure,
        # not a discovery crash like the one this class exists for.
        d = tempfile.mkdtemp()
        put(d, "tests/__init__.py", "")
        put(d, "tests/helpers.py", "import a_module_that_does_not_exist\n")
        put(d, "tests/test_thing.py", spec_src("thing"))
        cmd, p, out = self.detected_run(d)
        self.assertNotIn(
            "Start directory is not importable", out,
            f"{cmd!r} crashed on discovery rather than reporting the helper")
        self.assertIn("RAN:thing", out,
                      f"{cmd!r} stopped collecting the real test because a "
                      f"neighbouring module was broken:\n{out[-600:]}")
        self.assertNotEqual(p.returncode, 0,
                            f"{cmd!r} graded this suite green while a module "
                            f"in it could not be imported:\n{out[-600:]}")

    def test_a_hostile_test_dir_cannot_execute_anything(self):
        # `test_dir` is read from .qwen-delegate.json, which is REPO DATA -- so
        # it is attacker-controlled for anyone who delegates into a repo they
        # did not write, and the string it is interpolated into is handed to a
        # shell (qd/engine.py runs the detected command with shell=True).
        # Unquoted, that was arbitrary command execution rather than the
        # cosmetic spaces bug it was first taken for. Reproduced 2026-08-06:
        #
        #   test_dir = "tests; touch <marker> ;"
        #     -> python3 -m unittest discover -s tests; touch <marker> ; -p ...
        #     -> marker created
        #
        # Same class as the `-p "*.py"` quoting, which is what makes that one
        # load-bearing rather than tidy. Note this class RUNS the command, so
        # this is observed, not argued from the string's shape.
        for payload in ("tests; touch {m} ;",
                        "tests$(touch {m})",
                        "tests`touch {m}`",
                        # Trailing `#` swallows the rest of the line. Without
                        # it this payload is VACUOUS: touch inherits the
                        # remaining `-p "*.py" -v`, rejects `-p`, and creates
                        # nothing -- so the sub-case passed even against the
                        # unquoted build. Caught by mutation-testing the gate
                        # rather than by reading it.
                        "tests | touch {m} #"):
            with self.subTest(payload=payload):
                d = tempfile.mkdtemp()
                marker = os.path.join(tempfile.mkdtemp(), "PWNED")
                put(d, ".qwen-delegate.json",
                    json.dumps({"test_dir": payload.format(m=marker)}))
                put(d, "tests/alpha_spec.py", spec_src("alpha"))
                cmd = bootstrap.detect_test_cmd(d)
                subprocess.run(cmd, shell=True, cwd=d, capture_output=True,
                               text=True, timeout=60,
                               stdin=subprocess.DEVNULL)
                self.assertFalse(
                    os.path.exists(marker),
                    f"{cmd!r} executed a command injected through "
                    f"test_dir in .qwen-delegate.json")

    def test_quoting_the_test_dir_did_not_loosen_the_ordinary_command(self):
        # The fix above quotes MINIMALLY (shlex.quote), so an ordinary folder
        # name passes through byte-identical and the assertions that pin the
        # exact command a real project gets (TestLocation, :263 and :269) keep
        # binding. Asserted here so nobody "simplifies" it to a blanket
        # '"{d}"', which would close the hole while quietly making those two
        # assertions describe a command no longer issued.
        d = tempfile.mkdtemp()
        put(d, "tests/test_thing.py", spec_src("thing"))
        self.assertIn(" -s tests ", bootstrap.detect_test_cmd(d))

    def test_the_fixtures_above_are_shaped_like_this_repo(self):
        # Keeps the fixtures honest. If this repo's own layout drifts away
        # from what the fixtures model, these tests stop being evidence about
        # the command engine.py builds HERE -- so the resemblance is asserted,
        # not assumed. (This repo's suite is never invoked from inside a test;
        # that would be a minutes-long recursive run.)
        self.assertEqual(bootstrap.test_dir(ROOT), "specs")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "specs",
                                                     "__init__.py")),
                         "specs/ became a package -- the ImportError this "
                         "class pins would no longer reproduce")
        names = os.listdir(os.path.join(ROOT, "specs"))
        self.assertTrue([n for n in names if n.endswith("_spec.py")])
        self.assertFalse([n for n in names if n.startswith("test")],
                         "unittest's default test*.py pattern would now match "
                         "something here; the fixtures assume it matches none")


if __name__ == "__main__":
    unittest.main(verbosity=1)
