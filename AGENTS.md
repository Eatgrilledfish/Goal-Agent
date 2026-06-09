# ShopHub Competition Agent Rules

## Mission

Use AI agents to compare design documents, frozen REST API contract, and Java Spring Boot implementation. Find inconsistencies and fix code to match design documents while preserving the frozen API contract.

## Source of Truth

1. `design-docs/` is the business source of truth.
2. `API基线文档.md` is the frozen REST API contract.
3. `test-cases/` public black-box tests are diagnostic signals only.
4. Current code behavior is not authoritative when it conflicts with design documents.

## Forbidden Changes

Do not modify:

- `design-docs/**`
- `API基线文档.md`
- `比赛说明.md`
- `黑盒用例说明.md`

Do not change:

- REST API URL
- HTTP Method
- Request Header definition
- Request Body field names or types
- Response Body field names or types

## Allowed Changes

You may modify:

- Java source code
- `application.yml`
- `application.yaml`
- `pom.xml`
- JUnit tests under `code/`

Avoid modifying `test-cases/`.

## Required Workflow

1. Read design documents.
2. Read API baseline.
3. Build code map.
4. Run baseline tests.
5. Find design-code inconsistencies.
6. Fix code in small steps.
7. Run focused tests after each fix.
8. Run full verification before final report.
9. Produce `修复报告.md`.

## Verification Commands

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```

## Completion Definition

A task is not complete unless:

- Design basis is recorded.
- Code behavior before fix is recorded.
- Modified files are listed.
- API contract is preserved.
- Relevant tests were run.
- Risks are documented.
