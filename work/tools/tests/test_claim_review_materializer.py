from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import claim_review_materializer as materializer  # noqa: E402
import claim_review_validator as validator  # noqa: E402


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in values),
        encoding="utf-8",
    )


def _semantic_review(
    strength: str, *, decision: str = "accept",
) -> dict[str, object]:
    repair = decision == "repair"
    return {
        "quote_entailment": {
            "assessment": "not_entailed" if repair else "entailed",
            "rationale": (
                "The behavior broadens the cited requirement."
                if repair else "The cited sentence directly states the behavior."
            ),
        },
        "normative_strength": {
            "assessment": "correct",
            "recommended_strength": strength,
            "rationale": "The claim preserves the cited modal strength.",
        },
        "atomicity": {
            "assessment": "atomic",
            "obligations": ["Perform the stated behavior."],
            "rationale": "The claim expresses one independently observable obligation.",
        },
        "applicability": {
            "assessment": "supported",
            "rationale": "The cited design scope supports this component.",
        },
        "decision": decision,
        "repair_rationale": (
            "Narrow the behavior to the condition present in the cited text."
            if repair else ""
        ),
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
    session_id = "session-claim-review-materializer"
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
    design_manifest = {
        "session_id": session_id,
        "prepared_at": workspace_manifest["prepared_at"],
        "review_design_root": str(design.resolve()),
        "design": dict(workspace_manifest["design"]),
        "preflight_problems": [],
    }
    ac.save_json(state / "design_agent_manifest.json", design_manifest)
    claims = [
        {
            "claim_id": "CLAIM-1",
            "session_id": session_id,
            "normative_strength": "mandatory",
            "behavior": "Retain every accepted item.",
            "source_ref": {
                "path": "contract.md", "line_start": 1, "line_end": 1,
                "source_sha256": ac.sha256_file(design / "contract.md"),
            },
        },
        {
            "claim_id": "CLAIM-2",
            "session_id": session_id,
            "normative_strength": "recommended",
            "behavior": "Expose a status indication.",
            "source_ref": {
                "path": "status.md", "line_start": 1, "line_end": 1,
                "source_sha256": ac.sha256_file(design / "status.md"),
            },
        },
    ]
    _write_jsonl(state / "design_claims.jsonl", claims)
    coverage_groups = [
        {
            "document_key": "contract", "members": ["contract.md"],
            "claim_ids": ["CLAIM-1"],
        },
        {
            "document_key": "status", "members": ["status.md"],
            "claim_ids": ["CLAIM-2"],
        },
    ]
    ac.save_json(state / "design_coverage.json", {
        "session_id": session_id,
        "document_groups": coverage_groups,
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
    ac.save_json(state / "claim_review_scope.json", {
        "session_id": session_id,
        "round_id": "ROUND-001",
        "claim_ids": ["CLAIM-1", "CLAIM-2"],
    })
    semantic = {
        "reviews": [
            _semantic_review("mandatory"),
            _semantic_review("recommended", decision="repair"),
        ],
    }
    semantic_path = tmp_path / "spec-critic-semantic.json"
    output_path = state / "design_claim_review.json"
    trace_path = logs / "trace" / "claim_review_materialization.json"
    ac.save_json(semantic_path, semantic)
    return {
        "code": code, "design": design, "result": result, "logs": logs,
        "state": state, "semantic": semantic, "semantic_path": semantic_path,
        "output_path": output_path, "trace_path": trace_path,
        "session_id": session_id, "claims": claims,
    }


def _run(values: dict[str, object]) -> int:
    return materializer.main([
        "--state-root", str(values["state"]),
        "--input", str(values["semantic_path"]),
        "--trace", str(values["trace_path"]),
    ])


def _run_validator(values: dict[str, object]) -> tuple[int, dict]:
    result = validator.run(argparse.Namespace(
        code_root=str(values["code"]),
        design_root=str(values["design"]),
        result_root=str(values["result"]),
        log_root=str(values["logs"]),
        state_root=None,
        design_entry=[],
        source_manifest=None,
    ))
    trace = ac.load_json(
        Path(values["logs"]) / "trace" / "claim_review_validation.json"
    )
    return result, trace


def test_materializer_builds_review_accepted_by_current_validator(review_workspace):
    assert _run(review_workspace) == 0

    review = ac.load_json(Path(review_workspace["output_path"]))
    claims = review_workspace["claims"]
    assert isinstance(claims, list)
    assert review["session_id"] == review_workspace["session_id"]
    assert review["group_reviews"] == []
    assert review["decision"] == "repair"
    assert review["claim_reviews"][0]["claim_id"] == "CLAIM-1"
    assert review["claim_reviews"][1]["claim_id"] == "CLAIM-2"
    for item, claim in zip(review["claim_reviews"], claims):
        assert item["claim_sha256"] == validator._claim_digest(claim)
        assert item["source_sha256"] == claim["source_ref"]["source_sha256"]
        assert item["session_id"] == review_workspace["session_id"]
        assert item["spec_critic_prompt_version"] == validator.SPEC_CRITIC_PROMPT_VERSION
        assert item["normative_strength"]["stated_strength"] == claim["normative_strength"]
    state = Path(review_workspace["state"])
    assert review["input_digests"] == {
        name: ac.sha256_file(state / name)
        for name in materializer.BOUND_INPUTS
    }

    code, trace = _run_validator(review_workspace)
    assert code == 0
    assert trace["passed"] is True
    assert trace["accepted_claim_ids"] == ["CLAIM-1"]
    assert trace["repaired_claim_ids"] == ["CLAIM-2"]
    assert trace["metrics"]["group_reviews"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claim_id", "CLAIM-ATTACKER"),
        ("session_id", "stale-session"),
        ("claim_sha256", "0" * 64),
        ("source_sha256", "1" * 64),
        ("source_ref", {"path": "forged.md"}),
        ("spec_critic_prompt_version", "forged-version"),
    ],
)
def test_materializer_rejects_identity_digest_source_or_version_override(
    review_workspace, field, value,
):
    semantic = dict(review_workspace["semantic"])
    semantic["reviews"] = [dict(item) for item in semantic["reviews"]]
    semantic["reviews"][0][field] = value
    ac.save_json(Path(review_workspace["semantic_path"]), semantic)

    assert _run(review_workspace) == 1
    assert not Path(review_workspace["output_path"]).exists()
    trace = ac.load_json(Path(review_workspace["trace_path"]))
    assert trace["passed"] is False
    assert any("unsupported fields" in error for error in trace["errors"])


def test_materializer_rejects_stated_strength_override(review_workspace):
    semantic = dict(review_workspace["semantic"])
    semantic["reviews"] = [dict(item) for item in semantic["reviews"]]
    semantic["reviews"][0]["normative_strength"] = dict(
        semantic["reviews"][0]["normative_strength"],
        stated_strength="informational",
    )
    ac.save_json(Path(review_workspace["semantic_path"]), semantic)

    assert _run(review_workspace) == 1
    assert not Path(review_workspace["output_path"]).exists()
    trace = ac.load_json(Path(review_workspace["trace_path"]))
    assert any("unsupported fields" in error for error in trace["errors"])


def test_materializer_binds_reviews_by_scope_order_without_model_ids(review_workspace):
    semantic = dict(review_workspace["semantic"])
    semantic["reviews"] = [
        _semantic_review("mandatory", decision="repair"),
        _semantic_review("recommended"),
    ]
    ac.save_json(Path(review_workspace["semantic_path"]), semantic)

    assert _run(review_workspace) == 0
    review = ac.load_json(Path(review_workspace["output_path"]))
    assert [item["claim_id"] for item in review["claim_reviews"]] == [
        "CLAIM-1", "CLAIM-2",
    ]
    assert [item["decision"] for item in review["claim_reviews"]] == [
        "repair", "accept",
    ]


def test_materializer_requires_exact_review_count(review_workspace):
    semantic = {"reviews": [review_workspace["semantic"]["reviews"][0]]}
    ac.save_json(Path(review_workspace["semantic_path"]), semantic)

    assert _run(review_workspace) == 1
    assert not Path(review_workspace["output_path"]).exists()
    trace = ac.load_json(Path(review_workspace["trace_path"]))
    assert any("exactly one ordered review" in error for error in trace["errors"])


def test_materializer_rejects_inconsistent_semantic_decision(review_workspace):
    semantic = dict(review_workspace["semantic"])
    semantic["reviews"] = [dict(item) for item in semantic["reviews"]]
    first = semantic["reviews"][0]
    first["quote_entailment"] = {
        "assessment": "not_entailed",
        "rationale": "The claim broadens the quoted text.",
    }
    ac.save_json(Path(review_workspace["semantic_path"]), semantic)

    assert _run(review_workspace) == 1
    assert not Path(review_workspace["output_path"]).exists()
    trace = ac.load_json(Path(review_workspace["trace_path"]))
    assert any("decision must be 'repair'" in error for error in trace["errors"])


def test_materializer_rejects_changed_design_source(review_workspace):
    (Path(review_workspace["design"]) / "contract.md").write_text(
        "The component may now retain an accepted item.\n", encoding="utf-8",
    )

    assert _run(review_workspace) == 1
    assert not Path(review_workspace["output_path"]).exists()
    trace = ac.load_json(Path(review_workspace["trace_path"]))
    assert any("does not match source file" in error for error in trace["errors"])
