from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import candidate_pipeline as cp  # noqa: E402
import handoff_merge as hm  # noqa: E402


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    state = tmp_path / "state"
    design = tmp_path / "design"
    state.mkdir()
    design.mkdir()
    logs = tmp_path / "logs"
    (logs / "trace").mkdir(parents=True)
    session_id = "session-candidate-pipeline"
    (design / "contract.md").write_text(
        "# Contract\nWhen input is negative, the service MUST reject it.\n",
        encoding="utf-8",
    )
    source_sha = ac.sha256_file(design / "contract.md")
    ac.save_json(state / "agent_loop_state.json", {"session_id": session_id})
    ac.save_json(state / "workspace_manifest.json", {
        "paths": {"log_root": str(logs)},
    })
    ac.save_json(state / "design_inventory.json", {
        "session_id": session_id,
        "document_groups": [{
            "document_key": "contract", "members": ["contract.md"],
            "scope_relation": "required",
            "sections": [{
                "section_id": "SECTION-CONTRACT",
                "source_ref": {
                    "path": "contract.md", "line_start": 1, "line_end": 2,
                    "source_sha256": source_sha,
                },
                "path": "contract.md", "line_start": 1, "line_end": 2,
                "heading": "Contract", "behavior_families": ["negative input"],
                "ambiguities": [],
            }],
        }],
    })
    ac.save_json(state / "risk_sweep_plan.json", {
        "session_id": session_id,
        "slices": [{
            "sweep_id": "SCOUT-CONTRACT", "direction": "design_to_code",
            "document_keys": ["contract"],
            "section_ids": ["SECTION-CONTRACT"], "anchor_paths": [],
        }],
    })
    plan_sha = ac.sha256_file(state / "risk_sweep_plan.json")
    observation = {
        "observation_id": "OBS-NEGATIVE", "session_id": session_id,
        "sweep_id": "SCOUT-CONTRACT", "direction": "design_to_code",
        "mismatch_signal": "direct_conflict",
        "behavior_question": "Does the service reject negative input?",
        "design_section_ids": ["SECTION-CONTRACT"],
        "design_requirement": {
            "source_ref": {
                "path": "contract.md", "line_start": 2, "line_end": 2,
            },
            "subject": "service", "trigger": "input is negative",
            "obligation": "reject the input",
            "observable_result": "the service rejects the negative input",
            "normative_strength": "mandatory", "applicability": "public service",
            "exceptions": [], "ambiguities": [],
        },
        "code_evidence": [{
            "file": "service.py", "line_start": 1, "line_end": 2,
            "symbol": "accept", "snippet": "def accept(value):\n    return True",
        }],
        "review_lenses": ["input acceptance"],
        "architecture_boundaries": ["BOUNDARY-API"],
        "implementation_planes": ["PLANE-SERVICE"],
        "parallel_path_ids": [],
    }
    _write_jsonl(state / "risk_observations.jsonl", [observation])
    handoff = (
        state / "handoffs" / "risks" / "SCOUT-CONTRACT" / "SCOUT-CONTRACT.json"
    )
    handoff.parent.mkdir(parents=True)
    handoff.write_text(json.dumps([observation]) + "\n", encoding="utf-8")
    coverage = state / "scout-coverage" / "SCOUT-CONTRACT.json"
    coverage.parent.mkdir(parents=True)
    ac.save_json(coverage, {
        "sweep_id": "SCOUT-CONTRACT",
        "reviewed_section_ids": ["SECTION-CONTRACT"],
        "reviewed_anchor_paths": [],
    })
    _write_jsonl(state / "scout_receipts.jsonl", [{
        "session_id": session_id, "sweep_id": "SCOUT-CONTRACT",
        "risk_sweep_plan_sha256": plan_sha, "status": "complete",
        "candidate_count": 1,
        "candidate_ids": ["OBS-NEGATIVE"],
        "direction": "design_to_code",
        "handoff_sha256": ac.sha256_file(handoff),
        "coverage_report_sha256": ac.sha256_file(coverage),
        "assigned_section_ids": ["SECTION-CONTRACT"],
        "reviewed_section_ids": ["SECTION-CONTRACT"],
        "assigned_anchor_paths": [],
        "reviewed_anchor_paths": [],
    }])
    ac.save_json(state / "candidate_selection.json", {
        "candidate_ids": ["OBS-NEGATIVE"],
    })
    return state, design, session_id


def test_selection_requires_every_scout_receipt(tmp_path: Path) -> None:
    state, design, _session = _fixture(tmp_path)
    (state / "scout_receipts.jsonl").write_text("", encoding="utf-8")

    try:
        cp.select(state, design, state / "candidate_selection.json")
    except ValueError as exc:
        assert "all semantic scouts must complete" in str(exc)
    else:
        raise AssertionError("selection unexpectedly bypassed incomplete breadth")


def test_selection_and_plan_preserve_candidate_code_and_design_binding(tmp_path: Path) -> None:
    state, design, session_id = _fixture(tmp_path)

    selected = cp.select(state, design, state / "candidate_selection.json")
    claim_id = selected["claim_ids"][0]
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert errors == []
    claim = claims[0]
    assert claim["candidate_id"] == "OBS-NEGATIVE"
    assert claim["quote"] == "When input is negative, the service MUST reject it."
    assert not claim["observable_result"].startswith(
        "The reachable implementation does not produce"
    )
    ac.save_json(state / "design_claim_review.json", {
        "session_id": session_id,
        "claim_reviews": [{"claim_id": claim_id, "decision": "accept"}],
    })
    ac.save_json(state.parent / "logs" / "trace" / "claim_review_validation.json", {
        "passed": True, "session_id": session_id,
        "accepted_claim_ids": [claim_id],
    })

    planned = cp.plan(state)

    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    task = tasks[0]
    assert planned["task_ids"] == [task["task_id"]]
    assert task["candidate_id"] == "OBS-NEGATIVE"
    assert task["risk_observation_ids"] == ["OBS-NEGATIVE"]
    assert task["starting_points"] == [{
        "file": "service.py", "line_start": 1, "line_end": 2,
    }]
    assert task["claim_branch"] == ac.canonical_claim_branch(claim)
    assert task["hypothesis"] == ac.canonical_claim_hypothesis(claim)
    assert task["hypothesis"].count(
        "The reachable implementation does not produce the required observable result:"
    ) == 1
    assert hm.validate_artifact(task, "task", "task") == []


def test_selection_rejects_compliance_observation(tmp_path: Path) -> None:
    state, design, _session = _fixture(tmp_path)
    observations, errors = ac.load_jsonl(state / "risk_observations.jsonl")
    assert errors == []
    observations[0]["mismatch_signal"] = "no_difference"
    _write_jsonl(state / "risk_observations.jsonl", observations)

    try:
        cp.select(state, design, state / "candidate_selection.json")
    except ValueError as exc:
        assert "lacks a candidate mismatch_signal" in str(exc)
    else:
        raise AssertionError("compliance observation unexpectedly entered frontier")


def test_selection_rejects_informational_candidate_source(tmp_path: Path) -> None:
    state, design, _session = _fixture(tmp_path)
    inventory = ac.load_json(state / "design_inventory.json")
    inventory["document_groups"][0]["scope_relation"] = "informational"
    ac.save_json(state / "design_inventory.json", inventory)

    try:
        cp.select(state, design, state / "candidate_selection.json")
    except ValueError as exc:
        assert "source group must be required/in_scope" in str(exc)
    else:
        raise AssertionError("informational catalog source entered candidate frontier")


def test_selection_rejects_non_atomic_source_range_over_80_lines(
    tmp_path: Path,
) -> None:
    state, design, _session = _fixture(tmp_path)
    observations, errors = ac.load_jsonl(state / "risk_observations.jsonl")
    assert errors == []
    observations[0]["design_requirement"]["source_ref"]["line_end"] = 82
    _write_jsonl(state / "risk_observations.jsonl", observations)

    try:
        cp.select(state, design, state / "candidate_selection.json")
    except ValueError as exc:
        assert "source_ref exceeds 80 lines" in str(exc)
    else:
        raise AssertionError("over-broad design citation entered candidate frontier")


def test_nonempty_observations_require_nonempty_selection(tmp_path: Path) -> None:
    state, design, _session = _fixture(tmp_path)
    ac.save_json(state / "candidate_selection.json", {"candidate_ids": []})

    try:
        cp.select(state, design, state / "candidate_selection.json")
    except ValueError as exc:
        assert "non-empty observations require" in str(exc)
    else:
        raise AssertionError("non-empty observations were silently dropped")


def test_code_origin_candidate_is_not_rejected_by_architecture_test_metadata(
    tmp_path: Path,
) -> None:
    state, design, _session = _fixture(tmp_path)
    ac.save_json(state / "architecture_map.json", {
        "test_surfaces": [{"path": "service.py"}],
    })
    plan = ac.load_json(state / "risk_sweep_plan.json")
    plan["slices"][0].update({
        "direction": "code_to_design", "document_keys": [],
        "section_ids": [], "anchor_paths": ["service.py"],
    })
    ac.save_json(state / "risk_sweep_plan.json", plan)
    plan_sha = ac.sha256_file(state / "risk_sweep_plan.json")
    receipts, errors = ac.load_jsonl(state / "scout_receipts.jsonl")
    assert errors == []
    receipts[0].update({
        "direction": "code_to_design",
        "risk_sweep_plan_sha256": plan_sha,
        "assigned_section_ids": [], "reviewed_section_ids": [],
        "assigned_anchor_paths": ["service.py"],
        "reviewed_anchor_paths": ["service.py"],
    })
    coverage = state / "scout-coverage" / "SCOUT-CONTRACT.json"
    ac.save_json(coverage, {
        "sweep_id": "SCOUT-CONTRACT",
        "reviewed_section_ids": [],
        "reviewed_anchor_paths": ["service.py"],
    })
    receipts[0]["coverage_report_sha256"] = ac.sha256_file(coverage)
    _write_jsonl(state / "scout_receipts.jsonl", receipts)
    observations, errors = ac.load_jsonl(state / "risk_observations.jsonl")
    assert errors == []
    observations[0]["direction"] = "code_to_design"
    _write_jsonl(state / "risk_observations.jsonl", observations)

    selected = cp.select(state, design, state / "candidate_selection.json")
    assert selected["candidate_ids"] == ["OBS-NEGATIVE"]
