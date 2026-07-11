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


def _prepare_claim_scope(workspace) -> None:
    state = workspace["state"]
    assert isinstance(state, Path)
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert errors == []
    scope_path = state / "claim_review_scope.json"
    ac.save_json(scope_path, {
        "session_id": workspace["session_id"],
        "round_id": "ROUND-001",
        "design_claims_sha256": ac.sha256_file(state / "design_claims.jsonl"),
        "claim_ids": [claim["claim_id"] for claim in claims],
    })
    review_path = state / "design_claim_review.json"
    review = ac.load_json(review_path)
    review["input_digests"] = {
        name: ac.sha256_file(state / name)
        for name in (
            "design_claims.jsonl", "design_coverage.json", "design_agent_manifest.json",
            "claim_review_scope.json",
        )
    }
    ac.save_json(review_path, review)
    run_runner(
        "claim-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
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
    _prepare_claim_scope(workspace)
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


def test_task_check_requires_each_task_in_exactly_one_round(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    rounds, errors = ac.load_jsonl(state / "investigation_rounds.jsonl")
    assert errors == []
    duplicate = dict(rounds[0])
    duplicate.update({
        "round_id": "ROUND-002",
        "task_ids": ["TASK-001"],
        "claim_ids": ["CLAIM-001"],
        "finding_ids": ["FINDING-TASK-001"],
    })
    _rewrite_jsonl(state / "investigation_rounds.jsonl", [rounds[0], duplicate])

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_validation.json")
    assert any(
        "task TASK-001: must belong to exactly one investigation round; found 2" in error
        for error in trace["errors"]
    )


def test_task_check_freezes_later_round_until_earlier_round_drains(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    tasks[0]["status"] = "pending"
    tasks[2]["status"] = "pending"
    tasks[3]["status"] = "pending"
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)
    findings, errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert errors == []
    _rewrite_jsonl(
        state / "investigation_findings.jsonl",
        [
            finding for finding in findings
            if finding["task_id"] not in {"TASK-001", "TASK-003", "TASK-004"}
        ],
    )
    rounds, errors = ac.load_jsonl(state / "investigation_rounds.jsonl")
    assert errors == []
    first = dict(rounds[0])
    first.update({
        "task_ids": ["TASK-001", "TASK-002"],
        "claim_ids": ["CLAIM-001", "CLAIM-002"],
        "finding_ids": ["FINDING-TASK-002"],
    })
    second = dict(rounds[0])
    second.update({
        "round_id": "ROUND-002",
        "task_ids": ["TASK-003", "TASK-004"],
        "claim_ids": ["CLAIM-003", "CLAIM-004"],
        "finding_ids": [],
    })
    _rewrite_jsonl(state / "investigation_rounds.jsonl", [first, second])

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_validation.json")
    assert any(
        "ROUND-002: cannot exist while earlier round ROUND-001" in error
        for error in trace["errors"]
    )


def test_task_check_binds_tasks_bidirectionally_to_accepted_claim_review_scope(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    review_path = state / "design_claim_review.json"
    review = ac.load_json(review_path)
    original_review = json.loads(json.dumps(review))
    scope_path = state / "claim_review_scope.json"
    scope = ac.load_json(scope_path)
    scope["claim_ids"] = ["CLAIM-001", "CLAIM-002", "CLAIM-003"]
    ac.save_json(scope_path, scope)
    review["claim_reviews"] = [
        item for item in review["claim_reviews"] if item["claim_id"] != "CLAIM-004"
    ]
    review["input_digests"] = {
        name: ac.sha256_file(state / name)
        for name in (
            "design_claims.jsonl", "design_coverage.json", "design_agent_manifest.json",
            "claim_review_scope.json",
        )
    }
    ac.save_json(review_path, review)
    run_runner(
        "claim-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_validation.json")
    assert any(
        "task TASK-004: claim_id 'CLAIM-004' is outside accepted claim review scope" in error
        for error in trace["errors"]
    )

    ac.save_json(review_path, original_review)
    _prepare_claim_scope(workspace)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    _rewrite_jsonl(
        state / "investigation_tasks.jsonl",
        [task for task in tasks if task["task_id"] != "TASK-004"],
    )
    findings, errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert errors == []
    _rewrite_jsonl(
        state / "investigation_findings.jsonl",
        [finding for finding in findings if finding["task_id"] != "TASK-004"],
    )
    rounds, errors = ac.load_jsonl(state / "investigation_rounds.jsonl")
    assert errors == []
    rounds[0]["task_ids"].remove("TASK-004")
    rounds[0]["finding_ids"].remove("FINDING-TASK-004")
    _rewrite_jsonl(state / "investigation_rounds.jsonl", rounds)

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_validation.json")
    assert any(
        "accepted claim review scope claim CLAIM-004: missing investigation task" in error
        for error in trace["errors"]
    )


def test_first_round_boundary_requires_matching_code_to_design_risk_task(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    for task in tasks:
        task["exploration_mode"] = "design-to-code obligation tracing"
        task["risk_observation_ids"] = []
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_validation.json")
    assert any(
        "first-round portfolio needs at least one risk-backed code-to-design task" in error
        for error in trace["errors"]
    )


def test_first_round_can_defer_other_parallel_planes_to_coverage(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    architecture_path = state / "architecture_map.json"
    architecture = ac.load_json(architecture_path)
    architecture["implementation_planes"].append({
        "plane_id": "PLANE-ADAPTER",
        "kind": "adapter",
        "paths": ["service.py"],
        "reachable_evidence": "The adapter reaches the same public service implementation.",
    })
    architecture["parallel_behavior_paths"] = [{
        "path_id": "PATH-SERVICE",
        "behavior": "The public behavior is reachable through two planes.",
        "plane_ids": ["PLANE-SERVICE", "PLANE-ADAPTER"],
        "evidence": "Both planes reach the public service boundary.",
    }]
    ac.save_json(architecture_path, architecture)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    tasks[0]["parallel_path_ids"] = ["PATH-SERVICE"]
    tasks[0]["implementation_planes"] = ["PLANE-SERVICE", "PLANE-ADAPTER"]
    tasks[1]["parallel_path_ids"] = ["PATH-SERVICE"]
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 0


def test_round_frontier_rejects_more_than_contract_limit(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    extra = dict(tasks[0])
    extra["task_id"] = "TASK-005"
    extra["status"] = "pending"
    tasks.append(extra)
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)
    rounds, errors = ac.load_jsonl(state / "investigation_rounds.jsonl")
    assert errors == []
    rounds[0]["task_ids"].append("TASK-005")
    _rewrite_jsonl(state / "investigation_rounds.jsonl", rounds)

    proc = _run_stage(workspace, "task-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_validation.json")
    assert any(
        "task_ids exceeds max_tasks_per_round=4" in error
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
