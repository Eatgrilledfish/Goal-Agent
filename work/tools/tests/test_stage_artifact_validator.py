from __future__ import annotations

import json
from pathlib import Path

from test_agent_pipeline import populate_handoffs, run_runner, workspace  # noqa: F401

import agent_common as ac


def _run_stage(workspace, command: str, *, check: bool = True):
    return run_runner(
        command,
        workspace["code"],
        workspace["design"],
        workspace["result"],
        workspace["logs"],
        check=check,
    )


def _rewrite_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def test_architecture_check_rejects_duplicate_id_and_unknown_reference(workspace):
    populate_handoffs(workspace, count=1)
    _run_stage(workspace, "architecture-check")

    state = workspace["state"]
    assert isinstance(state, Path)
    architecture_path = state / "architecture_map.json"
    architecture = ac.load_json(architecture_path)
    architecture["implementation_planes"].append(
        dict(architecture["implementation_planes"][0])
    )
    architecture["parallel_behavior_paths"] = [{
        "path_id": "PATH-UNKNOWN",
        "behavior": "The same behavior is implemented in two execution planes.",
        "plane_ids": ["PLANE-SERVICE", "PLANE-MISSING"],
        "evidence": "The architecture inventory must resolve both plane references.",
    }]
    ac.save_json(architecture_path, architecture)

    proc = _run_stage(workspace, "architecture-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "architecture_validation.json")
    assert trace["passed"] is False
    assert any("duplicate plane_id PLANE-SERVICE" in error for error in trace["errors"])
    assert any("unknown plane_ids ['PLANE-MISSING']" in error for error in trace["errors"])


def test_task_check_rejects_code_to_design_task_without_risk_refs(workspace):
    populate_handoffs(workspace)
    _run_stage(workspace, "task-check")

    state = workspace["state"]
    assert isinstance(state, Path)
    task_path = state / "investigation_tasks.jsonl"
    tasks, parse_errors = ac.load_jsonl(task_path)
    assert parse_errors == []
    code_to_design = next(
        task for task in tasks
        if task["exploration_mode"] == "code-to-design risk backtracking"
    )
    code_to_design["risk_observation_ids"] = []
    _rewrite_jsonl(task_path, tasks)

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_validation.json")
    assert trace["passed"] is False
    assert any(
        "code-to-design task requires risk_observation_ids" in error
        for error in trace["errors"]
    )


def test_coverage_check_rejects_legacy_semantic_coverage_fields_early(workspace):
    populate_handoffs(workspace)
    _run_stage(workspace, "coverage-check")

    state = workspace["state"]
    assert isinstance(state, Path)
    semantic_path = state / "semantic_coverage.json"
    semantic = ac.load_json(semantic_path)
    lens = semantic["lenses"][0]
    lens["status"] = lens.pop("disposition")
    lens["tasks"] = lens.pop("task_ids")
    lens["findings"] = lens.pop("finding_ids")
    ac.save_json(semantic_path, semantic)

    proc = _run_stage(workspace, "coverage-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert trace["passed"] is False
    assert any("missing disposition" in error for error in trace["errors"])
    assert any("missing task_ids" in error for error in trace["errors"])
    assert any("missing finding_ids" in error for error in trace["errors"])


def test_coverage_check_requires_round_modes_to_have_completed_task_evidence(workspace):
    populate_handoffs(workspace)
    _run_stage(workspace, "coverage-check")

    state = workspace["state"]
    assert isinstance(state, Path)
    contract = ac.load_json(state / "agent_loop_contract.json")
    only_mode = contract["coverage_contract"]["exploration_modes"][0]
    task_path = state / "investigation_tasks.jsonl"
    tasks, parse_errors = ac.load_jsonl(task_path)
    assert parse_errors == []
    for task in tasks:
        task["exploration_mode"] = only_mode
        task["risk_observation_ids"] = []
    _rewrite_jsonl(task_path, tasks)

    proc = _run_stage(workspace, "coverage-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert trace["passed"] is False
    assert any(
        "has no task with that mode" in error for error in trace["errors"]
    )
