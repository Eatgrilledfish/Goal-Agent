# ShopHub Goal Runner

This repository contains an OpenCode skill + hidden-agent Goal Runner for the `HW-ICT-CMP-04` ShopHub design/implementation consistency competition.

The zip submission entry is:

```text
INSTRUCTION.md
work/
```

The runtime assets under `work/` are:

```text
work/skill/SKILL.md
work/.opencode/commands/shophub.md
work/.opencode/agents/shophub-*.md
work/.opencode/skills/shophub-goal-runner/SKILL.md
work/tools/scripts/*.py
```

## Install Into OpenCode

Install from this submission package into the target competition repository:

```bash
bash work/install_opencode.sh /path/to/HW-ICT-CMP-04
```

The installer copies assets into the target repository:

```text
.opencode/commands/shophub.md
.opencode/agents/shophub-*.md
.opencode/skills/shophub-goal-runner/SKILL.md
.opencode/shophub/tools/scripts/
```

No Codex plugin or `~/plugins` installation is required.

## Run

From the target `HW-ICT-CMP-04` repository:

```bash
opencode
```

Then enter:

```text
/shophub
```

Optional arguments:

```text
/shophub max-rounds=20
/shophub dry-run
/shophub report-only
```

Do not skip tests during a real competition run.

## Real Competition Layout

The real repository layout is:

```text
README.md
code/
design-docs/
test-cases/
```

The frozen API baseline is in:

- `README.md`, section `6. API 基线（冻结契约）`
- `design-docs/附录A-API接口参考.md`

Do not require older placeholder files such as `API基线文档.md`, `比赛说明.md`, or `黑盒用例说明.md`.

## Verification

Local package validation:

```bash
python3 -m py_compile scripts/*.py work/tools/scripts/*.py
bash -n work/install_opencode.sh
PYTHONPATH=/tmp/codex-plugin-validator-deps python3 /Users/fangjianqiao/.codex/skills/.system/skill-creator/scripts/quick_validate.py work/skill
```

Competition verification:

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install -DskipTests
mvn -f test-cases/pom.xml test
```
