---
description: Run the ShopHub competition Goal Runner in the current repository until DONE or a safety stop condition.
---

# ShopHub Goal Runner

Run the ShopHub design-implementation consistency workflow in the current working directory. This command is intended to be invoked as `/shophub` from the repository that already contains the competition inputs.

`$ARGUMENTS` may contain:

- `dry-run`: build indexes and reports without applying code fixes.
- `no-tests`: skip Maven execution. Use only for local command validation, never for a real competition run.
- `max-rounds=N`: override the default repair loop cap of 20.
- `report-only`: regenerate `修复报告.md` from existing `.agent-work/` evidence.

## Preflight

1. Identify the current repository root with `pwd` and `git rev-parse --show-toplevel` when available.
2. Confirm the required competition inputs exist in the current repo:
   - `code/`
   - `design-docs/`
   - `test-cases/`
   - `API基线文档.md`
   - `黑盒用例说明.md`
   - `比赛说明.md`
3. If any required input is missing, stop after writing a clear status summary. Do not fabricate issue findings.
4. Check `git status --short`. Never revert user changes. Record dirty state in `.agent-work/state.json` or the final summary.
5. Check that Maven is available if tests are not skipped:
   - `mvn -version`
6. If helper scripts are present in the current repo, use them for bookkeeping:
   - `scripts/shophub_goal_runner.py`
   - `scripts/api_snapshot.py`
   - `scripts/summarize_test_logs.py`
   - `scripts/issue_queue.py`
   - `scripts/round_recorder.py`
7. If helper scripts are not present, check whether the installed plugin CLI is available:

   ```bash
   command -v shophub-goal-runner
   ```

8. If either local helper scripts or the installed plugin CLI are available, use them for deterministic bookkeeping.
9. If neither is available, execute the same workflow manually and create the `.agent-work/` files yourself.

## Plan

Act as the ShopHub Goal Runner Orchestrator. Continue until DONE or a safety stop condition is reached.

Follow this state machine:

```text
INIT
READ_SPECS
READ_API_BASELINE
MAP_CODE
RUN_BASELINE_TESTS
AUDIT_INCONSISTENCIES
PRIORITIZE_ISSUES
FIX_LOOP
RUN_FULL_TESTS
WRITE_REPORT
DONE
```

Safety rules:

- Do not modify `design-docs/**`.
- Do not modify `API基线文档.md`.
- Do not modify `比赛说明.md`.
- Do not modify `黑盒用例说明.md`.
- Avoid modifying `test-cases/**`.
- Do not change REST API URLs, HTTP methods, request headers, request body field names/types, response body field names/types, or public error-code semantics.
- Every issue and fix must cite design evidence from `design-docs/` or contract evidence from `API基线文档.md`.
- Public black-box tests are symptoms only.

## Commands

### 1. Initialize Evidence

If helper scripts exist:

```bash
python3 scripts/shophub_goal_runner.py init
```

If only the installed plugin CLI exists:

```bash
shophub-goal-runner --root . init
```

Otherwise create:

```text
.agent-work/state.json
.agent-work/goal.md
.agent-work/rounds/
.agent-work/test-results/
.agent-work/reports/
```

### 2. Build Indexes

If helper scripts exist:

```bash
python3 scripts/shophub_goal_runner.py read-specs
python3 scripts/shophub_goal_runner.py read-api
python3 scripts/shophub_goal_runner.py map-code
```

If only the installed plugin CLI exists:

```bash
shophub-goal-runner --root . read-specs
shophub-goal-runner --root . read-api
shophub-goal-runner --root . map-code
```

Otherwise manually produce:

```text
.agent-work/spec_rules.jsonl
.agent-work/01_spec_index.md
.agent-work/api_contract.json
.agent-work/api_snapshot_baseline.json
.agent-work/api_snapshot_current.json
.agent-work/02_api_contract_index.md
.agent-work/code_map.md
.agent-work/code_call_chains.jsonl
```

### 3. Run Baseline Tests

Unless `$ARGUMENTS` contains `no-tests`, run:

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

Save logs under `.agent-work/test-results/` and summarize failures in `.agent-work/baseline_tests.md`. If helper scripts exist:

```bash
python3 scripts/shophub_goal_runner.py baseline-tests
```

If only the installed plugin CLI exists:

```bash
shophub-goal-runner --root . baseline-tests
```

### 4. Audit and Prioritize

Find design-code inconsistencies. Each issue must include:

- `issue_id`
- `severity`
- `module`
- `design_basis`
- `code_location`
- `design_behavior`
- `actual_behavior`
- `type`
- `api_impact`
- `fix_suggestion`
- `test_suggestion`
- `confidence`
- `estimated_fix_effort`
- `status`

Write `.agent-work/issues.jsonl` and `.agent-work/fix_plan.md`.

If helper scripts exist:

```bash
python3 scripts/shophub_goal_runner.py audit
python3 scripts/shophub_goal_runner.py prioritize
```

If only the installed plugin CLI exists:

```bash
shophub-goal-runner --root . audit
shophub-goal-runner --root . prioritize
```

Then refine weak heuristic issues manually by reading the cited design and code before fixing anything.

### 5. Fix Loop

Skip this section if `$ARGUMENTS` contains `dry-run` or `report-only`.

Repeat until DONE:

1. Select the highest priority open issue with design evidence.
2. Create a round record:

   ```bash
   python3 scripts/shophub_goal_runner.py next-round
   ```

   If only the installed plugin CLI exists:

   ```bash
   shophub-goal-runner --root . next-round
   ```

   If helper tooling is unavailable, create `.agent-work/rounds/round-XXX.md` manually.
3. Edit the smallest necessary set of files, preferably Service/Domain logic before Controller/DTO.
4. Add or update focused tests under `code/**/src/test/**` when useful.
5. Rebuild the API snapshot and compare against baseline:

   ```bash
   python3 scripts/api_snapshot.py --root .
   ```

   If only the installed plugin CLI exists:

   ```bash
   shophub-goal-runner --root . read-api
   ```

6. Run focused tests first, then full tests when the change is broad or every third round:

   ```bash
   mvn -f code/pom.xml test
   mvn -f code/pom.xml install
   mvn -f test-cases/pom.xml test
   ```

7. Review the current diff for:
   - design match;
   - API safety;
   - minimality;
   - no public-test hardcoding;
   - hidden-test risk.
8. If the round is accepted, mark it PASS:

   ```bash
   python3 scripts/round_recorder.py --root . finish --round <N> --result PASS --tests "<commands run>"
   ```

   If only the installed plugin CLI exists:

   ```bash
   shophub-goal-runner --root . finish-round --round <N> --result PASS --tests "<commands run>"
   ```

9. If tests fail or API safety is not preserved, stop new issue work and repair or revert the current round before continuing.
10. Re-run audit/prioritize after each accepted fix.

### 6. DONE Conditions

Stop fixing and write the final report when any of these are true:

- high and medium issues are handled and full tests pass;
- public black-box tests pass and remaining issues are low confidence or high risk;
- three consecutive rounds have no effective progress;
- two consecutive rounds introduce regressions;
- `max-rounds` is reached;
- API contract drift is detected;
- continuing has higher risk than benefit.

Do not declare DONE if:

- code cannot compile;
- API contract is broken;
- fixed issues lack verification records;
- `修复报告.md` is missing.

## Verification

Before the final answer, run the full verification unless `$ARGUMENTS` contains `no-tests`:

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

Regenerate the report:

```bash
python3 scripts/shophub_goal_runner.py report
```

If only the installed plugin CLI exists:

```bash
shophub-goal-runner --root . report
```

If helper tooling is unavailable, write `修复报告.md` manually with the required sections.

## Summary

Final response must include:

- final status: DONE, BLOCKED, or STOPPED_BY_SAFETY;
- number of issues found/fixed/unfixed;
- API contract status;
- exact verification commands and results;
- path to `修复报告.md`;
- any remaining risks.

## Next Steps

If stopped before DONE, state the exact blocker and the next command or file the user should inspect. If DONE, recommend committing the final code and report.
