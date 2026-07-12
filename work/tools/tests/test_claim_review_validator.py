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


def _accepted_claim(session_id: str, claim: dict) -> dict:
    claim_id = claim["claim_id"]
    strength = claim["normative_strength"]
    return {
        "session_id": session_id,
        "claim_id": claim_id,
        "claim_sha256": validator._claim_digest(claim),
        "source_sha256": claim["source_ref"]["source_sha256"],
        "spec_critic_prompt_version": validator.SPEC_CRITIC_PROMPT_VERSION,
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


def _accepted_group(session_id: str, group: dict) -> dict:
    complete = {"assessment": "complete", "missing_items": [], "rationale": "No gap found."}
    return {
        "session_id": session_id,
        "document_key": group["document_key"],
        "group_sha256": group["group_sha256"],
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
    (design / "status.md").write_text(
        "The component should expose a status indication.\n", encoding="utf-8",
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
            "document_count": 2,
            "document_group_count": 2,
            "documents": [],
            "document_groups": [
                {"document_key": "contract", "members": ["contract.md"]},
                {"document_key": "status", "members": ["status.md"]},
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
            "source_ref": {
                "path": "contract.md", "line_start": 1, "line_end": 1,
                "source_sha256": ac.sha256_file(design / "contract.md"),
            },
        },
        {
            "claim_id": "CLAIM-2", "session_id": session_id,
            "normative_strength": "recommended", "behavior": "Expose a status indication.",
            "source_ref": {
                "path": "status.md", "line_start": 1, "line_end": 1,
                "source_sha256": ac.sha256_file(design / "status.md"),
            },
        },
    ]
    _write_jsonl(state / "design_claims.jsonl", claims)
    ac.save_json(state / "design_coverage.json", {
        "session_id": session_id,
        "document_groups": [
            {
                "document_key": "contract", "members": ["contract.md"],
                "claim_ids": ["CLAIM-1"],
            },
            {
                "document_key": "status", "members": ["status.md"],
                "claim_ids": ["CLAIM-2"],
            },
        ],
    })
    inventory_groups = [
        {
            "document_key": "contract", "members": ["contract.md"],
            "scope_relation": "required", "sections": [],
        },
        {
            "document_key": "status", "members": ["status.md"],
            "scope_relation": "required", "sections": [],
        },
    ]
    for group in inventory_groups:
        group["group_sha256"] = validator._inventory_group_digest(group)
    ac.save_json(state / "design_inventory.json", {
        "session_id": session_id,
        "document_groups": inventory_groups,
    })
    values: dict[str, object] = {
        "code": code, "design": design, "result": result, "logs": logs,
        "state": state, "session_id": session_id,
    }
    write_scope(values, ["CLAIM-1"])
    write_valid_review(values)
    return values


def write_scope(values: dict[str, object], claim_ids: list[str]) -> None:
    state = values["state"]
    assert isinstance(state, Path)
    ac.save_json(state / "claim_review_scope.json", {
        "session_id": str(values["session_id"]),
        "round_id": "ROUND-001",
        "claim_ids": claim_ids,
    })


def write_valid_review(values: dict[str, object]) -> None:
    state = values["state"]
    assert isinstance(state, Path)
    session_id = str(values["session_id"])
    scope = ac.load_json(state / "claim_review_scope.json")
    claim_groups = {"CLAIM-1": "contract", "CLAIM-2": "status"}
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert not errors
    claim_index = {claim["claim_id"]: claim for claim in claims}
    inventory = ac.load_json(state / "design_inventory.json")
    group_index = {
        group["document_key"]: group for group in inventory["document_groups"]
    }
    scope_claim_ids = scope["claim_ids"]
    ac.save_json(state / "design_claim_review.json", {
        "session_id": session_id,
        "claim_reviews": [
            _accepted_claim(session_id, claim_index[claim_id])
            for claim_id in scope_claim_ids
        ],
        "group_reviews": [
            _accepted_group(session_id, group_index[document_key])
            for document_key in sorted({claim_groups[claim_id] for claim_id in scope_claim_ids})
        ],
        "decision": "accept",
        "summary": "The scoped claims and their document groups passed semantic review.",
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


def load_claim(values: dict[str, object], claim_id: str) -> dict:
    claims, errors = ac.load_jsonl(Path(values["state"]) / "design_claims.jsonl")
    assert not errors
    return next(claim for claim in claims if claim["claim_id"] == claim_id)


def load_inventory_group(values: dict[str, object], document_key: str) -> dict:
    inventory = ac.load_json(Path(values["state"]) / "design_inventory.json")
    return next(
        group for group in inventory["document_groups"]
        if group["document_key"] == document_key
    )


def test_valid_complete_review_passes_and_writes_bound_trace(review_workspace):
    code, trace = run_validator(review_workspace)
    assert code == 0
    assert trace["passed"] is True
    assert trace["metrics"] == {
        "claims": 2, "claim_reviews": 1, "document_groups": 2,
        "group_reviews": 1, "repairs": 0, "expansion_requests": 0,
    }
    state = Path(review_workspace["state"])
    assert trace["input_digests"] == {
        "design_claims.jsonl": ac.sha256_file(state / "design_claims.jsonl"),
        "design_coverage.json": ac.sha256_file(state / "design_coverage.json"),
        "design_inventory.json": ac.sha256_file(state / "design_inventory.json"),
        "design_agent_manifest.json": ac.sha256_file(state / "design_agent_manifest.json"),
        "claim_review_scope.json": ac.sha256_file(state / "claim_review_scope.json"),
    }
    assert trace["scope_digest"] == ac.sha256_file(
        Path(review_workspace["state"]) / "claim_review_scope.json"
    )
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["repaired_claim_ids"] == []
    assert trace["expansion_requests"] == []


def test_review_requires_exact_claim_coverage_and_rejects_out_of_scope_groups(review_workspace):
    review = load_review(review_workspace)
    review["claim_reviews"] = [
        _accepted_claim(
            str(review_workspace["session_id"]), load_claim(review_workspace, "CLAIM-2"),
        ),
    ]
    review["group_reviews"] = [
        _accepted_group(
            str(review_workspace["session_id"]),
            load_inventory_group(review_workspace, "status"),
        ),
    ]
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("missing claim reviews: ['CLAIM-1']" in error for error in trace["errors"])
    assert any("out-of-scope claim reviews: ['CLAIM-2']" in error for error in trace["errors"])
    assert any("out-of-scope group reviews: ['status']" in error for error in trace["errors"])


def test_group_review_is_optional_when_no_concrete_group_gap_was_found(review_workspace):
    review = load_review(review_workspace)
    review["group_reviews"] = []
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)

    assert code == 0
    assert trace["passed"] is True
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["metrics"]["group_reviews"] == 0


def test_group_review_field_may_be_omitted_when_no_concrete_gap_was_found(review_workspace):
    review = load_review(review_workspace)
    review.pop("group_reviews")
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)

    assert code == 0
    assert trace["passed"] is True
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["metrics"]["group_reviews"] == 0


def test_review_rejects_duplicate_scoped_claim_reviews(review_workspace):
    review = load_review(review_workspace)
    review["claim_reviews"].append(copy.deepcopy(review["claim_reviews"][0]))
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("duplicate claim reviews" in error for error in trace["errors"])


def test_review_items_bind_session_claim_source_and_prompt_version(review_workspace):
    review = load_review(review_workspace)
    review["session_id"] = "old-session"
    review["claim_reviews"][0]["session_id"] = "old-session"
    review["claim_reviews"][0]["claim_sha256"] = "0" * 64
    review["claim_reviews"][0]["source_sha256"] = "1" * 64
    review["claim_reviews"][0]["spec_critic_prompt_version"] = "old-prompt"
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("session_id does not match current session" in error for error in trace["errors"])
    assert any("claim_sha256 does not match the current claim" in error for error in trace["errors"])
    assert any("source_sha256 does not match the current claim source" in error for error in trace["errors"])
    assert any("spec_critic_prompt_version must be" in error for error in trace["errors"])


def test_review_source_binding_detects_changed_design_source(review_workspace):
    (Path(review_workspace["design"]) / "contract.md").write_text(
        "The component must now retain every accepted item twice.\n", encoding="utf-8",
    )

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert trace["accepted_claim_ids"] == []
    assert any(
        "source_ref.source_sha256 does not match source file" in error
        for error in trace["errors"]
    )


def test_scope_requires_current_session_and_round_but_not_whole_claim_digest(review_workspace):
    state = Path(review_workspace["state"])
    scope = ac.load_json(state / "claim_review_scope.json")
    scope["session_id"] = "old-session"
    scope["round_id"] = ""
    ac.save_json(state / "claim_review_scope.json", scope)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("scope.json session_id does not match" in error for error in trace["errors"])
    assert any("scope.json round_id must be a non-empty string" in error for error in trace["errors"])


@pytest.mark.parametrize(
    ("claim_ids", "message"),
    [
        ([], "claim_ids must not be empty"),
        (["CLAIM-1", "CLAIM-1"], "duplicate claim_ids"),
        (["CLAIM-UNKNOWN"], "unknown claim_ids"),
        ([""], "claim_ids entries must be non-empty strings"),
    ],
)
def test_scope_claim_ids_must_be_nonempty_unique_and_known(
    review_workspace, claim_ids, message,
):
    write_scope(review_workspace, claim_ids)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any(message in error for error in trace["errors"])


def test_claim_review_scope_is_capped_at_twenty_four_claims(review_workspace):
    state = Path(review_workspace["state"])
    scope = ac.load_json(state / "claim_review_scope.json")
    scope["claim_ids"] = [f"CLAIM-{index}" for index in range(1, 26)]
    ac.save_json(state / "claim_review_scope.json", scope)

    code, trace = run_validator(review_workspace)

    assert code == 1
    assert any("at most 24 claims" in error for error in trace["errors"])


def test_scoped_claims_must_belong_to_a_design_coverage_group(review_workspace):
    state = Path(review_workspace["state"])
    coverage = ac.load_json(state / "design_coverage.json")
    coverage["document_groups"][0]["claim_ids"] = []
    ac.save_json(state / "design_coverage.json", coverage)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("not assigned to a design_coverage document group" in error for error in trace["errors"])


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
    assert code == 0
    assert trace["schema_valid"] is True
    assert trace["passed"] is True
    assert trace["repair_required"] is True
    assert trace["metrics"]["repairs"] == 1
    assert trace["accepted_claim_ids"] == []
    assert trace["repaired_claim_ids"] == ["CLAIM-1"]


def test_group_gap_becomes_nonblocking_expansion_request(review_workspace):
    review = load_review(review_workspace)
    group = review["group_reviews"][0]
    group["branches"] = {
        "assessment": "gaps_found", "missing_items": [],
        "rationale": "An independent fallback branch is not represented.",
    }
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("gaps_found assessment requires at least one missing item" in error for error in trace["errors"])

    group["branches"]["missing_items"] = [{
        "description": "Fallback behavior", "path": "contract.md", "section": "Fallback",
        "line_start": 1, "line_end": 1,
        "quote": "The component must retain each accepted item.",
        "why_independent": "The fallback has its own trigger and observable result.",
        "affected_claim_ids": [],
    }]
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)
    code, trace = run_validator(review_workspace)
    assert code == 0
    assert trace["schema_valid"] is True
    assert trace["repair_required"] is False
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["repaired_claim_ids"] == []
    assert trace["metrics"]["expansion_requests"] == 1
    assert trace["expansion_requests"][0]["blocking"] is False
    assert trace["expansion_requests"][0]["document_key"] == "contract"


def test_group_gap_affecting_claim_semantics_requires_that_claim_to_repair(review_workspace):
    state = Path(review_workspace["state"])
    review = load_review(review_workspace)
    group = review["group_reviews"][0]
    group["branches"] = {
        "assessment": "gaps_found",
        "missing_items": [{
            "description": "A condition that changes the current obligation",
            "path": "contract.md", "section": "Condition", "line_start": 1,
            "line_end": 1, "quote": "The component must retain each accepted item.",
            "why_independent": "The condition narrows when the scoped claim applies.",
            "affected_claim_ids": ["CLAIM-1"],
        }],
        "rationale": "The omitted condition changes CLAIM-1 applicability.",
    }
    group["decision"] = "repair"
    group["repair_actions"] = ["Repair CLAIM-1 applicability before investigation."]
    ac.save_json(state / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("must have matching repair claim reviews" in error for error in trace["errors"])

    claim = review["claim_reviews"][0]
    claim["applicability"] = {
        "assessment": "unsupported",
        "rationale": "The omitted condition makes the current scope unsupported.",
    }
    claim["decision"] = "repair"
    claim["repair_actions"] = ["Narrow the claim applicability to the cited condition."]
    review["decision"] = "repair"
    ac.save_json(state / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 0
    assert trace["accepted_claim_ids"] == []
    assert trace["repaired_claim_ids"] == ["CLAIM-1"]
    assert trace["expansion_requests"][0]["blocking"] is True


def test_unrelated_claim_addition_does_not_invalidate_accepted_review(review_workspace):
    state = Path(review_workspace["state"])
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert not errors
    claims.append({
        "claim_id": "CLAIM-UNSCOPED", "session_id": review_workspace["session_id"],
        "normative_strength": "informational", "behavior": "Describe an unrelated note.",
        "source_ref": {
            "path": "status.md", "line_start": 1, "line_end": 1,
            "source_sha256": ac.sha256_file(Path(review_workspace["design"]) / "status.md"),
        },
    })
    _write_jsonl(state / "design_claims.jsonl", claims)

    code, trace = run_validator(review_workspace)
    assert code == 0
    assert trace["passed"] is True
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["metrics"]["claims"] == 3


def test_changed_scoped_claim_invalidates_only_its_review_item(review_workspace):
    state = Path(review_workspace["state"])
    write_scope(review_workspace, ["CLAIM-1", "CLAIM-2"])
    write_valid_review(review_workspace)
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert not errors
    next(claim for claim in claims if claim["claim_id"] == "CLAIM-2")["behavior"] = (
        "Expose a changed status indication."
    )
    _write_jsonl(state / "design_claims.jsonl", claims)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["repaired_claim_ids"] == []
    assert any("claim_sha256 does not match the current claim" in error for error in trace["errors"])


def test_one_repair_claim_does_not_block_other_accepted_claims(review_workspace):
    state = Path(review_workspace["state"])
    write_scope(review_workspace, ["CLAIM-1", "CLAIM-2"])
    write_valid_review(review_workspace)
    review = load_review(review_workspace)
    claim = next(item for item in review["claim_reviews"] if item["claim_id"] == "CLAIM-2")
    claim["atomicity"] = {
        "assessment": "bundled",
        "obligations": ["Expose status.", "Refresh status."],
        "rationale": "The indexed behavior combines two observable obligations.",
    }
    claim["decision"] = "repair"
    claim["repair_actions"] = ["Split the status claim into two atomic claims."]
    review["decision"] = "repair"
    ac.save_json(state / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 0
    assert trace["passed"] is True
    assert trace["repair_required"] is True
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["repaired_claim_ids"] == ["CLAIM-2"]


def test_group_review_is_bound_to_its_inventory_group_digest(review_workspace):
    review = load_review(review_workspace)
    review["group_reviews"][0]["group_sha256"] = "0" * 64
    ac.save_json(Path(review_workspace["state"]) / "design_claim_review.json", review)

    code, trace = run_validator(review_workspace)
    assert code == 1
    assert any("group_sha256 does not match the current inventory group" in error for error in trace["errors"])


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
