# ShopHub Goal Runner

This repository contains a local Goal Runner plugin for the ShopHub design/implementation consistency competition.

The runner follows `design-document.md` and coordinates these steps:

1. Create the agent work area.
2. Extract business rules from `design-docs/`.
3. Extract the frozen REST API contract from `API基线文档.md`.
4. Build a Java/Spring code map from `code/`.
5. Run baseline Maven tests and summarize symptoms.
6. Maintain an issue queue and fix plan.
7. Record repair rounds.
8. Optionally call an external patch agent in a continuous loop.
9. Generate `修复报告.md`.

The runner is intentionally conservative. It never edits `design-docs/`, `API基线文档.md`, `比赛说明.md`, `黑盒用例说明.md`, or `test-cases/`. In `auto-run` mode, code repair is delegated one issue at a time to the external command passed with `--patch-command`.

## Plugin Usage

This repository is a plugin root with one user-facing entry:

```text
.codex-plugin/plugin.json
commands/shophub.md
```

Install it locally:

```bash
scripts/install_plugin.sh
```

The installer:

- symlinks this repository to `~/plugins/shophub-goal-runner`;
- adds/updates `~/.agents/plugins/marketplace.json`;
- runs `codex plugin add shophub-goal-runner@personal` when the Codex CLI is available;
- links the same slash command into OpenCode at `~/.config/opencode/commands/shophub.md`;
- removes legacy direct helper links if they exist.

After installing, restart the CLI/app if needed.

From a ShopHub competition repository, run the slash command in Codex or OpenCode:

```text
/shophub
```

Optional slash arguments:

```text
/shophub dry-run
/shophub no-tests
/shophub max-rounds=10
/shophub report-only
```

Do not call internal scripts directly during normal use. They are implementation details used by `/shophub`.

## Expected Competition Layout

```text
.
├── code/
├── design-docs/
├── test-cases/
├── API基线文档.md
├── 黑盒用例说明.md
├── 比赛说明.md
├── AGENTS.md
└── .agent-work/
```

This scaffold can be initialized before the competition files exist. Missing competition inputs are reported in `.agent-work/goal.md` and `state.json`.

## Verification

```bash
python3 -m py_compile scripts/*.py
PYTHONPATH=/tmp/codex-plugin-validator-deps python3 /Users/fangjianqiao/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

When the real ShopHub competition files are present, final verification is:

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```
