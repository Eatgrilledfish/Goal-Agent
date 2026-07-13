from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import pipeline_controller as pc  # noqa: E402
import risk_sweep_plan_validator as rpv  # noqa: E402
import stage_artifact_validator as sav  # noqa: E402


SESSION_ID = "session-pipeline-controller"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    (tmp_path / "logs" / "trace").mkdir(parents=True)
    ac.save_json(root / "workspace_manifest.json", {
        "paths": {"log_root": str(tmp_path / "logs")},
    })
    ac.save_json(root / "agent_loop_state.json", {
        "session_id": SESSION_ID,
        "status": "in_progress",
        "current_phase": "code_risk_backtracking",
        "next_actions": ["stale model-authored action"],
    })
    ac.save_json(root / "agent_loop_contract.json", {
        "session": {"session_id": SESSION_ID},
    })
    ac.save_json(root / "architecture_map.json", {
        "session_id": SESSION_ID,
        "repository_summary": "Test architecture.",
    })
    ac.save_json(root / "design_inventory.json", {
        "session_id": SESSION_ID,
        "document_groups": [],
    })
    for name in (
        "risk_observations.jsonl", "scout_receipts.jsonl",
        "investigation_tasks.jsonl", "investigation_findings.jsonl",
        "investigation_rounds.jsonl", "critic_reviews.jsonl",
    ):
        (root / name).write_text("", encoding="utf-8")
    trace_root = tmp_path / "logs" / "trace"
    architecture_inputs, architecture_combined = sav._input_digests(
        root, sav._stage_inputs(root, "architecture"),
    )
    ac.save_json(trace_root / "architecture_validation.json", {
        "stage": "architecture",
        "session_id": SESSION_ID,
        "passed": True,
        "input_digests": architecture_inputs,
        "combined_input_sha256": architecture_combined,
    })
    ac.save_json(trace_root / "design_validation.json", {
        "session_id": SESSION_ID,
        "mode": "inventory",
        "passed": True,
        "input_digests": {
            "design_inventory.json": ac.sha256_file(root / "design_inventory.json"),
            "design_claims.jsonl": "",
            "design_coverage.json": "",
            "workspace_manifest.json": ac.sha256_file(root / "workspace_manifest.json"),
        },
        "lineage_input_digests": {
            "design_lookup_requests.jsonl": "",
            "risk_observations.jsonl": "",
        },
    })
    return root


def _jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


def _plan(root: Path) -> None:
    ac.save_json(root / "risk_sweep_plan.json", {
        "session_id": SESSION_ID,
        "slices": [{"sweep_id": "SCOUT-01"}, {"sweep_id": "SCOUT-02"}],
    })
    log_root = Path(ac.load_json(root / "workspace_manifest.json")["paths"]["log_root"])
    inputs, combined = rpv.plan_input_digests(root)
    ac.save_json(log_root / "trace" / "risk_sweep_plan_validation.json", {
        "session_id": SESSION_ID,
        "passed": True,
        "input_digests": inputs,
        "combined_input_sha256": combined,
    })


def _complete_scouts(root: Path) -> None:
    plan_digest = ac.sha256_file(root / "risk_sweep_plan.json")
    _jsonl(root / "scout_receipts.jsonl", [
        {
            "session_id": SESSION_ID, "sweep_id": "SCOUT-01",
            "risk_sweep_plan_sha256": plan_digest, "status": "complete",
            "candidate_count": 0, "candidate_ids": [],
        },
        {
            "session_id": SESSION_ID, "sweep_id": "SCOUT-02",
            "risk_sweep_plan_sha256": plan_digest, "status": "complete",
            "candidate_count": 0, "candidate_ids": [],
        },
    ])


def _set_observations(root: Path, candidate_ids: list[str]) -> None:
    _jsonl(root / "risk_observations.jsonl", [
        {"observation_id": candidate_id, "sweep_id": "SCOUT-01"}
        for candidate_id in candidate_ids
    ])
    receipts, errors = ac.load_jsonl(root / "scout_receipts.jsonl")
    assert errors == []
    for receipt in receipts:
        values = candidate_ids if receipt["sweep_id"] == "SCOUT-01" else []
        receipt["candidate_ids"] = values
        receipt["candidate_count"] = len(values)
    _jsonl(root / "scout_receipts.jsonl", receipts)


def _select_candidates(root: Path, candidate_ids: list[str]) -> None:
    _set_observations(root, candidate_ids)
    ac.save_json(root / "candidate_selection.json", {
        "candidate_ids": candidate_ids,
    })
    ac.save_json(root / "claim_review_scope.json", {
        "claim_ids": [f"CLAIM-{index}" for index, _value in enumerate(candidate_ids, 1)],
    })
    _jsonl(root / "design_claims.jsonl", [
        {"claim_id": f"CLAIM-{index}", "candidate_id": candidate_id}
        for index, candidate_id in enumerate(candidate_ids, 1)
    ])
    _jsonl(root / "design_lookup_requests.jsonl", [
        {"request_id": f"LOOKUP-{index}", "candidate_id": candidate_id}
        for index, candidate_id in enumerate(candidate_ids, 1)
    ])


def _accept_claims(root: Path, count: int) -> None:
    ac.save_json(root / "design_claim_review.json", {
        "claim_reviews": [
            {"claim_id": f"CLAIM-{index}", "decision": "accept"}
            for index in range(1, count + 1)
        ],
    })
    log_root = Path(ac.load_json(root / "workspace_manifest.json")["paths"]["log_root"])
    ac.save_json(log_root / "trace" / "claim_review_validation.json", {
        "passed": True,
        "accepted_claim_ids": [f"CLAIM-{index}" for index in range(1, count + 1)],
    })


def _tasks(root: Path, count: int, *, deferred: bool = False) -> None:
    _jsonl(root / "investigation_tasks.jsonl", [
        {
            "task_id": f"TASK-{index:02d}",
            "candidate_id": f"CANDIDATE-{index:02d}",
            **({"status": "deferred"} if deferred else {}),
        }
        for index in range(1, count + 1)
    ])


def _task_gates(root: Path, count: int, *, plan_passed: bool = True) -> None:
    task_ids = [f"TASK-{index:02d}" for index in range(1, count + 1)]
    log_root = Path(ac.load_json(root / "workspace_manifest.json")["paths"]["log_root"])
    trace_root = log_root / "trace"
    claims, claim_errors = ac.load_jsonl(root / "design_claims.jsonl")
    risks, risk_errors = ac.load_jsonl(root / "risk_observations.jsonl")
    tasks, task_errors = ac.load_jsonl(root / "investigation_tasks.jsonl")
    findings, finding_errors = ac.load_jsonl(root / "investigation_findings.jsonl")
    rounds, round_errors = ac.load_jsonl(root / "investigation_rounds.jsonl")
    assert not [
        *claim_errors, *risk_errors, *task_errors, *finding_errors, *round_errors,
    ]
    claim_index = {item["claim_id"]: item for item in claims}
    risk_index = {item["observation_id"]: item for item in risks}
    task_index = {item["task_id"]: item for item in tasks}
    finding_index = {item["finding_id"]: item for item in findings}
    stable_digests = {
        "task-plan": sav.task_plan_snapshot_sha256(
            root,
            contract=ac.load_json(root / "agent_loop_contract.json"),
            architecture=ac.load_json(root / "architecture_map.json"),
            claims=claim_index,
            risks=risk_index,
            tasks=task_index,
            rounds=rounds,
        ),
        "task-lifecycle": sav.task_lifecycle_snapshot_sha256(
            tasks=task_index, findings=finding_index, rounds=rounds,
        ),
    }
    for filename, stage, digest_field, passed in (
        ("task_plan_validation.json", "task-plan", "task_plan_sha256", plan_passed),
        ("task_lifecycle_validation.json", "task-lifecycle", "task_lifecycle_sha256", True),
    ):
        ac.save_json(trace_root / filename, {
            "stage": stage,
            "session_id": SESSION_ID,
            "passed": passed,
            "global_passed": passed,
            "valid_task_ids": task_ids,
            digest_field: stable_digests[stage],
        })


def _refresh_lifecycle_gate(root: Path) -> None:
    tasks, task_errors = ac.load_jsonl(root / "investigation_tasks.jsonl")
    findings, finding_errors = ac.load_jsonl(root / "investigation_findings.jsonl")
    rounds, round_errors = ac.load_jsonl(root / "investigation_rounds.jsonl")
    assert not [*task_errors, *finding_errors, *round_errors]
    log_root = Path(ac.load_json(root / "workspace_manifest.json")["paths"]["log_root"])
    trace_path = log_root / "trace" / "task_lifecycle_validation.json"
    trace = ac.load_json(trace_path)
    trace["task_lifecycle_sha256"] = sav.task_lifecycle_snapshot_sha256(
        tasks={item["task_id"]: item for item in tasks},
        findings={item["finding_id"]: item for item in findings},
        rounds=rounds,
    )
    ac.save_json(trace_path, trace)


def test_bootstrap_actions_require_current_validated_artifacts(tmp_path: Path) -> None:
    root = _root(tmp_path)
    trace_root = tmp_path / "logs" / "trace"

    assert pc.derive_status(root)["next_action"] == "build_scout_plan"

    (trace_root / "design_validation.json").unlink()
    inventory_status = pc.derive_status(root)
    assert inventory_status["next_action"] == "build_inventory"
    assert inventory_status["pending_ids"] == ["DESIGN-INVENTORY"]

    (trace_root / "architecture_validation.json").unlink()
    architecture_status = pc.derive_status(root)
    assert architecture_status["next_action"] == "map_architecture"
    assert architecture_status["pending_ids"] == ["ARCHITECTURE-MAP"]


def test_stale_architecture_trace_cannot_advance_bootstrap(tmp_path: Path) -> None:
    root = _root(tmp_path)
    architecture = ac.load_json(root / "architecture_map.json")
    architecture["repository_summary"] = "Changed after validation."
    ac.save_json(root / "architecture_map.json", architecture)

    status = pc.derive_status(root)

    assert status["next_action"] == "map_architecture"
    assert status["blocked_reason"] == "architecture_missing_or_stale"
    assert any("stale" in error for error in status["errors"])


def test_later_full_design_trace_still_proves_inventory_ready(tmp_path: Path) -> None:
    root = _root(tmp_path)
    trace_path = tmp_path / "logs" / "trace" / "design_validation.json"
    trace = ac.load_json(trace_path)
    trace["mode"] = "all"
    trace["input_digests"]["design_claims.jsonl"] = "a" * 64
    trace["input_digests"]["design_coverage.json"] = "b" * 64
    trace["lineage_input_digests"] = {
        "design_lookup_requests.jsonl": "c" * 64,
        "risk_observations.jsonl": "d" * 64,
    }
    ac.save_json(trace_path, trace)

    status = pc.derive_status(root)

    assert status["next_action"] == "build_scout_plan"


def test_status_short_circuits_at_earliest_unmet_precondition(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)

    assert pc.next_action(root) == "finish_scouts"

    plan_digest = ac.sha256_file(root / "risk_sweep_plan.json")
    _jsonl(root / "scout_receipts.jsonl", [{
        "session_id": SESSION_ID, "sweep_id": "SCOUT-01",
        "risk_sweep_plan_sha256": plan_digest, "status": "complete",
        "candidate_count": 0, "candidate_ids": [],
    }])
    _jsonl(root / "investigation_tasks.jsonl", [{
        "task_id": "TASK-01", "candidate_id": "CANDIDATE-01",
    }])
    _jsonl(root / "investigation_findings.jsonl", [
        {"finding_id": "FINDING-01", "task_id": "TASK-01"},
    ])
    _jsonl(root / "critic_reviews.jsonl", [{"finding_id": "FINDING-01"}])
    assert pc.next_action(root) == "finish_scouts"

    _complete_scouts(root)
    (root / "investigation_findings.jsonl").write_text("", encoding="utf-8")
    (root / "critic_reviews.jsonl").write_text("", encoding="utf-8")
    assert pc.next_action(root) == "select_candidates"
    _select_candidates(root, ["CANDIDATE-01"])
    assert pc.next_action(root) == "review_claims"
    _accept_claims(root, 1)
    _task_gates(root, 1)
    assert pc.next_action(root) == "finish_investigations"


def test_status_walks_selection_investigation_critic_and_final(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)

    assert pc.next_action(root) == "select_candidates"

    _select_candidates(root, ["CANDIDATE-01", "CANDIDATE-02"])
    assert pc.next_action(root) == "review_claims"

    _accept_claims(root, 2)
    assert pc.next_action(root) == "plan_investigations"

    _tasks(root, 2)
    _task_gates(root, 2)
    assert pc.next_action(root) == "finish_investigations"

    _jsonl(root / "investigation_findings.jsonl", [
        {"finding_id": "FINDING-01", "task_id": "TASK-01"},
        {"finding_id": "FINDING-02", "task_id": "TASK-02"},
    ])
    tasks, errors = ac.load_jsonl(root / "investigation_tasks.jsonl")
    assert not errors
    for task in tasks:
        task["status"] = "complete"
    _jsonl(root / "investigation_tasks.jsonl", tasks)
    _refresh_lifecycle_gate(root)
    assert pc.next_action(root) == "finish_critics"

    _jsonl(root / "critic_reviews.jsonl", [
        {"finding_id": "FINDING-01"},
    ])
    assert pc.next_action(root) == "finish_critics"

    _jsonl(root / "critic_reviews.jsonl", [
        {"finding_id": "FINDING-01"},
        {"finding_id": "FINDING-02"},
    ])
    assert pc.next_action(root) == "run_final"


def test_valid_empty_selection_runs_final_when_scouts_found_nothing(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, [])
    _accept_claims(root, 0)
    _task_gates(root, 0)

    assert pc.next_action(root) == "run_final"


def test_nonempty_observations_cannot_be_discarded_by_empty_selection(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _set_observations(root, ["CANDIDATE-01"])
    ac.save_json(root / "candidate_selection.json", {"candidate_ids": []})
    (root / "design_claims.jsonl").write_text("", encoding="utf-8")
    (root / "design_lookup_requests.jsonl").write_text("", encoding="utf-8")

    status = pc.derive_status(root)

    assert status["next_action"] == "select_candidates"
    assert status["blocked_reason"] == "candidate_selection_empty_with_observations"
    assert status["pending_ids"] == ["CANDIDATE-01"]


def test_deferred_task_does_not_require_finding(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, ["CANDIDATE-01"])
    _accept_claims(root, 1)
    _tasks(root, 1, deferred=True)
    _task_gates(root, 1)

    assert pc.next_action(root) == "run_final"


def test_receipts_must_match_current_session_and_plan_digest(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    receipts, errors = ac.load_jsonl(root / "scout_receipts.jsonl")
    assert errors == []
    receipts[0]["session_id"] = "stale-session"
    receipts[1]["risk_sweep_plan_sha256"] = "0" * 64
    _jsonl(root / "scout_receipts.jsonl", receipts)

    status = pc.derive_status(root)

    assert status["next_action"] == "finish_scouts"
    assert status["pending_ids"] == ["SCOUT-01", "SCOUT-02"]
    assert status["counts"] == {"expected": 2, "completed": 0, "pending": 2}
    assert status["blocked_reason"] == "scout_receipts_invalid"
    assert any("session_id" in error for error in status["errors"])
    assert any("stale" in error for error in status["errors"])


def test_obsolete_selected_candidates_file_cannot_shadow_task_ledger(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, ["CANDIDATE-01"])
    _accept_claims(root, 1)
    _jsonl(root / "selected_candidates.jsonl", [{
        "task_id": "STALE-TASK", "candidate_id": "CANDIDATE-01",
    }])

    assert pc.next_action(root) == "plan_investigations"


def test_failed_task_plan_gate_blocks_investigation_dispatch(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, ["CANDIDATE-01"])
    _accept_claims(root, 1)
    _tasks(root, 1)
    _task_gates(root, 1, plan_passed=False)

    status = pc.derive_status(root)

    assert status["next_action"] == "plan_investigations"
    assert status["pending_ids"] == ["TASK-01"]
    assert status["blocked_reason"] == "task_validation_missing_or_failed"
    assert any("passed/global_passed" in error for error in status["errors"])


def test_both_current_task_gates_are_required_before_dispatch(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, ["CANDIDATE-01"])
    _accept_claims(root, 1)
    _tasks(root, 1)

    assert pc.next_action(root) == "plan_investigations"

    _task_gates(root, 1)
    (Path(ac.load_json(root / "workspace_manifest.json")["paths"]["log_root"])
     / "trace" / "task_lifecycle_validation.json").unlink()
    assert pc.next_action(root) == "plan_investigations"

    _task_gates(root, 1)
    assert pc.next_action(root) == "finish_investigations"


def test_lifecycle_transition_does_not_stale_stable_task_plan(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, ["CANDIDATE-01", "CANDIDATE-02"])
    _accept_claims(root, 2)
    _tasks(root, 2)
    _task_gates(root, 2)
    trace_root = tmp_path / "logs" / "trace"
    plan_trace_before = (trace_root / "task_plan_validation.json").read_bytes()
    assert pc.next_action(root) == "finish_investigations"

    tasks, errors = ac.load_jsonl(root / "investigation_tasks.jsonl")
    assert not errors
    tasks[0]["status"] = "complete"
    _jsonl(root / "investigation_tasks.jsonl", tasks)
    _jsonl(root / "investigation_findings.jsonl", [{
        "finding_id": "FINDING-01", "task_id": "TASK-01",
    }])
    _refresh_lifecycle_gate(root)

    status = pc.derive_status(root)

    assert (trace_root / "task_plan_validation.json").read_bytes() == plan_trace_before
    assert status["next_action"] == "finish_investigations"
    assert status["pending_ids"] == ["TASK-02"]
    assert status["blocked_reason"] == "investigations_incomplete"


def test_cli_reconciles_stale_global_phase_and_reports_progress(
    tmp_path: Path, capsys,
) -> None:
    root = _root(tmp_path)
    _plan(root)

    assert pc.main(["status", "--state-root", str(root)]) == 0
    status = json.loads(capsys.readouterr().out)
    state = ac.load_json(root / "agent_loop_state.json")

    assert status["next_action"] == "finish_scouts"
    assert status["current_phase"] == "semantic_scouting"
    assert status["pending_ids"] == ["SCOUT-01", "SCOUT-02"]
    assert status["last_progress_at"]
    assert state["current_phase"] == "semantic_scouting"
    assert state["next_actions"] == ["finish_scouts"]
    assert state["last_progress_at"] == status["last_progress_at"]

    first_progress = status["last_progress_at"]
    assert pc.main(["status", "--state-root", str(root)]) == 0
    unchanged = json.loads(capsys.readouterr().out)
    assert unchanged["last_progress_at"] == first_progress


def test_terminal_final_gate_state_is_not_reopened(tmp_path: Path) -> None:
    root = _root(tmp_path)
    state = ac.load_json(root / "agent_loop_state.json")
    state.update({"status": "complete", "stop_reason": "final_gate_passed"})
    ac.save_json(root / "agent_loop_state.json", state)

    status = pc.reconcile(root)
    reconciled = ac.load_json(root / "agent_loop_state.json")

    assert status["terminal"] is True
    assert status["next_action"] is None
    assert reconciled["current_phase"] == "complete"
    assert reconciled["next_actions"] == []


def test_hard_deadline_block_is_not_reopened(tmp_path: Path) -> None:
    root = _root(tmp_path)
    state = ac.load_json(root / "agent_loop_state.json")
    state.update({
        "status": "blocked",
        "stop_reason": "hard_deadline_reached",
        "current_phase": "time_limit",
        "next_actions": [],
    })
    ac.save_json(root / "agent_loop_state.json", state)

    status = pc.reconcile(root)
    reconciled = ac.load_json(root / "agent_loop_state.json")

    assert status["terminal"] is True
    assert status["next_action"] is None
    assert status["current_phase"] == "time_limit"
    assert status["blocked_reason"] == "hard_deadline_reached"
    assert reconciled["status"] == "blocked"
    assert reconciled["stop_reason"] == "hard_deadline_reached"
    assert reconciled["current_phase"] == "time_limit"
    assert reconciled["next_actions"] == []
