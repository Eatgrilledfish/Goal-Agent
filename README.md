# ShopHub Goal Runner

This repository contains a local Goal Runner and Codex/OpenCode-compatible plugin for the ShopHub design/implementation consistency competition.

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

This repository is also a plugin root:

```text
.codex-plugin/plugin.json
commands/shophub.md
skills/shophub-goal-runner/SKILL.md
agents/*.md
```

Install it locally:

```bash
scripts/install_plugin.sh
```

The installer:

- symlinks this repository to `~/plugins/shophub-goal-runner`;
- adds/updates `~/.agents/plugins/marketplace.json`;
- exposes a CLI helper at `~/.local/bin/shophub-goal-runner`;
- links the skill into `~/.config/opencode/skills/shophub-goal-runner`;
- links the skill into `~/.codex/skills/shophub-goal-runner`.
- runs `codex plugin add shophub-goal-runner@personal` when the Codex CLI is available.

After installing, restart the CLI/app if needed.

From a ShopHub competition repository, run the slash command:

```text
/shophub
```

Useful variants:

```text
/shophub dry-run
/shophub no-tests
/shophub max-rounds=10
/shophub report-only
```

For OpenCode, use the installed `shophub-goal-runner` skill by asking it to run the ShopHub Goal Runner in the current repo. The skill is installed at:

```text
~/.config/opencode/skills/shophub-goal-runner
```

The slash command and the skill both expect the current working directory to contain the competition inputs.

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
├── .opencode/agents/
└── .agent-work/
```

This scaffold can be initialized before the competition files exist. Missing competition inputs are reported in `.agent-work/goal.md` and `state.json`.

## Usage

```bash
python3 scripts/shophub_goal_runner.py init
python3 scripts/shophub_goal_runner.py run --no-tests
python3 scripts/shophub_goal_runner.py run
python3 scripts/shophub_goal_runner.py auto-run
python3 scripts/shophub_goal_runner.py status
```

Useful focused commands:

```bash
python3 scripts/shophub_goal_runner.py read-specs
python3 scripts/shophub_goal_runner.py read-api
python3 scripts/shophub_goal_runner.py map-code
python3 scripts/shophub_goal_runner.py baseline-tests
python3 scripts/shophub_goal_runner.py audit
python3 scripts/shophub_goal_runner.py prioritize
python3 scripts/shophub_goal_runner.py next-round
python3 scripts/shophub_goal_runner.py report
```

Auxiliary scripts are also available:

```bash
python3 scripts/api_snapshot.py --root .
python3 scripts/summarize_test_logs.py --root .
python3 scripts/issue_queue.py --root . next
python3 scripts/round_recorder.py --root . start
python3 scripts/round_recorder.py --root . finish --round 1 --result PASS
```

## Continuous Goal Mode

Use `auto-run` when you want the runner to keep working until the competition goals are complete or a safety stop condition is reached.

The runner handles orchestration, indexing, tests, API checks, round records, scoring, and reporting. The actual code repair must be supplied through `--patch-command` or the `SHOPHUB_PATCH_COMMAND` environment variable.

The patch command is called once per round and receives these placeholders:

- `{root}`: shell-quoted project root.
- `{round}`: current round number.
- `{round_file}`: shell-quoted `.agent-work/rounds/round-XXX.md`.
- `{issue_id}`: shell-quoted issue id.
- `{issue_json}`: shell-quoted JSON file for the selected issue.

Example template:

```bash
export SHOPHUB_PATCH_COMMAND='codex exec --full-auto "$(cat {round_file})"'
python3 scripts/shophub_goal_runner.py auto-run --max-rounds 20
```

If your patch agent expects a file path instead of inline prompt text:

```bash
python3 scripts/shophub_goal_runner.py auto-run \
  --patch-command 'opencode run --agent patch-agent --prompt-file {round_file}' \
  --max-rounds 20
```

For a dry run that verifies the loop without Maven execution:

```bash
python3 scripts/shophub_goal_runner.py auto-run --no-tests --max-rounds 2
```

Do not use `--no-tests` for an actual competition run.

`auto-run` stops and writes `修复报告.md` when any design-document stop condition is met, including:

- required competition inputs are missing;
- high and medium issues are handled and full tests pass;
- there are no open issues but tests still fail;
- API contract drift is detected;
- the patch command fails;
- consecutive no-progress or regression limits are reached;
- `--max-rounds` is reached.

## Verification

```bash
python3 -m py_compile scripts/*.py
python3 scripts/shophub_goal_runner.py --help
```

When the real ShopHub competition files are present, final verification is:

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```
