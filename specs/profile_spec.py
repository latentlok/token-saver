#!/usr/bin/env python3
"""
Spec for qd/profiles.py -- executor profiles (HLD C1/C7, LLD "qd/profiles.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

The load-bearing cases, in order of what would hurt most if broken:

  1. The builtin qwen-local profile must render the EXACT invocation the v1 server
     uses today (the regression pin). If this drifts, every delegation changes
     behavior silently.
  2. Resolution precedence: call arg > project .qwen-delegate.json "executor" >
     machine executors.json "default" > builtin. A swapped order silently routes
     work to the wrong executor.
  3. Endpoint config is the concurrency domain (C7): profiles resolve to an
     endpoint with a parallel_max; a missing endpoints section degrades safely to
     an implicit per-profile endpoint of capacity 1.
  4. Endpoint/model switching happens ONLY via settings_overlay (probed: flags and
     env are silently ignored by qwen-code) -- the builtin's overlay must be None
     so the user's own ~/.qwen config is inherited untouched.
  5. Cost must be computed, and 0.0 must mean "priced at zero", never "unmeasured".

Public surface pinned here:
    qd.profiles.ProfileError            exception; message names file/known names
    qd.profiles.QWEN_LOCAL              the builtin profile dict
    qd.profiles.resolve(cwd, call_executor=None) -> profile dict
        (defaults applied; adds "endpoint_cfg": {"name", "parallel_max"})
    qd.profiles.render_argv(profile, task, mode, resume) -> list[str]
    qd.profiles.cost_usd(profile, tokens_in, tokens_out) -> float

Machine file: ~/.qwen-delegate/executors.json, overridable via the
QWEN_DELEGATE_EXECUTORS env var (mirrors QWEN_DELEGATE_REGISTRY).

Run:  python3 specs/profile_spec.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import profiles  # noqa: E402


def write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


class Fixture(unittest.TestCase):
    """Temp cwd + temp machine executors file, both cleaned up."""

    def setUp(self):
        self._old_env = dict(os.environ)
        os.environ.pop("QWEN_BIN", None)
        self.cwd = tempfile.mkdtemp()
        self.machine = os.path.join(tempfile.mkdtemp(), "executors.json")
        os.environ["QWEN_DELEGATE_EXECUTORS"] = self.machine

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def machine_cfg(self, default=None, endpoints=None, extra_profiles=None):
        profs = {
            "fast": {"argv": ["fastcli", "-p", "{task}", "--approval-mode",
                              "{mode}", "-o", "json"],
                     "endpoint": "gpu"},
            "paid": {"argv": ["paidcli", "-p", "{task}", "--approval-mode",
                              "{mode}", "-o", "json"],
                     "endpoint": "api",
                     "price_in_per_mtok": 0.74, "price_out_per_mtok": 4.66},
        }
        profs.update(extra_profiles or {})
        cfg = {"profiles": profs}
        if default:
            cfg["default"] = default
        if endpoints is not None:
            cfg["endpoints"] = endpoints
        write(self.machine, cfg)

    def project_cfg(self, executor):
        write(os.path.join(self.cwd, ".qwen-delegate.json"),
              {"executor": executor})


class BuiltinPin(Fixture):
    """Case 1: the regression pin -- byte-for-byte the v1 invocation."""

    def test_render_matches_v1_invocation_no_resume(self):
        argv = profiles.render_argv(profiles.QWEN_LOCAL, "TASKTEXT",
                                    "auto-edit", None)
        self.assertEqual(argv, ["qwen", "-p", "TASKTEXT",
                                "--approval-mode", "auto-edit", "-o", "json"])

    def test_render_matches_v1_invocation_with_resume(self):
        argv = profiles.render_argv(profiles.QWEN_LOCAL, "T", "plan", "sess-1")
        self.assertEqual(argv, ["qwen", "-p", "T", "--approval-mode", "plan",
                                "-o", "json", "-r", "sess-1"])

    def test_builtin_shape(self):
        p = profiles.QWEN_LOCAL
        self.assertEqual(p["name"], "qwen-local")
        self.assertIsNone(p["settings_overlay"])          # case 4: inherit ~/.qwen
        self.assertEqual(p["env"], {})
        self.assertEqual(p["price_in_per_mtok"], 0)
        self.assertEqual(p["price_out_per_mtok"], 0)
        self.assertEqual(p["rules_file"], "QWEN.md")
        self.assertEqual(p["altitude"], "lld")
        self.assertEqual(p["defaults"],
                         {"max_iterations": 3, "timeout": 900})

    def test_resolve_with_nothing_configured_returns_builtin(self):
        p = profiles.resolve(self.cwd, None)
        self.assertEqual(p["name"], "qwen-local")
        self.assertEqual(p["endpoint_cfg"], {"name": "local", "parallel_max": 1})

    def test_task_text_is_never_shell_interpolated(self):
        # argv is a list; task lands as ONE element verbatim, quoting intact.
        nasty = 'a "b" $(rm -rf) \n c'
        argv = profiles.render_argv(profiles.QWEN_LOCAL, nasty, "auto-edit", None)
        self.assertEqual(argv[2], nasty)


class Precedence(Fixture):
    """Case 2: the four-level chain, each level beating the next."""

    def test_call_arg_beats_project_and_machine(self):
        self.machine_cfg(default="paid")
        self.project_cfg("paid")
        self.assertEqual(profiles.resolve(self.cwd, "fast")["name"], "fast")

    def test_project_beats_machine_default(self):
        self.machine_cfg(default="paid")
        self.project_cfg("fast")
        self.assertEqual(profiles.resolve(self.cwd, None)["name"], "fast")

    def test_machine_default_beats_builtin(self):
        self.machine_cfg(default="paid")
        self.assertEqual(profiles.resolve(self.cwd, None)["name"], "paid")

    def test_builtin_reachable_by_name_without_machine_file(self):
        self.assertEqual(profiles.resolve(self.cwd, "qwen-local")["name"],
                         "qwen-local")

    def test_deterministic(self):
        self.machine_cfg(default="fast")
        self.assertEqual(profiles.resolve(self.cwd, None),
                         profiles.resolve(self.cwd, None))


class Refusals(Fixture):
    """Structured errors, never tracebacks-by-accident."""

    def test_unknown_name_lists_known_and_file(self):
        self.machine_cfg()
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.resolve(self.cwd, "nope")
        msg = str(ctx.exception)
        self.assertIn("nope", msg)
        self.assertIn("fast", msg)             # lists known names
        self.assertIn("qwen-local", msg)

    def test_malformed_machine_file_names_the_file(self):
        with open(self.machine, "w") as f:
            f.write("{not json")
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.resolve(self.cwd, None)
        self.assertIn(self.machine, str(ctx.exception))

    def test_argv_without_task_placeholder_refused(self):
        self.machine_cfg(extra_profiles={
            "bad": {"argv": ["x", "--approval-mode", "{mode}"]}})
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.resolve(self.cwd, "bad")
        self.assertIn("{task}", str(ctx.exception))


class Endpoints(Fixture):
    """Case 3: C7 -- the endpoint is the concurrency domain."""

    def test_named_endpoint_resolved(self):
        self.machine_cfg(endpoints={"gpu": {"parallel_max": 2}})
        p = profiles.resolve(self.cwd, "fast")
        self.assertEqual(p["endpoint_cfg"], {"name": "gpu", "parallel_max": 2})

    def test_missing_endpoints_section_degrades_to_capacity_1(self):
        self.machine_cfg()  # no endpoints key at all
        p = profiles.resolve(self.cwd, "fast")
        self.assertEqual(p["endpoint_cfg"]["parallel_max"], 1)

    def test_parallel_max_below_1_clamped(self):
        self.machine_cfg(endpoints={"gpu": {"parallel_max": 0}})
        p = profiles.resolve(self.cwd, "fast")
        self.assertEqual(p["endpoint_cfg"]["parallel_max"], 1)

    def test_unknown_endpoint_reference_refused(self):
        self.machine_cfg(endpoints={"other": {"parallel_max": 1}},
                         extra_profiles={"lost": {
                             "argv": ["x", "-p", "{task}"],
                             "endpoint": "ghost"}})
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.resolve(self.cwd, "lost")
        self.assertIn("ghost", str(ctx.exception))

    def test_defaults_applied_to_machine_profile(self):
        self.machine_cfg()
        p = profiles.resolve(self.cwd, "fast")
        self.assertEqual(p["rules_file"], "QWEN.md")
        self.assertEqual(p["altitude"], "lld")
        self.assertEqual(p["price_in_per_mtok"], 0)
        self.assertIsNone(p["settings_overlay"])
        self.assertEqual(p["env"], {})
        self.assertEqual(p["defaults"],
                         {"max_iterations": 3, "timeout": 900})


class Dispatch(Fixture):
    """Case 6: the serial policy. One local endpoint is one GPU -- concurrent
    requests share its context budget, and a request given less context than it
    was promised is a request that gets truncated mid tool-call. `dispatch`
    pins that, and it is enforced in resolve() so no call site can route
    around it."""

    def cfg(self, where, mode):
        path = (os.path.join(self.cwd, ".qwen-delegate.json") if where == "project"
                else os.path.join(os.path.dirname(self.machine), "config.json"))
        write(path, {"dispatch": mode})
        if where != "project":
            os.environ["QWEN_DELEGATE_CONFIG"] = path

    def test_unset_is_serial_out_of_the_box(self):
        # No endpoints section => one slot each, so nothing to configure.
        p = profiles.resolve(self.cwd, None)
        self.assertIsNone(profiles.dispatch_mode(self.cwd))
        self.assertEqual(p["endpoint_cfg"]["parallel_max"], 1)
        self.assertEqual(p["dispatch"], "serial")

    def test_serial_pins_a_multi_slot_endpoint_to_one(self):
        self.machine_cfg(endpoints={"gpu": {"parallel_max": 4},
                                    "api": {"parallel_max": 1}})
        self.assertEqual(profiles.resolve(self.cwd, "fast")
                         ["endpoint_cfg"]["parallel_max"], 4)
        self.cfg("project", "serial")
        p = profiles.resolve(self.cwd, "fast")
        self.assertEqual(p["endpoint_cfg"]["parallel_max"], 1)
        self.assertEqual(p["dispatch"], "serial")

    def test_parallel_leaves_an_explicit_capacity_alone(self):
        self.machine_cfg(endpoints={"gpu": {"parallel_max": 4},
                                    "api": {"parallel_max": 1}})
        self.cfg("project", "parallel")
        p = profiles.resolve(self.cwd, "fast")
        self.assertEqual(p["endpoint_cfg"]["parallel_max"], 4)
        self.assertEqual(p["dispatch"], "parallel")

    def test_project_beats_machine(self):
        self.machine_cfg(endpoints={"gpu": {"parallel_max": 4},
                                    "api": {"parallel_max": 1}})
        self.cfg("machine", "parallel")
        self.cfg("project", "serial")
        self.assertEqual(profiles.dispatch_mode(self.cwd), "serial")
        self.assertEqual(profiles.resolve(self.cwd, "fast")
                         ["endpoint_cfg"]["parallel_max"], 1)

    def test_machine_default_applies_with_no_project_file(self):
        self.machine_cfg(endpoints={"gpu": {"parallel_max": 4},
                                    "api": {"parallel_max": 1}})
        self.cfg("machine", "serial")
        self.assertEqual(profiles.dispatch_mode(self.cwd), "serial")
        self.assertEqual(profiles.resolve(self.cwd, "fast")
                         ["endpoint_cfg"]["parallel_max"], 1)

    def test_typo_reads_as_serial_never_turns_concurrency_on(self):
        self.machine_cfg(endpoints={"gpu": {"parallel_max": 4},
                                    "api": {"parallel_max": 1}})
        self.cfg("project", "Parallell")
        self.assertEqual(profiles.dispatch_mode(self.cwd), "serial")
        self.assertEqual(profiles.resolve(self.cwd, "fast")
                         ["endpoint_cfg"]["parallel_max"], 1)

    def test_corrupt_config_is_not_an_error(self):
        with open(os.path.join(self.cwd, ".qwen-delegate.json"), "w") as f:
            f.write("{not json")
        self.assertIsNone(profiles.dispatch_mode(self.cwd))
        self.assertEqual(profiles.resolve(self.cwd, None)["dispatch"], "serial")


class Cost(Fixture):
    """Case 5: recorded, exact, and 0.0 means priced-at-zero."""

    def test_cost_math(self):
        self.machine_cfg()
        paid = profiles.resolve(self.cwd, "paid")
        self.assertAlmostEqual(profiles.cost_usd(paid, 1_000_000, 500_000),
                               0.74 + 2.33, places=6)

    def test_zero_price_is_zero_not_missing(self):
        got = profiles.cost_usd(profiles.QWEN_LOCAL, 123_456, 78_900)
        self.assertEqual(got, 0.0)
        self.assertIsInstance(got, float)


if __name__ == "__main__":
    unittest.main(verbosity=1)
