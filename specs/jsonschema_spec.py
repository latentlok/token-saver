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

Python-specific trap pinned below: `True == 1` and `isinstance(True, int)`, so
an unordered type check reports booleans as integers and lets `true` satisfy a
schema demanding a count.

Public surface pinned here:
    qd.jsonschema.validate(value, schema, path="$") -> [str]
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
