#!/usr/bin/env bash
# CI entrypoint: run the full hermetic spec suite.
# Kept out of the workflow YAML on purpose -- a committed script can't trip GitHub's
# workflow-file validator the way an embedded heredoc/loop block scalar can.
#
# Excluded: dispatch_spec -- its OVERLAP assertions ("these ran concurrently") are
# wall-clock claims a loaded box can fail honestly. The load-robust half -- the
# SERIALIZATION claims, which load separates further rather than interleaves --
# lives in serialize_spec.py and runs here like any other spec.
# verdict_spec reads a context window from ~/.qwen/settings.json for its compaction test,
# so we write a minimal stub (the dev window, 196608) to make that assertion bind.
set -uo pipefail

# One throwaway root for EVERYTHING this run creates, removed on exit. The
# specs mkdtemp a git repo per test (~40 inodes each) and unittest never cleans
# them up -- a day of local runs filled /tmp to ENOSPC (83k inodes) and took
# every session on the machine down with it. TMPDIR routes every
# tempfile.mkdtemp in every spec under this root; the trap ends the leak.
export TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Run under a THROWAWAY HOME. This script writes a stub ~/.qwen/settings.json and
# a --global git identity; on a CI runner that is harmless, but run locally it
# silently overwrote a developer's real qwen config -- API key, endpoint and all.
# A test suite that eats your credentials on a bad day is not a test suite.
export HOME="$(mktemp -d)"
echo "specs running under HOME=$HOME (throwaway, inside TMPDIR=$TMPDIR)"

git config --global user.email "ci@token-saver.test"
git config --global user.name "token-saver CI"

mkdir -p "$HOME/.qwen"
python3 - <<'PY'
import json, os
json.dump(
    {"model": {"name": "ci-model"},
     "modelProviders": {"ci": [
         {"id": "ci-model", "generationConfig": {"contextWindowSize": 196608}}]}},
    open(os.path.join(os.path.expanduser("~"), ".qwen", "settings.json"), "w"))
PY

fail=0

# Worker-written tests (*_qwen.py) run here too. They are never a delegation
# GATE -- that stays the Claude-authored spec -- but once their work is
# accepted they are regression coverage like any other, and a test file that
# ran once and is never run again protects nothing.
for f in specs/*_spec.py qd/*_qwen.py; do
  [ -e "$f" ] || continue
  case "$f" in
    */dispatch_spec.py) echo "skip (flaky): $f"; continue ;;
  esac
  echo "== $f =="
  python3 "$f" || fail=1
done
exit "$fail"
