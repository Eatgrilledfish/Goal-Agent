# ShopHub Goal Runner

This repository contains a local Goal Runner for the ShopHub design/implementation consistency competition.

The runner follows `design-document.md` and coordinates these steps:

1. Create the agent work area.
2. Extract business rules from `design-docs/`.
3. Extract the frozen REST API contract from `API基线文档.md`.
4. Build a Java/Spring code map from `code/`.
5. Run baseline Maven tests and summarize symptoms.
6. Maintain an issue queue and fix plan.
7. Record repair rounds.
8. Generate `修复报告.md`.

The runner is intentionally conservative. It never edits `design-docs/`, `API基线文档.md`, `比赛说明.md`, `黑盒用例说明.md`, or `test-cases/`. Code repair is performed one issue at a time by the patch agent or by Codex following the generated round file.

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
