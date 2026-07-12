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
import design_source_materializer as materializer  # noqa: E402


def write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def claim_draft(
    session_id: str, claim_id: str, path: str, line: int, family: str,
    *, strength: str = "mandatory", candidate_id: str | None = None,
    request_id: str | None = None, document_key: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "claim_id": claim_id,
        "session_id": session_id,
        "source_ref": {"path": path, "line_start": line, "line_end": line},
        "subject": "The documented component",
        "trigger": "When the documented operation is invoked",
        "obligation": f"It fulfills the atomic obligation identified by {claim_id}.",
        "exceptions": [],
        "observable_result": "The documented result is externally observable.",
        "behavior_family": family,
        "normative_strength": strength,
        "applicability": "The supplied component in the documented operating mode.",
        "ambiguities": [],
    }
    if candidate_id is not None:
        value["candidate_id"] = candidate_id
    if request_id is not None:
        value["request_id"] = request_id
    if document_key is not None:
        value["document_key"] = document_key
    return value


def inventory_draft(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "document_groups": [
            {
                "document_key": "alpha",
                "members": ["alpha.md"],
                "scope_relation": "required",
                "scope_evidence": {
                    "source_ref": {"path": "alpha.md", "line_start": 1, "line_end": 2},
                },
                "sections": [
                    {
                        "section_id": "alpha-contract",
                        "source_ref": {"path": "alpha.md", "line_start": 1, "line_end": 2},
                        "behavior_families": ["record preservation"],
                        "ambiguities": [],
                    },
                ],
            },
            {
                "document_key": "beta",
                "members": ["beta.md"],
                "scope_relation": "relevant",
                "scope_evidence": {
                    "source_ref": {"path": "beta.md", "line_start": 1, "line_end": 2},
                },
                "sections": [
                    {
                        "section_id": "beta-contract",
                        "source_ref": {"path": "beta.md", "line_start": 1, "line_end": 2},
                        "behavior_families": ["failure reporting"],
                        "ambiguities": [],
                    },
                ],
            },
        ],
    }


def coverage_fixture(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "document_groups": [
            {
                "document_key": "alpha",
                "members": ["alpha.md"],
                "disposition": "applicable",
                "evidence": "Alpha defines behavior for the supplied component.",
                "claim_ids": ["CLAIM-ALPHA"],
                "behavior_families": ["record preservation"],
            },
            {
                "document_key": "beta",
                "members": ["beta.md"],
                "disposition": "applicable",
                "evidence": "Beta defines applicable failure behavior for the component.",
                "claim_ids": [],
                "behavior_families": ["failure reporting"],
            },
        ],
    }


@pytest.fixture
def artifacts(tmp_path: Path) -> dict[str, object]:
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    state = logs / "state"
    for path in (code, design, result, state):
        path.mkdir(parents=True)
    (design / "alpha.md").write_text(
        "# Alpha\nThe component must preserve every record.\n", encoding="utf-8",
    )
    (design / "beta.md").write_text(
        "# Beta\nThe component must report failures.\n", encoding="utf-8",
    )
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
    inventory = materializer.materialize_inventory(inventory_draft(session_id), design)
    ac.save_json(state / "design_inventory.json", inventory)
    candidate_id = "OBS-ALPHA"
    request_id = "LOOKUP-ALPHA"
    draft = claim_draft(
        session_id, "CLAIM-ALPHA", "alpha.md", 2, "record preservation",
        candidate_id=candidate_id, request_id=request_id, document_key="alpha",
    )
    claims = materializer.materialize_claims([draft], design)
    write_jsonl(state / "design_claims.jsonl", claims)
    code_evidence = [{
        "file": "service.py", "line_start": 1, "line_end": 1,
        "symbol": "handle", "snippet": "return first_record",
    }]
    write_jsonl(state / "risk_observations.jsonl", [{
        "observation_id": candidate_id,
        "session_id": session_id,
        "sweep_id": "SCOUT-ALPHA",
        "direction": "design_to_code",
        "mismatch_signal": "direct_conflict",
        "behavior_question": "Does the implementation preserve every record?",
        "design_section_ids": ["alpha-contract"],
        "design_requirement": {
            "source_ref": {"path": "alpha.md", "line_start": 2, "line_end": 2},
            "subject": draft["subject"],
            "trigger": draft["trigger"],
            "obligation": draft["obligation"],
            "exceptions": draft["exceptions"],
            "observable_result": draft["observable_result"],
            "normative_strength": draft["normative_strength"],
            "applicability": draft["applicability"],
            "ambiguities": draft["ambiguities"],
        },
        "code_evidence": code_evidence,
    }])
    write_jsonl(state / "design_lookup_requests.jsonl", [{
        "request_id": request_id,
        "candidate_id": candidate_id,
        "session_id": session_id,
        "origin": "semantic_scout",
        "origin_id": candidate_id,
        "sweep_id": "SCOUT-ALPHA",
        "direction": "design_to_code",
        "document_keys": ["alpha"],
        "section_ids": ["alpha-contract"],
        "question": "Does the implementation preserve every record?",
        "required_branch": ac.canonical_claim_branch(claims[0]),
        "mismatch_signal": "direct_conflict",
        "code_evidence": code_evidence,
    }])
    ac.save_json(state / "design_coverage.json", coverage_fixture(session_id))
    return {
        "code": code, "design": design, "result": result, "logs": logs, "state": state,
        "session_id": session_id, "manifest": manifest,
    }


def run_validator(
    values: dict[str, object], mode: str = "all",
) -> tuple[int, dict[str, object]]:
    args = argparse.Namespace(
        code_root=str(values["code"]), design_root=str(values["design"]),
        result_root=str(values["result"]), log_root=str(values["logs"]), state_root=None,
        design_entry=[], source_manifest=None, mode=mode,
    )
    result = validator.run(args)
    trace = ac.load_json(Path(values["logs"]) / "trace" / "design_validation.json")
    return result, trace


def load_claims(values: dict[str, object]) -> list[dict[str, object]]:
    claims, errors = ac.load_jsonl(Path(values["state"]) / "design_claims.jsonl")
    assert not errors
    return claims


def load_inventory(values: dict[str, object]) -> dict[str, object]:
    return ac.load_json(Path(values["state"]) / "design_inventory.json")


def load_coverage(values: dict[str, object]) -> dict[str, object]:
    return ac.load_json(Path(values["state"]) / "design_coverage.json")


def test_inventory_mode_passes_before_any_claim_exists(artifacts):
    (Path(artifacts["state"]) / "design_claims.jsonl").unlink()
    (Path(artifacts["state"]) / "design_coverage.json").unlink()
    code, trace = run_validator(artifacts, "inventory")
    assert code == 0
    assert trace["passed"] is True
    assert trace["metrics"]["claims"] == 0


def test_claims_mode_requires_a_valid_inventory(artifacts):
    (Path(artifacts["state"]) / "design_inventory.json").unlink()
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["ARTIFACT_MISSING"] == 1


def test_empty_inventory_is_not_treated_as_a_valid_inventory_fixture(artifacts):
    ac.save_json(Path(artifacts["state"]) / "design_inventory.json", {})
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["SESSION_MISMATCH"] >= 1
    assert trace["error_count_by_code"]["ARTIFACT_SCHEMA_INVALID"] >= 1
    assert trace["error_count_by_code"]["INVENTORY_GROUP_COVERAGE"] == 1


def test_on_demand_claims_do_not_require_full_group_or_oracle_coverage(artifacts):
    code, trace = run_validator(artifacts)
    assert code == 0
    assert trace["passed"] is True
    assert trace["metrics"] == {
        "claims": 1,
        "coverage_document_groups": 2,
        "manifest_document_groups": 2,
        "inventory_document_groups": 2,
    }
    assert trace["input_digests"]["design_coverage.json"] == ac.sha256_file(
        Path(artifacts["state"]) / "design_coverage.json"
    )
    assert trace["lineage_input_digests"] == {
        "design_lookup_requests.jsonl": ac.sha256_file(
            Path(artifacts["state"]) / "design_lookup_requests.jsonl"
        ),
        "risk_observations.jsonl": ac.sha256_file(
            Path(artifacts["state"]) / "risk_observations.jsonl"
        ),
    }


def test_claim_lineage_is_one_to_one_across_lookup_and_candidate(artifacts):
    state = Path(artifacts["state"])
    lookups, errors = ac.load_jsonl(state / "design_lookup_requests.jsonl")
    assert errors == []
    duplicate = dict(lookups[0])
    duplicate["request_id"] = "LOOKUP-UNCLAIMED"
    write_jsonl(state / "design_lookup_requests.jsonl", [*lookups, duplicate])

    code, trace = run_validator(artifacts, "claims")

    assert code == 1
    assert trace["error_count_by_code"]["LOOKUP_LINEAGE_DUPLICATE"] == 1
    assert trace["error_count_by_code"]["CLAIM_LOOKUP_COVERAGE"] >= 1


def test_claim_lineage_rejects_candidate_identity_drift(artifacts):
    state = Path(artifacts["state"])
    claims = load_claims(artifacts)
    claims[0]["candidate_id"] = "OBS-SUBSTITUTED"
    write_jsonl(state / "design_claims.jsonl", claims)

    code, trace = run_validator(artifacts, "claims")

    assert code == 1
    assert trace["error_count_by_code"]["CLAIM_LINEAGE_INVALID"] >= 1
    assert trace["error_count_by_code"]["CLAIM_LOOKUP_COVERAGE"] >= 1


def test_claim_lineage_rejects_selected_compliance_observation(artifacts):
    state = Path(artifacts["state"])
    observations, errors = ac.load_jsonl(state / "risk_observations.jsonl")
    assert errors == []
    observations[0]["mismatch_signal"] = "no_difference"
    write_jsonl(state / "risk_observations.jsonl", observations)
    lookups, errors = ac.load_jsonl(state / "design_lookup_requests.jsonl")
    assert errors == []
    lookups[0]["mismatch_signal"] = "no_difference"
    write_jsonl(state / "design_lookup_requests.jsonl", lookups)

    code, trace = run_validator(artifacts, "claims")

    assert code == 1
    assert trace["error_count_by_code"]["MISMATCH_CANDIDATE_INVALID"] >= 2


def test_claim_lineage_rejects_semantic_and_source_range_drift(artifacts):
    state = Path(artifacts["state"])
    claims = load_claims(artifacts)
    claims[0]["obligation"] = "Only preserve the first record."
    claims[0]["source_ref"]["line_start"] = 1
    claims[0]["line_start"] = 1
    claims[0]["quote"] = "# Alpha\nThe component must preserve every record."
    write_jsonl(state / "design_claims.jsonl", claims)

    code, trace = run_validator(artifacts, "claims")

    assert code == 1
    assert trace["error_count_by_code"]["CLAIM_LINEAGE_INVALID"] >= 1
    assert trace["error_count_by_code"]["CLAIM_SOURCE_SCOPE_INVALID"] >= 1


def test_claims_mode_requires_design_coverage(artifacts):
    (Path(artifacts["state"]) / "design_coverage.json").unlink()
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["ARTIFACT_MISSING"] == 1
    assert trace["input_digests"]["design_coverage.json"] == ""


def test_claims_mode_rejects_damaged_design_coverage_json(artifacts):
    coverage_path = Path(artifacts["state"]) / "design_coverage.json"
    coverage_path.write_text('{"session_id":', encoding="utf-8")
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["ARTIFACT_PARSE_ERROR"] == 1
    assert trace["input_digests"]["design_coverage.json"] == ac.sha256_file(coverage_path)


def test_coverage_session_groups_and_members_are_bound_to_inventory(artifacts):
    coverage = load_coverage(artifacts)
    coverage["session_id"] = "stale-session"
    coverage["document_groups"][0]["members"] = ["beta.md"]
    coverage["document_groups"].pop()
    ac.save_json(Path(artifacts["state"]) / "design_coverage.json", coverage)
    code, trace = run_validator(artifacts, "all")
    assert code == 1
    assert trace["error_count_by_code"]["SESSION_MISMATCH"] == 1
    assert trace["error_count_by_code"]["COVERAGE_MEMBER_MISMATCH"] == 1
    assert trace["error_count_by_code"]["COVERAGE_GROUP_COVERAGE"] == 1


def test_coverage_requires_current_group_schema_without_semantic_inference(artifacts):
    coverage = load_coverage(artifacts)
    alpha = coverage["document_groups"][0]
    alpha.pop("evidence")
    alpha["disposition"] = "catalog_implies_required"
    alpha["behavior_families"] = ["invented implementation family"]
    ac.save_json(Path(artifacts["state"]) / "design_coverage.json", coverage)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["ARTIFACT_SCHEMA_INVALID"] == 1
    assert trace["error_count_by_code"]["COVERAGE_SCHEMA_INVALID"] == 2
    assert trace["error_count_by_code"]["COVERAGE_FAMILY_INVALID"] == 1


def test_coverage_claim_ids_must_exist_and_belong_to_the_group(artifacts):
    coverage = load_coverage(artifacts)
    coverage["document_groups"][0]["claim_ids"] = ["CLAIM-MISSING"]
    coverage["document_groups"][1]["claim_ids"] = ["CLAIM-ALPHA"]
    ac.save_json(Path(artifacts["state"]) / "design_coverage.json", coverage)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["COVERAGE_CLAIM_INVALID"] == 2
    assert any("unknown claim_id 'CLAIM-MISSING'" in error for error in trace["errors"])
    assert any("belongs to document group 'alpha', not 'beta'" in error for error in trace["errors"])


def test_coverage_claim_ids_are_unique_across_the_coverage_index(artifacts):
    coverage = load_coverage(artifacts)
    coverage["document_groups"][1]["claim_ids"] = ["CLAIM-ALPHA"]
    ac.save_json(Path(artifacts["state"]) / "design_coverage.json", coverage)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["COVERAGE_CLAIM_DUPLICATE"] == 1


def test_every_materialized_claim_is_indexed_by_coverage(artifacts):
    coverage = load_coverage(artifacts)
    coverage["document_groups"][0]["claim_ids"] = []
    ac.save_json(Path(artifacts["state"]) / "design_coverage.json", coverage)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["COVERAGE_CLAIM_INVALID"] == 1
    assert any("not assigned to coverage groups" in error for error in trace["errors"])


def test_required_or_in_scope_group_has_no_claim_quota(artifacts):
    coverage = load_coverage(artifacts)
    coverage["document_groups"][0]["claim_ids"] = []
    state = Path(artifacts["state"])
    ac.save_json(state / "design_coverage.json", coverage)
    (state / "design_claims.jsonl").write_text("", encoding="utf-8")
    (state / "design_lookup_requests.jsonl").write_text("", encoding="utf-8")

    code, trace = run_validator(artifacts, "claims")

    assert code == 0
    assert trace["passed"] is True


def test_evidence_pair_claim_portfolio_is_capped_at_twelve() -> None:
    inventory_groups = {
        "broad": {
            "document_key": "broad",
            "members": ["broad.md"],
            "scope_relation": "required",
            "sections": [],
        },
    }
    claims = {
        f"CLAIM-{index:02d}": {"path": "broad.md", "line_start": 1, "line_end": 1}
        for index in range(13)
    }
    coverage = {
        "session_id": "session",
        "document_groups": [{
            "document_key": "broad", "members": ["broad.md"],
            "disposition": "applicable", "evidence": "The group is in scope.",
            "claim_ids": list(claims),
            "behavior_families": [],
        }],
    }
    issues = validator.Issues()

    validator.validate_coverage(
        coverage, "session", inventory_groups, claims,
        {"broad.md": "broad"}, issues,
    )

    assert any("at most 12 evidence-pair candidates" in error for error in issues.errors)


def test_required_inventory_rejects_uncovered_source_lines(artifacts):
    state = Path(artifacts["state"])
    inventory = ac.load_json(state / "design_inventory.json")
    section = inventory["document_groups"][0]["sections"][0]
    section["line_start"] = 2
    section["source_ref"]["line_start"] = 2
    group = inventory["document_groups"][0]
    group["group_sha256"] = validator.canonical_object_sha256(
        group, excluded={"group_sha256"},
    )
    ac.save_json(state / "design_inventory.json", inventory)

    code, trace = run_validator(artifacts, "inventory")

    assert code == 1
    assert trace["error_count_by_code"]["INVENTORY_SECTION_COVERAGE"] >= 1


def test_catalog_provenance_does_not_force_declared_capability(artifacts):
    manifest = artifacts["manifest"]
    assert isinstance(manifest, dict)
    manifest["design"]["source_manifest"] = {
        "sources": [
            {"source_id": "catalog", "bundle_path": "catalog/index.md"},
            {
                "source_id": "alpha-source", "bundle_path": "alpha.md",
                "catalog_evidence": {"path": "index.md"},
            },
        ],
    }
    ac.save_json(Path(artifacts["state"]) / "workspace_manifest.json", manifest)
    code, trace = run_validator(artifacts)
    assert code == 0
    assert trace["passed"] is True


def test_claim_materializer_owns_quote_heading_path_and_hash(artifacts):
    draft = claim_draft(
        str(artifacts["session_id"]), "CLAIM-M", "./alpha.md", 2, "record preservation",
    )
    draft.update({"path": "wrong.md", "quote": "invented", "section": "invented"})
    draft["source_ref"]["source_sha256"] = "0" * 64
    materialized = materializer.materialize_claims([draft], Path(artifacts["design"]))[0]
    assert materialized["source_ref"] == {
        "path": "alpha.md", "line_start": 2, "line_end": 2,
        "source_sha256": ac.sha256_file(Path(artifacts["design"]) / "alpha.md"),
    }
    assert materialized["path"] == "alpha.md"
    assert materialized["quote"] == "The component must preserve every record."
    assert materialized["section"] == "Alpha"
    assert materialized["document"] == "alpha.md"


def test_materializer_does_not_invent_semantic_claim_fields(artifacts):
    bare = {
        "source_ref": {"path": "alpha.md", "line_start": 2, "line_end": 2},
    }
    value = materializer.materialize_claims([bare], Path(artifacts["design"]))[0]
    assert not {
        "subject", "trigger", "obligation", "exceptions", "observable_result",
        "behavior_family", "normative_strength", "applicability", "ambiguities",
    }.intersection(value)


def test_artifact_materializer_refuses_to_write_into_design_root(artifacts):
    state = Path(artifacts["state"])
    draft_path = state / "claim-draft.jsonl"
    write_jsonl(draft_path, [
        claim_draft(
            str(artifacts["session_id"]), "CLAIM-M", "alpha.md", 2, "record preservation",
        ),
    ])
    output_path = Path(artifacts["design"]) / "forbidden.jsonl"
    args = argparse.Namespace(
        design_root=str(artifacts["design"]), input=str(draft_path), output=str(output_path),
        trace=None, materialize="claims",
    )
    assert materializer.materialize_artifact(args) == 1
    assert not output_path.exists()


def test_existing_local_source_plan_mode_remains_available(tmp_path):
    source_root = tmp_path / "source"
    output_root = tmp_path / "bundle"
    source_root.mkdir()
    (source_root / "catalog.md").write_text(
        "# Sources\nUse alpha.md as the design.\n", encoding="utf-8",
    )
    (source_root / "alpha.md").write_text("# Alpha\nRequired behavior.\n", encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    ac.save_json(plan_path, {
        "catalog_path": "catalog.md",
        "sources": [
            {
                "source_id": "alpha", "kind": "local", "location": "alpha.md",
                "output_path": "sources/alpha.md",
                "catalog_evidence": {
                    "path": "catalog.md", "line_start": 2, "line_end": 2,
                    "quote": "Use alpha.md as the design.",
                },
            },
        ],
    })
    args = argparse.Namespace(
        source_root=str(source_root), output_root=str(output_root), plan=str(plan_path),
        manifest=str(manifest_path), approval_log=None, allow_network=False,
        max_bytes=1024 * 1024, timeout_seconds=1,
    )
    assert materializer.materialize(args) == 0
    manifest = ac.load_json(manifest_path)
    assert manifest["passed"] is True
    assert (output_root / "sources" / "alpha.md").read_text(encoding="utf-8") == (
        "# Alpha\nRequired behavior.\n"
    )


def test_source_plan_mode_reports_invalid_json_without_traceback(tmp_path):
    source_root = tmp_path / "source"
    output_root = tmp_path / "bundle"
    source_root.mkdir()
    (source_root / "catalog.md").write_text("# Sources\n", encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    plan_path.write_text('{"catalog_path": "catalog.md",\tbad}', encoding="utf-8")
    args = argparse.Namespace(
        source_root=str(source_root), output_root=str(output_root), plan=str(plan_path),
        manifest=str(manifest_path), approval_log=None, allow_network=False,
        max_bytes=1024 * 1024, timeout_seconds=1,
    )

    assert materializer.materialize(args) == 1
    manifest = ac.load_json(manifest_path)
    assert manifest["passed"] is False
    assert manifest["sources"] == []
    assert any("could not load design source plan" in error for error in manifest["errors"])
    assert manifest["plan_sha256"] == ac.sha256_file(plan_path)


def test_source_plan_mode_rejects_real_catalog_quote_bound_to_another_local_source(tmp_path):
    source_root = tmp_path / "source"
    output_root = tmp_path / "bundle"
    source_root.mkdir()
    (source_root / "catalog.md").write_text("Use contract-a.md.\n", encoding="utf-8")
    (source_root / "contract-a.md").write_text("Contract A.\n", encoding="utf-8")
    (source_root / "contract-b.md").write_text("Contract B.\n", encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    ac.save_json(plan_path, {
        "catalog_path": "catalog.md",
        "sources": [{
            "source_id": "substituted", "kind": "local", "location": "contract-b.md",
            "output_path": "sources/contract-b.md",
            "catalog_evidence": {
                "path": "catalog.md", "line_start": 1, "line_end": 1,
                "quote": "Use contract-a.md.",
            },
        }],
    })
    args = argparse.Namespace(
        source_root=str(source_root), output_root=str(output_root), plan=str(plan_path),
        manifest=str(manifest_path), approval_log=None, allow_network=False,
        max_bytes=1024 * 1024, timeout_seconds=1,
    )

    assert materializer.materialize(args) == 1
    manifest = ac.load_json(manifest_path)
    assert manifest["passed"] is False
    assert any("location is not cited" in error for error in manifest["errors"])
    assert not (output_root / "sources" / "contract-b.md").exists()


def test_source_plan_mode_rejects_uncited_url_before_network_access(
    tmp_path, monkeypatch,
):
    source_root = tmp_path / "source"
    output_root = tmp_path / "state" / "design-sources"
    source_root.mkdir()
    (source_root / "catalog.md").write_text(
        "Use specs.example/contract-a.\n", encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    approval_log = output_root.parent / "approval_events.jsonl"
    ac.save_json(plan_path, {
        "catalog_path": "catalog.md",
        "sources": [{
            "source_id": "substituted-url", "kind": "url",
            "location": "https://unrelated.example/contract-b",
            "output_path": "sources/contract-b.txt",
            "catalog_evidence": {
                "path": "catalog.md", "line_start": 1, "line_end": 1,
                "quote": "Use specs.example/contract-a.",
            },
        }],
    })
    monkeypatch.setattr(
        materializer, "_fetch",
        lambda *args, **kwargs: pytest.fail("uncited URL must not be fetched"),
    )
    args = argparse.Namespace(
        source_root=str(source_root), output_root=str(output_root), plan=str(plan_path),
        manifest=str(manifest_path), approval_log=str(approval_log), allow_network=True,
        max_bytes=1024 * 1024, timeout_seconds=1,
    )

    assert materializer.materialize(args) == 1
    manifest = ac.load_json(manifest_path)
    assert manifest["passed"] is False
    assert any("location is not cited" in error for error in manifest["errors"])
    assert not approval_log.exists()


@pytest.mark.parametrize(
    ("kind", "location", "quote", "expected"),
    [
        (
            "url", "https://www.docs.example/spec/",
            "Use docs.example/spec.", True,
        ),
        (
            "url", "https://docs.example/spec",
            "Use evil-docs.example/spec.", False,
        ),
        (
            "url", "https://docs.example:444/spec",
            "Use docs.example/spec.", False,
        ),
        (
            "local", "Design Spec.md",
            "Use [the contract](Design Spec.md).", True,
        ),
        (
            "local", "contract.md",
            "Use old-contract.md.", False,
        ),
        (
            "local", "contract.md",
            "Use archive/contract.md.", False,
        ),
    ],
)
def test_catalog_location_binding_requires_equal_tokens(kind, location, quote, expected):
    assert materializer._catalog_cites_location(kind, location, quote) is expected


@pytest.mark.parametrize(
    "overlap",
    ("output", "plan", "manifest", "approval"),
)
def test_source_plan_mode_rejects_writes_or_control_files_in_supplied_source(
    tmp_path, overlap,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "catalog.md").write_text("Use contract.md.\n", encoding="utf-8")
    (source_root / "contract.md").write_text("Contract.\n", encoding="utf-8")
    output_root = tmp_path / "bundle"
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    approval_log = tmp_path / "approval_events.jsonl"
    if overlap == "output":
        output_root = source_root / "bundle"
    elif overlap == "plan":
        plan_path = source_root / "plan.json"
    elif overlap == "manifest":
        manifest_path = source_root / "manifest.json"
    else:
        approval_log = source_root / "approval_events.jsonl"
    ac.save_json(plan_path, {
        "catalog_path": "catalog.md",
        "sources": [{
            "source_id": "contract", "kind": "local", "location": "contract.md",
            "output_path": "sources/contract.md",
            "catalog_evidence": {
                "path": "catalog.md", "line_start": 1, "line_end": 1,
                "quote": "Use contract.md.",
            },
        }],
    })
    before = {
        path.relative_to(source_root).as_posix(): path.read_bytes()
        for path in source_root.rglob("*") if path.is_file()
    }
    args = argparse.Namespace(
        source_root=str(source_root), output_root=str(output_root), plan=str(plan_path),
        manifest=str(manifest_path), approval_log=str(approval_log), allow_network=False,
        max_bytes=1024 * 1024, timeout_seconds=1,
    )

    assert materializer.materialize(args) == 2
    after = {
        path.relative_to(source_root).as_posix(): path.read_bytes()
        for path in source_root.rglob("*") if path.is_file()
    }
    assert after == before
    assert not output_root.exists()
    if overlap == "manifest":
        assert not manifest_path.exists()
    else:
        assert ac.load_json(manifest_path)["passed"] is False


def test_inventory_materializer_owns_evidence_heading_and_group_digest(artifacts):
    inventory = load_inventory(artifacts)
    group = inventory["document_groups"][0]
    assert group["scope_evidence"]["quote"] == (
        "# Alpha\nThe component must preserve every record."
    )
    assert group["sections"][0]["heading"] == "Alpha"
    assert group["group_sha256"] == validator.canonical_object_sha256(
        group, excluded={"group_sha256"},
    )


def test_auto_inventory_mechanically_indexes_complete_manifest(tmp_path):
    design = tmp_path / "design"
    state = tmp_path / "state"
    design.mkdir()
    state.mkdir()
    (design / "catalog").mkdir()
    (design / "sources").mkdir()
    catalog = design / "catalog" / "benchmark.md"
    catalog.write_text("# Catalog\nListed specification.\n", encoding="utf-8")
    lines = [f"line {index}" for index in range(1, 1606)]
    lines[0] = "# Overview"
    lines[800] = "# Middle"
    lines[1600] = "# Tail"
    spec = design / "sources" / "contract.txt"
    spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    session_id = "session-auto-inventory"
    groups = [
        {
            "document_key": "catalog/benchmark",
            "members": ["catalog/benchmark.md"],
            "explicit_entry": False,
        },
        {
            "document_key": "sources/contract",
            "members": ["sources/contract.txt"],
            "explicit_entry": False,
        },
    ]
    manifest = {
        "session_id": session_id,
        "design": {
            "documents": [
                {
                    "path": "catalog/benchmark.md",
                    "sha256": ac.sha256_file(catalog),
                },
                {
                    "path": "sources/contract.txt",
                    "sha256": ac.sha256_file(spec),
                },
            ],
            "document_groups": groups,
        },
    }
    manifest_path = state / "design_agent_manifest.json"
    output_path = state / "design_inventory.json"
    trace_path = state / "auto_inventory_trace.json"
    ac.save_json(manifest_path, manifest)

    assert materializer.main([
        "--materialize", "auto-inventory",
        "--design-root", str(design),
        "--input", str(manifest_path),
        "--output", str(output_path),
        "--trace", str(trace_path),
    ]) == 0

    inventory = ac.load_json(output_path)
    assert inventory["session_id"] == session_id
    catalog_group, spec_group = inventory["document_groups"]
    assert catalog_group["scope_relation"] == "informational"
    assert spec_group["scope_relation"] == "in_scope"
    assert spec_group["scope_evidence"]["quote"] == "# Overview"
    assert [
        (section["line_start"], section["line_end"])
        for section in spec_group["sections"]
    ] == [(1, 800), (801, 1600), (1601, 1605)]
    assert [
        section["behavior_families"] for section in spec_group["sections"]
    ] == [
        ["Overview", "sources/contract.txt"],
        ["Middle", "sources/contract.txt"],
        ["Tail", "sources/contract.txt"],
    ]
    assert all("quote" not in section for section in spec_group["sections"])
    assert all(
        section["line_end"] - section["line_start"] + 1 <= 800
        for section in spec_group["sections"]
    )
    issues = validator.Issues()
    validator.validate_inventory(
        inventory, session_id, design,
        {group["document_key"]: group for group in groups}, issues,
    )
    assert issues.errors == []
    trace = ac.load_json(trace_path)
    assert trace["passed"] is True
    assert trace["artifact_kind"] == "auto-inventory"
    assert trace["semantic_analysis_performed"] is False


def test_auto_inventory_rejects_manifest_source_hash_drift(tmp_path):
    design = tmp_path / "design"
    state = tmp_path / "state"
    design.mkdir()
    state.mkdir()
    source = design / "contract.md"
    source.write_text("# Contract\nRequired behavior.\n", encoding="utf-8")
    manifest_path = state / "design_agent_manifest.json"
    output_path = state / "design_inventory.json"
    trace_path = state / "auto_inventory_trace.json"
    ac.save_json(manifest_path, {
        "session_id": "session-auto-inventory",
        "design": {
            "documents": [{"path": "contract.md", "sha256": "0" * 64}],
            "document_groups": [{
                "document_key": "contract", "members": ["contract.md"],
            }],
        },
    })

    assert materializer.main([
        "--materialize", "auto-inventory",
        "--design-root", str(design),
        "--input", str(manifest_path),
        "--output", str(output_path),
        "--trace", str(trace_path),
    ]) == 1
    assert not output_path.exists()
    trace = ac.load_json(trace_path)
    assert trace["passed"] is False
    assert any("sha256 does not match" in error for error in trace["errors"])


def test_group_digest_detects_post_materialization_change(artifacts):
    inventory = load_inventory(artifacts)
    inventory["document_groups"][0]["scope_relation"] = "informational"
    ac.save_json(Path(artifacts["state"]) / "design_inventory.json", inventory)
    code, trace = run_validator(artifacts, "inventory")
    assert code == 1
    assert trace["error_count_by_code"]["GROUP_DIGEST_MISMATCH"] == 1


def test_claim_source_hash_mismatch_is_grouped(artifacts):
    claims = load_claims(artifacts)
    claims[0]["source_ref"]["source_sha256"] = "0" * 64
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["SOURCE_HASH_MISMATCH"] == 1
    assert trace["error_groups"][0].keys() == {"code", "count", "samples"}


def test_claim_quote_must_be_exact_materialized_text(artifacts):
    claims = load_claims(artifacts)
    claims[0]["quote"] = "component must preserve"
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["QUOTE_RANGE_MISMATCH"] == 1


def test_repeated_structural_errors_are_aggregated_by_code(artifacts):
    claims = load_claims(artifacts)
    second = dict(claims[0])
    second["claim_id"] = "CLAIM-SECOND"
    second["source_ref"] = dict(second["source_ref"])
    for value in (claims[0], second):
        value["source_ref"]["source_sha256"] = "0" * 64
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", [claims[0], second])
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["SOURCE_HASH_MISMATCH"] == 2
    group = next(item for item in trace["error_groups"] if item["code"] == "SOURCE_HASH_MISMATCH")
    assert group["count"] == 2
    assert len(group["samples"]) == 2


def test_inventory_scope_relation_is_model_supplied_but_schema_checked(artifacts):
    inventory = load_inventory(artifacts)
    inventory["document_groups"][0]["scope_relation"] = "catalog_means_required"
    inventory["document_groups"][0]["group_sha256"] = validator.canonical_object_sha256(
        inventory["document_groups"][0], excluded={"group_sha256"},
    )
    ac.save_json(Path(artifacts["state"]) / "design_inventory.json", inventory)
    code, trace = run_validator(artifacts, "inventory")
    assert code == 1
    assert any("invalid scope_relation" in error for error in trace["errors"])


def test_claim_path_must_belong_to_an_inventory_document_group(artifacts):
    design = Path(artifacts["design"])
    (design / "orphan.md").write_text("# Orphan\nMust be ignored.\n", encoding="utf-8")
    claims = materializer.materialize_claims([
        claim_draft(
            str(artifacts["session_id"]), "CLAIM-ORPHAN", "orphan.md", 2, "orphan behavior",
        ),
    ], design)
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["CLAIM_GROUP_INVALID"] == 1


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("obligation", [], "obligation"),
        ("exceptions", "none", "exceptions must be an array"),
        ("normative_strength", ["mandatory"], "normative_strength must be a string"),
    ],
)
def test_claim_semantic_fields_remain_model_owned_and_typed(
    artifacts, field, bad_value, message,
):
    claims = load_claims(artifacts)
    claims[0][field] = bad_value
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert any(message in error for error in trace["errors"])


def test_probe_oracle_is_optional_but_checked_when_present(artifacts):
    claims = load_claims(artifacts)
    claims[0]["probe_oracle"] = {"testability": "candidate", "preconditions": []}
    write_jsonl(Path(artifacts["state"]) / "design_claims.jsonl", claims)
    code, trace = run_validator(artifacts, "claims")
    assert code == 1
    assert trace["error_count_by_code"]["CLAIM_SCHEMA_INVALID"] == 2
