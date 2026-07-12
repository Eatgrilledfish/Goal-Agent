from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import coverage_materializer as materializer  # noqa: E402
import stage_artifact_validator as validator  # noqa: E402


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


@pytest.fixture
def coverage_state(tmp_path: Path) -> dict[str, object]:
    state = tmp_path / "logs" / "state"
    trace = tmp_path / "logs" / "trace"
    state.mkdir(parents=True)
    trace.mkdir()
    session_id = "session-coverage-materializer"
    lenses = ["lens-one", "lens-two"]
    modes = ["mode-one", "mode-two"]
    contract = {
        "session": {"session_id": session_id},
        "coverage_contract": {
            "portfolio_lenses": lenses,
            "exploration_modes": modes,
        },
    }
    inventory = {
        "session_id": session_id,
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "scope_relation": "in_scope",
            "sections": [],
        }],
    }
    architecture = {
        "session_id": session_id,
        "repository_summary": "A two-plane service fixture.",
        "languages": ["Python"],
        "entrypoints": [{
            "path": "service.py", "purpose": "Public API",
            "evidence": "The public function is directly callable.",
        }],
        "subsystems": [{
            "subsystem_id": "SUBSYSTEM-SERVICE", "name": "Service",
            "role": "Implements the contract.", "paths": ["service.py"],
        }],
        "implementation_planes": [
            {
                "plane_id": "PLANE-PRIMARY", "kind": "owned",
                "paths": ["service.py"],
                "reachable_evidence": "The public entry reaches this plane.",
            },
            {
                "plane_id": "PLANE-ALTERNATE", "kind": "adapter",
                "paths": ["alternate.py"],
                "reachable_evidence": "An alternate adapter is mapped.",
            },
        ],
        "integration_boundaries": [
            {
                "boundary_id": "BOUNDARY-PRIMARY", "name": "Public boundary",
                "paths": ["service.py"], "plane_ids": ["PLANE-PRIMARY"],
                "risk": "high", "why": "Externally visible behavior crosses here.",
            },
            {
                "boundary_id": "BOUNDARY-ALTERNATE", "name": "Adapter boundary",
                "paths": ["alternate.py"], "plane_ids": ["PLANE-ALTERNATE"],
                "risk": "low", "why": "An alternate mapped path crosses here.",
            },
        ],
        "capability_surfaces": [],
        "configuration_surfaces": [],
        "alternate_execution_paths": [],
        "test_surfaces": [],
        "parallel_behavior_paths": [{
            "path_id": "PARALLEL-SERVICE",
            "behavior": "The contract can be reached through two planes.",
            "plane_ids": ["PLANE-PRIMARY", "PLANE-ALTERNATE"],
            "evidence": "Both planes expose the same designed behavior.",
        }],
        "probe_capabilities": {
            "isolated_copy_feasible": False,
            "available_runtime": [],
            "constraints": ["Static fixture only."],
        },
    }
    claim = {
        "claim_id": "CLAIM-001", "session_id": session_id,
        "document_key": "contract", "path": "contract.md",
    }
    task = {
        "task_id": "TASK-001", "session_id": session_id,
        "claim_id": "CLAIM-001", "status": "complete",
        "review_lenses": ["lens-one"],
        "architecture_boundaries": ["BOUNDARY-PRIMARY"],
        "implementation_planes": ["PLANE-PRIMARY"],
        "parallel_path_ids": ["PARALLEL-SERVICE"],
        "exploration_mode": "mode-one",
    }
    finding = {
        "finding_id": "FINDING-TASK-001", "session_id": session_id,
        "task_id": "TASK-001", "claim_id": "CLAIM-001",
        "review_lenses": ["lens-one"],
        "code_evidence": [{"file": "service.py"}],
    }
    round_item = {
        "round_id": "ROUND-001", "session_id": session_id,
        "exploration_modes": ["mode-one"],
        "task_ids": ["TASK-001"],
        "finding_ids": ["FINDING-TASK-001"],
    }
    ac.save_json(state / "agent_loop_contract.json", contract)
    ac.save_json(state / "design_inventory.json", inventory)
    ac.save_json(state / "architecture_map.json", architecture)
    ac.save_json(state / "design_claim_review.json", {
        "session_id": session_id,
        "claim_reviews": [{"claim_id": "CLAIM-001", "decision": "accept"}],
    })
    ac.save_json(state / "coverage_supplement_history.json", {
        "session_id": session_id, "requests": [],
    })
    _write_jsonl(state / "design_claims.jsonl", [claim])
    _write_jsonl(state / "investigation_tasks.jsonl", [task])
    _write_jsonl(state / "investigation_findings.jsonl", [finding])
    _write_jsonl(state / "investigation_rounds.jsonl", [round_item])
    return {
        "state": state,
        "trace": trace / "coverage-materialization.json",
        "session_id": session_id,
        "lenses": lenses,
        "modes": modes,
        "inventory": inventory,
        "architecture": architecture,
        "claims": {"CLAIM-001": claim},
        "tasks": {"TASK-001": task},
        "findings": {"FINDING-TASK-001": finding},
        "rounds": {"ROUND-001": round_item},
    }


def _run(values: dict[str, object]) -> int:
    return materializer.main([
        "--state-root", str(values["state"]),
        "--trace", str(values["trace"]),
    ])


def test_materialized_outputs_pass_current_coverage_projection_validators(
    coverage_state,
):
    assert _run(coverage_state) == 0
    state = Path(coverage_state["state"])
    semantic = ac.load_json(state / "semantic_coverage.json")
    audit = ac.load_json(state / "coverage_audit.json")
    architecture_errors, architecture_indexes = validator.validate_architecture(
        coverage_state["architecture"], str(coverage_state["session_id"]),
    )
    assert architecture_errors == []
    design_groups = {"contract": {"claim_ids": ["CLAIM-001"]}}
    errors: list[str] = []
    validator._validate_semantic_coverage(
        semantic,
        str(coverage_state["session_id"]),
        set(coverage_state["lenses"]),
        design_groups,
        coverage_state["claims"],
        coverage_state["tasks"],
        coverage_state["findings"],
        set(architecture_indexes["boundaries"]),
        errors,
    )
    validator._validate_coverage_audit(
        audit,
        session_id=str(coverage_state["session_id"]),
        manifest={"design": {"document_groups": [{
            "document_key": "contract", "members": ["contract.md"],
        }]}},
        design_groups=design_groups,
        claims=coverage_state["claims"],
        risks={},
        tasks=coverage_state["tasks"],
        findings=coverage_state["findings"],
        rounds=coverage_state["rounds"],
        observed_modes={"mode-one"},
        modes=set(coverage_state["modes"]),
        lenses=set(coverage_state["lenses"]),
        scoped_claim_ids={"CLAIM-001"},
        architecture_indexes=architecture_indexes,
        errors=errors,
    )
    assert errors == []
    assert semantic["lenses"] == [
        {
            "lens": "lens-one",
            "disposition": "investigated",
            "evidence": "Completed task/finding evidence: TASK-001/FINDING-TASK-001",
            "task_ids": ["TASK-001"],
            "finding_ids": ["FINDING-TASK-001"],
            "design_group_refs": ["contract"],
            "boundary_refs": ["BOUNDARY-PRIMARY"],
            "counterfactual": "",
        },
        {
            "lens": "lens-two",
            "disposition": "gap_recorded",
            "evidence": "No completed task/finding pair supplies direct lens evidence.",
            "task_ids": [],
            "finding_ids": [],
            "design_group_refs": ["contract"],
            "boundary_refs": ["BOUNDARY-ALTERNATE", "BOUNDARY-PRIMARY"],
            "counterfactual": (
                "A completed task and finding declaring this lens and a mapped "
                "architecture boundary would be required."
            ),
        },
    ]
    assert audit["next_round_tasks"] == []
    assert audit["supplement_rounds"] == 0
    assert {(gap["kind"], gap["ref_id"]) for gap in audit["remaining_gaps"]} == {
        ("lens", "lens-two"),
        ("architecture_boundary", "BOUNDARY-ALTERNATE"),
        ("parallel_path", "PARALLEL-SERVICE"),
        ("exploration_mode", "mode-two"),
    }
    assert ac.load_json(Path(coverage_state["trace"]))["supplement_created"] is False


def test_materializer_is_byte_stable_for_unchanged_evidence(coverage_state):
    assert _run(coverage_state) == 0
    state = Path(coverage_state["state"])
    first_semantic = (state / "semantic_coverage.json").read_bytes()
    first_audit = (state / "coverage_audit.json").read_bytes()

    assert _run(coverage_state) == 0

    assert (state / "semantic_coverage.json").read_bytes() == first_semantic
    assert (state / "coverage_audit.json").read_bytes() == first_audit


@pytest.mark.parametrize("broken", ["pending", "missing_finding"])
def test_materializer_rejects_accepted_claim_without_complete_finding(
    coverage_state, broken,
):
    state = Path(coverage_state["state"])
    if broken == "pending":
        task = dict(coverage_state["tasks"]["TASK-001"])
        task["status"] = "pending"
        _write_jsonl(state / "investigation_tasks.jsonl", [task])
    else:
        (state / "investigation_findings.jsonl").write_text("", encoding="utf-8")

    assert _run(coverage_state) == 1
    assert not (state / "semantic_coverage.json").exists()
    assert not (state / "coverage_audit.json").exists()
    trace = ac.load_json(Path(coverage_state["trace"]))
    assert any("requires complete task TASK-001" in error for error in trace["errors"])
