from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import claim_review_validator as validator  # noqa: E402


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in values), encoding="utf-8")


def _accepted_claim(session_id: str, claim_id: str, strength: str) -> dict:
    return {
        "session_id": session_id,
        "claim_id": claim_id,
        "quote_entailment": {
            "assessment": "entailed", "rationale": "The cited sentence directly states it.",
        },
        "normative_strength": {
            "assessment": "correct", "stated_strength": strength,
            "recommended_strength": strength, "rationale": "The modal language matches.",
        },
        "atomicity": {
            "assessment": "atomic", "obligations": ["Perform the stated behavior."],
            "rationale": "Only one independently observable obligation is expressed.",
        },
        "applicability": {
            "assessment": "supported", "rationale": "The supplied scope names this component.",
        },
        "decision": "accept",
        "repair_actions": [],
    }


def _accepted_group(session_id: str, document_key: str) -> dict:
    complete = {"assessment": "complete", "missing_items": [], "rationale": "No gap found."}
    return {
        "session_id": session_id,
        "document_key": document_key,
        "behavior_families": copy.deepcopy(complete),
        "roles": copy.deepcopy(complete),
        "branches": copy.deepcopy(complete),
        "decision": "accept",
        "repair_actions": [],
    }


@pytest.fixture
def review_workspace(tmp_path: Path) -> dict[str, object]:
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    state = logs / "state"
    for path in (code, design, result, state):
        path.mkdir(parents=True)
    (design / "contract.md").write_text(
        "The component must retain each accepted item.\n", encoding="utf-8",
    )
    session_id = "session-claim-review"
    ac.save_json(state / "agent_loop_state.json", {"session_id": session_id})
    workspace_manifest = {
        "session_id": session_id,
        "prepared_at": "2026-01-01T00:00:00Z",
        "paths": {
            "code_root": str(code.resolve()),
            "design_root": str(design.resolve()),
            "result_root": str(result.resolve()),
            "log_root": str(logs.resolve()),
            "state_root": str(state.resolve()),
            "review_design_root": str(design.resolve()),
        },
        "design": {
            "document_count": 1,
            "document_group_count": 1,
            "documents": [],
            "document_groups": [
                {"document_key": "contract", "members": ["contract.md"]},
            ],
            "source_manifest": None,
        },
        "preflight_problems": [],
    }
    ac.save_json(state / "workspace_manifest.json", workspace_manifest)
    ac.save_json(state / "design_agent_manifest.json", {
        "session_id": session_id,
        "prepared_at": workspace_manifest["prepared_at"],
        "review_design_root": str(design.resolve()),
        "design": {
            key: workspace_manifest["design"].get(key)
            for key in (
                "document_count", "document_group_count", "documents",
                "document_groups", "source_manifest",
            )
        },
        "preflight_problems": [],
    })
    claims = [
        {
            "claim_id": "CLAIM-1", "session_id": session_id,
            "normative_strength": "mandatory", "behavior": "Retain every accepted item.",
        },
        {
            "claim_id": "CLAIM-2", "session_id": session_id,
            "normative_strength": "recommended", "behavior": "Expose a status indication.",
        },
    ]
    _write_jsonl(state / "design_claims.jsonl", claims)
    ac.save_json(state / "design_coverage.json", {
        "session_id": session_id,
        "document_groups": [{
            "document_key": "contract", "members": ["contract.md"],
            "claim_ids": ["CLAIM-1", "CLAIM-2"],
        }],
    })
    values: dict[str, object] = {
        "code": code, "design": design, "result": result, "logs": logs,
        "state": state, "session_id": session_id,
    }
    write_valid_review(values)
    return values


def write_valid_review(values: dict[str, object]) -> None:
    state = values["state"]
    assert isinstance(state, Path)
    session_id = str(values["session_id"])
    ac.save_json(state / "design_claim_review.json", {
        "session_id": session_id,
        "input_digests": {
            "design_claims.jsonl": ac.sha256_file(state / "design_claims.jsonl"),
            "design_coverage.json": ac.sha256_file(state / "design_coverage.json"),
            "design_agent_manifest.json": ac.sha256_file(state / "design_agent_manifest.json"),
        },
        "claim_reviews": [
            _accepted_claim(session_id, "CLAIM-1", "mandatory"),
            _accepted_claim(session_id, "CLAIM-2", "recommended"),
        ],
        "group_reviews": [_accepted_group(session_id, "contract")],
        "decision": "accept",
        "summary": "Both claims and the document group passed semantic review.",
    })


def run_validator(values: dict[str, object]) -> tuple[int, dict]:
    result = validator.run(argparse.Namespace(
        code_root=str(values["code"]), design_root=str(values["design"]),
        result_root=str(values["result"]), log_root=str(values["logs"]),
        state_root=None, design_entry=[], source_manifest=None,
    ))
    trace = ac.load_json(Path(values["logs"]) / "trace" / "claim_review_validation.json")
    return result, trace


def load_review(values: dict[str, object]) -> dict:
    return ac.load_json(Path(values["state"]) / "design_claim_review.json")


def test_valid_complete_review_passes_and_writes_bound_trace(review_workspace):
    code, trace = run_validator(review_workspace)
    assert code == 0
    assert trace["passed"] is True
    assert trace["metrics"] == {
        "claims": 2, "claim_reviews": 2, "document_groups": 1,
        "group_reviews": 1, "repairs": 0,
    }
    assert trace["input_digests"] == load_review(review_workspace)["input_digests"]


def test_review_requires_exact_claim_and_group_id_coverage(review_workspace):
    review = load_review(review_workspace)
    review["claim_reviews"] = [review["claim_reviews"][0], copy.deepcopy(review["claim_reviews"][0])]
    review["group_reviews"][0]["document_key"] = "unknown-group"
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("duplicate claim reviews" in error for error in trace["errors"])
    assert any("missing claim reviews: ['CLAIM-2']" in error for error in trace["errors"])
    assert any("missing group reviews: ['contract']" in error for error in trace["errors"])
    assert any("unknown group reviews: ['unknown-group']" in error for error in trace["errors"])


def test_review_is_bound_to_current_session_and_all_input_digests(review_workspace):
    review = load_review(review_workspace)
    review["session_id"] = "old-session"
    review["claim_reviews"][0]["session_id"] = "old-session"
    review["input_digests"]["design_claims.jsonl"] = "0" * 64
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("session_id does not match current session" in error for error in trace["errors"])
    assert any("input_digests do not match current inputs" in error for error in trace["errors"])


def test_design_agent_manifest_must_match_design_only_workspace_projection(review_workspace):
    state = Path(review_workspace["state"])
    manifest = ac.load_json(state / "design_agent_manifest.json")
    manifest["review_design_root"] = "/unexpected/design"
    ac.save_json(state / "design_agent_manifest.json", manifest)
    write_valid_review(review_workspace)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("does not match the current design-only workspace projection" in error for error in trace["errors"])


def test_claim_repair_decision_must_follow_assessments_and_have_action(review_workspace):
    review = load_review(review_workspace)
    claim = review["claim_reviews"][0]
    claim["quote_entailment"] = {
        "assessment": "not_entailed", "rationale": "The behavior broadens the quoted condition.",
    }
    review["decision"] = "repair"
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("decision must be 'repair' for its assessments" in error for error in trace["errors"])

    claim["decision"] = "repair"
    claim["repair_actions"] = ["Rewrite behavior so its condition matches the quote."]
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)
    code, trace = run_validator(review_workspace)
    assert code == 1
    assert trace["schema_valid"] is True
    assert trace["passed"] is False
    assert trace["repair_required"] is True
    assert trace["metrics"]["repairs"] == 1


def test_group_gap_requires_evidence_item_repair_and_overall_repair(review_workspace):
    review = load_review(review_workspace)
    group = review["group_reviews"][0]
    group["branches"] = {
        "assessment": "gaps_found", "missing_items": [],
        "rationale": "An independent fallback branch is not represented.",
    }
    group["decision"] = "repair"
    group["repair_actions"] = ["Add an independent claim for the fallback branch."]
    review["decision"] = "repair"
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("gaps_found assessment requires at least one missing item" in error for error in trace["errors"])

    group["branches"]["missing_items"] = [{
        "description": "Fallback behavior", "path": "contract.md", "section": "Fallback",
        "line_start": 1, "line_end": 1,
        "quote": "The component must retain each accepted item.",
        "why_independent": "The fallback has its own trigger and observable result.",
    }]
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)
    code, trace = run_validator(review_workspace)
    assert code == 1
    assert trace["schema_valid"] is True
    assert trace["repair_required"] is True
    assert trace["metrics"]["repairs"] == 1


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda review: review["claim_reviews"][0]["atomicity"].update(
                {"assessment": "bundled", "obligations": ["Only one"]},
            ),
            "bundled assessment requires at least two obligations",
        ),
        (
            lambda review: review["claim_reviews"][0]["normative_strength"].update(
                {"assessment": "ambiguous", "recommended_strength": "mandatory"},
            ),
            "ambiguous normative strength must recommend undetermined",
        ),
        (
            lambda review: review.update({"decision": "repair"}),
            "decision must be 'accept' for child decisions",
        ),
    ],
)
def test_structural_decision_consistency_is_enforced(review_workspace, mutation, message):
    review = load_review(review_workspace)
    mutation(review)
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)
    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any(message in error for error in trace["errors"])


def test_validator_does_not_second_guess_model_semantics(review_workspace):
    review = load_review(review_workspace)
    review["claim_reviews"][0]["quote_entailment"]["rationale"] = (
        "This intentionally terse rationale is a model judgement."
    )
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)
    code, trace = run_validator(review_workspace)
    assert code == 0
    assert trace["passed"] is True
