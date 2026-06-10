# Command Conventions

Slash commands in this plugin are deterministic competition workflows. Every command should:

1. Run preflight checks before editing files.
2. State the plan and safety constraints.
3. Treat `design-docs/` as business truth and `API基线文档.md` as frozen contract.
4. Use tests as symptoms only, not as design authority.
5. Fix one issue or one tightly related issue group per round.
6. Re-check API safety and run relevant tests after every fix.
7. Write or update `.agent-work/` evidence and `修复报告.md`.

Command files live in `commands/` and end in `.md`. Files prefixed with `_` are documentation and are not slash commands.
