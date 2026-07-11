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
import design_artifact_validator as validator  # noqa: E402


@pytest.fixture
def artifacts(tmp_path: Path) -> dict[str, object]:
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    state = logs / "state"
    for path in (code, design, result, state):
        path.mkdir(parents=True)
    (design / "alpha.md").write_text("# Alpha\nThe component must preserve every record.\n", encoding="utf-8")
    (design / "beta.md").write_text("# Beta\nThe component must report failures.\n", encoding="utf-8")
    session_id = "session-design-validator"
    ac.save_json(state / "agent_loop_state.json", {"session_id": session_id})
    manifest = {
        "paths": {
            "code_root": str(code.resolve()),
            "design_root": str(design.resolve()),
            "result_root": str(result.resolve()),
            "log_root": str(logs.resolve()),
            "state_root": str(state.resolve()),
        },
        "design": {
            "document_groups": [
                {"document_key": "alpha", "members": ["alpha.md"], "explicit_entry": False},
                {"document_key": "beta", "members": ["beta.md"], "explicit_entry": False},
            ],
        },
    }
    ac.save_json(state / "workspace_manifest.json", manifest)
    values: dict[str, object] = {
        "code": code, "design": design, "result": result, "logs": logs, "state": state,
        "session_id": session_id, "manifest": manifest,
    }
    write_valid_artifacts(values)
    return values


def claim(
    session_id: str,
    claim_id: str,
    path: str,
    quote: str,
    family: str,
    *,
    strength: str = "mandatory",
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "session_id": session_id,
        "document": Path(path).name,
        "path": path,
        "section": "Contract",
        "line_start": 2,
        "line_end": 2,
        "quote": quote,
        "behavior": f"The implementation exhibits the bounded behavior described by {claim_id}.",
        "behavior_family": family,
        "normative_strength": strength,
        "applicability": "The supplied component in the documented operating mode.",
        "priority": "high",
        "ambiguities": [],
        "probe_oracle": {
            "testability": "candidate",
            "preconditions": ["The documented operation can be invoked."],
            "stimulus": "Invoke the documented operation with a covered input.",
            "expected_observation": quote,
            "non_testable_reason": "",
        },
    }


def write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def write_valid_artifacts(values: dict[str, object]) -> None:
    state = values["state"]
    assert isinstance(state, Path)
    session_id = str(values["session_id"])
    claims = [
        claim(
            session_id, "CLAIM-ALPHA", "alpha.md",
            "The component must preserve every record.", "record preservation",
        ),
        claim(
            session_id, "CLAIM-BETA", "beta.md",
            "The component must report failures.", "failure reporting",
        ),
    ]
    write_jsonl(state / "design_claims.jsonl", claims)
    ac.save_json(state / "design_coverage.json", {
        "session_id": session_id,
        "document_groups": [
            {
                "document_key": "alpha", "members": ["alpha.md"], "disposition": "applicable",
                "evidence": "The document defines behavior for the supplied component.",
                "claim_ids": ["CLAIM-ALPHA"], "behavior_families": ["record preservation"],
            },
            {
                "document_key": "beta", "members": ["beta.md"], "disposition": "applicable",
                "evidence": "The document defines behavior for the supplied component.",
                "claim_ids": ["CLAIM-BETA"], "behavior_families": ["failure reporting"],
            },
        ],
    })


def run_validator(values: dict[str, object]) -> tuple[int, dict[str, object]]:
    args = argparse.Namespace(
        code_root=str(values["code"]), design_root=str(values["design"]),
        result_root=str(values["result"]), log_root=str(values["logs"]), state_root=None,
        design_entry=[], source_manifest=None,
    )
    result = validator.run(args)
    trace = ac.load_json(Path(values["logs"]) / "trace" / "design_validation.json")
    return result, trace


def load_claims(values: dict[str, object]) -> list[dict[str, object]]:
    state = values["state"]
    assert isinstance(state, Path)
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert not errors
    return claims


def load_coverage(values: dict[str, object]) -> dict[str, object]:
    state = values["state"]
    assert isinstance(state, Path)
    return ac.load_json(state / "design_coverage.json")


def test_valid_direct_design_artifacts_pass_without_forcing_optional_or_capability_claims(artifacts):
    code, trace = run_validator(artifacts)
    assert code == 0
    assert trace["passed"] is True


def test_applicable_behavior_family_requires_a_same_group_claim(artifacts):
    coverage = load_coverage(artifacts)
    coverage["document_groups"][0]["behavior_families"].append("independent timing behavior")
    ac.save_json(Path(artifacts["state"]) / "design_coverage.json", coverage)
    code, trace = run_validator(artifacts)
    assert code == 1
    assert any("behavior families lack same-group claims" in error for error in trace["errors"])


def test_applicable_claim_family_must_be_declared_by_its_group(artifacts):
    claims = load_claims(artifacts)
    claims[0]["behavior_family"] = "undeclared family"
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts)
    assert code == 1
    assert any("same-group claims use undeclared behavior families" in error for error in trace["errors"])


def test_coverage_rejects_unknown_and_cross_group_claim_references(artifacts):
    coverage = load_coverage(artifacts)
    coverage["document_groups"][0]["claim_ids"] = ["CLAIM-MISSING", "CLAIM-BETA"]
    ac.save_json(Path(artifacts["state"]) / "design_coverage.json", coverage)
    code, trace = run_validator(artifacts)
    assert code == 1
    assert any("unknown claim_id 'CLAIM-MISSING'" in error for error in trace["errors"])
    assert any("claim 'CLAIM-BETA' cites different document group 'beta'" in error for error in trace["errors"])


def test_claim_path_must_be_a_member_of_its_manifest_group(artifacts):
    claims = load_claims(artifacts)
    claims[0]["path"] = "unlisted.md"
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts)
    assert code == 1
    assert any("path is not a member of any manifest document group" in error for error in trace["errors"])


def test_catalog_scoped_applicable_group_requires_declared_capability_claim(artifacts):
    manifest = artifacts["manifest"]
    assert isinstance(manifest, dict)
    manifest["design"]["source_manifest"] = {
        "sources": [
            {
                "source_id": "catalog", "bundle_path": "catalog/index.md",
                "catalog_evidence": {"path": "index.md"},
            },
            {
                "source_id": "alpha-source", "bundle_path": "alpha.md",
                "catalog_evidence": {"path": "index.md", "line_start": 1, "line_end": 1, "quote": "Alpha"},
            },
        ],
    }
    ac.save_json(Path(artifacts["state"]) / "workspace_manifest.json", manifest)
    code, trace = run_validator(artifacts)
    assert code == 1
    assert any("catalog-scoped applicable group needs a declared_capability claim" in error for error in trace["errors"])

    claims = load_claims(artifacts)
    claims[0]["normative_strength"] = "declared_capability"
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts)
    assert code == 0
    assert trace["passed"] is True


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("behavior", [], "missing/empty behavior"),
        ("quote", {}, "missing/empty quote"),
        ("normative_strength", ["mandatory"], "normative_strength must be a string"),
        ("line_start", "2", "line_start must be an integer"),
    ],
)
def test_claim_requires_independent_typed_contract_fields(artifacts, field, bad_value, message):
    claims = load_claims(artifacts)
    claims[0][field] = bad_value
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts)
    assert code == 1
    assert any(message in error for error in trace["errors"])
