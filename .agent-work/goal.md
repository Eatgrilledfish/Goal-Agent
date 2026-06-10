# ShopHub Goal Runner State

Generated: 2026-06-10T08:00:16+08:00

## Objective

Compare ShopHub design documents, frozen REST API contract, and Spring Boot implementation. Find and repair design-code inconsistencies in small, evidence-backed rounds while preserving the API contract.

## Required Inputs Missing

- `code`
- `design-docs`
- `test-cases`
- `API基线文档.md`
- `黑盒用例说明.md`
- `比赛说明.md`

## Safety Rules

- Do not modify `design-docs/**`.
- Do not modify `API基线文档.md`.
- Do not modify `比赛说明.md` or `黑盒用例说明.md`.
- Avoid modifying `test-cases/**`.
- Do not change REST API URLs, methods, headers, request fields, response fields, or error-code semantics.

## Verification Commands

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```
