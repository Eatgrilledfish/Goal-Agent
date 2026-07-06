---
name: goal-agent-spec-driven
description: Spec-driven design-implementation consistency repair. Builds API contracts from frozen baseline, scans Spring Boot code, builds trace matrix, runs static checks, generates tests, coordinates minimal patch-agent repairs with focused validation, enforces forbidden-change guard, and verifies stability.
---

# Goal Agent — Spec-Driven Pipeline

This SKILL defines the main spec-driven pipeline for design-implementation consistency checking and automatic repair. It orchestrates subagents and deterministic Python scripts from the submitted `/work` directory.

## Runtime Work Rules

Treat the submitted `work/` directory as the delivery root.

All runnable assets are under:

```text
skills/goal-agent-spec-driven/SKILL.md
skills/*.md
tools/scripts/*.py
tools/config/*.json
```

Do not depend on files outside `work/` at runtime. Do not install a plugin or copy delivery assets into the target repository. The platform loads `/INSTRUCTION.md`; the running agent should read this Skill from `work/skills/goal-agent-spec-driven/SKILL.md`, then load subagent definitions from `work/skills/*.md`, and execute against the target repository in place.

The target competition repository layout is:

```text
README.md
code/
design-docs/
test-cases/
```

The frozen API baseline is identified semantically from:

- `README.md` (its API baseline / frozen-contract section, not a hard-coded section number).
- The design-doc file(s) that carry the REST API reference (semantically identified, not a hard-coded filename).

Use the target repository files listed above as the complete required competition inputs.

## Runtime Assumption

The competition platform and local validation environment both support subagent/Task invocation.

Therefore, the primary and only supported competition execution model is:

```text
INSTRUCTION.md
  -> goal-agent-spec-driven/SKILL.md
  -> shophub-orchestrator
  -> shophub-* subagents
  -> deterministic helper scripts
  -> final_goal_gate.py
```

Do not delegate the repair loop to `shophub_goal_runner.py`. The runner is a helper toolkit, not an autonomous repair actor.

## Pipeline Overview

```text
Phase 0: Preflight
Phase 1: Build Contracts (API + Business Rules)
Phase 2: Scan Code (Controller/DTO/Service/Repository/Entity/Exception)
Phase 3: Build Trace Matrix + Static Consistency Check
Phase 4: Generate Spec-Driven Tests
Phase 5: Baseline Test Run
Phase 6: Localize & Prioritize Repair Tasks
Phase 7: Spec-Verified Repair Loop (patch-agent minimal fix → focused spec verification → review → public smoke → optional sandbox/selector → unmasking diagnostics)
Phase 8: Stability Loop (3x/5x rerun, focused/shuffle, flaky-to-task)
Phase 9: Final Goal Gate + Report & Deliver
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT init
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/api_contract_builder.py --root $PROJECT_ROOT
python3 <SUBMISSION_ROOT>/work/tools/scripts/business_rule_builder.py --root $PROJECT_ROOT
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/spring_scanner.py --root $PROJECT_ROOT
python3 <SUBMISSION_ROOT>/work/tools/scripts/dto_analyzer.py --root $PROJECT_ROOT
python3 <SUBMISSION_ROOT>/work/tools/scripts/exception_analyzer.py --root $PROJECT_ROOT
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/contract_checker.py --root $PROJECT_ROOT
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/spec_test_generator.py --root $PROJECT_ROOT
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT baseline-tests
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT prioritize
```

## Phase 7: Fix Loop

**Goal**: Repair one issue at a time with minimal, design-backed code changes.

### Default path

For each P0/P1 repair task:

1. Invoke `shophub-patch-agent`.
2. The patch agent restates the issue summary, design/API evidence, exact code location, API safety boundary, and expected verification.
3. Apply the smallest necessary edit under `code/**`.
4. Run focused verification when feasible.
5. Run contract checker and forbidden-change guard.
6. Invoke `shophub-review-agent`.
7. Run public black-box matrix when the focused fix is accepted.
8. Convert newly exposed failures into repair tasks.

### Optional candidate path

Use `candidate_sandbox.py` and `patch_selector.py` only when:

- the issue is P0;
- the first minimal patch fails;
- there are multiple plausible repair strategies;
- API compatibility risk is high.

The default competition path is one minimal patch per issue.

### Record round
```bash
python3 <SUBMISSION_ROOT>/work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT finish-round \
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
python3 <SUBMISSION_ROOT>/work/tools/scripts/stability_runner.py --root $PROJECT_ROOT --runs 3
python3 <SUBMISSION_ROOT>/work/tools/scripts/stability_runner.py --root $PROJECT_ROOT --runs 5 --shuffle
python3 <SUBMISSION_ROOT>/work/tools/scripts/stability_runner.py --root $PROJECT_ROOT --focused <Class#method>
```
Requirement: ALL 3 runs must pass.

### Step 8.2: Final forbidden-change guard
```bash
python3 <SUBMISSION_ROOT>/work/tools/scripts/forbidden_change_guard.py --root $PROJECT_ROOT --strict
```
Requirement: ZERO blockers, ZERO warnings (in strict mode).

### Step 8.3: Final contract check
```bash
python3 <SUBMISSION_ROOT>/work/tools/scripts/contract_checker.py --root $PROJECT_ROOT
```
Requirement: ZERO P0 issues.

### Step 8.4: Final goal gate
```bash
python3 <SUBMISSION_ROOT>/work/tools/scripts/final_goal_gate.py --root $PROJECT_ROOT
```
Requirement: returns 0. This is the only machine-accepted DONE signal.

### Step 8.5: Invoke stability-verifier
Call `stability-verifier` subagent to:
- Analyze flaky indicators across runs
- Verify no regression from baseline
- Confirm all gates pass

## Phase 9: Report & Deliver

### Step 9.1: Generate reports
```bash
python3 <SUBMISSION_ROOT>/work/tools/scripts/shophub_goal_runner.py --root $PROJECT_ROOT report
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
- `patch-generator` — Optional candidate generation for high-risk repairs
- `stability-verifier` — Validation + guard + stability

### Legacy (existing, preserved)
- `shophub-spec-librarian` — design rule filling
- `shophub-api-guardian` — API contract guard
- `shophub-code-mapper` — code map
- `shophub-module-mapper` — module mapping
- `shophub-test-diagnoser` — test diagnosis
- `shophub-module-auditor` — per-module audit
- `shophub-cross-cut-auditor` — cross-module audit
- `shophub-patch-agent` — primary code-editing actor; applies one minimal design-backed fix per issue
- `shophub-review-agent` — patch review
- `shophub-report-writer` — report generation

## Deterministic Scripts

All under `work/tools/scripts/`:

| Script | Phase | Purpose |
|--------|-------|---------|
| `shophub_goal_runner.py` | All | Core runner: init, specs, API, map, tests, audit, prioritize, report |
| `api_contract_builder.py` | 1 | Extract REST endpoints from markdown |
| `business_rule_builder.py` | 1 | Extract business rules from design-docs |
| `public_case_rule_builder.py` | 1 | competition-final: write public diagnostics only; local-public-debug: derive public-case rules |
| `feature_registry.py` | 1-9 | Maintain external feature pass/fail memory |
| `spring_scanner.py` | 2 | Scan Spring Boot code → repo_map.json |
| `dto_analyzer.py` | 2 | DTO validation coverage analysis |
| `exception_analyzer.py` | 2 | ExceptionHandler coverage analysis |
| `contract_checker.py` | 3 | Deterministic API contract consistency check |
| `checkers/money_formula_checker.py` | 3 | Money formula risk detection |
| `checkers/state_machine_checker.py` | 3 | State-machine and state-guard detection |
| `checkers/clock_usage_checker.py` | 3 | Direct system clock usage detection |
| `checkers/failure_isolation_checker.py` | 3 | Post-action failure isolation detection |
| `checkers/sorting_pagination_checker.py` | 3 | Stable ordering and pagination detection |
| `spec_test_generator.py` | 4 | Generate MockMvc tests from contracts |
| `blackbox_explorer.py` | 5,7 | competition-final public smoke; local-public-debug suite/class/method/sweep/focused/shuffle exploration |
| `rule_issue_builder.py` | 6 | Convert rules/checkers/matrix into issues.jsonl |
| `repair_task_builder.py` | 6 | Build repair_tasks.json and repair_tasks.jsonl |
| `candidate_sandbox.py` | 7 | Optionally validate high-risk candidate patches in isolated workspaces |
| `patch_selector.py` | 7 | Optionally hard-filter and score high-risk candidate patches |
| `review/fresh_context_review.py` | 7 | Fresh-context diff review scaffold |
| `review/hardcoding_guard.py` | 7,8 | Detect public fixture hardcoding risks |
| `unmasking_gate.py` | 7 | Detect newly exposed failures after patch application |
| `flaky_to_repair_tasks.py` | 8 | Convert stability findings into repair tasks |
| `forbidden_change_guard.py` | 7,8 | Forbidden change detection |
| `stability_runner.py` | 8 | Multi-run stability verification |
| `final_goal_gate.py` | 9 | Machine-checkable DONE gate |
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
- `python3 <SUBMISSION_ROOT>/work/tools/scripts/stability_runner.py --root $PROJECT_ROOT --runs 3` passes
- `python3 <SUBMISSION_ROOT>/work/tools/scripts/forbidden_change_guard.py --root $PROJECT_ROOT --strict` passes
- `python3 <SUBMISSION_ROOT>/work/tools/scripts/contract_checker.py --root $PROJECT_ROOT` has zero P0 issues
- `python3 <SUBMISSION_ROOT>/work/tools/scripts/review/hardcoding_guard.py --root $PROJECT_ROOT` passes
- `python3 <SUBMISSION_ROOT>/work/tools/scripts/final_goal_gate.py --root $PROJECT_ROOT` returns 0
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
