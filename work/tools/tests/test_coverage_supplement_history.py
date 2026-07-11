from __future__ import annotations

import json
from pathlib import Path

from test_agent_pipeline import populate_handoffs, run_runner, workspace  # noqa: F401

import agent_common as ac
import stage_artifact_validator


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8",
    )


def _run_coverage(workspace, *, check: bool = True):
    return run_runner(
        "coverage-check",
        workspace["code"],
        workspace["design"],
        workspace["result"],
        workspace["logs"],
        check=check,
    )


def _install_supplement_request(workspace) -> dict:
    state = workspace["state"]
    assert isinstance(state, Path)
    coverage_path = state / "coverage_audit.json"
    coverage = ac.load_json(coverage_path)
    contract = ac.load_json(state / "agent_loop_contract.json")
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert errors == []
    coverage["remaining_gaps"] = [{
        "gap_id": "GAP-SUPPLEMENT-001",
        "kind": "frontier_claim",
        "ref_id": "CLAIM-001",
        "reason": "An alternate reachable path has not been tested.",
        "evidence": "The completed finding covers only the primary entry path.",
    }]
    coverage["next_round_tasks"] = [{
        "claim_id": "CLAIM-001",
        "claim_branch": "CLAIM-001: alternate reachable evidence path",
        "hypothesis": "Does an independent entry path enforce the same obligation?",
        "obligation_sha256": stage_artifact_validator.claim_obligation_sha256(claims[0]),
        "exploration_mode": "design-to-code obligation tracing",
        "review_lenses": [contract["coverage_contract"]["portfolio_lenses"][0]],
        "architecture_boundaries": ["BOUNDARY-API"],
        "implementation_planes": ["PLANE-SERVICE"],
        "parallel_path_ids": [],
        "risk_observation_ids": [],
        "source_gap_ids": ["GAP-SUPPLEMENT-001"],
        "priority_reason": "Coverage found a concrete alternate-path evidence gap.",
    }]
    coverage["supplement_rounds"] = 0
    ac.save_json(coverage_path, coverage)
    return coverage


def _record_deferred_supplement_task(workspace) -> None:
    state = workspace["state"]
    assert isinstance(state, Path)
    history = ac.load_json(state / "coverage_supplement_history.json")
    request = history["requests"][0]
    spec = request["task_specs"][0]
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    task = json.loads(json.dumps(tasks[0]))
    task.update(spec)
    task.update({
        "task_id": "TASK-SUPPLEMENT-001",
        "coverage_request_sha256": request["request_sha256"],
        "status": "deferred",
        "defer_reason": "Two provider attempts failed before evidence collection.",
        "defer_evidence": {
            "kind": "provider_failure",
            "attempts": [
                {
                    "attempt_id": "supplement-attempt-1",
                    "outcome": "failed",
                    "evidence": "The provider terminated before returning an artifact.",
                },
                {
                    "attempt_id": "supplement-attempt-2",
                    "outcome": "failed",
                    "evidence": "The independent retry also terminated without an artifact.",
                },
            ],
        },
    })
    _write_jsonl(state / "investigation_tasks.jsonl", [*tasks, task])
    rounds, errors = ac.load_jsonl(state / "investigation_rounds.jsonl")
    assert errors == []
    supplement_round = {
        "session_id": workspace["session_id"],
        "round_id": "ROUND-SUPPLEMENT-001",
        "strategy": "Attempt the single evidence-backed coverage supplement.",
        "exploration_modes": [],
        "document_groups": ["contract"],
        "architecture_boundaries": spec["architecture_boundaries"],
        "implementation_planes": spec["implementation_planes"],
        "lenses": spec["review_lenses"],
        "claim_ids": [spec["claim_id"]],
        "task_ids": [task["task_id"]],
        "finding_ids": [],
        "outcome": "The task was deferred after two recorded provider failures.",
        "next_strategy": "Finalize coverage with the evidence limitation recorded.",
    }
    _write_jsonl(state / "investigation_rounds.jsonl", [*rounds, supplement_round])


def test_single_supplement_request_is_recorded_and_exact_replay_is_idempotent(workspace):
    populate_handoffs(workspace)
    _install_supplement_request(workspace)

    _run_coverage(workspace)
    state = workspace["state"]
    history_path = state / "coverage_supplement_history.json"
    history = ac.load_json(history_path)
    assert history["session_id"] == workspace["session_id"]
    assert len(history["requests"]) == 1
    request = history["requests"][0]
    assert request["source_gap_ids"] == ["GAP-SUPPLEMENT-001"]
    assert request["prior_task_ids"] == [
        "TASK-001", "TASK-002", "TASK-003", "TASK-004",
    ]
    assert len(request["task_specs"]) == 1
    assert len(request["request_sha256"]) == 64
    before = history_path.read_bytes()

    _run_coverage(workspace)
    assert history_path.read_bytes() == before


def test_different_second_supplement_request_is_rejected(workspace):
    populate_handoffs(workspace)
    coverage = _install_supplement_request(workspace)
    _run_coverage(workspace)

    coverage["next_round_tasks"][0]["hypothesis"] = (
        "Does a different entry path expose a distinct behavior?"
    )
    ac.save_json(workspace["state"] / "coverage_audit.json", coverage)
    proc = _run_coverage(workspace, check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert any(
        "different or second supplement request is forbidden" in error
        for error in trace["errors"]
    )


def test_completed_supplement_can_remove_resolved_gap_but_cannot_request_another(workspace):
    populate_handoffs(workspace)
    coverage = _install_supplement_request(workspace)
    _run_coverage(workspace)
    _record_deferred_supplement_task(workspace)

    coverage["next_round_tasks"] = []
    coverage["remaining_gaps"] = []
    coverage["supplement_rounds"] = 1
    coverage["rounds_completed"] = 2
    ac.save_json(workspace["state"] / "coverage_audit.json", coverage)
    _run_coverage(workspace)

    coverage["remaining_gaps"] = [{
        "gap_id": "GAP-SUPPLEMENT-002",
        "kind": "frontier_claim",
        "ref_id": "CLAIM-001",
        "reason": "A later audit proposed another pass.",
        "evidence": "This request appears after the single supplement completed.",
    }]
    coverage["next_round_tasks"] = json.loads(json.dumps(
        _install_supplement_request(workspace)["next_round_tasks"]
    ))
    coverage["next_round_tasks"][0]["source_gap_ids"] = ["GAP-SUPPLEMENT-002"]
    coverage["supplement_rounds"] = 1
    ac.save_json(workspace["state"] / "coverage_audit.json", coverage)
    proc = _run_coverage(workspace, check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert any(
        "next_round_tasks are allowed only before the single supplement" in error
        for error in trace["errors"]
    )


def test_supplement_cannot_be_marked_complete_without_the_recorded_tasks(workspace):
    populate_handoffs(workspace)
    coverage = _install_supplement_request(workspace)
    _run_coverage(workspace)
    coverage["next_round_tasks"] = []
    coverage["supplement_rounds"] = 1
    ac.save_json(workspace["state"] / "coverage_audit.json", coverage)

    proc = _run_coverage(workspace, check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert any(
        "completed supplement tasks do not exactly match the recorded request" in error
        for error in trace["errors"]
    )


def test_clearing_history_after_a_bound_supplement_task_fails_closed(workspace):
    populate_handoffs(workspace)
    coverage = _install_supplement_request(workspace)
    _run_coverage(workspace)
    _record_deferred_supplement_task(workspace)
    ac.save_json(workspace["state"] / "coverage_supplement_history.json", {
        "session_id": workspace["session_id"], "requests": [],
    })
    coverage["next_round_tasks"] = []
    coverage["remaining_gaps"] = []
    coverage["supplement_rounds"] = 0
    coverage["rounds_completed"] = 2
    ac.save_json(workspace["state"] / "coverage_audit.json", coverage)

    proc = _run_coverage(workspace, check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert any(
        "supplement binding exists without a recorded coverage request" in error
        for error in trace["errors"]
    )


def test_clearing_history_before_task_creation_is_detected_by_tool_event(workspace):
    populate_handoffs(workspace)
    coverage = _install_supplement_request(workspace)
    _run_coverage(workspace)
    ac.save_json(workspace["state"] / "coverage_supplement_history.json", {
        "session_id": workspace["session_id"], "requests": [],
    })
    coverage["next_round_tasks"] = []
    coverage["supplement_rounds"] = 0
    ac.save_json(workspace["state"] / "coverage_audit.json", coverage)

    proc = _run_coverage(workspace, check=False)

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert any(
        "was cleared after a supplement request was recorded" in error
        for error in trace["errors"]
    )
