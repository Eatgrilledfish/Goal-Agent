#!/usr/bin/env python3
"""Create a semantic-neutral finding handoff scaffold from one task and claim."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import stage_artifact_validator as sav


def _index_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ValueError("; ".join(errors))
    indexed = {str(item.get(key)): item for item in values if item.get(key)}
    if len(indexed) != len(values):
        raise ValueError(f"{path.name} has missing or duplicate {key}")
    return indexed


def _current_task_validation(
    state_root: Path, task_id: str,
) -> tuple[dict[str, Any], list[str]]:
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
    trace_root = Path(log_root_value).resolve() / "trace"
    plan_path = trace_root / "task_plan_validation.json"
    lifecycle_path = trace_root / "task_lifecycle_validation.json"
    missing = [path for path in (plan_path, lifecycle_path) if not path.is_file()]
    if missing:
        return {}, [
            "current task plan/lifecycle validation is missing: "
            + ", ".join(str(path) for path in missing)
        ]
    try:
        plan_trace = ac.load_json(plan_path)
        lifecycle_trace = ac.load_json(lifecycle_path)
        contract = ac.load_json(state_root / "agent_loop_contract.json")
        architecture = ac.load_json(state_root / "architecture_map.json")
        claims = _index_jsonl(state_root / "design_claims.jsonl", "claim_id")
        risks = _index_jsonl(state_root / "risk_observations.jsonl", "observation_id")
        tasks = _index_jsonl(state_root / "investigation_tasks.jsonl", "task_id")
        findings = _index_jsonl(state_root / "investigation_findings.jsonl", "finding_id")
        rounds = _ordered_rounds(state_root / "investigation_rounds.jsonl")
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot load task plan/lifecycle validation inputs: {exc}"]
    except ValueError as exc:
        return {}, [f"cannot index task plan/lifecycle validation inputs: {exc}"]
    if not isinstance(plan_trace, dict) or not isinstance(lifecycle_trace, dict):
        return {}, ["task plan/lifecycle validation traces must be objects"]
    session_id = str(state.get("session_id") or "") if isinstance(state, dict) else ""
    task = tasks.get(task_id)
    if task is None:
        return {}, [f"unknown task_id: {task_id}"]
    claim = claims.get(str(task.get("claim_id") or ""))
    task_findings = [
        finding for finding in findings.values() if finding.get("task_id") == task_id
    ]
    expected_plan_sha256 = sav.task_plan_snapshot_sha256(
        state_root, contract=contract, architecture=architecture, claims=claims,
        risks=risks, tasks=tasks, rounds=rounds,
    )
    expected_lifecycle_sha256 = sav.task_lifecycle_snapshot_sha256(
        tasks=tasks, findings=findings, rounds=rounds,
    )
    expected_plan_candidate = sav.task_plan_digest(task, claim, rounds)
    expected_lifecycle_candidate = sav.task_lifecycle_digest(task, task_findings)
    for trace, expected_stage, label in (
        (plan_trace, "task-plan", "task plan"),
        (lifecycle_trace, "task-lifecycle", "task lifecycle"),
    ):
        if trace.get("stage") != expected_stage:
            errors.append(f"{label} validation trace has the wrong stage")
        if trace.get("session_id") != session_id:
            errors.append(f"{label} validation trace belongs to a different session")
        if trace.get("global_passed") is not True:
            errors.append(f"{label} global validation has not passed")
        if task_id not in trace.get("valid_task_ids", []):
            errors.append(f"{label} validation did not accept candidate {task_id}")
    if plan_trace.get("task_plan_sha256") != expected_plan_sha256:
        errors.append("task plan validation is stale for current stable plan inputs")
    if lifecycle_trace.get("task_lifecycle_sha256") != expected_lifecycle_sha256:
        errors.append("task lifecycle validation is stale for current lifecycle inputs")
    if plan_trace.get("candidate_digests", {}).get(task_id) != expected_plan_candidate:
        errors.append(f"task plan candidate digest is stale for {task_id}")
    if lifecycle_trace.get("candidate_digests", {}).get(task_id) != expected_lifecycle_candidate:
        errors.append(f"task lifecycle candidate digest is stale for {task_id}")
    return {"plan": plan_trace, "lifecycle": lifecycle_trace}, errors


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
    obligation = claim.get("obligation")
    observable_result = claim.get("observable_result")
    if not isinstance(obligation, str) or not obligation.strip():
        raise ValueError("supplied claim needs one non-empty obligation")
    if not isinstance(observable_result, str) or not observable_result.strip():
        raise ValueError("supplied claim needs one non-empty observable_result")
    expected_obligation_digest = sav.claim_obligation_sha256(claim)
    if task.get("obligation_sha256") != expected_obligation_digest:
        raise ValueError("task obligation_sha256 does not match supplied claim")
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
        "claim_branch": task.get("claim_branch", ""),
        "obligation_sha256": task.get("obligation_sha256", ""),
        "hypothesis": task.get("hypothesis", ""),
        "expected_behavior": (
            f"{obligation.strip()} Observable result: {observable_result.strip()}"
        ),
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--task-id")
    mode.add_argument(
        "--frontier", action="store_true",
        help="Generate templates for up to two eligible tasks in the earliest open round.",
    )
    parser.add_argument("--output")
    parser.add_argument("--output-dir")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    tasks_path = Path(args.tasks).resolve()
    claims_path = Path(args.claims).resolve()
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
    if args.frontier:
        if args.output or not args.output_dir:
            print(json.dumps({
                "passed": False,
                "error": "--frontier requires --output-dir and does not accept --output",
            }))
            return 2
        output_dir = Path(args.output_dir).resolve()
        if output_dir != template_root:
            print(json.dumps({
                "passed": False,
                "error": f"output-dir must be the canonical template root: {template_root}",
            }))
            return 2
    else:
        if not args.output or args.output_dir:
            print(json.dumps({
                "passed": False,
                "error": "--task-id requires --output and does not accept --output-dir",
            }))
            return 2
        output = Path(args.output).resolve()
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
        if output.exists() and not args.force:
            print(json.dumps({"passed": False, "error": "output already exists"}))
            return 2
    try:
        tasks = _index_jsonl(tasks_path, "task_id")
        rounds = _ordered_rounds(state_root / "investigation_rounds.jsonl")
        claims = _index_jsonl(claims_path, "claim_id")
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1

    if args.frontier:
        round_id, eligible_task_ids = _eligible_pending_frontier(tasks, rounds)
        prepared: list[tuple[str, Path, dict[str, Any]]] = []
        validation_errors: dict[str, list[str]] = {}
        try:
            for task_id in eligible_task_ids:
                _traces, task_errors = _current_task_validation(state_root, task_id)
                if task_errors:
                    validation_errors[task_id] = task_errors
                    continue
                task = tasks[task_id]
                claim = claims.get(str(task.get("claim_id") or ""))
                if claim is None:
                    validation_errors[task_id] = [
                        f"task references unknown claim_id: {task.get('claim_id')}"
                    ]
                    continue
                output = template_root / f"{task_id}.json"
                if output.exists() and not args.force:
                    validation_errors[task_id] = [f"template already exists: {output}"]
                    continue
                prepared.append((task_id, output, finding_template(task, claim)))
        except (OSError, ValueError) as exc:
            print(json.dumps({"passed": False, "error": str(exc)}))
            return 1
        if validation_errors:
            print(json.dumps({
                "passed": False,
                "mode": "frontier",
                "round_id": round_id,
                "task_ids": eligible_task_ids,
                "error": (
                    "current task plan and lifecycle validation is required "
                    "before frontier template creation"
                ),
                "validation_errors": validation_errors,
            }))
            return 3
        outputs: list[dict[str, str]] = []
        for task_id, output, template in prepared:
            ac.save_json(output, template)
            outputs.append({
                "task_id": task_id,
                "finding_id": str(template["finding_id"]),
                "output": str(output),
            })
        print(json.dumps({
            "passed": True,
            "mode": "frontier",
            "round_id": round_id,
            "task_ids": eligible_task_ids,
            "count": len(outputs),
            "outputs": outputs,
        }))
        return 0

    assert args.task_id is not None
    _, validation_errors = _current_task_validation(state_root, args.task_id)
    if validation_errors:
        print(json.dumps({
            "passed": False,
            "error": "current task plan and lifecycle validation is required before template creation",
            "validation_errors": validation_errors,
        }))
        return 3
    try:
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
