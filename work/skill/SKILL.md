---
name: goal-agent-spec-driven
description: Spec-driven design-implementation consistency repair. Builds API contracts from frozen baseline, scans Spring Boot code, builds trace matrix, runs static checks, generates tests, produces multi-candidate patches with sandbox validation, enforces forbidden-change guard, and verifies stability.
---

# Goal Agent — Spec-Driven Pipeline

This SKILL defines the main spec-driven pipeline for design-implementation consistency checking and automatic repair. It orchestrates subagents and deterministic Python scripts from the submitted `/work` directory.

## Pipeline Overview

```text
Phase 0: Preflight
Phase 1: Build Contracts (API + Business Rules)
Phase 2: Scan Code (Controller/DTO/Service/Repository/Entity/Exception)
Phase 3: Build Trace Matrix + Static Consistency Check
Phase 4: Generate Spec-Driven Tests
Phase 5: Baseline Test Run
Phase 6: Localize & Prioritize Repair Tasks
Phase 7: Fix Loop (per task: generate candidates → sandbox → score → apply)
Phase 8: Stability Gate (3x rerun + contract re-check + forbidden-change guard)
Phase 9: Report & Deliver
```

## Competition Layout

Accept this repository layout in PROJECT_ROOT:

```text
README.md           ← API baseline / frozen-contract section (semantically identified)
code/               ← Java Spring Boot project
design-docs/        ← design truth (business rules, API reference)
test-cases/         ← public black-box tests (symptoms, not truth)
maven-settings.xml  ← internal Maven mirror config
```

## Phase 0: Preflight

**Goal**: Verify environment and capture baseline state.

**Deterministic steps** (run via Bash):
```bash
python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT init
```

**Verify**:
- `README.md`, `code/pom.xml`, `design-docs/`, `test-cases/pom.xml` exist
- `mvn -version` works
- Record `git status --short`
- Missing inputs → STOP with `missing_competition_inputs`

## Phase 1: Build Contracts

**Goal**: Extract structured API contract and business rules from documentation.

### Step 1.1: Run deterministic extractors
```bash
python3 work/tools/scripts/api_contract_builder.py --root $PROJECT_ROOT
python3 work/tools/scripts/business_rule_builder.py --root $PROJECT_ROOT
```

### Step 1.2: Invoke contract-builder subagent
Call `contract-builder` to semantically enrich:
- Fill endpoint summaries, confirm status codes
- Set field required/optional based on documentation
- Classify rules by type (validation/state_transition/business_rule/api_contract)
- Assign P0/P1/P2 priorities
- Link rules to API endpoints

**Outputs**: `.agent-work/api_contract.json`, `.agent-work/business_rules.json`

## Phase 2: Scan Code

**Goal**: Build complete code inventory of the Spring Boot project.

### Step 2.1: Run deterministic scanners
```bash
python3 work/tools/scripts/spring_scanner.py --root $PROJECT_ROOT
python3 work/tools/scripts/dto_analyzer.py --root $PROJECT_ROOT
python3 work/tools/scripts/exception_analyzer.py --root $PROJECT_ROOT
```

### Step 2.2: Invoke code-analyzer subagent
Call `code-analyzer` to enrich:
- Map controllers to services (trace call chains)
- Map DTOs to endpoints
- Check validation annotation coverage
- Flag missing exception handlers
- Detect anti-patterns (swallowed exceptions, missing @Valid, missing @Transactional)

**Outputs**: `.agent-work/repo_map.json`, `.agent-work/dto_validation_report.json`, `.agent-work/exception_coverage.json`

## Phase 3: Trace Matrix + Static Check

**Goal**: Build requirement→code traceability and find inconsistencies without running tests.

### Step 3.1: Run deterministic checker
```bash
python3 work/tools/scripts/contract_checker.py --root $PROJECT_ROOT
```

### Step 3.2: Invoke consistency-checker subagent
Call `consistency-checker` to:
- Check route existence (contract endpoint → controller method)
- Check DTO field presence and type compatibility
- Check validation coverage against contract requirements
- Check exception handler coverage (400→MethodArgumentNotValid, 404→EntityNotFound, 409→Conflict)
- Check response schema compliance (ApiResponse wrapper, data field, pagination metadata)
- Generate repair task candidates for each P0/P1 issue

**Outputs**: `.agent-work/consistency_report.json`, `.agent-work/trace_matrix.json`, `.agent-work/repair_tasks.json`

## Phase 4: Generate Spec-Driven Tests

**Goal**: Create MockMvc tests from API contract and business rules to simulate hidden tests.

### Step 4.1: Run test generator
```bash
python3 work/tools/scripts/spec_test_generator.py --root $PROJECT_ROOT
```

This generates tests covering:
- Positive: valid requests should succeed
- Negative: null/blank/zero/negative → 400
- Boundary: edge values
- Error format: response code/message structure
- Pagination: page=0, size=0, empty list, metadata
- Not-found: non-existent IDs → 404

**Output**: `.tmp/generated-tests/` (NOT committed, verified then discarded)

## Phase 5: Baseline Test Run

**Goal**: Run public black-box tests to establish baseline pass/fail state.

```bash
python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT baseline-tests
```

Or directly:
```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

Save test symptoms to `.agent-work/test_symptoms.jsonl`.

## Phase 6: Localize & Prioritize Repair Tasks

**Goal**: Merge static issues + test failures into a prioritized repair queue.

- Load `consistency_report.json` issues
- Load test symptoms from baseline run
- Load `repair_tasks.json` from consistency checker
- Merge and deduplicate
- Prioritize: P0 (API contract, validation, error codes) → P1 (pagination, sorting, defaults) → P2 (style, logs)

```bash
python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT prioritize
```

## Phase 7: Fix Loop

**Goal**: Repair one issue at a time, with multi-candidate generation and sandbox validation.

### For each P0 repair task (then P1, then P2):

### Step 7.1: Generate candidate patches
Invoke `patch-generator` subagent for the current task.

Each task gets 3-5 candidates:
- **candidate-1**: Minimal DTO/annotation fix (preferred for validation issues)
- **candidate-2**: Service-layer explicit validation
- **candidate-3**: Controller + ExceptionHandler fix
- **candidate-4**: Repository/query logic fix
- **candidate-5**: Comprehensive fix (diff budget ≤ 50 lines)

### Step 7.2: Sandbox validate each candidate
For each candidate:
1. Apply to isolated workspace (git worktree or `.tmp/candidates/TASK-XXX/candidate-N/`)
2. Run compile check: `mvn -s maven-settings.xml -f code/pom.xml compile`
3. Run contract checker: `python3 work/tools/scripts/contract_checker.py --root $SANDBOX`
4. Run forbidden-change guard: `python3 work/tools/scripts/forbidden_change_guard.py --root $SANDBOX`
5. Run public tests: `mvn -s maven-settings.xml -f test-cases/pom.xml test`
6. Run generated tests: compile to `src/test/java/generated/`, run, then clean up
7. Record results in `.agent-work/candidate_patches.jsonl`

### Step 7.3: Score and select best candidate
Score each candidate:
```text
score = 40% * public_test_pass_rate
      + 25% * generated_test_pass_rate
      + 15% * contract_checker_pass (1 or 0)
      + 10% * diff_minimization (fewer files/lines = higher)
      + 10% * stability (consistent pass = higher)
```

**Direct elimination**: modifies design-docs, API baseline, test code, swallows exceptions, returns 200 universally, hardcodes test data, fails to compile.

Select the candidate with the highest score. If tie, prefer minimal diff.

### Step 7.4: Apply best candidate
Apply the selected patch to the main workspace.
Run API contract re-check:
```bash
python3 work/tools/scripts/api_contract_builder.py --root $PROJECT_ROOT
python3 work/tools/scripts/contract_checker.py --root $PROJECT_ROOT
```
If API contract violated → REJECT and try next-best candidate.

### Step 7.5: Invoke review
Call `shophub-review-agent` to review the applied patch:
- Does it follow the repair strategy?
- Is it minimal?
- Any side effects?
- Risk assessment

### Step 7.6: Record round
```bash
python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT finish-round \
  --round N --result PASS --tests "focused tests passed"
```

**Stop conditions** (exit fix loop):
- All public tests pass AND all P0/P1 issues resolved
- 3 consecutive rounds with no progress
- 2 consecutive regressions
- Max rounds reached (default 20)

## Phase 8: Stability Gate

**Goal**: Verify fixes are stable, not flaky.

### Step 8.1: Stability rerun
```bash
python3 work/tools/scripts/stability_runner.py --root $PROJECT_ROOT --runs 3
```
Requirement: ALL 3 runs must pass.

### Step 8.2: Final forbidden-change guard
```bash
python3 work/tools/scripts/forbidden_change_guard.py --root $PROJECT_ROOT --strict
```
Requirement: ZERO blockers, ZERO warnings (in strict mode).

### Step 8.3: Final contract check
```bash
python3 work/tools/scripts/contract_checker.py --root $PROJECT_ROOT
```
Requirement: ZERO P0 issues.

### Step 8.4: Invoke stability-verifier
Call `stability-verifier` subagent to:
- Analyze flaky indicators across runs
- Verify no regression from baseline
- Confirm all gates pass

## Phase 9: Report & Deliver

### Step 9.1: Generate reports
```bash
python3 work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT report
```

### Step 9.2: Invoke shophub-report-writer
Call `shophub-report-writer` subagent to produce final `修复报告.md`.

### Step 9.3: Generate result/output.md
Ensure `result/output.md` contains:
- Pipeline execution summary
- Contracts built (endpoint count, rule count)
- Code scan summary (controllers, DTOs, services, repos)
- Issues found/fixed (by type and priority)
- Modified files list
- Test results (baseline, after fix, stability)
- Forbidden-change guard status
- Final conclusion and remaining risks

## Mandatory Subagents

### Spec-Driven (new)
- `contract-builder` — API contract + business rule extraction
- `code-analyzer` — Spring Boot code scanning
- `consistency-checker` — Static check + trace matrix
- `patch-generator` — Multi-candidate patch generation
- `stability-verifier` — Validation + guard + stability

### Legacy (existing, preserved)
- `shophub-spec-librarian` — design rule filling
- `shophub-api-guardian` — API contract guard
- `shophub-code-mapper` — code map
- `shophub-module-mapper` — module mapping
- `shophub-test-diagnoser` — test diagnosis
- `shophub-module-auditor` — per-module audit
- `shophub-cross-cut-auditor` — cross-module audit
- `shophub-patch-agent` — single-issue patch execution (used as fallback)
- `shophub-review-agent` — patch review
- `shophub-report-writer` — report generation

## Deterministic Scripts

All under `work/tools/scripts/`:

| Script | Phase | Purpose |
|--------|-------|---------|
| `shophub_goal_runner.py` | All | Core runner: init, specs, API, map, tests, audit, prioritize, report |
| `api_contract_builder.py` | 1 | Extract REST endpoints from markdown |
| `business_rule_builder.py` | 1 | Extract business rules from design-docs |
| `spring_scanner.py` | 2 | Scan Spring Boot code → repo_map.json |
| `dto_analyzer.py` | 2 | DTO validation coverage analysis |
| `exception_analyzer.py` | 2 | ExceptionHandler coverage analysis |
| `contract_checker.py` | 3 | Deterministic API contract consistency check |
| `spec_test_generator.py` | 4 | Generate MockMvc tests from contracts |
| `forbidden_change_guard.py` | 7,8 | Forbidden change detection |
| `stability_runner.py` | 8 | Multi-run stability verification |
| `api_snapshot.py` | util | API snapshot generation |
| `issue_queue.py` | util | Issue queue management |
| `round_recorder.py` | util | Round start/finish |
| `summarize_test_logs.py` | util | Test log summarization |

## Verification Commands

Use local Maven with `maven-settings.xml`:

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

## Safety Rules (ABSOLUTE)

### NEVER modify:
- `design-docs/**` — design documents are truth
- `README.md` API baseline sections — frozen contract
- `test-cases/**` — public tests are symptoms, not to be manipulated

### NEVER do:
- Change REST API URLs, HTTP methods, or documented field names
- Change documented response field types or success status codes
- Hardcode test fixture values (no `if (name.equals("test-product"))`)
- Swallow exceptions (no `catch (Exception e) { return success; }`)
- Return 200 for all errors (no uniform success response)
- Comment out `@Valid`, validation annotations, or `@Transactional`
- Delete Repository query filters (status, deleted flag, userId)
- Skip tests or modify test framework

### ALWAYS prefer:
- Minimal diff (fewest files, fewest lines)
- DTO/annotation fix over Service code change
- Service validation over Controller change
- API contract compliance over test passing (fix behavior, not tests)
- Evidence-backed changes (cite design doc section)

## Completion Criteria

DONE requires ALL of:
- `mvn -s maven-settings.xml -f code/pom.xml test` passes
- `mvn -s maven-settings.xml -f code/pom.xml install -DskipTests` passes
- `mvn -s maven-settings.xml -f test-cases/pom.xml test` passes (or remaining failures documented with design-backed risk)
- `python3 work/tools/scripts/stability_runner.py --runs 3` passes
- `python3 work/tools/scripts/forbidden_change_guard.py --strict` passes
- `python3 work/tools/scripts/contract_checker.py` has zero P0 issues
- API baseline remains compatible
- `修复报告.md` exists in PROJECT_ROOT
- `result/output.md` exists in SUBMISSION_ROOT

Final response must include:
- Status: DONE / BLOCKED / STOPPED_BY_SAFETY
- Issues found / fixed / remaining
- API contract status
- Forbidden-change guard status
- Stability rerun status
- Verification command results
- `修复报告.md` path
- Remaining risks
