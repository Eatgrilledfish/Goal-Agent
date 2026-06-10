#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="shophub-goal-runner"
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PERSONAL_PLUGINS_DIR="${HOME}/plugins"
PLUGIN_LINK="${PERSONAL_PLUGINS_DIR}/${PLUGIN_NAME}"
MARKETPLACE_DIR="${HOME}/.agents/plugins"
MARKETPLACE_JSON="${MARKETPLACE_DIR}/marketplace.json"

mkdir -p "$PERSONAL_PLUGINS_DIR" "$MARKETPLACE_DIR"

ln -sfn "$PLUGIN_ROOT" "$PLUGIN_LINK"

rm -f "${HOME}/.local/bin/shophub-goal-runner"
rm -f "${HOME}/.config/opencode/skills/${PLUGIN_NAME}"
rm -f "${HOME}/.codex/skills/${PLUGIN_NAME}"

python3 - "$MARKETPLACE_JSON" "$PLUGIN_NAME" <<'PY'
import json
import sys
from pathlib import Path

marketplace_path = Path(sys.argv[1])
plugin_name = sys.argv[2]
marketplace_path.parent.mkdir(parents=True, exist_ok=True)

if marketplace_path.exists():
    data = json.loads(marketplace_path.read_text(encoding="utf-8"))
else:
    data = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [],
    }

data.setdefault("name", "personal")
data.setdefault("interface", {"displayName": "Personal"})
data.setdefault("plugins", [])

entry = {
    "name": plugin_name,
    "source": {
        "source": "local",
        "path": f"./plugins/{plugin_name}",
    },
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": "Developer Tools",
}

plugins = [item for item in data["plugins"] if item.get("name") != plugin_name]
plugins.append(entry)
data["plugins"] = plugins

marketplace_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

CODEX_INSTALL_STATUS="skipped: codex CLI not found"
if command -v codex >/dev/null 2>&1; then
  if codex plugin add "${PLUGIN_NAME}@personal" --json >/tmp/${PLUGIN_NAME}-codex-plugin-add.json 2>/tmp/${PLUGIN_NAME}-codex-plugin-add.err; then
    CODEX_INSTALL_STATUS="installed: $(cat /tmp/${PLUGIN_NAME}-codex-plugin-add.json)"
  else
    CODEX_INSTALL_STATUS="failed: $(cat /tmp/${PLUGIN_NAME}-codex-plugin-add.err)"
  fi
fi

cat <<MSG
Installed ${PLUGIN_NAME}.

Codex plugin symlink:
  ${PLUGIN_LINK}

Marketplace:
  ${MARKETPLACE_JSON}

Codex plugin add:
  ${CODEX_INSTALL_STATUS}

Restart your CLI/app if slash commands are not immediately visible.
Use the single entry /shophub from a ShopHub competition repository.
MSG
