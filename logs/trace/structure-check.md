# Structure Check

## Required package structure

```text
/INSTRUCTION.md
/work
/work/skills/goal-agent-spec-driven/SKILL.md
/work/skills/*.md
/work/tools/scripts/*.py
/work/tools/config/*.json
/result
/result/output.md
/logs
/logs/interaction.md
/logs/trace
```

## Runtime architecture

The submission uses subagent-first execution.

```text
INSTRUCTION.md
  -> goal-agent-spec-driven/SKILL.md
  -> shophub-orchestrator.md
  -> shophub-* subagents
  -> helper scripts
  -> final_goal_gate.py
```

The legacy autonomous path is intentionally removed from the competition runtime.
