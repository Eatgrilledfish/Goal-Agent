---
name: shophub-goal-runner
description: Use when working on the ShopHub design-implementation consistency competition. Runs a persistent Goal Runner workflow over code/, design-docs/, test-cases/, API基线文档.md, 黑盒用例说明.md, and 比赛说明.md; fixes code in small evidence-backed rounds while preserving the frozen REST API contract; writes 修复报告.md.
---

# ShopHub Goal Runner

Use this skill when the current repository is a ShopHub competition workspace.

## Mission

Complete the competition task end to end:

1. Read business design documents in `design-docs/`.
2. Read the frozen REST API contract in `API基线文档.md`.
3. Map the Spring Boot implementation in `code/`.
4. Run baseline and black-box tests in `code/` and `test-cases/`.
5. Find design-code inconsistencies with explicit design evidence.
6. Fix one issue per round or one tightly related issue group.
7. Preserve the frozen API contract.
8. Verify every fix.
9. Write `修复报告.md`.

## Required Inputs

The current working directory must contain:

- `code/`
- `design-docs/`
- `test-cases/`
- `API基线文档.md`
- `黑盒用例说明.md`
- `比赛说明.md`

If any are missing, stop and report the missing inputs.

## Safety Rules

- `design-docs/` is the business source of truth.
- `API基线文档.md` is the frozen REST API contract.
- Do not modify `design-docs/**`.
- Do not modify `API基线文档.md`.
- Do not modify `比赛说明.md`.
- Do not modify `黑盒用例说明.md`.
- Avoid modifying `test-cases/**`.
- Do not change REST URLs, methods, headers, request body fields/types, response body fields/types, or public error-code semantics.
- Tests are symptoms, not design authority.
- Never hardcode behavior only to satisfy public tests.

## Persistent Workflow

Maintain `.agent-work/`:

- `state.json`
- `goal.md`
- `spec_rules.jsonl`
- `api_contract.json`
- `api_snapshot_baseline.json`
- `api_snapshot_current.json`
- `code_map.md`
- `baseline_tests.md`
- `issues.jsonl`
- `fix_plan.md`
- `rounds/`
- `test-results/`
- `reports/`

If the current repo includes `scripts/shophub_goal_runner.py`, prefer it for deterministic bookkeeping. If it does not, check for the installed plugin CLI:

```bash
command -v shophub-goal-runner
```

Use `shophub-goal-runner --root . <command>` when available. Otherwise create the same files manually.

State machine:

```text
INIT -> READ_SPECS -> READ_API_BASELINE -> MAP_CODE -> RUN_BASELINE_TESTS
-> AUDIT_INCONSISTENCIES -> PRIORITIZE_ISSUES -> FIX_LOOP
-> RUN_FULL_TESTS -> WRITE_REPORT -> DONE
```

## Fix Loop

For each round:

1. Select the highest priority open issue with explicit design evidence.
2. Record the issue, design basis, before behavior, planned fix, API impact, tests, result, and risk in `.agent-work/rounds/round-XXX.md`.
3. Modify the smallest necessary files.
4. Prefer Service/Domain changes before Controller/DTO changes.
5. Add focused tests when useful.
6. Re-run API snapshot comparison.
7. Run focused tests, then full tests when appropriate.
8. Review the diff for design match, API safety, minimality, hardcoding risk, and hidden-test risk.
9. Mark the issue fixed only after verification.

## Verification Commands

Final verification:

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

If these cannot run, document the exact reason in `修复报告.md`.

## Completion

Write `修复报告.md` with:

- discovered inconsistencies;
- repair details;
- unresolved risks;
- summary counts;
- API contract status;
- final verification commands and results.

Do not claim DONE until the report exists and API safety is confirmed.
