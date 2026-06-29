---
name: shophub-goal-runner
description: Design-implementation consistency repair skill. Coordinates subagents to read design-docs and the README API baseline, run Maven black-box tests, diagnose Java/Spring defects via per-module fan-out, patch code in small rounds, preserve the frozen REST contract, and write 修复报告.md.
---

# Goal Runner

Use this skill when the CLI loads `/INSTRUCTION.md` and runs against a competition repository with the layout below. The framework is **domain-agnostic**: module discovery, design-doc → code mapping, and audit rules are all inferred at runtime — no business concepts are hard-coded.

The goal is to maximize hidden and public test pass rate by fixing design-code inconsistencies in `code/` while preserving the frozen REST API contract.

## Competition Layout

Accept this repository layout:

```text
README.md
code/
design-docs/
test-cases/
```

The frozen API contract is identified **semantically** from:
- `README.md` (its API baseline / frozen-contract section — not a hard-coded section number).
- The design-doc file(s) that carry the REST API reference (semantically identified, not a hard-coded appendix name).

The design truth is all files under `design-docs/`.

## Mandatory Subagents

Load subagent definitions from `work/skills/*.md`. Use the Task/subagent tool when available. Invoke these agents by name:

- `shophub-spec-librarian`
- `shophub-api-guardian`
- `shophub-code-mapper`
- `shophub-module-mapper`
- `shophub-test-diagnoser`
- `shophub-module-auditor`
- `shophub-cross-cut-auditor`
- `shophub-patch-agent`
- `shophub-review-agent`
- `shophub-report-writer`

Do not run as a single monolithic pass unless the runtime cannot invoke subagents; in that fallback, the main agent traverses modules sequentially (process one module's slice, write issues, release context, then the next).

## Module Mapping

Module discovery is dynamic: `shophub-code-mapper` scans `code/**/pom.xml` and writes `.agent-work/modules.json` (the authoritative module set — **no seed map**). `shophub-module-mapper` then semantically infers the design-doc → code-module mapping from `design_docs.json` + `modules.json` and writes `.agent-work/module_mapping.json`. Do not assume a fixed design-to-code mapping — the competition domain may differ.

Never create "module missing" issues merely because a design filename does not match a Maven artifact.

## Required Workflow

1. Preflight:
   - Verify `README.md`, `code/pom.xml`, `design-docs/`, and `test-cases/pom.xml`.
   - Verify `mvn -version`.
   - Record `git status --short`.
2. Read design:
   - Call `shophub-spec-librarian` (fills semantic fields into script-segmented spec records).
   - Extract concrete business rules, not whole-document summaries.
3. Read API:
   - Call `shophub-api-guardian` (semantically identifies the API baseline; fills endpoint DTO fields for field-level drift detection).
4. Map code:
   - Call `shophub-code-mapper` → `modules.json`, `design_docs.json`, `code_map.jsonl`.
5. Map modules:
   - Call `shophub-module-mapper` → `module_mapping.json` (design-doc → code-module, inferred, no seed).
6. Run tests:
   - Call `shophub-test-diagnoser`.
   - Run public black-box tests after building and installing the repaired Maven modules into the local Maven repository.
7. Fan-out module audit:
   - For each `code_module` in `modules.json`, invoke one `shophub-module-auditor` with its spec slice + code slice.
   - Each issue must cite design/API evidence and exact code locations (`file#method`).
   - Auditors append issues via `add-issue` (true append). Then run `audit` to validate + dedup.
8. Cross-cut audit:
   - Call `shophub-cross-cut-auditor` (L1 deterministic signals: API `field_drifts` / call chains / state enums → L2/L3 LLM).
9. Prioritize:
   - Run `prioritize` (priority from `work/config/audit_priorities.json` — generic engineering priorities, non-business).
10. Fix loop:
    - Call `shophub-patch-agent` for one issue at a time.
    - Call `shophub-api-guardian` after each patch (field-level re-compare).
    - Run focused tests, then full public tests when feasible.
    - Call `shophub-review-agent` before accepting a round.
11. Report:
    - Call `shophub-report-writer`.
    - Write `修复报告.md`.

## Verification Commands

Use local Maven and pass the project-root `maven-settings.xml` to every Maven command so the internal mirror is used:

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```

Focused public tests: `shophub-test-diagnoser` discovers test classes under `test-cases/` dynamically and selects focus targets from failure symptoms (no hard-coded test class name). Pass a focus via the runner `--test-filter` when needed.

Do not use `no-tests` in a real competition run. DONE requires `last_full_test == passed` plus no open high/medium issues — `no_open_issues_tests_not_required` is not an accepted stop reason, and tests failing with no open issue returns to fan-out rather than auto-stopping.

## Repair Priorities

Prioritize issues that affect (generic engineering order, non-business):

1. Application compile/startup.
2. Public black-box test failures.
3. API contract field-level compatibility (status codes, response fields, types).
4. Cross-module data-flow consistency (hidden tests often probe these).
5. State-machine correctness.
6. Validation / error-code correctness.
7. Hidden-test design rules not covered by public tests.

Public tests are symptoms, but they are valuable triage signals. Fix the underlying design behavior, not a specific test fixture.

## Safety Rules

- Do not modify `design-docs/**`.
- Do not modify `README.md` API baseline or competition instructions.
- Avoid modifying `test-cases/**`.
- Do not change the documented REST URL prefix, HTTP methods, request headers, request fields, documented response fields, success status codes, or public error-code semantics.
- Additive response aliases are allowed only when they expose existing domain state, do not remove or rename documented fields, and are needed for API compatibility observed in README, the API reference doc, or public black-box fixtures.
- Do not expose database reset/bootstrap APIs.
- Do not hardcode fixture values from public tests.
- Keep each repair round small and reviewable.

## Local Helper Scripts

Deterministic helper scripts are under:

```text
work/tools/scripts/
```

Use them for indexing, logs, and report scaffolding only. They do not replace subagent analysis or code repair.

## Completion

DONE requires:

- `mvn -s maven-settings.xml -f code/pom.xml test` succeeds.
- `mvn -s maven-settings.xml -f code/pom.xml install -DskipTests` succeeds.
- `mvn -s maven-settings.xml -f test-cases/pom.xml test` succeeds, or remaining failures are explicitly documented with design-backed risk.
- API baseline remains compatible.
- `修复报告.md` exists.
