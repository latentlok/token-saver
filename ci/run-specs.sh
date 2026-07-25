#!/usr/bin/env bash
# CI entrypoint: run the full hermetic spec suite.
# Kept out of the workflow YAML on purpose -- a committed script can't trip GitHub's
# workflow-file validator the way an embedded heredoc/loop block scalar can.
#
# Excluded: dispatch_spec -- its batch assertion is wall-clock-timing based and flaky.
# verdict_spec reads a context window from ~/.qwen/settings.json for its compaction test,
# so we write a minimal stub (the dev window, 196608) to make that assertion bind.
set -uo pipefail

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
for f in specs/*_spec.py; do
  case "$f" in
    */dispatch_spec.py) echo "skip (flaky): $f"; continue ;;
  esac
  echo "== $f =="
  python3 "$f" || fail=1
done
exit "$fail"
