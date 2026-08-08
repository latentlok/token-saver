#!/usr/bin/env python3
"""
Spec for qd/playbook.py -- versioned delegation documents (U6, C9).

Claude-authored gate (never delegate this file -- it defines what correct means).

A playbook is a repo file that carries a delegation: body = task, front matter
= the call params the caller did not pass, {{slot}}s fill from vars, and
`chain: true` compiles `## Step <n>` sections into chain links. Load-bearing:

  1. The document is read CONFINED to the workspace -- a path or symlink that
     resolves outside it is refused by name, never read.
  2. Every parse defect is a refusal BY NAME: unfilled slot, unused var,
     unknown front-matter key, wrong-shaped value, unclosed fence, chain with
     no steps. A typo'd gate key silently ignored is a gate that never runs.
  3. Precedence is args > front matter (> project > machine config, which the
     engine already owns): a front-matter value binds ONLY where the call arg
     is absent.
  4. `resolve` with no brief_file returns args untouched -- idempotent, safe
     on every path -- and a compiled link carries `_brief`, never
     `brief_file`, so no link re-reads or re-amends the document.
  5. The server seam (engine.expand_playbook) turns a `chain: true` playbook
     into `chain` items BEFORE routing; a missing document refuses in the
     RESPONSE with no receipt file -- nobody polls for a run never spawned.

Engine-side behavior (protection revert, amendment timing, per-brief ledger,
BRIEF receipt line) is pinned in engine_spec/verdict_spec/runlog_spec.

Public surface pinned here:
    qd.playbook.read(cwd, rel) -> (text, refusal)
    qd.playbook.substitute(text, vars) -> (text, missing, unused)
    qd.playbook.front_matter(text) -> (meta, body, refusal)
    qd.playbook.compile_steps(body, base_args) -> (links, refusal)
    qd.playbook.amend(cwd, rel, message, date) -> refusal|None
    qd.playbook.amendment_count(text) -> int
    qd.playbook.resolve(args, amended=False) -> (args, refusal)
    qd.engine.expand_playbook(args) -> (args, refusal)

Run:  python3 specs/playbook_spec.py
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import playbook  # noqa: E402
from qd import server  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()

    def write(self, rel, text):
        full = os.path.join(self.cwd, rel)
        d = os.path.dirname(full)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
        return rel

    def resolve(self, rel=None, **over):
        args = {"task": "", "cwd": self.cwd}
        if rel:
            args["brief_file"] = rel
        args.update(over)
        return playbook.resolve(args)


class Confinement(Fixture):
    """Claim 1: the read never leaves the workspace."""

    def test_a_repo_relative_path_reads(self):
        self.write("playbooks/b.md", "the body\n")
        text, refusal = playbook.read(self.cwd, "playbooks/b.md")
        self.assertIsNone(refusal)
        self.assertEqual(text, "the body\n")

    def test_a_missing_file_is_refused_by_name(self):
        text, refusal = playbook.read(self.cwd, "playbooks/absent.md")
        self.assertIsNone(text)
        self.assertIn("playbooks/absent.md", refusal)
        self.assertIn("no readable file", refusal)

    def test_a_dot_dot_escape_is_refused(self):
        outside = os.path.join(os.path.dirname(self.cwd), "outside.md")
        with open(outside, "w") as f:
            f.write("secret\n")
        _, refusal = playbook.read(self.cwd, "../outside.md")
        self.assertIn("outside the workspace", refusal)

    def test_a_symlink_out_of_the_tree_is_refused(self):
        outside = os.path.join(tempfile.mkdtemp(), "real.md")
        with open(outside, "w") as f:
            f.write("secret\n")
        os.symlink(outside, os.path.join(self.cwd, "link.md"))
        _, refusal = playbook.read(self.cwd, "link.md")
        self.assertIn("outside the workspace", refusal)


class Slots(Fixture):
    """Claim 2, substitution half: both directions refuse by name."""

    def test_slots_fill_from_vars_and_values_stringify(self):
        out, missing, unused = playbook.substitute(
            "run {{cmd}} {{n}} times", {"cmd": "pytest", "n": 3})
        self.assertEqual(out, "run pytest 3 times")
        self.assertEqual((missing, unused), ([], []))

    def test_an_unfilled_slot_is_named(self):
        _, missing, _ = playbook.substitute("do {{what}} and {{how}}",
                                            {"what": "x"})
        self.assertEqual(missing, ["how"])

    def test_a_var_matching_no_slot_is_named(self):
        _, _, unused = playbook.substitute("do {{what}}",
                                           {"what": "x", "waht": "y"})
        self.assertEqual(unused, ["waht"])

    def test_resolve_refuses_both_directions(self):
        rel = self.write("b.md", "do {{what}}\n")
        _, refusal = self.resolve(rel)
        self.assertIn("{{what}}", refusal)
        self.assertIn("vars", refusal)
        _, refusal = self.resolve(rel, vars={"what": "x", "typo": "y"})
        self.assertIn("typo", refusal)

    def test_a_slot_in_the_front_matter_fills_too(self):
        # The useful case: substitution runs on the WHOLE file before the
        # front matter is parsed, so a slot inside verify: works.
        rel = self.write("b.md",
                         "---\nverify: pytest {{mod}}_test.py\n---\nbody\n")
        args, refusal = self.resolve(rel, vars={"mod": "reader"})
        self.assertIsNone(refusal)
        self.assertEqual(args["verify"], "pytest reader_test.py")


class FrontMatter(Fixture):
    """Claim 2, parse half: json-vs-string values, refusals by name."""

    def test_no_fence_is_all_body(self):
        meta, body, refusal = playbook.front_matter("just prose\n")
        self.assertEqual((meta, refusal), ({}, None))
        self.assertEqual(body, "just prose\n")

    def test_the_fence_must_be_the_first_line_exactly(self):
        # An indented or offset --- is prose, not an opener.
        meta, body, refusal = playbook.front_matter(" ---\nverify: x\n---\n")
        self.assertEqual(meta, {})
        self.assertIsNone(refusal)

    def test_values_parse_as_json_else_bare_string(self):
        meta, body, refusal = playbook.front_matter(
            "---\nverify: python3 -m pytest\ntimeout_sec: 1200\n"
            "touch_scope: [\"a.py\", \"b.py\"]\nchain: false\n---\nbody\n")
        self.assertIsNone(refusal)
        self.assertEqual(meta["verify"], "python3 -m pytest")
        self.assertEqual(meta["timeout_sec"], 1200)
        self.assertEqual(meta["touch_scope"], ["a.py", "b.py"])
        self.assertIs(meta["chain"], False)
        self.assertEqual(body, "body")

    def test_blank_lines_inside_the_fence_are_allowed(self):
        meta, _, refusal = playbook.front_matter(
            "---\nverify: x\n\napproval_mode: plan\n---\nbody\n")
        self.assertIsNone(refusal)
        self.assertEqual(meta["approval_mode"], "plan")

    def test_an_unknown_key_is_refused_by_name(self):
        _, _, refusal = playbook.front_matter("---\nverfy: x\n---\nbody\n")
        self.assertIn("verfy", refusal)
        self.assertIn("not recognised", refusal)

    def test_a_malformed_line_is_refused_with_its_number(self):
        _, _, refusal = playbook.front_matter(
            "---\nverify: x\njust some prose\n---\nbody\n")
        self.assertIn("line 3", refusal)
        self.assertIn("just some prose", refusal)

    def test_an_unclosed_fence_is_refused(self):
        _, _, refusal = playbook.front_matter("---\nverify: x\n")
        self.assertIn("never closes", refusal)

    def test_wrong_shaped_values_are_refused_by_name(self):
        for line, want in (
                ("touch_scope: a.py, b.py", "JSON list"),
                ("timeout_sec: soon", "integer"),
                ("chain: yes", "true or false"),
                ("verify: 42", "string")):
            _, _, refusal = playbook.front_matter(f"---\n{line}\n---\nb\n")
            self.assertIn(want, refusal, line)

    def test_the_document_cannot_set_trust_or_executor(self):
        # Who is trusted and where the run happens are the CALLER's
        # decisions; a document that could grant itself L5 would be a
        # privilege escalation written in markdown.
        for key in ("trust", "executor", "worktree"):
            _, _, refusal = playbook.front_matter(f"---\n{key}: x\n---\nb\n")
            self.assertIn(key, refusal)


class Steps(Fixture):
    """Steps -> chain links: preamble + own step, overrides bind per link."""

    DOC = ("---\nverify: bash gate.sh\nchain: true\n---\n"
           "Shared context.\n\n"
           "## Step 1: first\nverify: echo one\ntouch_scope: [\"a.py\"]\n"
           "Do the first thing.\n\n"
           "## step 2\nDo the second thing.\n")

    def links(self, doc=None, **over):
        rel = self.write("c.md", doc or self.DOC)
        args, refusal = self.resolve(rel, **over)
        self.assertIsNone(refusal)
        return args["chain"]

    def test_each_link_is_preamble_plus_its_own_step(self):
        links = self.links()
        self.assertEqual(len(links), 2)
        self.assertIn("Shared context.", links[0]["task"])
        self.assertIn("Do the first thing.", links[0]["task"])
        self.assertNotIn("Do the second thing.", links[0]["task"])
        self.assertIn("Do the second thing.", links[1]["task"])

    def test_step_matching_is_case_insensitive(self):
        self.assertEqual(len(self.links()), 2)   # "## step 2" matched

    def test_per_step_overrides_beat_the_front_matter(self):
        links = self.links()
        self.assertEqual(links[0]["verify"], "echo one")
        self.assertEqual(links[0]["touch_scope"], ["a.py"])
        self.assertEqual(links[1]["verify"], "bash gate.sh")

    def test_override_lines_are_stripped_from_the_task(self):
        links = self.links()
        self.assertNotIn("echo one", links[0]["task"])
        self.assertNotIn("touch_scope", links[0]["task"])

    def test_prose_opening_with_a_colon_is_not_an_override(self):
        # Goal:/Note: routinely open a step; the front matter's
        # refuse-on-unknown-key rule must NOT be reused here.
        links = self.links(
            "---\nchain: true\nverify: bash g.sh\n---\n"
            "## Step 1\nGoal: make it work.\nDo it.\n")
        self.assertIn("Goal: make it work.", links[0]["task"])

    def test_a_link_never_carries_the_document_keys(self):
        for link in self.links(executor="stub"):
            for key in ("brief_file", "vars", "chain", "wait", "retry_of",
                        "amend_brief"):
                self.assertNotIn(key, link)
            self.assertEqual(link["executor"], "stub")
            self.assertEqual(link["_brief"]["path"], "c.md")

    def test_chain_true_with_no_steps_is_refused_by_name(self):
        rel = self.write("d.md", "---\nchain: true\n---\nNo steps.\n")
        _, refusal = self.resolve(rel)
        self.assertIn("no `## Step <n>` sections", refusal)

    def test_chain_false_is_inert(self):
        rel = self.write("e.md",
                         "---\nchain: false\n---\n## Step 1\nStill one task.\n")
        args, refusal = self.resolve(rel)
        self.assertIsNone(refusal)
        self.assertNotIn("chain", args)


class Amendments(Fixture):
    def test_amend_creates_the_section_and_appends_dated_lines(self):
        rel = self.write("b.md", "body\n")
        self.assertIsNone(playbook.amend(self.cwd, rel, "fix A", "2026-07-28"))
        self.assertIsNone(playbook.amend(self.cwd, rel, "fix B", "2026-07-29"))
        with open(os.path.join(self.cwd, rel)) as f:
            text = f.read()
        self.assertIn("## Amendments\n- 2026-07-28 fix A\n- 2026-07-29 fix B",
                      text)
        self.assertEqual(playbook.amendment_count(text), 2)

    def test_amending_a_missing_file_is_refused(self):
        self.assertIn("absent.md",
                      playbook.amend(self.cwd, "absent.md", "m", "2026-07-28"))

    def test_count_stops_at_the_next_section(self):
        text = "## Amendments\n- a\n- b\n\n## Other\n- not one\n"
        self.assertEqual(playbook.amendment_count(text), 2)


class Resolve(Fixture):
    """Claims 3 and 4: precedence, composition, idempotence, _brief."""

    def test_no_brief_file_returns_args_untouched(self):
        args = {"task": "t", "cwd": self.cwd, "vars": {"x": 1}}
        out, refusal = playbook.resolve(args)
        self.assertIs(out, args)
        self.assertIsNone(refusal)

    def test_the_body_is_the_task_and_the_callers_task_is_an_addendum(self):
        rel = self.write("b.md", "Build the parser.\n")
        args, _ = self.resolve(rel, task="Focus on utf-8.")
        self.assertEqual(args["task"], "Build the parser.\n\nFocus on utf-8.")
        self.assertEqual(args["_brief"]["addendum"], "Focus on utf-8.")

    def test_a_call_arg_beats_the_front_matter(self):
        rel = self.write("b.md", "---\nverify: front-gate\n"
                                 "timeout_sec: 60\n---\nbody\n")
        args, _ = self.resolve(rel, verify="call-gate")
        self.assertEqual(args["verify"], "call-gate")
        self.assertEqual(args["timeout_sec"], 60)
        # Only the values the DOCUMENT supplied are marked as filled -- the
        # engine keeps those out of the stored brief so a retry re-reads them.
        self.assertEqual(args["_brief"]["filled"], ["timeout_sec"])

    def test_the_brief_digest_names_the_document_version(self):
        rel = self.write("b.md", "body one\n")
        a1, _ = self.resolve(rel)
        a2, _ = self.resolve(rel)
        self.assertEqual(a1["_brief"]["sha256"], a2["_brief"]["sha256"])
        self.assertEqual(len(a1["_brief"]["sha256"]), 16)
        self.write("b.md", "body two\n")
        a3, _ = self.resolve(rel)
        self.assertNotEqual(a1["_brief"]["sha256"], a3["_brief"]["sha256"])

    def test_brief_carries_size_and_amendment_count(self):
        rel = self.write("b.md",
                         "body\n\n## Amendments\n- 2026-07-27 fix A\n")
        args, _ = self.resolve(rel)
        self.assertEqual(args["_brief"]["amendments"], 1)
        self.assertEqual(args["_brief"]["chars"], len(args["task"]))
        self.assertIs(args["_brief"]["amended"], False)


class ServerSeam(Fixture):
    """Claim 5, on chain_spec's fake-handler pattern: no worker, no clock."""

    def setUp(self):
        super().setUp()
        self._env = dict(os.environ)
        os.environ["DELEGATION_LOCKS"] = tempfile.mkdtemp()
        os.environ["DELEGATION_EXECUTORS"] = os.path.join(
            tempfile.mkdtemp(), "absent.json")
        os.environ["DELEGATION_REGISTRY"] = os.path.join(
            tempfile.mkdtemp(), "reg.jsonl")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def spy_engine(self):
        from qd import engine
        calls = []
        real = engine.run
        engine.run = lambda args: calls.append(args) or \
            "STATUS: success\nSESSION: s-1\nbody"
        self.addCleanup(setattr, engine, "run", real)
        return calls

    def test_a_chain_playbook_runs_as_a_chain(self):
        calls = self.spy_engine()
        rel = self.write("c.md", Steps.DOC)
        out = server.run_delegate_batch({"task": "", "cwd": self.cwd,
                                         "brief_file": rel})
        self.assertEqual(len(calls), 2)
        self.assertIn("=== chain link 2/2:", out)
        for args in calls:
            self.assertNotIn("brief_file", args)
            self.assertEqual(args["_brief"]["path"], rel)

    def test_a_single_playbook_reaches_the_run_unexpanded(self):
        # The single path resolves its own brief INSIDE the run -- that keeps
        # the amendment on exactly one code path.
        calls = self.spy_engine()
        rel = self.write("b.md", "Build it.\n")
        server.run_delegate_batch({"task": "", "cwd": self.cwd,
                                   "brief_file": rel})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["brief_file"], rel)
        self.assertNotIn("_brief", calls[0])

    def test_a_missing_document_refuses_in_the_response_with_no_receipt(self):
        calls = self.spy_engine()
        out = server.submit_delegate({"task": "", "cwd": self.cwd,
                                      "brief_file": "playbooks/absent.md"})
        self.assertIn("STATUS: refused", out)
        self.assertIn("playbooks/absent.md", out)
        self.assertEqual(calls, [])
        receipts = os.path.join(self.cwd, ".delegation", "receipts")
        self.assertFalse(os.path.isdir(receipts) and os.listdir(receipts))

    def test_brief_file_beside_chain_or_batch_is_refused_by_name(self):
        calls = self.spy_engine()
        rel = self.write("b.md", "Build it.\n")
        for shape in ({"chain": [{"task": "t", "cwd": self.cwd}]},
                      {"batch": [{"task": "t", "cwd": self.cwd}]}):
            out = server.run_delegate_batch(
                dict({"task": "", "cwd": self.cwd, "brief_file": rel},
                     **shape))
            self.assertIn("STATUS: refused", out)
            self.assertIn("brief_file", out)
        self.assertEqual(calls, [])

    def test_a_parse_refusal_is_synchronous_too(self):
        calls = self.spy_engine()
        rel = self.write("b.md", "---\nverfy: x\n---\nbody\n")
        out = server.submit_delegate({"task": "", "cwd": self.cwd,
                                      "brief_file": rel})
        self.assertIn("STATUS: refused", out)
        self.assertIn("verfy", out)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
