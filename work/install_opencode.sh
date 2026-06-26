#!/usr/bin/env bash
set -euo pipefail

SUBMISSION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "${SUBMISSION_ROOT}/scripts/install_plugin.sh" ]]; then
  echo "Cannot find scripts/install_plugin.sh under submission root: ${SUBMISSION_ROOT}" >&2
  exit 1
fi

bash "${SUBMISSION_ROOT}/scripts/install_plugin.sh"
