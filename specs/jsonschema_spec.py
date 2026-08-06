#!/usr/bin/env python3
"""
Spec for qd/jsonschema.py -- the result contract (U5.1, C9).

Claude-authored gate (never delegate this file -- it defines what correct means).

This is what stands between "the worker said something" and "the caller can
read a value out of it". Three claims carry the feature:

  1. The SUBSET is honest about what it enforces: type, required, properties,
     items, enum, recursively. Anything else in the schema is ignored, never
     silently "passed" -- a caller reading these cases knows exactly what its
     schema buys.
  2. Every violation names its PATH and what arrived. The strings are fed back
     to the worker verbatim as the retry instruction, and "invalid" is not
     something a worker can act on.
  3. The block read is the LAST fenced json block in the reply, and its RAW
     text survives: a worker that shows its working ends with the real answer,
     and the receipt hands back the bytes the worker wrote.
  4. A constraint this module cannot enforce is refused where the schema is
     ACCEPTED, not here. `validate()` skipping `minimum` is deliberate and
     stays; what is not survivable is the CALL going ahead on a promise the
     gate will never check, because `schema_suffix` pastes the whole schema
     into the worker's prompt -- so the worker usually obeys `minimum: 5` and
     the constraint LOOKS enforced right up until the model stops complying,
     at which point a violating payload is reported as conforming and the
     builder's word is the only thing standing (PRINCIPLES §I).

Python-specific trap pinned below: `True == 1` and `isinstance(True, int)`, so
an unordered type check reports booleans as integers and lets `true` satisfy a
schema demanding a count.

Public surface pinned here:
    qd.jsonschema.validate(value, schema, path="$") -> [str]
    qd.jsonschema.schema_refusal(schema) -> str | None
    qd.jsonschema.last_json_block(text) -> (value, raw, error)
    qd.jsonschema.schema_suffix(schema) -> str
    qd.jsonschema.kind(value) -> str

Run:  python3 specs/jsonschema_spec.py
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import jsonschema  # noqa: E402


OBJ = {"type": "object",
       "required": ["name", "count"],
       "properties": {"name": {"type": "string"},
                      "count": {"type": "integer"},
                      "tags": {"type": "array", "items": {"type": "string"}},
                      "level": {"enum": ["low", "high"]}}}

# (name, schema, value, n_errors, substring one error must contain)
TABLE = [
    # --- types -------------------------------------------------------
    ("string ok", {"type": "string"}, "x", 0, ""),
    ("string got int", {"type": "string"}, 3, 1, "expected string, got integer"),
    ("integer ok", {"type": "integer"}, 3, 0, ""),
    ("integer got float", {"type": "integer"}, 3.5, 1, "got number"),
    ("integer got bool", {"type": "integer"}, True, 1, "got boolean"),
    ("number takes an int", {"type": "number"}, 3, 0, ""),
    ("number takes a float", {"type": "number"}, 3.5, 0, ""),
    ("boolean ok", {"type": "boolean"}, False, 0, ""),
    ("boolean got int", {"type": "boolean"}, 0, 1, "expected boolean"),
    ("null ok", {"type": "null"}, None, 0, ""),
    ("null got string", {"type": "null"}, "", 1, "expected null"),
    ("array ok", {"type": "array"}, [], 0, ""),
    ("object got array", {"type": "object"}, [], 1, "expected object, got array"),
    ("a union of types", {"type": ["string", "null"]}, None, 0, ""),
    ("a union that matches neither", {"type": ["string", "null"]}, 1, 1,
     "expected string or null"),
    # --- required ----------------------------------------------------
    ("all present", OBJ, {"name": "a", "count": 1}, 0, ""),
    ("one missing", OBJ, {"name": "a"}, 1, "$.count: required property"),
    ("both missing", OBJ, {}, 2, "$.name: required property"),
    ("present but null still counts as present", OBJ,
     {"name": "a", "count": None}, 1, "$.count: expected integer, got null"),
    # --- properties (recursion) --------------------------------------
    ("nested type error names its path", OBJ,
     {"name": 1, "count": 1}, 1, "$.name: expected string"),
    ("unknown properties are allowed", OBJ,
     {"name": "a", "count": 1, "extra": {}}, 0, ""),
    # --- items -------------------------------------------------------
    ("items ok", OBJ, {"name": "a", "count": 1, "tags": ["x", "y"]}, 0, ""),
    ("an item names its index", OBJ,
     {"name": "a", "count": 1, "tags": ["x", 2]}, 1,
     "$.tags[1]: expected string"),
    ("every bad item is reported", OBJ,
     {"name": "a", "count": 1, "tags": [1, 2]}, 2, "$.tags[0]"),
    # --- enum --------------------------------------------------------
    ("enum ok", OBJ, {"name": "a", "count": 1, "level": "low"}, 0, ""),
    ("enum violated", OBJ, {"name": "a", "count": 1, "level": "mid"}, 1,
     "is not one of"),
    ("enum does not accept a look-alike", {"enum": [1, 2]}, True, 1,
     "is not one of"),
    # --- deep nesting ------------------------------------------------
    ("two levels down", {"type": "object", "properties": {
        "a": {"type": "object", "properties": {"b": {"type": "integer"}}}}},
     {"a": {"b": "no"}}, 1, "$.a.b: expected integer"),
    ("array of objects", {"type": "array", "items": {
        "type": "object", "required": ["id"]}}, [{"id": 1}, {}], 1,
     "$[1].id: required property"),
]

# Every constraint keyword `validate()` walks straight past, with a value a
# caller would plausibly write. One entry per keyword rather than a count,
# because a keyword that stops being reported has to fail BY NAME -- a check
# that quietly narrowed is the same silent-abstain failure being closed here.
OUT_OF_SUBSET = [
    # numeric
    ("minimum", 5), ("maximum", 99), ("exclusiveMinimum", 0),
    ("exclusiveMaximum", 10), ("multipleOf", 2),
    # string
    ("minLength", 1), ("maxLength", 80), ("pattern", "^[a-z]+$"),
    ("format", "email"),
    # object
    ("additionalProperties", False), ("patternProperties", {"^x": {}}),
    ("propertyNames", {"type": "string"}), ("minProperties", 1),
    ("maxProperties", 4), ("dependencies", {"a": ["b"]}),
    ("dependentRequired", {"a": ["b"]}),
    # array
    ("minItems", 1), ("maxItems", 10), ("uniqueItems", True),
    ("contains", {"type": "string"}), ("additionalItems", False),
    # combinators
    ("oneOf", [{"type": "string"}]), ("anyOf", [{"type": "string"}]),
    ("allOf", [{"type": "string"}]), ("not", {"type": "string"}),
    # value
    ("const", 3),
    # references -- the whole subschema lives somewhere this module never reads
    ("$ref", "#/$defs/row"), ("$defs", {"row": {"type": "object"}}),
    ("definitions", {"row": {"type": "object"}}),
]


class Table(unittest.TestCase):
    """One case per rule, accept and reject."""

    def test_the_table(self):
        for name, schema, value, n, substring in TABLE:
            with self.subTest(case=name):
                errors = jsonschema.validate(value, schema)
                self.assertEqual(len(errors), n, f"{name}: {errors}")
                if substring:
                    self.assertTrue(any(substring in e for e in errors),
                                    f"{name}: {errors}")


class ErrorsAreActionable(unittest.TestCase):
    """Claim 2: these strings ARE the retry instruction."""

    def test_every_error_starts_with_its_path(self):
        errors = jsonschema.validate({"name": 1, "tags": [2]}, OBJ)
        self.assertTrue(errors)
        for e in errors:
            self.assertTrue(e.startswith("$"), e)

    def test_a_wrong_type_stops_the_descent(self):
        # Nested rules against the wrong type produce noise, not findings.
        errors = jsonschema.validate("not an object", OBJ)
        self.assertEqual(len(errors), 1)
        self.assertIn("expected object, got string", errors[0])


class UnreadableSchemas(unittest.TestCase):
    """The schema is the caller's. An unusable one constrains nothing here --
    refusing the run over it is a decision for the call, not the validator."""

    def test_a_non_object_schema_constrains_nothing(self):
        self.assertEqual(jsonschema.validate({"a": 1}, "nonsense"), [])
        self.assertEqual(jsonschema.validate({"a": 1}, None), [])

    def test_an_empty_schema_accepts_anything(self):
        for value in ({}, [], "", 0, None, True):
            self.assertEqual(jsonschema.validate(value, {}), [])

    def test_unsupported_keywords_are_ignored_not_enforced(self):
        # minLength is outside the subset: it must not fail, and must not be
        # reported as satisfied either -- the docstring is the contract.
        self.assertEqual(
            jsonschema.validate("", {"type": "string", "minLength": 5}), [])


class TheAcceptCheck(unittest.TestCase):
    """Claim 4: `schema_refusal(schema)` -> refusal text, or None to proceed.

    The one function BOTH call sites use -- `_preconditions` for qwen_delegate
    and `run_query` for qwen_query -- so the keyword list cannot drift between
    two surfaces that share nothing else. `qwen_query` never enters engine.py;
    a check living at one of them is this repo's signature bug, covered on the
    path someone thought about and silently open on the other.

    Returning TEXT rather than a keyword list is the same decision: the two
    surfaces would otherwise word the same refusal differently, and a caller
    reading receipts from both would have to learn two vocabularies for one
    fact.

    None means "nothing here this server cannot check" -- NOT "this schema is
    good". A schema it cannot read at all is somebody else's problem (see
    UnreadableSchemas above, and the two live call-site pins named in
    test_a_non_dict_schema_is_not_a_refusal).
    """

    def test_a_schema_inside_the_subset_is_accepted_silently(self):
        # The feature is not being withdrawn: everything validate() actually
        # enforces has to keep flowing through untouched.
        for schema in (OBJ, {}, {"type": "string"}, {"enum": [1, 2]},
                       {"type": ["string", "null"]},
                       {"type": "array", "items": {"type": "string"}},
                       {"type": "object", "required": ["a"],
                        "properties": {"a": {"type": "integer"}}}):
            self.assertIsNone(jsonschema.schema_refusal(schema), repr(schema))

    def test_metadata_that_constrains_nothing_is_accepted(self):
        # None of these changes which payloads conform, so ignoring them
        # costs the caller nothing and refusing them would cost a run. $schema
        # and $id name the dialect and the document -- inert, and the most
        # common boilerplate in a hand-written schema. ($ref is NOT in this
        # company: it moves the rules somewhere this module never reads.)
        self.assertIsNone(jsonschema.schema_refusal(
            {"type": "object", "title": "Result",
             "description": "what to hand back", "default": {},
             "examples": [{"a": 1}], "$comment": "for the reader",
             "$schema": "https://json-schema.org/draft/2020-12/schema",
             "$id": "urn:qd:result"}))

    def test_every_unenforced_constraint_keyword_is_reported_by_name(self):
        for keyword, value in OUT_OF_SUBSET:
            with self.subTest(keyword=keyword):
                text = jsonschema.schema_refusal({"type": "object",
                                                  keyword: value})
                self.assertIsNotNone(text, f"{keyword} was accepted")
                self.assertIn(keyword, text)

    def test_a_keyword_this_module_has_never_heard_of_is_reported_too(self):
        # The check is an ALLOWLIST of what is honoured, not a denylist of
        # known-bad keywords, and the difference is the whole defect class.
        # `if`/`prefixItems`/`minContains` are real constraints absent from the
        # table above; a misspelled `minumum` is a constraint the CALLER
        # believes is enforced. A denylist accepts every one of them and
        # rebuilds the silent abstention this check exists to close.
        for keyword, value in (("if", {"type": "string"}),
                               ("then", {"type": "string"}),
                               ("prefixItems", [{"type": "string"}]),
                               ("unevaluatedProperties", False),
                               ("minContains", 1),
                               ("minumum", 5)):
            with self.subTest(keyword=keyword):
                text = jsonschema.schema_refusal({"type": "object",
                                                  keyword: value})
                self.assertIsNotNone(text, f"{keyword} was accepted")
                self.assertIn(keyword, text)

    def test_the_live_defect_a_minimum_under_properties(self):
        # The payload that started this: validate({"n": 1}, this) == [], i.e.
        # reported to the caller as CONFORMING. Checking only the top level
        # would be a fix that looks like success and leaves this case exactly
        # as it was.
        text = jsonschema.schema_refusal(
            {"type": "object",
             "properties": {"n": {"type": "integer", "minimum": 5}}})
        self.assertIsNotNone(text)
        self.assertIn("minimum", text)

    def test_a_keyword_under_items_is_reported(self):
        text = jsonschema.schema_refusal(
            {"type": "array", "items": {"type": "string", "format": "email"}})
        self.assertIsNotNone(text)
        self.assertIn("format", text)

    def test_the_descent_does_not_stop_one_level_down(self):
        # validate() recurses without limit through properties and items, so a
        # check that stops shallower disagrees with the thing it is guarding.
        text = jsonschema.schema_refusal({"type": "object", "properties": {
            "rows": {"type": "array", "items": {
                "type": "object", "properties": {
                    "email": {"type": "string", "pattern": "@"}}}}}})
        self.assertIsNotNone(text)
        self.assertIn("pattern", text)

    def test_a_tuple_form_items_is_refused_and_its_elements_are_descended(self):
        # draft-07 tuple validation. validate()'s items branch is gated on
        # isinstance(schema.get("items"), dict) (:117), so a LIST there skips
        # that whole block -- not one element is ever checked, no matter what
        # the elements say. Reviewer-verified repro before this fix:
        # schema_refusal(this) was None and validate([1, 2, 3], this) was []
        # -- the exact defect this module exists to close, narrower trigger.
        text = jsonschema.schema_refusal(
            {"type": "array",
             "items": [{"type": "string"}, {"type": "integer", "minimum": 5}]})
        self.assertIsNotNone(text)
        self.assertIn("items", text)
        # The keyword buried at tuple position 1 is named by ITS OWN name,
        # not folded away into a single generic "items" finding.
        self.assertIn("minimum", text)

    def test_an_all_in_subset_tuple_is_still_refused(self):
        # The tuple FORM is unenforced regardless of what is inside it --
        # validate() never even reaches element 0 to check it -- so this
        # must refuse even though every element, read alone, is in-subset.
        text = jsonschema.schema_refusal(
            {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]})
        self.assertIsNotNone(text)
        self.assertIn("items", text)

    def test_a_boolean_subschema_is_refused_not_silently_permissive(self):
        # `true`/`false` are real JSON Schema, not typos -- `false` means
        # "nothing satisfies this", the opposite of unconstrained -- but
        # validate() only walks DICT schemas, so both were silently treated
        # as "no constraint here" like genuine nonsense
        # (test_an_unreadable_SUBSCHEMA_is_not_a_refusal_either, below) even
        # though a boolean is a meaningful answer and "nonsense" is not.
        for literal in (False, True):
            with self.subTest(literal=literal):
                text = jsonschema.schema_refusal(
                    {"type": "object", "properties": {"a": literal}})
                self.assertIsNotNone(text, repr(literal))
                self.assertIn(json.dumps(literal), text)

    def test_the_refusal_carries_the_path_to_each_offender(self):
        # Minor but cheap once the recursion already carries a path: a
        # refusal naming only the keyword sends the caller back to eyeball
        # its own schema for every occurrence, the same gap tiers.py's
        # refusal closes by naming the question AND what to paste.
        text = jsonschema.schema_refusal(
            {"type": "object",
             "properties": {"n": {"type": "integer", "minimum": 5}}})
        self.assertIn("$.n.minimum", text)

    def test_a_property_NAMED_like_a_keyword_is_not_one(self):
        # `properties` maps FIELD NAMES to subschemas. A result object with a
        # field called `pattern` or `const` is ordinary, and refusing it would
        # be a false refusal on a schema entirely inside the subset -- the
        # cheapest way to make this check hated and switched off.
        self.assertIsNone(jsonschema.schema_refusal(
            {"type": "object", "required": ["pattern"],
             "properties": {"pattern": {"type": "string"},
                            "const": {"type": "integer"},
                            "$ref": {"type": "string"},
                            "not": {"type": "boolean"}}}))

    def test_values_are_data_not_schema(self):
        # `required` holds field names; `enum`, `default` and `examples` hold
        # literal PAYLOAD values. A dict sitting in any of them is the shape of
        # an answer, not a place rules can hide.
        self.assertIsNone(jsonschema.schema_refusal(
            {"type": "object", "required": ["minimum", "format"]}))
        self.assertIsNone(jsonschema.schema_refusal(
            {"enum": [{"minimum": 1}, {"oneOf": "x"}]}))
        self.assertIsNone(jsonschema.schema_refusal(
            {"type": "object", "default": {"pattern": "x"},
             "examples": [{"multipleOf": 3}]}))

    def test_a_non_dict_schema_is_not_a_refusal(self):
        # MUST-PRESERVE, pinned live at two call sites:
        # specs/engine_spec.py test_a_malformed_schema_is_not_a_refusal and
        # specs/queries_spec.py test_a_malformed_schema_is_ignored_not_fatal.
        # An unreadable schema constrains nothing and always did; only a DICT
        # carrying out-of-subset keywords may stop a run.
        for schema in ("not a schema", None, ["a"], 7, True):
            self.assertIsNone(jsonschema.schema_refusal(schema), repr(schema))

    def test_an_unreadable_SUBSCHEMA_is_not_a_refusal_either(self):
        # Same rule one level down, for the same reason: validate() skips
        # these too, so there is no promise being broken.
        self.assertIsNone(jsonschema.schema_refusal(
            {"type": "object", "properties": {"a": "nonsense"}}))
        self.assertIsNone(jsonschema.schema_refusal(
            {"type": "array", "items": "nonsense"}))
        self.assertIsNone(jsonschema.schema_refusal({"properties": ["a"]}))


class TheRefusalIsActionable(unittest.TestCase):
    """A refusal nobody can act on is a dead end, not a gate.

    The precedent is qd/core/tiers.py, which refuses with the question AND the
    JSON to paste, and qd/jsonschema.py's own rule for validation errors --
    every one names its path and what arrived, because "invalid" alone is not
    something the reader can do anything with. This refusal is read by the
    orchestrator that wrote the schema, so it has to say which keyword, and
    what it may use instead.
    """

    def test_it_names_every_offending_keyword_not_just_the_first(self):
        # A caller that fixes one keyword and gets refused again for the next
        # pays a round trip per keyword to learn what one message could say.
        text = jsonschema.schema_refusal(
            {"type": "object", "additionalProperties": False,
             "properties": {"n": {"type": "integer", "minimum": 5},
                            "s": {"type": "string", "pattern": "^a"}}})
        for keyword in ("minimum", "pattern", "additionalProperties"):
            self.assertIn(keyword, text)

    def test_it_names_the_subset_the_caller_may_use_instead(self):
        # The whole fix has to be reachable from the refusal itself. Sending
        # the caller to read this module's source for the list of five is a
        # refusal that costs a file read to act on.
        text = jsonschema.schema_refusal({"type": "string", "minLength": 5})
        for supported in ("type", "enum", "required", "properties", "items"):
            self.assertIn(supported, text)

    def test_the_keywords_are_named_in_a_stable_order(self):
        # Sets of strings iterate in an order that varies BETWEEN processes
        # (PYTHONHASHSEED), so a set-ordered message makes two identical calls
        # produce receipts that differ in bytes -- and a diff that moves for no
        # reason is one nobody reads. Sorted, so it does not.
        text = jsonschema.schema_refusal(
            {"type": "object", "uniqueItems": True, "pattern": "^a",
             "properties": {"n": {"type": "integer", "minimum": 5}}})
        self.assertLess(text.index("minimum"), text.index("pattern"))
        self.assertLess(text.index("pattern"), text.index("uniqueItems"))


class Kinds(unittest.TestCase):
    def test_bool_is_never_an_integer(self):
        self.assertEqual(jsonschema.kind(True), "boolean")
        self.assertEqual(jsonschema.kind(1), "integer")
        self.assertEqual(jsonschema.kind(1.0), "number")


class LastBlock(unittest.TestCase):
    """Claim 3: which block, and in what form."""

    def test_the_last_block_wins(self):
        text = ("roughly:\n```json\n{\"a\": 1}\n```\n"
                "final answer:\n```json\n{\"a\": 2}\n```\n")
        value, raw, err = jsonschema.last_json_block(text)
        self.assertIsNone(err)
        self.assertEqual(value, {"a": 2})
        self.assertEqual(raw, '{"a": 2}')

    def test_the_raw_text_is_the_workers_own_bytes(self):
        text = '```json\n{\n  "a":   1\n}\n```'
        _, raw, _ = jsonschema.last_json_block(text)
        self.assertEqual(raw, '{\n  "a":   1\n}')

    def test_a_missing_block_says_so_in_one_sentence(self):
        value, raw, err = jsonschema.last_json_block("no block here")
        self.assertIsNone(value)
        self.assertIsNone(raw)
        self.assertIn("no ```json block", err)

    def test_an_unparseable_block_names_the_problem(self):
        value, raw, err = jsonschema.last_json_block(
            "```json\n{'a': 1,}\n```")
        self.assertIsNone(value)
        self.assertEqual(raw, "{'a': 1,}")
        self.assertIn("not valid JSON", err)

    def test_the_fence_tag_is_case_insensitive(self):
        value, _, err = jsonschema.last_json_block("```JSON\n[1]\n```")
        self.assertIsNone(err)
        self.assertEqual(value, [1])

    def test_an_untagged_fence_is_not_the_result_block(self):
        # A plain ``` block is a code sample; taking it would let any pasted
        # snippet stand in for the answer.
        _, _, err = jsonschema.last_json_block("```\n{\"a\": 1}\n```")
        self.assertIsNotNone(err)

    def test_empty_text_is_a_missing_block_not_a_crash(self):
        for text in ("", None):
            self.assertIsNotNone(jsonschema.last_json_block(text)[2])


class Suffix(unittest.TestCase):
    def test_the_suffix_carries_the_schema_the_worker_must_meet(self):
        suffix = jsonschema.schema_suffix(OBJ)
        self.assertIn("```json", suffix)
        self.assertIn('"required"', suffix)
        self.assertIn(json.dumps(OBJ, indent=2), suffix)

    def test_it_says_last_because_the_parser_reads_the_last_one(self):
        self.assertIn("LAST", jsonschema.schema_suffix({"type": "object"}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
