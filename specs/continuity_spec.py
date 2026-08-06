#!/usr/bin/env python3
"""
Spec for the four grades of continuity between chain links (PARKED B, §10.3).

Claude-authored gate (never delegate this file -- it defines what correct means).

THE BUG THIS STARTS FROM. `qd/server.py:471` is the entire setting:

    if carried and args.get("carry") != "none":
        args["task"] = carried + (args.get("task") or "")

Any value except the literal "none" -- "structured", "session", "banana" --
means "forward the handoff preamble", and the receipt reports success. A caller
asking for a grade that was never built silently gets a DIFFERENT grade, and
nothing anywhere says so. `carry` is also absent from `qd/schemas.py`: an
UNDECLARED wire parameter, with no enum, no validation and no documentation.
No spec pins any grade name -- "handoff"/"structured"/"session" appear in no
spec and in no non-doc source file, and `specs/chain_spec.py:469` exercises
only the "none" value. PRINCIPLES.md:110-114: "an instrument that can fail
silently eventually will, and you will not notice."

WHAT §10.3 DECLARES (docs/archive/a92e876/DESIGN-v06-test-first.md:668-686 -- note that
docs/archive/a92e876/PARKED.md:79 points at DESIGN-modular-architecture.md, which has no §10.3;
that is a known doc error, not the design):

    none        nothing                        full isolation
    handoff     the four typed lines           full -- fresh session
    structured  result_schema JSON, validated  full -- fresh session
    session     the entire conversation        NONE

FOUR DECISIONS THIS GATE MAKES, AND WHY

1. `structured` IS BUILT. It is the useful grade and carries no safety
   argument: a validated payload in a fresh session is the handoff with a type.

2. `session` IS REFUSED, not built, and the refusal carries §10.3's own reason.
   It is unbuilt today and silently aliases to `handoff` (the bug above), so
   nothing is taken away by refusing it. Building it would ship the one grade
   the design names "never" -- and would ship it on COST grounds, which is
   precisely the trap §10.3 warns about: "the cheapest grade for us ... and the
   most dangerous. Cost and safety run in opposite directions." A refusal is
   honest and reversible; a silent alias is neither. `session` is deliberately
   absent from the schema enum AND refused by name on the wire: the enum says
   what exists, the refusal answers the caller who asks for what does not.

3. THE HOLDER FOR `structured` IS THE RECEIPT'S STAMPED BLOCK -- run_chain
   re-reads it from the text it already has. The alternative was changing the
   handler contract to return `(text, payload)`, and it was rejected on
   evidence: `run_chain(items, handler)` shares its handler with the batch path
   (`qd/server.py:639`, `_batch_item`), and `specs/chain_spec.py:74-81` and
   `specs/async_spec.py:596` both drive it with fakes that return a bare
   string. A two-value return breaks a sibling gate and a second dispatch path
   to move a value across ONE function boundary. run_chain already re-reads
   this same text twice -- `_receipt_status` for the halt decision and
   `verdict.parse_handoff` for the preamble -- so this adds no new channel,
   only a third read of a string already in hand.

   The cost the scout named -- "re-reads a possibly size-capped receipt" -- is
   answered by reading the STAMPED block rather than the last one.
   `qd/verdict.py:565-571` puts `RESULT: valid (schema)` and the fenced block
   in the receipt BODY, "never the droppable region and never the truncated
   tail", precisely because the payload is a deliverable. The worker's echoed
   reply lower down IS capped (RESULT_CAP, `qd/verdict.py:17`) and ends in the
   same fence it was asked for -- so `last_json_block(receipt)` finds the
   WRONG, truncatable block. Reading the stamp also means we never re-validate:
   the stamp is this server's own record that it already did, and a payload
   with no stamp is not a validated result and does not cross.

4. THE PAYLOAD LANDS IN A DECLARED SLOT (§10.2: "as a declared slot"), not
   glued onto `task`. JSON prepended as prose is the handoff with more tokens
   and no type. The slot is a reserved arg in the CHAIN_ARG/WT_ARG family
   (`qd/engine.py:81-107`) -- only run_chain may set it, so a hand-written call
   cannot claim to have inherited a result from a link that never ran. The
   engine renders it as a PREFIX layer through `qd/core/prompt.py compose()`,
   which today has no production caller at all: the Decorator landed for
   suffixes only and the prefix half of its own docstring is aspirational
   (`qd/engine.py:1304` builds the one existing prefix by concatenation). A
   sixth prefix added by string-concat re-creates exactly the scatter that
   module exists to end.

`handoff` KEEPS ITS CURRENT SHAPE -- a preamble prepended to `task`. Moving it
into the slot would break the prompt shape `specs/chain_spec.py:436-440` pins
(the task must read LAST) and buys nothing this task needs. The grades are
EXCLUSIVE: `structured` sends the payload and NOT the preamble. A caller who
asked for a typed result gets a typed result, not both bills.

ALSO PINNED HERE, AND WHY IT IS WORTH ONE TEST EACH

  · run_chain never places a session id into a link's args. `qd/server.py` has
    zero session_id references today and NOTHING pins that absence, so the
    safest thing in the chain is safe by accident. `session` is reachable from
    here in about ten lines (`profiles.py:234-251` renders {resume} from argv;
    the id is on receipt line 2, `qd/verdict.py:248`), which is the cheapness
    §10.3 warns about. This makes today's absence load-bearing.

  · A receipt line naming the grade. Continuity is invisible to the caller at
    EVERY grade today -- nothing in a receipt says link 2 was given link 1's
    handoff. DESIGN-v06-test-first.md:331-339: chain and single delegation are
    "two different evidence models. The receipt must be able to say which one
    you got; collapsing makes that invisible."

NOT PINNED HERE, deliberately:
  · `carry` on a LONE delegation or a batch item. There is no previous link, so
    the parameter has no meaning there; validating it would need a second
    refusal site for a call that cannot mean anything by it.
  · A producer/consumer match between link N's `result_schema` and link N+1's
    expectation. That is `qd/features/gates/contract.py`'s shape and a separate
    piece of work; carrying a payload does not require it.

Public surface pinned here:
    qd.server.CARRY_GRADES      ("none", "handoff", "structured")
    qd.server.CARRY_DEFAULT     "handoff"
    qd.engine.CARRIED_ARG       the reserved slot key on link N+1's args
    qd.verdict.RESULT_VALID_LINE  the stamp, shared by the emitter and reader
    the `carry` property of qd.schemas.TOOL

Run:  python3 specs/continuity_spec.py
"""

import inspect
import json
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from chain_spec import Fixture, GREEN  # noqa: E402
from engine_spec import Fixture as EngineFixture  # noqa: E402
from qd import engine, schemas, server, verdict  # noqa: E402
from qd.core import prompt as prompt_module  # noqa: E402

# The stamp qd/verdict.py:570 writes above a validated payload. Pinned
# end-to-end through a real render at specs/engine_spec.py:2364 and
# specs/queries_spec.py:210, so this literal is what a receipt really carries.
STAMP = "RESULT: valid (schema)"

# Distinctive on purpose: the session-id tests below assert this string reaches
# NO link's args by any key, which a plausible-looking "s-1" could pass by luck.
SESSION = "SESSID-DO-NOT-CARRY"


def fenced(obj):
    return "```json\n%s\n```" % json.dumps(obj)


def receipt(payload=None, *, stamped=True, handoff=True, echo=None,
            status="success"):
    """A receipt shaped like the one qd/verdict.py actually renders.

    Blocks joined by a blank line, STATUS first (`_receipt_status` reads line
    1), the stamped payload in the body, and -- when `echo` is given -- the
    worker's own reply below it, ending in the fence the worker was ASKED for.
    That last part is not decoration: it is the reason "the last json block in
    the receipt" is the wrong block to read.
    """
    parts = [f"STATUS: {status}", f"SESSION: {SESSION}", "ATTEMPTS: 1/3"]
    if handoff:
        parts += ["HANDOFF: built the thing", "FILES: a.py", "NEXT: nothing"]
    if payload is not None:
        if stamped:
            parts.append(STAMP)
        parts.append(fenced(payload))
    if echo is not None:
        parts += ["--- qwen result ---", "prose the worker wrote",
                  fenced(echo)]
    return "\n\n".join(parts) + "\n"


class Carrying(Fixture):
    """chain_spec's fixture (real guards, throwaway paths, a fake handler that
    records the args each link was given) plus two readers for the new surface.
    """

    def slot(self, link):
        """The declared slot on link `link`'s args, or None."""
        return self.seen[link - 1].get(engine.CARRIED_ARG)

    def block(self, out, link):
        """Link `link`'s rendered block, separator included."""
        rest = out.split(f"=== chain link {link}/", 1)[1]
        return rest.split("=== chain link", 1)[0]

    def carry_line(self, out, link):
        """The CARRY: line inside link `link`'s block, or None."""
        for line in self.block(out, link).splitlines():
            if line.startswith("CARRY:"):
                return line
        return None


class ADeclaredValueWithAKnownSet(unittest.TestCase):
    """The fix for the live bug: `carry` stops being an undeclared string.

    One constant holds the set, and the schema is asserted against it rather
    than beside it -- the same reason `wireformat_spec` pins the request
    constant and its parser together: two copies of a vocabulary drift, and the
    failure mode of THIS drift is a grade the wire accepts and the code does
    not recognise, which is the bug this task exists to close.
    """

    def prop(self):
        return schemas.TOOL["inputSchema"]["properties"]["carry"]

    def test_the_known_set_is_one_constant(self):
        self.assertEqual(tuple(server.CARRY_GRADES),
                         ("none", "handoff", "structured"))

    def test_the_schema_declares_exactly_that_set_and_no_more(self):
        self.assertEqual(list(self.prop()["enum"]), list(server.CARRY_GRADES))

    def test_session_is_not_a_value_the_schema_offers(self):
        # The enum says what EXISTS. `session` is refused by name on the wire
        # (below) for the caller who asks for it anyway -- but offering it in
        # the capability map that is ambient in every session would advertise
        # the one grade the design names "never".
        self.assertNotIn("session", self.prop()["enum"])

    def test_it_is_optional_so_every_call_that_works_today_still_works(self):
        # C9 additive evolution (qd/schemas.py:11-15): a new param lands only
        # with a spec proving its ABSENCE leaves behaviour identical.
        self.assertNotIn("carry", schemas.TOOL["inputSchema"].get("required", []))

    def test_the_field_documents_its_own_refusal(self):
        # House pattern, named at specs/setup_spec.py:288-295: a refusal is
        # documented in the FIELD's description, not left for the caller to
        # discover by triggering it (see trust, verify_timeout_sec,
        # preflight_expect, result_schema).
        desc = self.prop()["description"]
        self.assertIn("session", desc)
        self.assertIn("refus", desc.lower())

    def test_the_query_tool_gains_nothing(self):
        # A query has no links. The shapes are frozen per tool.
        self.assertNotIn("carry",
                         schemas.QUERY_TOOL["inputSchema"]["properties"])

    def test_the_default_grade_is_the_safe_one_not_the_cheap_one(self):
        # §10.3: "do not let it default to whatever is cheap." It is also what
        # every chain gets today, so C9 holds: absence changes nothing.
        self.assertEqual(server.CARRY_DEFAULT, "handoff")
        self.assertIn(server.CARRY_DEFAULT, server.CARRY_GRADES)

    def test_the_slot_is_reserved_and_not_a_wire_parameter(self):
        # Same convention as CHAIN_ARG/WT_ARG (qd/engine.py:81-107): only
        # run_chain may set it, so a hand-written call cannot claim to have
        # inherited a validated result from a link that never ran.
        self.assertTrue(engine.CARRIED_ARG.startswith("_"), engine.CARRIED_ARG)
        self.assertNotIn(engine.CARRIED_ARG,
                         schemas.TOOL["inputSchema"]["properties"])

    def test_the_stamp_is_a_shared_constant_not_a_literal_at_each_end(self):
        # The reader of the stamp and its writer must be the same string.
        # Two literals is how "structured silently stopped carrying" ships as
        # a receipt-wording change nobody connects to it.
        self.assertEqual(verdict.RESULT_VALID_LINE, STAMP)
        self.assertGreaterEqual(
            inspect.getsource(verdict).count("RESULT_VALID_LINE"), 2,
            "the constant is defined but qd/verdict.py still emits a literal")


class RefusingWhatIsNotBuilt(Carrying):
    """An unrecognised grade stops the chain BEFORE any link runs.

    Refused rather than defaulted, and refused up front rather than at the link
    that carries it: the answer is available from the call alone, so spending
    links 1..N-1 to discover that link N asked for something unbuildable buys
    nothing and cannot be undone. This is the shape `_shape_refusal`
    (qd/server.py:605-616) already uses for chain+batch -- "Nothing was run."
    """

    def test_an_unknown_grade_refuses_the_chain_and_runs_nothing(self):
        items = self.items(2)
        items[1]["carry"] = "banana"
        out = server.run_chain(items, self.handler(GREEN, GREEN))
        self.assertEqual(self.seen, [], "a link ran under a grade nobody built")
        self.assertTrue(out.startswith("STATUS: error"), out[:80])
        self.assertNotIn("chain link", out)

    def test_the_unknown_value_refusal_names_the_value_and_the_known_set(self):
        # A typo is fixed by seeing what the real names are. Naming the value
        # back matters too: "carry" appears once per link, and the caller has
        # to know WHICH one to correct.
        items = self.items(3)
        items[2]["carry"] = "structrued"
        out = server.run_chain(items, self.handler(GREEN, GREEN, GREEN))
        self.assertIn("structrued", out)
        self.assertIn("carry", out)
        for grade in server.CARRY_GRADES:
            self.assertIn(grade, out)
        self.assertIn("nothing was run", out.lower())

    def test_a_bad_grade_on_a_later_link_spends_no_earlier_link(self):
        # The whole point of refusing up front: link 3's typo must not cost
        # two delegations and a worktree first.
        items = self.items(3)
        items[2]["carry"] = "banana"
        server.run_chain(items, self.handler(GREEN, GREEN, GREEN))
        self.assertEqual(self.seen, [])

    def test_session_is_refused_as_not_built_carrying_the_designs_reason(self):
        # NOT an alias, which is what it is today. The refusal has to say what
        # was declined and why, because the caller asked for it on purpose --
        # and §10.3's reason (isolation is what it removes) is the only thing
        # that makes the refusal reviewable rather than arbitrary.
        items = self.items(2)
        items[1]["carry"] = "session"
        out = server.run_chain(items, self.handler(GREEN, GREEN))
        self.assertEqual(self.seen, [])
        self.assertTrue(out.startswith("STATUS: refused"), out[:80])
        self.assertIn("session", out)
        low = out.lower()
        self.assertIn("not built", low)
        self.assertIn("isolation", low)
        self.assertIn("nothing was run", low)

    def test_the_two_refusals_are_not_one_message_wearing_two_hats(self):
        # An unknown value is a TYPO; `session` is a deliberate ask for
        # something this server declines to build. Same text for both teaches
        # the typist about isolation and tells the person who asked for
        # `session` that they misspelled something. The distinction is also
        # machine-readable in the status: `error` is the shape-mistake status
        # every other argument refusal here uses (qd/server.py:612,
        # qd/server.py:668), `refused` is the status this server uses when it
        # understood the ask and will not proceed.
        bad, sess = self.items(2), self.items(2)
        bad[1]["carry"] = "banana"
        sess[1]["carry"] = "session"
        out_bad = server.run_chain(bad, self.handler(GREEN, GREEN))
        out_sess = server.run_chain(sess, self.handler(GREEN, GREEN))
        self.assertNotEqual(out_bad, out_sess)
        self.assertNotIn("isolation", out_bad.lower(),
                         "the typo refusal explains a grade nobody asked for")

    def test_the_whole_call_refuses_not_only_the_link(self):
        # Through the real routing seam, with the engine spied: a refusal that
        # only reached run_chain's return value while engine.run had already
        # been called would be the same silent-success this task is closing.
        calls = []
        real = engine.run
        engine.run = lambda a: calls.append(a) or GREEN
        try:
            out = server.run_delegate_batch(
                {"cwd": self.cwd, "chain": [{"task": "a"},
                                            {"task": "b", "carry": "session"}]})
        finally:
            engine.run = real
        self.assertEqual(calls, [])
        self.assertIn("session", out)


class TheDefaultAndTheOptOut(Carrying):
    """C9: absence behaves exactly as it does today, and `none` still opts out.

    Every assertion in specs/chain_spec.py's HandoffForwarding class must
    survive this change unchanged -- they are the pinned behaviour of the grade
    that already works.
    """

    def test_absence_still_means_the_handoff_preamble(self):
        server.run_chain(self.items(2), self.handler(receipt(), GREEN))
        self.assertIn("HANDOFF: built the thing", self.seen[1]["task"])
        self.assertTrue(self.seen[1]["task"].endswith("step 2"))
        self.assertIsNone(self.slot(2), "the default grade filled the slot")

    def test_handoff_asked_for_by_name_is_the_same_thing(self):
        items = self.items(2)
        items[1]["carry"] = "handoff"
        server.run_chain(items, self.handler(receipt(payload={"a": 1}), GREEN))
        self.assertIn("HANDOFF: built the thing", self.seen[1]["task"])
        # Exclusive: asking for the preamble does not also ship the payload.
        self.assertIsNone(self.slot(2))

    def test_carry_none_opts_out_of_the_slot_as_well_as_the_task(self):
        # specs/chain_spec.py:469 pins the task half. Once there is a second
        # channel, "opts out" has to mean both or `none` stops meaning nothing.
        items = self.items(2)
        items[1]["carry"] = "none"
        server.run_chain(items, self.handler(receipt(payload={"a": 1}), GREEN))
        self.assertEqual(self.seen[1]["task"], "step 2")
        self.assertIsNone(self.slot(2))

    def test_a_call_level_grade_reaches_the_links(self):
        # `carry` is declared as a top-level property because that is where the
        # schema declares the item vocabulary (task, verify, cwd all sit
        # there). A caller who therefore sends it at the call and has it
        # silently do nothing has met this task's own bug in a new place.
        calls = []
        real = engine.run
        engine.run = lambda a: calls.append(a) or GREEN
        try:
            server.run_delegate_batch(
                {"cwd": self.cwd, "carry": "none",
                 "chain": [{"task": "a"}, {"task": "b"}]})
        finally:
            engine.run = real
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["carry"], "none")


class StructuredCarriesTheValidatedPayload(Carrying):
    """The grade this task builds: link N's checked JSON reaches link N+1.

    Today `carry: "structured"` gets the handoff preamble and a success
    receipt. Every test here fails on TODAY's code with the payload absent and
    the preamble present, which is the defect stated as an assertion.
    """

    def test_the_payload_arrives_in_the_declared_slot(self):
        items = self.items(2)
        items[1]["carry"] = "structured"
        server.run_chain(items, self.handler(receipt(payload={"port": 8080}),
                                             GREEN))
        slot = self.slot(2)
        self.assertIsInstance(slot, dict, "no declared slot on link 2")
        self.assertEqual(slot["grade"], "structured")
        self.assertEqual(slot["from"], 1)
        # Verbatim, as the worker wrote it and as the receipt keeps it
        # (qd/jsonschema.py:229-236 returns the raw text beside the value for
        # exactly this reason): a caller asked for a machine-read result and
        # gets the bytes, not this server's idea of how to format them.
        self.assertIsInstance(slot["json"], str)
        self.assertEqual(json.loads(slot["json"]), {"port": 8080})

    def test_the_payload_is_not_glued_onto_the_task(self):
        # §10.2 asks for "a declared slot". JSON prepended as prose is the
        # handoff with more tokens and no type -- and it is what today's code
        # does with every value that is not "none".
        items = self.items(2)
        items[1]["carry"] = "structured"
        server.run_chain(items, self.handler(receipt(payload={"port": 8080}),
                                             GREEN))
        self.assertEqual(self.seen[1]["task"], "step 2")

    def test_the_grades_are_exclusive_so_no_preamble_rides_along(self):
        # The caller asked for a typed result. Sending the four lines as well
        # would mean no `carry` value can ever mean "only the payload", and the
        # tokens the type was supposed to save are spent anyway.
        items = self.items(2)
        items[1]["carry"] = "structured"
        server.run_chain(items, self.handler(receipt(payload={"a": 1}), GREEN))
        self.assertNotIn("HANDOFF:", self.seen[1]["task"])

    def test_the_stamped_block_is_carried_not_the_last_one_in_the_receipt(self):
        # THE discriminating test for the holder route. A receipt ends with the
        # worker's own echoed reply, which ends in the fence it was asked for
        # -- and that tail is capped (RESULT_CAP, qd/verdict.py:17) while the
        # stamped body block is not (qd/verdict.py:565-571). `last_json_block`
        # over the whole receipt reads the truncatable copy, and on a long
        # reply it reads a mangled one. The stamp is the only place the server
        # says "I checked this".
        items = self.items(2)
        items[1]["carry"] = "structured"
        server.run_chain(
            items,
            self.handler(receipt(payload={"answer": "validated"},
                                 echo={"answer": "echoed tail"}), GREEN))
        self.assertEqual(json.loads(self.slot(2)["json"]),
                         {"answer": "validated"})

    def test_an_unstamped_block_is_not_a_validated_result(self):
        # §10.3 says "result_schema JSON, validated". A fenced block in a reply
        # that carried no result_schema was never checked by anything; carrying
        # it under the same name would put this server's stamp on a value it
        # never read.
        items = self.items(2)
        items[1]["carry"] = "structured"
        server.run_chain(items,
                         self.handler(receipt(payload={"a": 1}, stamped=False),
                                      GREEN))
        self.assertIsNone(self.slot(2))

    def test_nothing_to_carry_does_not_fall_back_to_the_handoff(self):
        # The bug in its purest form. Link 1 produced no validated result;
        # substituting the preamble would hand link 2 a different evidence
        # model than the caller declared, and report success.
        items = self.items(2)
        items[1]["carry"] = "structured"
        server.run_chain(items, self.handler(receipt(), GREEN))
        self.assertIsNone(self.slot(2))
        self.assertEqual(self.seen[1]["task"], "step 2")

    def test_only_the_previous_links_result_is_carried(self):
        # One hop, the property `carried` has today by being overwritten each
        # green link (qd/server.py:495, pinned specs/chain_spec.py:458-467). A
        # chain of eight would otherwise accumulate eight payloads, oldest and
        # least relevant first, in a slot whose whole value is that it is
        # typed and small.
        items = self.items(3)
        for item in items[1:]:
            item["carry"] = "structured"
        server.run_chain(items, self.handler(receipt(payload={"n": 1}),
                                             receipt(payload={"n": 2}), GREEN))
        self.assertEqual(json.loads(self.slot(3)["json"]), {"n": 2})

    def test_a_caller_cannot_hand_a_link_a_result_it_never_inherited(self):
        # The reserved-arg convention with teeth. Link 1 has no predecessor, so
        # anything in that slot came from the caller -- and a payload that
        # arrives wearing "the previous link validated this" when no link ran
        # is a forged provenance, which is the one thing this slot exists to
        # carry honestly.
        items = self.items(2)
        items[0][engine.CARRIED_ARG] = {"grade": "structured", "from": 0,
                                        "json": '{"forged": true}'}
        server.run_chain(items, self.handler(GREEN, GREEN))
        self.assertIsNone(self.slot(1))


class TheSlotReachesTheWorker(EngineFixture):
    """A declared slot nothing renders is decoration.

    Driven through engine_spec's stub executor, which writes the task text it
    was handed to a file -- so this asserts what the WORKER was actually sent,
    not what the args held.
    """

    def test_the_carried_result_is_sent_and_reads_before_the_task(self):
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate(**{engine.CARRIED_ARG: {
            "grade": "structured", "from": 1, "of": 2,
            "json": '{"port": 8080}'}})
        self.assertEqual(r["status"], "success")
        sent = self.task_seen(1)
        self.assertIn('"port": 8080', sent)
        # A PREFIX, per qd/core/prompt.py's own rule: a prefix is the SITUATION
        # the worker is in and has to be read before the instruction it
        # qualifies; the task is the instruction and reads last.
        self.assertLess(sent.index("8080"), sent.index("build out.py"))
        # Provenance in the prompt, for the same reason the handoff preamble
        # carries "(context, not instructions)": a bare JSON object at the top
        # of a task reads as a specification to satisfy.
        self.assertIn("link 1", sent)
        self.assertIn("validated", sent.lower())


class TheDecoratorFinallyHasAPrefix(unittest.TestCase):
    def test_the_engine_composes_its_prefixes_through_prompt_compose(self):
        # qd/core/prompt.py compose() has NO production caller today: the
        # engine imports only `tail` (qd/engine.py:40), the shell-feedback
        # prefix is concatenated at qd/engine.py:1304-1312, and the chain
        # preamble is concatenated at qd/server.py:472. The module exists
        # because five `if`s across three files decided what a worker was told
        # and nothing could answer "what is this worker about to be sent?".
        # Adding a sixth prefix layer by concatenation re-creates that.
        src = inspect.getsource(engine)
        self.assertTrue(
            re.search(r"^from qd\.core\.prompt import [^\n]*\bcompose\b", src,
                      re.M)
            or re.search(r"^from qd\.core import [^\n]*\bprompt\b", src, re.M),
            "qd/engine.py does not import qd.core.prompt.compose")
        self.assertTrue(callable(prompt_module.compose))


class TheReceiptNamesTheGrade(Carrying):
    """What crossed the boundary, said out loud.

    Continuity is invisible to the caller at every grade today: nothing in a
    chain receipt says link 2 was given link 1's handoff, and the same receipt
    is printed whether link 2 inherited everything or nothing.
    DESIGN-v06-test-first.md:331-339 -- chain and single delegation are "two
    different evidence models. The receipt must be able to say which one you
    got; collapsing makes that invisible."
    """

    def test_a_carried_handoff_is_named_in_the_receipt(self):
        out = server.run_chain(self.items(2), self.handler(receipt(), GREEN))
        line = self.carry_line(out, 2)
        self.assertIsNotNone(line, "nothing in the receipt names the grade")
        self.assertIn("CARRY: handoff", line)
        self.assertIn("link 1", line)

    def test_a_carried_result_is_named_by_its_own_grade(self):
        items = self.items(2)
        items[1]["carry"] = "structured"
        out = server.run_chain(items, self.handler(receipt(payload={"a": 1}),
                                                   GREEN))
        line = self.carry_line(out, 2)
        self.assertIsNotNone(line, "nothing in the receipt names the grade")
        self.assertIn("CARRY: structured", line)
        self.assertNotIn("nothing", line.lower())

    def test_a_grade_that_carried_nothing_says_nothing_was_carried(self):
        # The case a caller most needs to see and today cannot: they declared
        # structured, the receipt says success, and link 2 ran on its task
        # alone. Silence here is what makes the substitution invisible.
        items = self.items(2)
        items[1]["carry"] = "structured"
        out = server.run_chain(items, self.handler(receipt(), GREEN))
        line = self.carry_line(out, 2)
        self.assertIsNotNone(line)
        self.assertIn("CARRY: structured", line)
        self.assertIn("nothing", line.lower())

    def test_an_opt_out_is_visible_too(self):
        items = self.items(2)
        items[1]["carry"] = "none"
        out = server.run_chain(items, self.handler(receipt(), GREEN))
        line = self.carry_line(out, 2)
        self.assertIsNotNone(line, "an opt-out that nothing reports")
        self.assertIn("CARRY: none", line)

    def test_the_line_describes_the_input_so_it_reads_before_the_receipt(self):
        # It is run_chain's statement about what it handed the link, not part
        # of the receipt the engine wrote -- so it goes between the separator
        # and the receipt, and STATUS stays the receipt's first line.
        out = server.run_chain(self.items(2), self.handler(receipt(), GREEN))
        block = self.block(out, 2)
        self.assertIn("CARRY:", block)
        self.assertLess(block.index("CARRY:"), block.index("STATUS:"))

    def test_link_one_never_gets_one(self):
        # Nothing preceded it. A "CARRY: none" on link 1 would report a
        # decision nobody made.
        out = server.run_chain(self.items(2), self.handler(receipt(), GREEN))
        self.assertIsNone(self.carry_line(out, 1))

    def test_a_chain_with_nothing_to_say_says_nothing(self):
        # Byte-for-byte the output specs/async_spec.py:595-599 pins. A line
        # reporting "the default grade found nothing to carry" on every link of
        # every chain is noise, and it would break a sibling gate to add it.
        out = server.run_chain([{"task": "a"}, {"task": "b"}],
                               lambda args: GREEN)
        self.assertEqual(out, "=== chain link 1/2: success ===\n" + GREEN
                         + "\n\n=== chain link 2/2: success ===\n" + GREEN)


class NoSessionCrossesALinkBoundary(Carrying):
    """The grade this gate refuses, pinned as an absence.

    `qd/server.py` holds zero session_id references, so a chain link cannot
    reach a session today -- by accident, not by decision. The id is two lines
    away (`SESSION:` is receipt line 2, `qd/verdict.py:248`) and the executor
    already accepts it (`profiles.py:234-251` substitutes {resume}), so the
    most dangerous grade in §10.3's table is also the cheapest to ship. This is
    the test that turns an accident into a decision, and it is the one that
    fails the day someone adds those ten lines.

    Why it matters more than it looks: `session` breaks nothing a check can
    point at. revert_specs still reverts, the between-link commit still
    freezes, the receipt still says "chain link 2/2", the status is still
    success. What it removes is the PROVENANCE of link N+1's knowledge -- and
    no fact, detector, guard or gate in this repo observes provenance.
    """

    def test_no_link_is_given_a_session_id(self):
        items = self.items(3)
        for item in items[1:]:
            item["carry"] = "structured"
        server.run_chain(items, self.handler(receipt(payload={"n": 1}),
                                             receipt(payload={"n": 2}), GREEN))
        self.assertEqual(len(self.seen), 3)
        for args in self.seen:
            self.assertNotIn("session_id", args)

    def test_no_links_args_carry_the_previous_links_session_by_any_key(self):
        # Not just `session_id`: a resume smuggled in under another name, or
        # scraped off the receipt into the task text, is the same run sharing
        # the same conversation. Every receipt here reports the same
        # distinctive id, so any route that copies it shows up as this string.
        server.run_chain(self.items(3),
                         self.handler(receipt(), receipt(), GREEN))
        for k, args in enumerate(self.seen, 1):
            blob = json.dumps(args, default=str)
            self.assertNotIn(SESSION, blob, f"link {k} was handed a session id")


if __name__ == "__main__":
    unittest.main(verbosity=1)
