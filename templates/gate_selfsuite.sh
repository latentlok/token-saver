#!/bin/bash
# SUPERSEDED (R3): pass trust="self" instead — the server now generates and
# guards this gate itself. Kept only for standalone use outside the server.
#
# L5 gate: runs the DELEGATE'S OWN test suite. The only architect-side guard is
# mechanical — the suite must actually run and be non-vacuous. The grading itself
# is the delegate's (L5 trust).
#
# Usage: copy into the project root as <name>_spec.sh (the spec glob auto-protects
# it from delegate edits), chmod +x, pass as `verify`. Tune the two vars.
#
# MIN_TESTS guards the vacuous pass: `unittest discover` exits 0 on an EMPTY suite
# (Python <3.12; newer exits 5 — guard both). For pytest-based projects swap
# SUITE_CMD and the "Ran N tests" pattern (pytest prints "N passed").
MIN_TESTS="${MIN_TESTS:-8}"
SUITE_CMD="${SUITE_CMD:-python3 -m unittest discover -s tests -t . -v}"

cd "$(dirname "$0")" || exit 1
out=$($SUITE_CMD 2>&1)
status=$?
echo "$out" | tail -25
ran=$(echo "$out" | grep -Eo 'Ran [0-9]+ tests?' | grep -Eo '[0-9]+' | head -1)
[ "$status" -ne 0 ] && exit 1
if [ -z "$ran" ] || [ "$ran" -lt "$MIN_TESTS" ]; then
  echo "GATE: only ${ran:-0} tests ran — the quality bar requires a real suite (min $MIN_TESTS)"
  exit 1
fi
exit 0
