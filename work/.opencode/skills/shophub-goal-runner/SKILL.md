---
name: shophub-goal-runner
description: Use this skill in OpenCode for the HW-ICT-CMP-04 ShopHub design-implementation consistency competition. It coordinates hidden subagents to read design-docs and README API baseline, run Maven public black-box tests, diagnose concrete Java/Spring defects, patch code in small rounds, preserve the frozen /api/v1 REST contract, and write 修复报告.md.
---

# ShopHub Goal Runner

Use this skill when working in the `HW-ICT-CMP-04` ShopHub competition repository.

The goal is to maximize hidden and public test pass rate by fixing design-code inconsistencies in `code/` while preserving the frozen REST API contract.

## Real Competition Layout

Accept this repository layout:

```text
README.md
code/
design-docs/
test-cases/
```

Do not require these older placeholder files:

```text
API基线文档.md
比赛说明.md
黑盒用例说明.md
```

The frozen API contract is in:

- `README.md`, section `6. API 基线（冻结契约）`
- `design-docs/附录A-API接口参考.md`

The design truth is all files under `design-docs/`.

## Mandatory Subagents

Use the Task tool when available. Invoke these hidden agents by name:

- `shophub-spec-librarian`
- `shophub-api-guardian`
- `shophub-code-mapper`
- `shophub-test-diagnoser`
- `shophub-module-auditor`
- `shophub-patch-agent`
- `shophub-review-agent`
- `shophub-report-writer`

Do not run the competition as a single monolithic pass unless the runtime cannot invoke subagents.

## Module Mapping

Use this fixed mapping before auditing:

| Design doc | Code module |
|---|---|
| `04-用户服务设计.md` | `code/ecommerce-user` |
| `05-商品服务设计.md` | `code/ecommerce-product` |
| `06-库存服务设计.md` | `code/ecommerce-inventory` |
| `07-购物车服务设计.md` | `code/ecommerce-cart` |
| `08-订单服务设计.md` | `code/ecommerce-order` |
| `09-支付服务设计.md` | `code/ecommerce-payment` |
| `10-促销服务设计.md` | `code/ecommerce-promotion` |
| `11-物流服务设计.md` | `code/ecommerce-logistics` |
| `12-积分与会员服务设计.md` | `code/ecommerce-loyalty` |
| `13-评价服务设计.md` | `code/ecommerce-review` |
| `14-发票与结算设计.md` | `code/ecommerce-payment` |
| `15-本地通知组件设计.md` | `code/ecommerce-common` |
| Runtime/test support APIs | `code/ecommerce-app`, `code/ecommerce-common` |

Never create "module missing" issues merely because a design filename does not match a Maven artifact.

## Required Workflow

1. Preflight:
   - Verify `README.md`, `code/pom.xml`, `design-docs/`, and `test-cases/pom.xml`.
   - Verify `mvn -version`.
   - Record `git status --short`.
2. Read design:
   - Call `shophub-spec-librarian`.
   - Extract concrete business rules, not whole-document summaries.
3. Read API:
   - Call `shophub-api-guardian`.
   - Treat `README.md` section 6 and `design-docs/附录A-API接口参考.md` as the frozen API baseline.
4. Map code:
   - Call `shophub-code-mapper`.
   - Use the fixed module mapping above.
5. Run tests:
   - Call `shophub-test-diagnoser`.
   - Run public black-box tests after installing business code.
6. Audit:
   - Call `shophub-module-auditor` per failed public behavior and high-risk design module.
   - Each issue must cite design/API evidence and exact code locations.
7. Fix loop:
   - Call `shophub-patch-agent` for one issue at a time.
   - Call `shophub-api-guardian` after each patch.
   - Run focused tests, then full public tests when feasible.
   - Call `shophub-review-agent` before accepting a round.
8. Report:
   - Call `shophub-report-writer`.
   - Write `修复报告.md`.

## Verification Commands

Use these commands in order:

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install -DskipTests
mvn -f test-cases/pom.xml test
```

Focused public tests:

```bash
mvn -f test-cases/pom.xml -Dtest=PubBasicFlowTest test
mvn -f test-cases/pom.xml -Dtest=PubAdditionalBehaviorTest test
```

Do not use `no-tests` in a real competition run.

## Repair Priorities

Prioritize issues that affect:

1. Application compile/startup.
2. Public black-box test failures.
3. API status codes and response body compatibility.
4. User auth/activation/admin bootstrap.
5. Product + inventory setup.
6. Cart/order/payment happy path.
7. Promotion calculation.
8. Refund/invoice/settlement.
9. Logistics/loyalty/review behavior.
10. Hidden-test design rules not covered by public tests.

Public tests are symptoms, but they are valuable triage signals. Fix the underlying design behavior, not a specific test fixture.

## Safety Rules

- Do not modify `design-docs/**`.
- Do not modify `README.md` API baseline or competition instructions.
- Avoid modifying `test-cases/**`.
- Do not change `/api/v1/` URLs, HTTP methods, request headers, request fields, response fields, success status codes, or public error-code semantics.
- Do not expose database reset/bootstrap APIs.
- Do not hardcode fixture values from public tests.
- Keep each repair round small and reviewable.

## Local Helper Scripts

If present, deterministic helper scripts are under:

```text
.opencode/shophub/tools/scripts/
```

Use them for indexing, logs, and report scaffolding only. They do not replace subagent analysis or code repair.

## Completion

DONE requires:

- `mvn -f code/pom.xml test` succeeds.
- `mvn -f code/pom.xml install -DskipTests` succeeds.
- `mvn -f test-cases/pom.xml test` succeeds, or remaining failures are explicitly documented with design-backed risk.
- API baseline remains compatible.
- `修复报告.md` exists.
