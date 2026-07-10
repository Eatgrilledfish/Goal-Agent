#!/usr/bin/env python3
"""Create a semantic-neutral finding handoff scaffold from one task and claim."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac


def _index_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ValueError("; ".join(errors))
    indexed = {str(item.get(key)): item for item in values if item.get(key)}
    if len(indexed) != len(values):
        raise ValueError(f"{path.name} has missing or duplicate {key}")
    return indexed


def finding_template(task: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    claim_id = str(task.get("claim_id") or "")
    if not task_id or not claim_id:
        raise ValueError("task needs task_id and claim_id")
    if claim.get("claim_id") != claim_id:
        raise ValueError("task claim_id does not match supplied claim")
    if task.get("session_id") != claim.get("session_id"):
        raise ValueError("task and claim sessions do not match")
    design_evidence = {
        "document": claim.get("document", ""),
        "path": claim.get("path", ""),
        "section": claim.get("section", ""),
        "line_start": claim.get("line_start", 0),
        "line_end": claim.get("line_end", 0),
        "quote": claim.get("quote", ""),
    }
    return {
        "finding_id": f"FINDING-{task_id}",
        "session_id": task.get("session_id", ""),
        "task_id": task_id,
        "claim_id": claim_id,
        "hypothesis": task.get("question", ""),
        "expected_behavior": claim.get("behavior", ""),
        "observed_behavior": "",
        "design_evidence": [design_evidence],
        "code_evidence": [{
            "file": "", "line_start": 0, "line_end": 0, "symbol": "", "snippet": "",
        }],
        "supporting_evidence": [""],
        "disconfirming_evidence": [],
        "false_positive_checks": [
            {"question": "", "method": "", "target": "", "result": ""},
            {"question": "", "method": "", "target": "", "result": ""},
        ],
        "tool_trace": [
            {
                "seq": 1, "kind": "design_read", "tool": "read",
                "target": f"{design_evidence['path']}:{design_evidence['line_start']}-{design_evidence['line_end']}",
                "purpose": "Re-read the supplied design claim.", "result": "",
            },
            {
                "seq": 2, "kind": "code_search", "tool": "search", "target": "",
                "purpose": "Locate the implementation and relevant alternate paths.", "result": "",
            },
            {
                "seq": 3, "kind": "code_read", "tool": "read", "target": "",
                "purpose": "Derive actual behavior from reachable code.", "result": "",
            },
            {
                "seq": 4, "kind": "reverse_check", "tool": "search", "target": "",
                "purpose": "Check for compensating, configured, or parallel behavior.", "result": "",
            },
        ],
        "dynamic_probe_selection": {"disposition": "", "reason": ""},
        "assessment": "",
        "review_lenses": task.get("review_lenses", []),
        "recommendation": "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a generic finding handoff scaffold.")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--claims", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    tasks_path = Path(args.tasks).resolve()
    output = Path(args.output).resolve()
    state_root = tasks_path.parent
    template_root = (state_root / "handoff-templates" / "investigators").resolve()
    if output.parent != template_root:
        print(json.dumps({
            "passed": False,
            "error": f"output must be directly under {template_root}",
        }))
        return 2
    if output.exists() and not args.force:
        print(json.dumps({"passed": False, "error": "output already exists"}))
        return 2
    gate_path = state_root / "investigator_batch_gate.json"
    gate = ac.load_json(gate_path) if gate_path.is_file() else {}
    if gate and gate.get("passed") is not True:
        print(json.dumps({
            "passed": False,
            "error": "previous investigator batch has not passed merge; repair its invalid_ids first",
            "gate": str(gate_path),
        }))
        return 3
    validated_ids = set(gate.get("validated_ids", [])) if isinstance(gate, dict) else set()
    existing_ids: set[str] = set()
    if template_root.is_dir():
        for path in template_root.glob("*.json"):
            try:
                value = ac.load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("finding_id"):
                existing_ids.add(str(value["finding_id"]))
    unresolved = existing_ids - validated_ids
    if len(unresolved) >= 2 and f"FINDING-{args.task_id}" not in unresolved:
        print(json.dumps({
            "passed": False,
            "error": "two investigator templates are already unresolved; merge or repair that batch first",
            "unresolved_ids": sorted(unresolved),
        }))
        return 3
    try:
        tasks = _index_jsonl(tasks_path, "task_id")
        claims = _index_jsonl(Path(args.claims).resolve(), "claim_id")
        task = tasks.get(args.task_id)
        if not task:
            raise ValueError(f"unknown task_id: {args.task_id}")
        claim = claims.get(str(task.get("claim_id") or ""))
        if not claim:
            raise ValueError(f"task references unknown claim_id: {task.get('claim_id')}")
        template = finding_template(task, claim)
        ac.save_json(output, template)
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1
    print(json.dumps({
        "passed": True, "task_id": args.task_id,
        "finding_id": template["finding_id"], "output": str(output),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
