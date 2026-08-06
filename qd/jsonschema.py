"""The result contract: a tiny JSON-Schema subset, behavior frozen by
specs/jsonschema_spec.py.

A delegation's prose is not machine-readable and never was -- a caller that
needed a value out of a run parsed it back out of English, or asked again.
`result_schema` lets the caller state the shape it needs; this module is what
checks the answer, and the retry loop treats a violation like a red gate.

Deliberately a SUBSET -- type, required, properties, items, enum -- and
deliberately stdlib. What a local worker can reliably produce is a small
object, so the validator that polices it should be small enough to read in one
sitting; the alternative was a dependency, which this plugin does not have and
will not grow for a formatting check.

Two rules the error strings follow, because they are fed BACK to the worker as
the retry instruction: every one names its path (`$.items[0].name`), and every
one says what was expected against what arrived. "Invalid" alone is not
something a worker can act on.
"""

import json
import re

# The last fenced json block in a reply. LAST, not first: a worker that shows
# its working ("here is roughly what I mean: ```json ...```") ends with the
# real answer, and the first block is the sketch.
_BLOCK = re.compile(r"```[ \t]*json[ \t]*\r?\n(.*?)```",
                    re.DOTALL | re.IGNORECASE)

RESULT_SUFFIX = """

---
End your reply with a fenced ```json block -- the LAST thing in it, after any
prose and after the lines above -- holding your result in exactly this shape:

{schema}

It is read by a machine, not a person: no comments, no trailing commas, no
placeholder values. If you cannot fill a field honestly, say so in the prose
above rather than inventing one.
"""


def schema_suffix(schema):
    """The prompt suffix that asks for a conforming result block."""
    return RESULT_SUFFIX.format(schema=json.dumps(schema, indent=2))


def kind(value):
    """The schema type name for a parsed JSON value.

    `bool` is checked before `int` on purpose: in Python True IS 1, so an
    unordered check reports every boolean as an integer -- and a schema
    demanding an integer would accept `true`.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_ok(value, expected):
    got = kind(value)
    if expected == "number":
        # JSON Schema's "number" covers integers; the reverse is not true.
        return got in ("number", "integer")
    return got == expected


def validate(value, schema, path="$"):
    """Violations of `schema` in `value`, as caller-and-worker-readable lines.

    Empty list means conforming. A schema this module cannot read (not an
    object) constrains nothing rather than failing everything: the schema is
    the caller's, and refusing a run over it belongs at the call, not here.
    """
    if not isinstance(schema, dict):
        return []

    errors = []
    declared = schema.get("type")
    types = ([declared] if isinstance(declared, str)
             else [t for t in declared if isinstance(t, str)]
             if isinstance(declared, list) else [])
    if types and not any(_type_ok(value, t) for t in types):
        # Every nested rule below is about a value of the declared type, so
        # checking them against the wrong type produces noise, not findings.
        return [f"{path}: expected {' or '.join(types)}, got {kind(value)}"]

    enum = schema.get("enum")
    if isinstance(enum, list) and not any(
            v == value and kind(v) == kind(value) for v in enum):
        errors.append(f"{path}: {json.dumps(value)} is not one of "
                      f"{json.dumps(enum)}")

    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"{path}.{key}: required property is missing")
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in value:
                    errors += validate(value[key], sub, f"{path}.{key}")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")

    return errors


# The five keywords validate() enforces (:92-119), plus metadata that
# constrains no payload -- boilerplate a hand-written schema carries for free
# (title/description/default/examples/$comment/$schema/$id). ALLOWLIST, not a
# denylist: a denylist of "known-bad" keywords is the same silent-abstention
# bug this closes, because `if`, `prefixItems`, `minContains` and a typo like
# `minumum` are all real constraints nobody had listed as bad yet. $ref is
# deliberately absent from the metadata set -- it relocates the rules to a
# subschema this module never reads, which is the opposite of inert.
_ENFORCED = frozenset({"type", "enum", "required", "properties", "items"})
_METADATA = frozenset({"title", "description", "default", "examples",
                       "$comment", "$schema", "$id"})
_SAFE = _ENFORCED | _METADATA


def _offenders(schema, found):
    """Add every keyword outside `_SAFE` found in `schema`, recursively.

    Walks only `properties` and `items` -- the two keywords validate() (:107-
    119) itself descends through. Everything else a schema can hold (the
    field names in `required`, the literal payload values in `enum`/
    `default`/`examples`) is DATA, not a place a keyword can hide: recursing
    into it would refuse an ordinary result field named `pattern` or `const`
    (pinned by jsonschema_spec.py::test_a_property_NAMED_like_a_keyword_is_not_one).
    """
    if not isinstance(schema, dict):
        return
    found.update(key for key in schema if key not in _SAFE)
    props = schema.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            _offenders(sub, found)
    items = schema.get("items")
    if isinstance(items, dict):
        _offenders(items, found)


def schema_refusal(schema):
    """Refusal text for a `result_schema` this module cannot enforce, else None.

    The other half of Claim 4 (module docstring): `validate()` staying silent
    on `minimum` is deliberate, but the CALL going ahead on a promise that
    keyword will never be checked is not survivable, because `schema_suffix`
    pastes the schema into the worker's prompt -- the worker usually obeys
    `minimum: 5` anyway, so the constraint looks enforced right up until the
    model stops complying, and a violating payload comes back reported as
    conforming.

    None means "nothing here this server cannot check", not "this schema is
    good" -- a schema this module cannot even READ (not a dict) constrains
    nothing and stays non-fatal, same as validate() itself: only a DICT
    carrying a keyword outside the enforced five can stop a call, because
    that is the one case where a check was promised and would never run.
    """
    if not isinstance(schema, dict):
        return None
    found = set()
    _offenders(schema, found)
    if not found:
        return None
    offending = ", ".join(sorted(found))
    supported = ", ".join(sorted(_ENFORCED))
    return (
        f"result_schema uses keyword(s) this server does not enforce: "
        f"{offending}. Only {supported} are checked -- a schema built "
        f"entirely from that subset is honoured, so rewrite the schema "
        f"within it, or drop result_schema."
    )


def last_json_block(text):
    """(value, raw_block_text, error) for the last fenced json block in a reply.

    `error` is a sentence the worker can act on, not a parser trace. The raw
    text is returned beside the parsed value because the receipt keeps the
    block VERBATIM: a caller that asked for a machine-read result gets the
    bytes the worker wrote, not this module's idea of how to format them.
    """
    blocks = _BLOCK.findall(text or "")
    if not blocks:
        return None, None, ("no ```json block found at the end of the reply -- "
                            "the result block is required")
    raw = blocks[-1].strip()
    try:
        return json.loads(raw), raw, None
    except ValueError as e:
        return None, raw, (f"the final ```json block is not valid JSON ({e}) "
                           f"-- send one JSON value, nothing else, inside the "
                           f"fence")
