---
description: Run the HW-ICT-CMP-04 ShopHub Goal Runner with skill + hidden subagents until DONE or safety stop.
agent: shophub-orchestrator
subtask: true
---

# ShopHub Goal Runner

Run the `HW-ICT-CMP-04` design-implementation consistency workflow in the current competition repository.

Before starting, load and follow the `shophub-goal-runner` skill from:

```text
.opencode/skills/shophub-goal-runner/SKILL.md
```

This command must run through `shophub-orchestrator`. The orchestrator must call the hidden specialist subagents with the Task tool when available:

- `shophub-spec-librarian`
- `shophub-api-guardian`
- `shophub-code-mapper`
- `shophub-test-diagnoser`
- `shophub-module-auditor`
- `shophub-patch-agent`
- `shophub-review-agent`
- `shophub-report-writer`

`$ARGUMENTS` may contain:

- `max-rounds=N`: repair loop cap, default 20.
- `dry-run`: index and audit only; do not patch.
- `report-only`: regenerate `修复报告.md`.

Do not use `no-tests` in a real competition run.

## Real Repository Layout

Accept:

```text
README.md
code/
design-docs/
test-cases/
```

The frozen API baseline is in:

- `README.md`, section `6. API 基线（冻结契约）`
- `design-docs/附录A-API接口参考.md`

## Workflow

1. Preflight current repository:
   - `README.md`
   - `code/pom.xml`
   - `design-docs/`
   - `test-cases/pom.xml`
   - `mvn -version`
   - `git status --short`
2. Call `shophub-spec-librarian` to extract design rules.
3. Call `shophub-api-guardian` to extract and protect the frozen API baseline from README and appendix A.
4. Call `shophub-code-mapper` using the fixed design-doc-to-Maven-module mapping in the skill.
5. Call `shophub-test-diagnoser` and run:

   ```bash
   mvn -f code/pom.xml test
   mvn -f code/pom.xml install -DskipTests
   mvn -f test-cases/pom.xml test
   ```

6. Call `shophub-module-auditor` for each failed public behavior and high-risk module.
7. In each fix round:
   - select one concrete design-backed issue;
   - call `shophub-patch-agent`;
   - call `shophub-api-guardian`;
   - run focused and then full verification when feasible;
   - call `shophub-review-agent`;
   - accept, rework, or revert before moving on.
8. Call `shophub-report-writer` and write `修复报告.md`.

## Local Tools

Helper scripts, if useful, are available under:

```text
.opencode/shophub/tools/scripts/
```

Use them only for deterministic bookkeeping. The actual diagnosis and repair must be done by the subagents.

## Final Response

Include:

- status: DONE, BLOCKED, or STOPPED_BY_SAFETY;
- issue counts found/fixed/unfixed;
- API baseline status;
- exact verification commands and results;
- `修复报告.md` path;
- remaining risks.
