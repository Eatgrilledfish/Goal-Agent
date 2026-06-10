#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${SHOPHUB_PATCH_COMMAND:-}" ]]; then
  cat >&2 <<'MSG'
SHOPHUB_PATCH_COMMAND is required.

Example:
  export SHOPHUB_PATCH_COMMAND='codex exec --full-auto "$(cat {round_file})"'
  scripts/run_until_done.sh
MSG
  exit 2
fi

python3 "$ROOT/scripts/shophub_goal_runner.py" \
  --root "$ROOT" \
  auto-run \
  --patch-command "$SHOPHUB_PATCH_COMMAND" \
  --max-rounds "${SHOPHUB_MAX_ROUNDS:-20}" \
  --timeout "${SHOPHUB_TEST_TIMEOUT:-900}" \
  --patch-timeout "${SHOPHUB_PATCH_TIMEOUT:-1800}"
