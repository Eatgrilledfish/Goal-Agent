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
        "claim_ids": [claim["claim_id"] for claim in claims],
    })
    review_path = state / "design_claim_review.json"
    review = ac.load_json(review_path)
    review["input_digests"] = {
        name: ac.sha256_file(state / name)
        for name in (
            "design_claims.jsonl", "design_coverage.json", "design_inventory.json",
            "design_agent_manifest.json",
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
    _run_stage(workspace, "task-plan-check")

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

    proc = _run_stage(workspace, "task-plan-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert trace["passed"] is False
    assert any(
        "code-to-design task requires risk_observation_ids" in error
        for error in trace["errors"]
    )


def test_task_plan_requires_one_task_for_every_accepted_claim(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    tasks[-1]["claim_id"] = "CLAIM-003"
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    proc = _run_stage(workspace, "task-plan-check", check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert any(
        "every accepted claim must have an investigation task" in error
        and "CLAIM-004" in error
        for error in trace["errors"]
    )


def test_atomic_task_rejects_linking_a_second_scout_candidate(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    task = next(
        item for item in tasks
        if item["exploration_mode"] == "code-to-design risk backtracking"
        and item["architecture_boundaries"] == ["BOUNDARY-API"]
    )
    task["risk_observation_ids"].append("RISK-AUDIT-001")
    task["architecture_boundaries"].append("BOUNDARY-AUDIT")
    task["implementation_planes"].append("PLANE-AUDIT")
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    proc = _run_stage(workspace, "task-plan-check", check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert any(
        "risk_observation_ids must preserve exactly the selected candidate" in error
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

    proc = _run_stage(workspace, "task-plan-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert any(
        "task TASK-001: must belong to exactly one investigation round; found 2" in error
        for error in trace["errors"]
    )


def test_task_check_allows_later_round_planning_while_earlier_round_runs(workspace):
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

    proc = _run_stage(workspace, "task-lifecycle-check", check=False)
    assert proc.returncode == 0
    trace = ac.load_json(workspace["logs"] / "trace" / "task_lifecycle_validation.json")
    assert trace["passed"] is True
    assert trace["metrics"]["earliest_open_round"] == "ROUND-001"


def test_claim_review_rejects_partial_materialized_claim_scope(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    review_path = state / "design_claim_review.json"
    review = ac.load_json(review_path)
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
    proc = run_runner(
        "claim-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "claim_review_validation.json")
    assert any(
        "must include every materialized claim" in error and "CLAIM-004" in error
        for error in trace["errors"]
    )


def test_initial_frontier_allows_design_linked_entry_without_sweep_quota(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    design_tasks = [
        task for task in tasks
        if task["exploration_mode"] == "design-to-code obligation tracing"
    ]
    assert len(design_tasks) == 1
    assert design_tasks[0]["risk_observation_ids"] == [
        design_tasks[0]["candidate_id"]
    ]

    proc = _run_stage(workspace, "task-plan-check", check=False)
    assert proc.returncode == 0


def test_task_cannot_hide_scout_origin_by_relabeling_mode(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    audit_task = next(
        task for task in tasks
        if task["exploration_mode"] == "code-to-design risk backtracking"
        and task["architecture_boundaries"] == ["BOUNDARY-AUDIT"]
    )
    audit_task["exploration_mode"] = "design-to-code obligation tracing"
    audit_task["risk_observation_ids"] = []
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    proc = _run_stage(workspace, "task-plan-check", check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert any(
        "exploration_mode does not match candidate direction" in error
        for error in trace["errors"]
    )


def test_initial_frontier_rejects_code_risk_as_its_only_entry(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    for task in tasks:
        task["exploration_mode"] = "code-to-design risk backtracking"
        task["risk_observation_ids"] = [
            "RISK-AUDIT-001"
            if task["architecture_boundaries"] == ["BOUNDARY-AUDIT"]
            else "RISK-API-001"
        ]
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    proc = _run_stage(workspace, "task-plan-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert any(
        "code-risk observations cannot be the sole entry" in error
        for error in trace["errors"]
    )


def test_initial_frontier_obeys_configured_round_limit(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    template = ac.load_jsonl(state / "investigation_rounds.jsonl")[0][0]
    contract_path = state / "agent_loop_contract.json"
    contract = ac.load_json(contract_path)
    contract["iteration_policy"]["maximum_initial_frontier_rounds"] = 2
    ac.save_json(contract_path, contract)
    task_groups = [tasks[:2], tasks[2:3], tasks[3:]]
    rounds = []
    for index, group in enumerate(task_groups, start=1):
        round_item = dict(template)
        round_item["round_id"] = f"ROUND-{index:03d}"
        round_item["task_ids"] = [task["task_id"] for task in group]
        round_item["claim_ids"] = [task["claim_id"] for task in group]
        rounds.append(round_item)
    _rewrite_jsonl(state / "investigation_rounds.jsonl", rounds)

    proc = _run_stage(workspace, "task-plan-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert any(
        "maximum_initial_frontier_rounds=2" in error
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
    plan_path = state / "risk_sweep_plan.json"
    plan = ac.load_json(plan_path)
    plan["architecture_map_sha256"] = ac.sha256_file(architecture_path)
    plan["required_coverage"]["plane_ids"].append("PLANE-ADAPTER")
    plan["required_coverage"]["parallel_path_ids"] = ["PATH-SERVICE"]
    plan["slices"][0]["implementation_planes"].append("PLANE-ADAPTER")
    plan["slices"][0]["parallel_path_ids"] = ["PATH-SERVICE"]
    ac.save_json(plan_path, plan)
    plan_digest = ac.sha256_file(plan_path)
    risks, errors = ac.load_jsonl(state / "risk_observations.jsonl")
    assert errors == []
    risks[0]["implementation_planes"].append("PLANE-ADAPTER")
    risks[0]["parallel_path_ids"] = ["PATH-SERVICE"]
    for risk in risks:
        risk["risk_sweep_plan_sha256"] = plan_digest
    _rewrite_jsonl(state / "risk_observations.jsonl", risks)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    tasks[0]["parallel_path_ids"] = ["PATH-SERVICE"]
    tasks[0]["implementation_planes"] = ["PLANE-SERVICE", "PLANE-ADAPTER"]
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    proc = _run_stage(workspace, "task-plan-check", check=False)
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

    proc = _run_stage(workspace, "task-plan-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert any(
        "task_ids exceeds max_tasks_per_round=4" in error
        for error in trace["errors"]
    )


def test_atomic_task_identity_errors_are_candidate_local(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    task_path = state / "investigation_tasks.jsonl"
    tasks, errors = ac.load_jsonl(task_path)
    assert errors == []
    tasks[0]["claim_branch"] = ["branch-a", "branch-b"]
    tasks[1]["obligation_sha256"] = "0" * 64
    tasks[2]["hypothesis"] = ["first", "second"]
    _rewrite_jsonl(task_path, tasks)

    proc = _run_stage(workspace, "task-plan-check", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert trace["global_passed"] is True
    assert trace["valid_task_ids"] == ["TASK-004"]
    assert trace["invalid_task_ids"] == ["TASK-001", "TASK-002", "TASK-003"]
    assert any("claim_branch must be a string" in error for error in trace["errors_by_task"]["TASK-001"])
    assert any("does not match the linked claim obligation" in error for error in trace["errors_by_task"]["TASK-002"])
    assert any("hypothesis must be a string" in error for error in trace["errors_by_task"]["TASK-003"])


def test_finding_merge_only_requires_lifecycle_refresh(workspace):
    populate_handoffs(workspace)
    _prepare_claim_scope(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    task_path = state / "investigation_tasks.jsonl"
    finding_path = state / "investigation_findings.jsonl"
    round_path = state / "investigation_rounds.jsonl"
    tasks, errors = ac.load_jsonl(task_path)
    assert errors == []
    findings, errors = ac.load_jsonl(finding_path)
    assert errors == []
    rounds, errors = ac.load_jsonl(round_path)
    assert errors == []
    original_finding = next(item for item in findings if item["task_id"] == "TASK-001")
    tasks[0]["status"] = "pending"
    _rewrite_jsonl(task_path, tasks)
    _rewrite_jsonl(
        finding_path, [item for item in findings if item["task_id"] != "TASK-001"],
    )
    rounds[0]["finding_ids"].remove(original_finding["finding_id"])
    _rewrite_jsonl(round_path, rounds)
    _run_stage(workspace, "task-plan-check")
    _run_stage(workspace, "task-lifecycle-check")
    plan_path = workspace["logs"] / "trace" / "task_plan_validation.json"
    lifecycle_path = workspace["logs"] / "trace" / "task_lifecycle_validation.json"
    plan_before = plan_path.read_bytes()
    lifecycle_before = ac.load_json(lifecycle_path)["task_lifecycle_sha256"]

    tasks[0]["status"] = "complete"
    _rewrite_jsonl(task_path, tasks)
    _rewrite_jsonl(finding_path, findings)
    rounds[0]["finding_ids"].append(original_finding["finding_id"])
    _rewrite_jsonl(round_path, rounds)
    _run_stage(workspace, "task-lifecycle-check")

    assert plan_path.read_bytes() == plan_before
    lifecycle_after = ac.load_json(lifecycle_path)
    assert lifecycle_after["passed"] is True
    assert lifecycle_after["task_lifecycle_sha256"] != lifecycle_before


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
