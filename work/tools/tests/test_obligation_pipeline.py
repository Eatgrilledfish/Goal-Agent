from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import handoff_merge  # noqa: E402
import obligation_queue  # noqa: E402
import scout_materializer  # noqa: E402
import scout_receipt  # noqa: E402
import workspace_inventory  # noqa: E402


def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, dict]:
    state = tmp_path / "state"
    design = tmp_path / "design"
    state.mkdir()
    design.mkdir()
    (design / "spec.txt").write_text(
        "intro\nWhen enabled, the service MUST process every record in order.\n"
        "It SHOULD wait before sending an unsolicited update.\nend\n",
        encoding="utf-8",
    )
    design_sweep = {
        "sweep_id": "SCOUT-DESIGN-01", "direction": "design_to_code",
        "section_ids": ["SECTION-A"], "anchor_paths": [],
        "architecture_boundaries": [], "implementation_planes": [],
        "parallel_path_ids": [],
    }
    code_sweep = {
        "sweep_id": "SCOUT-CODE-01", "direction": "code_to_design",
        "section_ids": [], "anchor_paths": ["src/runtime.c"],
        "architecture_boundaries": ["BOUNDARY-A"],
        "implementation_planes": ["PLANE-A"], "parallel_path_ids": [],
    }
    ac.save_json(state / "agent_loop_state.json", {"session_id": "session-test"})
    ac.save_json(state / "risk_sweep_plan.json", {
        "session_id": "session-test", "slices": [design_sweep, code_sweep],
    })
    ac.save_json(state / "design_inventory.json", {
        "document_groups": [{
            "document_key": "spec", "scope_relation": "required",
            "sections": [{
                "section_id": "SECTION-A", "source_ref": {
                    "path": "spec.txt", "line_start": 1, "line_end": 4,
                },
            }],
        }],
    })
    ac.save_json(state / "workspace_manifest.json", {
        "paths": {"review_design_root": str(design)},
    })
    plan_index = {
        "slices": {
            design_sweep["sweep_id"]: design_sweep,
            code_sweep["sweep_id"]: code_sweep,
        },
    }
    for module in (obligation_queue, scout_materializer):
        monkeypatch.setattr(
            module.rpv, "load_validated_plan",
            lambda root, plan_index=plan_index: ({}, plan_index, []),
        )
    return state, design_sweep, code_sweep


def _semantic_obligations(path: Path) -> None:
    ac.save_json(path, {
        "obligations": [{
            "source_ref": {"path": "spec.txt", "line_start": 2, "line_end": 2},
            "subject": "enabled service", "trigger": "records arrive",
            "obligation": "process every record in order",
            "observable_result": "all records are processed in their input order",
            "normative_strength": "mandatory",
            "applicability": "the target implements the enabled service",
            "exceptions": [], "ambiguities": [],
            "review_mode": "contract_mechanics",
        }],
        "no_obligation_sections": [],
    })


def _candidate_payload() -> dict:
    return {
        "candidate_key": "first-record-only",
        "behavior_question": "Are all records processed?",
        "mismatch_signal": "direct_conflict",
        "observed_code_behavior": "Only the first record is processed.",
        "code_evidence": [{
            "file": "src/runtime.c", "line_start": 10, "line_end": 11,
            "symbol": "process", "snippet": "process(records[0]);",
        }],
        "false_positive_checks": [{
            "question": "Is there another loop?", "method": "search",
            "target": "src", "result": "No alternate loop was found.",
        }],
        "tool_trace": [
            {
                "seq": 1, "kind": "design_read", "tool": "read",
                "target": "spec.txt:2", "purpose": "confirm the obligation",
                "result": "the design requires every record",
            },
            {
                "seq": 2, "kind": "code_search", "tool": "rg", "target": "process",
                "purpose": "find record processing", "result": "one direct call",
            },
            {
                "seq": 3, "kind": "code_read", "tool": "read",
                "target": "src/runtime.c:10", "purpose": "inspect behavior",
                "result": "only records[0] is processed",
            },
            {
                "seq": 4, "kind": "reverse_check", "tool": "rg",
                "target": "src", "purpose": "find alternate traversal",
                "result": "no alternate traversal found",
            },
        ],
    }


def test_obligation_modes_are_the_canonical_coverage_vocabulary() -> None:
    assert set(workspace_inventory.PORTFOLIO_LENSES) == obligation_queue.REVIEW_MODES


def test_materializes_source_bound_obligation_and_projects_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "obligations.semantic.json"
    queue_path = state / "design-obligations" / "SCOUT-DESIGN-01.json"
    _semantic_obligations(semantic)
    queue = obligation_queue.materialize(
        state, design_sweep["sweep_id"], semantic, queue_path,
    )
    obligation = queue["obligations"][0]
    assert obligation["obligation_id"].startswith("OBL-")
    assert obligation["section_ids"] == ["SECTION-A"]
    assert "MUST process every record" in obligation["source_excerpt"]
    assert queue["session_id"] == "session-test"
    assert queue["section_checks"] == [{
        "section_id": "SECTION-A", "disposition": "obligations_extracted",
        "obligation_count": 1, "no_obligation_reason": "",
    }]

    raw_candidates = tmp_path / "candidates.semantic.json"
    raw_coverage = tmp_path / "coverage.semantic.json"
    candidate = {**_candidate_payload(), "obligation_id": obligation["obligation_id"]}
    ac.save_json(raw_candidates, [candidate])
    ac.save_json(raw_coverage, {"obligation_checks": [{
        "obligation_id": obligation["obligation_id"],
        "disposition": "candidate", "candidate_keys": ["first-record-only"],
        "code_search_summary": "Searched the runtime and its callers.",
        "countercheck": "No alternate traversal was found.",
    }]})
    handoff = tmp_path / "handoff.json"
    coverage_path = tmp_path / "coverage.json"
    candidates, coverage = scout_materializer.materialize(
        state, design_sweep["sweep_id"], raw_candidates, raw_coverage,
        handoff, coverage_path,
    )
    projected = candidates[0]
    assert projected["observation_id"].startswith("CANDIDATE-")
    assert "candidate_key" not in projected
    assert projected["session_id"] == "session-test"
    assert projected["risk_sweep_plan_sha256"] == ac.sha256_file(
        state / "risk_sweep_plan.json"
    )
    assert projected["design_requirement"]["obligation"] == (
        "process every record in order"
    )
    assert projected["design_section_ids"] == ["SECTION-A"]
    assert projected["review_lenses"] == ["contract_mechanics"]
    assert handoff_merge.validate_artifact(projected, "risk", "candidate") == []
    scout_receipt.validate_coverage_contract(
        state, design_sweep, candidates, coverage,
    )


def test_obligation_source_must_stay_inside_assigned_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "bad.json"
    _semantic_obligations(semantic)
    value = ac.load_json(semantic)
    value["obligations"][0]["source_ref"]["line_start"] = 9
    value["obligations"][0]["source_ref"]["line_end"] = 9
    ac.save_json(semantic, value)
    with pytest.raises(ValueError, match="exactly one assigned section"):
        obligation_queue.materialize(
            state, design_sweep["sweep_id"], semantic, tmp_path / "queue.json",
        )


def test_empty_design_section_requires_explicit_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "empty.json"
    ac.save_json(semantic, {
        "obligations": [],
        "no_obligation_sections": [{
            "section_id": "SECTION-A",
            "reason": "The assigned range contains only explanatory background.",
        }],
    })
    queue = obligation_queue.materialize(
        state, design_sweep["sweep_id"], semantic, tmp_path / "queue.json",
    )
    assert queue["obligations"] == []
    assert queue["section_checks"][0]["disposition"] == (
        "no_implementable_obligation"
    )


def test_extractor_cannot_silently_skip_assigned_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "silent-skip.json"
    ac.save_json(semantic, {"obligations": [], "no_obligation_sections": []})
    with pytest.raises(ValueError, match="exactly account"):
        obligation_queue.materialize(
            state, design_sweep["sweep_id"], semantic, tmp_path / "queue.json",
        )


def test_model_cannot_supply_mechanical_candidate_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "obligations.json"
    queue_path = state / "design-obligations" / "SCOUT-DESIGN-01.json"
    _semantic_obligations(semantic)
    queue = obligation_queue.materialize(state, design_sweep["sweep_id"], semantic, queue_path)
    candidate = {
        **_candidate_payload(),
        "obligation_id": queue["obligations"][0]["obligation_id"],
        "session_id": "model-authored",
    }
    raw_candidates = tmp_path / "candidates.json"
    raw_coverage = tmp_path / "coverage.json"
    ac.save_json(raw_candidates, [candidate])
    ac.save_json(raw_coverage, {"obligation_checks": []})
    with pytest.raises(ValueError, match="tool-owned envelope"):
        scout_materializer.materialize(
            state, design_sweep["sweep_id"], raw_candidates, raw_coverage,
            tmp_path / "handoff.json", tmp_path / "canonical-coverage.json",
        )


def test_code_origin_candidates_are_bound_to_primary_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _design_sweep, code_sweep = _state(tmp_path, monkeypatch)
    candidate = {
        **_candidate_payload(), "primary_anchor_path": "src/runtime.c",
        "design_requirement": {
            "source_ref": {"path": "spec.txt", "line_start": 2, "line_end": 2},
            "subject": "service", "trigger": "records arrive",
            "obligation": "process every record", "observable_result": "all processed",
            "normative_strength": "mandatory", "applicability": "implemented service",
            "exceptions": [], "ambiguities": [],
        },
        "design_section_ids": ["SECTION-A"],
        "review_lenses": ["routing_capability"],
    }
    raw_candidates = tmp_path / "code-candidates.json"
    raw_coverage = tmp_path / "code-coverage.json"
    ac.save_json(raw_candidates, [candidate])
    ac.save_json(raw_coverage, {"anchor_checks": [{
        "anchor_path": "src/runtime.c", "disposition": "candidate",
        "candidate_keys": ["first-record-only"],
        "code_search_summary": "Read the anchor and retrieved the design.",
        "countercheck": "Checked its only caller.",
    }]})
    candidates, coverage = scout_materializer.materialize(
        state, code_sweep["sweep_id"], raw_candidates, raw_coverage,
        tmp_path / "code-handoff.json", tmp_path / "code-canonical-coverage.json",
    )
    assert candidates[0]["origin_anchor_path"] == "src/runtime.c"
    assert candidates[0]["architecture_boundaries"] == ["BOUNDARY-A"]
    scout_receipt.validate_coverage_contract(state, code_sweep, candidates, coverage)
