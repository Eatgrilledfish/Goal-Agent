# ShopHub Goal Runner Work Rules

Treat this `work/` directory as the delivery root.

## Runtime Assets

All runnable OpenCode assets are under this directory:

```text
.opencode/commands/shophub.md
.opencode/agents/shophub-*.md
.opencode/skills/shophub-goal-runner/SKILL.md
tools/scripts/*.py
install_opencode.sh
```

Do not depend on files outside `work/` at runtime.

## Installation

Install into the target `HW-ICT-CMP-04` repository:

```bash
bash install_opencode.sh /path/to/HW-ICT-CMP-04
```

The installer copies this work package into the target repository's `.opencode/` directory.

## Target Repository Truth

The target competition repository layout is:

```text
README.md
code/
design-docs/
test-cases/
```

The frozen API baseline is:

- `README.md`, section `6. API 基线（冻结契约）`
- `design-docs/附录A-API接口参考.md`

Do not require `API基线文档.md`, `比赛说明.md`, or `黑盒用例说明.md`.

## Safety

- Do not modify target `design-docs/**`.
- Do not modify target `README.md` API baseline or competition instructions.
- Avoid modifying target `test-cases/**`.
- Do not change `/api/v1/` URLs, HTTP methods, request headers, request fields, documented response fields, success status codes, or public error-code semantics.
- Additive response aliases are allowed only when they expose existing domain state, do not remove or rename documented fields, and are needed for API compatibility observed in README, appendix A, or public black-box fixtures.
- Do not hardcode public test fixture values.
