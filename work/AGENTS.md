# ShopHub Goal Runner Work Rules

Treat this `work/` directory as the delivery root.

## Runtime Assets

All runnable assets are under this directory:

```text
skill/SKILL.md
skills/shophub-*.md
tools/scripts/*.py
```

Do not depend on files outside `work/` at runtime.

## Execution

Do not install a plugin or copy files into the target repository. The platform loads `/INSTRUCTION.md`; the running agent should read `work/skill/SKILL.md`, then load subagent definitions from `work/skills/*.md`, and execute against the target repository in place.

## Target Repository Truth

The target competition repository layout is:

```text
README.md
code/
design-docs/
test-cases/
```

The frozen API baseline is identified semantically from:

- `README.md` (its API baseline / frozen-contract section — not a hard-coded section number).
- The design-doc file(s) that carry the REST API reference (semantically identified, not a hard-coded filename).

Use the target repository files listed above as the complete required competition inputs.

## Safety

- Do not modify target `design-docs/**`.
- Do not modify target `README.md` API baseline or competition instructions.
- Avoid modifying target `test-cases/**`.
- Do not change `/api/v1/` URLs, HTTP methods, request headers, request fields, documented response fields, success status codes, or public error-code semantics.
- Additive response aliases are allowed only when they expose existing domain state, do not remove or rename documented fields, and are needed for API compatibility observed in README, appendix A, or public black-box fixtures.
- Do not hardcode public test fixture values.
