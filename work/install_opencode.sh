#!/usr/bin/env bash
set -euo pipefail

WORK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="${1:-$(pwd)}"

if [[ ! -d "${TARGET_ROOT}" ]]; then
  echo "Target repository does not exist: ${TARGET_ROOT}" >&2
  exit 1
fi

if [[ ! -f "${TARGET_ROOT}/code/pom.xml" || ! -d "${TARGET_ROOT}/design-docs" || ! -f "${TARGET_ROOT}/test-cases/pom.xml" ]]; then
  cat >&2 <<MSG
Target does not look like the HW-ICT-CMP-04 competition repository:
  ${TARGET_ROOT}

Expected:
  code/pom.xml
  design-docs/
  test-cases/pom.xml
MSG
  exit 1
fi

mkdir -p \
  "${TARGET_ROOT}/.opencode/commands" \
  "${TARGET_ROOT}/.opencode/agents" \
  "${TARGET_ROOT}/.opencode/skills" \
  "${TARGET_ROOT}/.opencode/shophub/tools"

cp "${WORK_ROOT}/.opencode/commands/shophub.md" \
  "${TARGET_ROOT}/.opencode/commands/shophub.md"

cp "${WORK_ROOT}/.opencode/agents"/shophub-*.md \
  "${TARGET_ROOT}/.opencode/agents/"

rm -rf "${TARGET_ROOT}/.opencode/skills/shophub-goal-runner"
cp -R "${WORK_ROOT}/.opencode/skills/shophub-goal-runner" \
  "${TARGET_ROOT}/.opencode/skills/shophub-goal-runner"

rm -rf "${TARGET_ROOT}/.opencode/shophub/tools/scripts"
cp -R "${WORK_ROOT}/tools/scripts" \
  "${TARGET_ROOT}/.opencode/shophub/tools/scripts"

cat <<MSG
Installed ShopHub Goal Runner into:
  ${TARGET_ROOT}/.opencode

OpenCode assets:
  .opencode/commands/shophub.md
  .opencode/agents/shophub-*.md
  .opencode/skills/shophub-goal-runner/SKILL.md
  .opencode/shophub/tools/scripts/

Run from the target repository:
  opencode

Then enter:
  /shophub
MSG
