#!/usr/bin/env python3
"""Create a semantic-neutral finding handoff scaffold from one task and claim."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import stage_artifact_validator as sav


def _valid_deferred_task(task: dict[str, Any]) -> bool:
    raw_evidence = task.get("defer_evidence")
    evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
    attempts = evidence.get("attempts")
    return (
        task.get("status") == "deferred"
        and bool(task.get("defer_reason"))
        and evidence.get("kind") in {"provider_failure", "tool_failure"}
        and isinstance(attempts, list)
        and len(attempts) >= 2
        and all(
            isinstance(attempt, dict)
            and attempt.get("attempt_id")
            and attempt.get("outcome") == "failed"
            and attempt.get("evidence")
            for attempt in attempts
        )
    )


def _index_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ValueError("; ".join(errors))
    indexed = {str(item.get(key)): item for item in values if item.get(key)}
    if len(indexed) != len(values):
        raise ValueError(f"{path.name} has missing or duplicate {key}")
    return indexed


def _current_task_validation(state_root: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    manifest_path = state_root / "workspace_manifest.json"
    state_path = state_root / "agent_loop_state.json"
    try:
        manifest = ac.load_json(manifest_path)
        state = ac.load_json(state_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot load prepared session for task validation: {exc}"]
    log_root_value = manifest.get("paths", {}).get("log_root") if isinstance(manifest, dict) else None
    if not isinstance(log_root_value, str) or not log_root_value:
        return {}, ["workspace_manifest.json lacks paths.log_root"]
    trace_path = Path(log_root_value).resolve() / "trace" / "task_validation.json"
    if not trace_path.is_file():
        return {}, [f"current passed task validation is missing: {trace_path}"]
    try:
        trace = ac.load_json(trace_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot load task validation trace: {exc}"]
    if not isinstance(trace, dict):
        return {}, ["task validation trace must be an object"]
    expected_inputs, expected_combined = sav._input_digests(
        state_root, sav._stage_inputs(state_root, "task"),
    )
    session_id = str(state.get("session_id") or "") if isinstance(state, dict) else ""
    if trace.get("stage") != "task":
        errors.append("task validation trace has the wrong stage")
    if trace.get("passed") is not True:
        errors.append("task validation trace has not passed")
    if trace.get("session_id") != session_id:
        errors.append("task validation trace belongs to a different session")
    if trace.get("input_digests") != expected_inputs:
        errors.append("task validation trace is stale for current frontier inputs")
    if trace.get("combined_input_sha256") != expected_combined:
        errors.append("task validation combined digest is stale")
    return trace, errors


def _ordered_rounds(path: Path) -> list[dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ValueError("; ".join(errors))
    return values


def _eligible_pending_frontier(
    tasks: dict[str, dict[str, Any]], rounds: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Return at most two ordered pending tasks from the earliest open round."""
    for index, round_item in enumerate(rounds, start=1):
        round_id = str(round_item.get("round_id") or f"#{index}")
        task_ids = round_item.get("task_ids")
        if not isinstance(task_ids, list):
            raise ValueError(f"investigation round {round_id} task_ids must be an array")
        ordered = [str(task_id) for task_id in task_ids if isinstance(task_id, str) and task_id]
        open_tasks = [
            task_id for task_id in ordered
            if task_id in tasks and tasks[task_id].get("status") in {"pending", "in_progress"}
        ]
        if not open_tasks:
            continue
        active = sum(tasks[task_id].get("status") == "in_progress" for task_id in open_tasks)
        pending = [task_id for task_id in open_tasks if tasks[task_id].get("status") == "pending"]
        return round_id, pending[:max(0, 2 - active)]
    return "", []


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
    claims_path = Path(args.claims).resolve()
    output = Path(args.output).resolve()
    state_root = tasks_path.parent
    template_root = (state_root / "handoff-templates" / "investigators").resolve()
    if tasks_path != (state_root / "investigation_tasks.jsonl").resolve():
        print(json.dumps({
            "passed": False,
            "error": "--tasks must be the canonical state investigation_tasks.jsonl",
        }))
        return 2
    if claims_path != (state_root / "design_claims.jsonl").resolve():
        print(json.dumps({
            "passed": False,
            "error": "--claims must be the canonical state design_claims.jsonl",
        }))
        return 2
    if output.parent != template_root:
        print(json.dumps({
            "passed": False,
            "error": f"output must be directly under {template_root}",
        }))
        return 2
    if output.name != f"{args.task_id}.json":
        print(json.dumps({
            "passed": False,
            "error": f"output filename must be {args.task_id}.json",
        }))
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
    if output.exists() and not args.force:
        print(json.dumps({"passed": False, "error": "output already exists"}))
        return 2
    _, validation_errors = _current_task_validation(state_root)
    if validation_errors:
        print(json.dumps({
            "passed": False,
            "error": "current passed task validation is required before template creation",
            "validation_errors": validation_errors,
        }))
        return 3
    try:
        tasks = _index_jsonl(tasks_path, "task_id")
        rounds = _ordered_rounds(state_root / "investigation_rounds.jsonl")
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1
    validated_ids = set(gate.get("validated_ids", [])) if isinstance(gate, dict) else set()
    deferred_ids = {
        f"FINDING-{task_id}" for task_id, task in tasks.items()
        if _valid_deferred_task(task)
    }
    existing_ids: set[str] = set()
    if template_root.is_dir():
        for path in template_root.glob("*.json"):
            try:
                value = ac.load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("finding_id"):
                existing_ids.add(str(value["finding_id"]))
    unresolved = existing_ids - validated_ids - deferred_ids
    if len(unresolved) >= 2 and f"FINDING-{args.task_id}" not in unresolved:
        print(json.dumps({
            "passed": False,
            "error": "two investigator templates are already unresolved; merge or repair that batch first",
            "unresolved_ids": sorted(unresolved),
        }))
        return 3
    try:
        claims = _index_jsonl(claims_path, "claim_id")
        task = tasks.get(args.task_id)
        if not task:
            raise ValueError(f"unknown task_id: {args.task_id}")
        round_id, eligible_task_ids = _eligible_pending_frontier(tasks, rounds)
        if task.get("status") != "pending":
            raise ValueError(
                f"task {args.task_id} is not pending and cannot receive a new investigator template"
            )
        if args.task_id not in eligible_task_ids:
            raise ValueError(
                f"task {args.task_id} is outside the ordered two-task frontier for earliest "
                f"open round {round_id or '(none)'}; eligible={eligible_task_ids}"
            )
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
