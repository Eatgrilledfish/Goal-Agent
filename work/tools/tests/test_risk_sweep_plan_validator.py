from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import risk_sweep_plan_validator as validator  # noqa: E402


SESSION_ID = "session-risk-plan-test"
ARCHITECTURE_DIGEST = "architecture-digest"
INVENTORY_DIGEST = "inventory-digest"
LENSES = ["normative behavior", "alternate execution path"]


def _inventory() -> dict:
    return {
        "session_id": SESSION_ID,
        "document_groups": [
            {
                "document_key": "design",
                "scope_relation": "required",
                "sections": [
                    {
                        "section_id": "SECTION-A", "path": "design.md",
                        "line_start": 1, "line_end": 10,
                    },
                    {
                        "section_id": "SECTION-B", "path": "design.md",
                        "line_start": 11, "line_end": 20,
                    },
                ],
            },
            {
                "document_key": "supporting",
                "scope_relation": "informational",
                "sections": [{
                    "section_id": "SECTION-SUPPORTING", "path": "supporting.md",
                    "line_start": 1, "line_end": 10,
                }],
            },
        ],
    }


def _architecture(*, distinct_boundary_paths: bool = False) -> dict:
    plane_paths = {
        "PLANE-A": "impl/a.py" if distinct_boundary_paths else "src/a.py",
        "PLANE-B": "impl/b.py" if distinct_boundary_paths else "src/b.py",
    }
    boundary_paths = {
        "BOUNDARY-A": "entry/a.py" if distinct_boundary_paths else "src/a.py",
        "BOUNDARY-B": "entry/b.py" if distinct_boundary_paths else "src/b.py",
    }
    return {
        "session_id": SESSION_ID,
        "implementation_planes": [
            {"plane_id": "PLANE-A", "paths": [plane_paths["PLANE-A"]]},
            {"plane_id": "PLANE-B", "paths": [plane_paths["PLANE-B"]]},
        ],
        "integration_boundaries": [
            {
                "boundary_id": "BOUNDARY-A",
                "risk": "high",
                "plane_ids": ["PLANE-A"],
                "paths": [boundary_paths["BOUNDARY-A"]],
            },
            {
                "boundary_id": "BOUNDARY-B",
                "risk": "high",
                "plane_ids": ["PLANE-B"],
                "paths": [boundary_paths["BOUNDARY-B"]],
            },
        ],
        "parallel_behavior_paths": [
            {"path_id": "PATH-A", "plane_ids": ["PLANE-A"]},
            {"path_id": "PATH-B", "plane_ids": ["PLANE-B"]},
        ],
        "test_surfaces": [],
    }


def _contract() -> dict:
    return {"coverage_contract": {"portfolio_lenses": LENSES}}


def _architecture_paths(
    architecture: dict, boundary_id: str, plane_id: str,
) -> list[str]:
    boundaries = {
        item["boundary_id"]: item for item in architecture["integration_boundaries"]
    }
    planes = {
        item["plane_id"]: item for item in architecture["implementation_planes"]
    }
    return sorted({
        *boundaries[boundary_id]["paths"],
        *planes[plane_id]["paths"],
    })


def _valid_plan(
    architecture: dict, architecture_digest: str = ARCHITECTURE_DIGEST,
) -> dict:
    return {
        "session_id": SESSION_ID,
        "plan_id": "RISK-PLAN-001",
        "architecture_map_sha256": architecture_digest,
        "design_inventory_sha256": INVENTORY_DIGEST,
        "required_coverage": {
            "boundary_ids": ["BOUNDARY-A", "BOUNDARY-B"],
            "plane_ids": ["PLANE-A", "PLANE-B"],
            "parallel_path_ids": ["PATH-A", "PATH-B"],
        },
        "slices": [
            {
                "sweep_id": "DESIGN-SCOUT",
                "direction": "design_to_code",
                "document_keys": ["design"],
                "section_ids": ["SECTION-A", "SECTION-B"],
                "architecture_boundaries": [],
                "implementation_planes": [],
                "parallel_path_ids": [],
                "anchor_paths": [],
                "review_lenses": LENSES,
                "scope_rationale": "Trace the required design group across the repository.",
            },
            {
                "sweep_id": "SWEEP-A",
                "direction": "code_to_design",
                "document_keys": [],
                "section_ids": [],
                "architecture_boundaries": ["BOUNDARY-A"],
                "implementation_planes": ["PLANE-A"],
                "parallel_path_ids": ["PATH-A"],
                "anchor_paths": _architecture_paths(
                    architecture, "BOUNDARY-A", "PLANE-A",
                ),
                "review_lenses": LENSES,
                "scope_rationale": "Own the independent A component.",
            },
            {
                "sweep_id": "SWEEP-B",
                "direction": "code_to_design",
                "document_keys": [],
                "section_ids": [],
                "architecture_boundaries": ["BOUNDARY-B"],
                "implementation_planes": ["PLANE-B"],
                "parallel_path_ids": ["PATH-B"],
                "anchor_paths": _architecture_paths(
                    architecture, "BOUNDARY-B", "PLANE-B",
                ),
                "review_lenses": LENSES,
                "scope_rationale": "Own the independent B component.",
            },
        ],
    }


def _validate(plan: dict, architecture: dict) -> tuple[list[str], dict]:
    return validator.validate_plan(
        plan,
        session_id=SESSION_ID,
        architecture=architecture,
        architecture_digest=ARCHITECTURE_DIGEST,
        contract=_contract(),
        inventory=_inventory(), inventory_digest=INVENTORY_DIGEST,
    )


def test_rejects_multi_document_design_slice() -> None:
    architecture = _architecture()
    inventory = _inventory()
    inventory["document_groups"].append({
        "document_key": "second-design",
        "scope_relation": "in_scope",
        "sections": [
            {
                "section_id": "SECTION-C", "path": "second.md",
                "line_start": 1, "line_end": 10,
            },
            {
                "section_id": "SECTION-D", "path": "second.md",
                "line_start": 11, "line_end": 20,
            },
        ],
    })
    plan = _valid_plan(architecture)
    plan["slices"][0]["document_keys"] = ["design", "second-design"]
    plan["slices"][0]["section_ids"] = [
        "SECTION-A", "SECTION-B", "SECTION-C", "SECTION-D",
    ]

    errors, index = validator.validate_plan(
        plan,
        session_id=SESSION_ID,
        architecture=architecture,
        architecture_digest=ARCHITECTURE_DIGEST,
        contract=_contract(),
        inventory=inventory,
        inventory_digest=INVENTORY_DIGEST,
    )

    assert any("at most 1 documents" in error for error in errors)
    assert index["covered_document_keys"] == {"design", "second-design"}


def test_accepts_one_large_document_split_across_bounded_design_slices() -> None:
    architecture = _architecture()
    inventory = _inventory()
    large_sections = [
        {
            "section_id": f"SECTION-LARGE-{index}",
            "path": "large.md",
            "line_start": index * 800 + 1,
            "line_end": (index + 1) * 800,
        }
        for index in range(5)
    ]
    inventory["document_groups"][0] = {
        "document_key": "large-design",
        "scope_relation": "required",
        "sections": large_sections,
    }
    plan = _valid_plan(architecture)
    plan["slices"][0]["document_keys"] = ["large-design"]
    plan["slices"][0]["section_ids"] = [large_sections[0]["section_id"]]
    for index, section in enumerate(large_sections[1:], start=1):
        plan["slices"].insert(index, {
            **plan["slices"][0],
            "sweep_id": f"DESIGN-SCOUT-{index}",
            "section_ids": [section["section_id"]],
        })

    errors, index = validator.validate_plan(
        plan,
        session_id=SESSION_ID,
        architecture=architecture,
        architecture_digest=ARCHITECTURE_DIGEST,
        contract=_contract(),
        inventory=inventory,
        inventory_digest=INVENTORY_DIGEST,
    )

    assert errors == []
    assert index["covered_document_keys"] == {"large-design"}
    assert index["covered_section_ids"] == {
        section["section_id"] for section in large_sections
    }


def test_accepts_broad_plane_shared_by_disjoint_partition_anchors(
    tmp_path: Path,
) -> None:
    review_root = tmp_path / "review-code"
    for child in ("left", "right"):
        target = review_root / "broad" / child
        target.mkdir(parents=True)
        (target / "runtime.c").write_text("int runtime;\n", encoding="utf-8")
    architecture = {
        "session_id": SESSION_ID,
        "implementation_planes": [{
            "plane_id": "PLANE-BROAD",
            "paths": ["broad"],
        }],
        "integration_boundaries": [{
            "boundary_id": "BOUNDARY-BROAD",
            "risk": "high",
            "plane_ids": ["PLANE-BROAD"],
            "paths": ["broad"],
        }],
        "parallel_behavior_paths": [],
        "test_surfaces": [],
    }
    plan = {
        "session_id": SESSION_ID,
        "plan_id": "RISK-PLAN-PARTITIONED",
        "architecture_map_sha256": ARCHITECTURE_DIGEST,
        "design_inventory_sha256": INVENTORY_DIGEST,
        "required_coverage": {
            "boundary_ids": ["BOUNDARY-BROAD"],
            "plane_ids": ["PLANE-BROAD"],
            "parallel_path_ids": [],
        },
        "slices": [
            {
                "sweep_id": "DESIGN-SCOUT",
                "direction": "design_to_code",
                "document_keys": ["design"],
                "section_ids": ["SECTION-A", "SECTION-B"],
                "architecture_boundaries": [],
                "implementation_planes": [],
                "parallel_path_ids": [],
                "anchor_paths": [],
                "review_lenses": LENSES,
                "scope_rationale": "Trace the complete design.",
            },
            {
                "sweep_id": "CODE-LEFT",
                "direction": "code_to_design",
                "document_keys": [],
                "section_ids": [],
                "architecture_boundaries": ["BOUNDARY-BROAD"],
                "implementation_planes": ["PLANE-BROAD"],
                "parallel_path_ids": [],
                "anchor_paths": ["broad/left"],
                "review_lenses": LENSES,
                "scope_rationale": "Review the left partition.",
            },
            {
                "sweep_id": "CODE-RIGHT",
                "direction": "code_to_design",
                "document_keys": [],
                "section_ids": [],
                "architecture_boundaries": [],
                "implementation_planes": ["PLANE-BROAD"],
                "parallel_path_ids": [],
                "anchor_paths": ["broad/right"],
                "review_lenses": LENSES,
                "scope_rationale": "Review the right partition.",
            },
        ],
    }

    errors, index = validator.validate_plan(
        plan,
        session_id=SESSION_ID,
        architecture=architecture,
        architecture_digest=ARCHITECTURE_DIGEST,
        contract=_contract(),
        inventory=_inventory(),
        inventory_digest=INVENTORY_DIGEST,
        review_code_root=review_root,
    )

    assert errors == []
    assert set(index["slices"]) == {"DESIGN-SCOUT", "CODE-LEFT", "CODE-RIGHT"}


def test_file_count_union_drops_redundant_nested_anchors() -> None:
    assert validator._minimal_anchor_scopes({
        "broad", "broad/left", "broad/left/runtime.c", "other/file.c",
    }) == ["broad", "other/file.c"]


def _write_valid_state(tmp_path: Path) -> tuple[Path, dict]:
    state_root = tmp_path / "state"
    state_root.mkdir()
    architecture = _architecture()
    ac.save_json(state_root / validator.ARCHITECTURE_NAME, architecture)
    architecture_digest = ac.sha256_file(state_root / validator.ARCHITECTURE_NAME)
    inventory = _inventory()
    ac.save_json(state_root / validator.INVENTORY_NAME, inventory)
    inventory_digest = ac.sha256_file(state_root / validator.INVENTORY_NAME)
    plan = _valid_plan(architecture, architecture_digest)
    plan["design_inventory_sha256"] = inventory_digest
    ac.save_json(state_root / validator.PLAN_NAME, plan)
    ac.save_json(state_root / validator.CONTRACT_NAME, _contract())
    ac.save_json(
        state_root / "agent_loop_state.json",
        {"session_id": SESSION_ID},
    )
    _write_receipts(state_root, plan)
    return state_root, plan


def _write_receipts(
    state_root: Path, plan: dict, sweep_ids: list[str] | None = None,
) -> None:
    selected = set(sweep_ids or [item["sweep_id"] for item in plan["slices"]])
    plan_digest = ac.sha256_file(state_root / validator.PLAN_NAME)
    directions = {
        item["sweep_id"]: item["direction"] for item in plan["slices"]
    }
    slices = {item["sweep_id"]: item for item in plan["slices"]}
    path = state_root / validator.SCOUT_RECEIPTS_NAME
    path.write_text("", encoding="utf-8")
    for sweep_id in sorted(selected):
        ac.append_jsonl(path, {
            "session_id": SESSION_ID,
            "sweep_id": sweep_id,
            "direction": directions[sweep_id],
            "risk_sweep_plan_sha256": plan_digest,
            "status": "complete",
            "candidate_count": 0,
            "candidate_ids": [],
            "coverage_report_sha256": "a" * 64,
            "negative_review_packet_sha256": "b" * 64,
            "negative_review_sha256": "c" * 64,
            "scout_provider_session_id": f"provider-scout-{sweep_id}",
            "reviewer_provider_session_ids": [f"provider-review-{sweep_id}"],
            "assigned_section_ids": list(slices[sweep_id].get("section_ids", [])),
            "reviewed_section_ids": list(slices[sweep_id].get("section_ids", [])),
            "assigned_anchor_paths": list(slices[sweep_id].get("anchor_paths", [])),
            "reviewed_anchor_paths": list(slices[sweep_id].get("anchor_paths", [])),
        })


def _sync_receipt_candidates(state_root: Path, risks: dict[str, dict]) -> None:
    receipts, errors = ac.load_jsonl(state_root / validator.SCOUT_RECEIPTS_NAME)
    assert errors == []
    by_sweep: dict[str, list[str]] = {}
    for observation_id, item in risks.items():
        by_sweep.setdefault(str(item["sweep_id"]), []).append(observation_id)
    for receipt in receipts:
        ids = sorted(by_sweep.get(str(receipt["sweep_id"]), []))
        receipt["candidate_ids"] = ids
        receipt["candidate_count"] = len(ids)
    (state_root / validator.SCOUT_RECEIPTS_NAME).write_text(
        "".join(json.dumps(item) + "\n" for item in receipts), encoding="utf-8",
    )


def _observation(
    state_root: Path,
    *,
    sweep: str,
    boundary: str,
    plane: str,
    path_id: str,
    code_file: str,
    lens: str,
) -> dict:
    section_id = "SECTION-A" if sweep == "SWEEP-A" else "SECTION-B"
    line = 1 if section_id == "SECTION-A" else 11
    return {
        "sweep_id": sweep,
        "direction": "code_to_design",
        "risk_sweep_plan_sha256": ac.sha256_file(state_root / validator.PLAN_NAME),
        "architecture_boundaries": [boundary] if boundary else [],
        "implementation_planes": [plane] if plane else [],
        "parallel_path_ids": [path_id] if path_id else [],
        "review_lenses": [lens],
        "design_section_ids": [section_id],
        "design_requirement": {
            "source_ref": {
                "path": "design.md", "line_start": line, "line_end": line,
            },
        },
        "code_evidence": [{"file": code_file, "line_start": 1, "line_end": 1}],
    }


def test_accepts_requirement_scout_plus_two_independent_code_components() -> None:
    architecture = _architecture()
    errors, index = _validate(_valid_plan(architecture), architecture)

    assert errors == []
    assert set(index["slices"]) == {"DESIGN-SCOUT", "SWEEP-A", "SWEEP-B"}
    assert len(index["components"]) == 2
    assert all(len(component) == 3 for component in index["components"])
    assert index["covered_document_keys"] == {"design"}


def test_design_to_code_owns_exact_contiguous_document_sections() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)

    errors, index = _validate(plan, architecture)

    assert errors == []
    assert index["required_document_keys"] == {"design"}
    assert index["covered_document_keys"] == {"design"}
    assert index["required_section_ids"] == {"SECTION-A", "SECTION-B"}
    assert index["covered_section_ids"] == {"SECTION-A", "SECTION-B"}
    assert plan["slices"][0]["section_ids"] == ["SECTION-A", "SECTION-B"]


def test_rejects_legacy_prebound_design_sections() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["slices"][0]["design_section_ids"] = ["SECTION-A"]

    errors, _index = _validate(plan, architecture)

    assert any("unsupported fields ['design_section_ids']" in error for error in errors)


def test_rejects_reordered_design_section_ownership() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["slices"][0]["section_ids"] = ["SECTION-B", "SECTION-A"]

    errors, _index = _validate(plan, architecture)

    assert any("contiguous document-local range" in error for error in errors)


def test_rejects_missing_or_duplicate_required_document_ownership() -> None:
    architecture = _architecture()
    missing_plan = _valid_plan(architecture)
    missing_plan["slices"][0]["document_keys"] = []
    missing_errors, _ = _validate(missing_plan, architecture)
    assert any("document coverage is incomplete" in error for error in missing_errors)

    duplicate_plan = _valid_plan(architecture)
    duplicate_plan["slices"].insert(1, {
        **duplicate_plan["slices"][0],
        "sweep_id": "DESIGN-SCOUT-DUPLICATE",
    })
    duplicate_errors, _ = _validate(duplicate_plan, architecture)
    assert any("already owned" in error for error in duplicate_errors)


def test_rejects_partial_lens_portfolio_in_any_slice() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["slices"][0]["review_lenses"] = [LENSES[0]]
    plan["slices"][1]["review_lenses"] = [LENSES[1]]

    errors, index = _validate(plan, architecture)

    assert len([
        error for error in errors
        if "review_lenses must equal the complete contract portfolio" in error
    ]) == 2
    assert set(index["slices"]) == {"DESIGN-SCOUT", "SWEEP-A", "SWEEP-B"}


@pytest.mark.parametrize("lenses", [[], ["unknown lens"]])
def test_rejects_empty_or_unknown_slice_lenses(lenses: list[str]) -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["slices"][0]["review_lenses"] = lenses

    errors, _index = _validate(plan, architecture)

    assert any("review_lenses" in error for error in errors)


def test_accepts_more_than_two_focused_slices() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    for suffix, lens in (("C", LENSES[0]), ("D", LENSES[1])):
        plane_id = f"PLANE-{suffix}"
        boundary_id = f"BOUNDARY-{suffix}"
        path_id = f"PATH-{suffix}"
        code_path = f"src/{suffix.lower()}.py"
        architecture["implementation_planes"].append({
            "plane_id": plane_id, "paths": [code_path],
        })
        architecture["integration_boundaries"].append({
            "boundary_id": boundary_id, "risk": "medium",
            "plane_ids": [plane_id], "paths": [code_path],
        })
        architecture["parallel_behavior_paths"].append({
            "path_id": path_id, "plane_ids": [plane_id],
        })
        plan["required_coverage"]["boundary_ids"].append(boundary_id)
        plan["required_coverage"]["plane_ids"].append(plane_id)
        plan["required_coverage"]["parallel_path_ids"].append(path_id)
        plan["slices"].append({
            "sweep_id": f"SWEEP-{suffix}",
            "direction": "code_to_design",
            "document_keys": [],
            "section_ids": [],
            "architecture_boundaries": [boundary_id],
            "implementation_planes": [plane_id],
            "parallel_path_ids": [path_id],
            "anchor_paths": [code_path],
            "review_lenses": LENSES,
            "scope_rationale": f"Own focused component {suffix} ({lens}).",
        })

    errors, index = _validate(plan, architecture)

    assert errors == []
    assert set(index["slices"]) == {
        "DESIGN-SCOUT", "SWEEP-A", "SWEEP-B", "SWEEP-C", "SWEEP-D",
    }


def test_plane_only_repository_partitions_all_reachable_planes() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    architecture["integration_boundaries"] = []
    architecture["parallel_behavior_paths"] = []
    plan["required_coverage"] = {
        "boundary_ids": [],
        "plane_ids": ["PLANE-A", "PLANE-B"],
        "parallel_path_ids": [],
    }
    for item, plane_id, path in zip(
        plan["slices"][1:], ["PLANE-A", "PLANE-B"], ["src/a.py", "src/b.py"],
    ):
        item["architecture_boundaries"] = []
        item["implementation_planes"] = [plane_id]
        item["parallel_path_ids"] = []
        item["anchor_paths"] = [path]

    errors, index = _validate(plan, architecture)

    assert errors == []
    assert index["required_planes"] == {"PLANE-A", "PLANE-B"}


def test_architecture_test_surface_metadata_does_not_delete_a_code_plane() -> None:
    architecture = _architecture()
    architecture["implementation_planes"].append({
        "plane_id": "PLANE-QA", "kind": "owned", "paths": ["qa/scenarios"],
    })
    architecture["test_surfaces"] = [{"path": "qa"}]
    plan = _valid_plan(architecture)

    errors, index = _validate(plan, architecture)

    assert "PLANE-QA" in index["required_planes"]
    assert any("PLANE-QA" in error for error in errors)


def test_rejects_stale_architecture_digest() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["architecture_map_sha256"] = "stale-digest"

    errors, _index = _validate(plan, architecture)

    assert any("architecture_map_sha256 is stale" in error for error in errors)


def test_rejects_unsafe_sweep_id() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["slices"][0]["sweep_id"] = "../../escape"

    errors, _index = _validate(plan, architecture)

    assert any("safe single filename component" in error for error in errors)


def test_rejects_repository_root_as_an_unfocused_anchor() -> None:
    architecture = _architecture(distinct_boundary_paths=True)
    architecture["implementation_planes"][0]["paths"] = ["."]
    plan = _valid_plan(architecture)
    plan["slices"] = [plan["slices"][0], {
        "sweep_id": "SWEEP-ALL",
        "direction": "code_to_design",
        "document_keys": [],
        "section_ids": [],
        "architecture_boundaries": ["BOUNDARY-A", "BOUNDARY-B"],
        "implementation_planes": ["PLANE-A", "PLANE-B"],
        "parallel_path_ids": ["PATH-A", "PATH-B"],
        "anchor_paths": [".", "entry/a.py", "entry/b.py", "impl/b.py"],
        "review_lenses": LENSES,
        "scope_rationale": "Own the single connected repository component.",
    }]

    errors, index = _validate(plan, architecture)

    assert validator._in_scope("src/child.py", ["."])
    assert len(index["components"]) == 1
    assert any("repository root is not a focused code scope" in error for error in errors)
    assert set(index["slices"]) == {"DESIGN-SCOUT", "SWEEP-ALL"}


def test_rejects_nonexistent_anchor_in_review_snapshot(tmp_path: Path) -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    review_root = tmp_path / "review-code"
    (review_root / "src").mkdir(parents=True)
    (review_root / "src" / "a.py").write_text("pass\n", encoding="utf-8")

    errors, _index = validator.validate_plan(
        plan,
        session_id=SESSION_ID,
        architecture=architecture,
        architecture_digest=ARCHITECTURE_DIGEST,
        contract=_contract(),
        review_code_root=review_root,
    )

    assert any(
        "scope path does not exist" in error and "src/b.py" in error
        for error in errors
    )


def test_accepts_one_slice_when_parallel_path_couples_all_risk_nodes() -> None:
    architecture = _architecture()
    architecture["parallel_behavior_paths"] = [{
        "path_id": "PATH-SHARED",
        "plane_ids": ["PLANE-A", "PLANE-B"],
    }]
    plan = _valid_plan(architecture)
    plan["required_coverage"]["parallel_path_ids"] = ["PATH-SHARED"]
    plan["slices"] = [plan["slices"][0], {
        "sweep_id": "SWEEP-SHARED",
        "direction": "code_to_design",
        "document_keys": [],
        "section_ids": [],
        "architecture_boundaries": ["BOUNDARY-A", "BOUNDARY-B"],
        "implementation_planes": ["PLANE-A", "PLANE-B"],
        "parallel_path_ids": ["PATH-SHARED"],
        "anchor_paths": ["src/a.py", "src/b.py"],
        "review_lenses": LENSES,
        "scope_rationale": "The shared parallel path makes this one coupled component.",
    }]

    errors, index = _validate(plan, architecture)

    assert len(index["components"]) == 1
    assert errors == []
    assert set(index["slices"]) == {"DESIGN-SCOUT", "SWEEP-SHARED"}


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("missing", "boundary coverage must include all required IDs"),
        ("unknown", "unknown IDs ['PLANE-UNKNOWN']"),
        ("derived_coverage", "must equal architecture-derived IDs"),
    ],
)
def test_rejects_missing_unknown_or_inexact_coverage(
    case: str, expected_error: str,
) -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    if case == "missing":
        plan["slices"][1]["architecture_boundaries"] = []
    elif case == "unknown":
        plan["slices"][0]["implementation_planes"].append("PLANE-UNKNOWN")
    else:
        plan["required_coverage"]["plane_ids"] = ["PLANE-A", "PLANE-UNKNOWN"]

    errors, _index = _validate(plan, architecture)

    assert any(expected_error in error for error in errors), errors


def test_accepts_connected_architecture_split_by_disjoint_primary_code_scope() -> None:
    architecture = _architecture(distinct_boundary_paths=True)
    plan = _valid_plan(architecture)
    architecture["parallel_behavior_paths"] = [{
        "path_id": "PATH-SHARED", "plane_ids": ["PLANE-A", "PLANE-B"],
    }]
    plan["required_coverage"]["parallel_path_ids"] = ["PATH-SHARED"]
    plan["slices"] = [
        plan["slices"][0],
        {
            "sweep_id": "SWEEP-A",
            "direction": "code_to_design",
            "document_keys": [],
            "section_ids": [],
            "architecture_boundaries": ["BOUNDARY-A"],
            "implementation_planes": ["PLANE-A"],
            "parallel_path_ids": ["PATH-SHARED"],
            "anchor_paths": ["entry/a.py", "impl/a.py"],
            "review_lenses": LENSES,
            "scope_rationale": "Inspect one disjoint pair of primary code paths.",
        },
        {
            "sweep_id": "SWEEP-B",
            "direction": "code_to_design",
            "document_keys": [],
            "section_ids": [],
            "architecture_boundaries": ["BOUNDARY-B"],
            "implementation_planes": ["PLANE-B"],
            "parallel_path_ids": ["PATH-SHARED"],
            "anchor_paths": ["entry/b.py", "impl/b.py"],
            "review_lenses": LENSES,
            "scope_rationale": "Inspect the other disjoint pair of primary code paths.",
        },
    ]

    errors, index = _validate(plan, architecture)

    assert errors == []
    assert set(index["slices"]) == {"DESIGN-SCOUT", "SWEEP-A", "SWEEP-B"}


def test_allows_architecture_ids_to_be_shared_across_disjoint_primary_scopes() -> None:
    architecture = _architecture()
    architecture["implementation_planes"][0]["paths"] = ["src"]
    architecture["integration_boundaries"][0]["paths"] = ["src"]
    plan = _valid_plan(architecture)
    plan["slices"] = [
        plan["slices"][0],
        {
            "sweep_id": "SWEEP-A",
            "direction": "code_to_design",
            "document_keys": [],
            "section_ids": [],
            "architecture_boundaries": ["BOUNDARY-A"],
            "implementation_planes": ["PLANE-A"],
            "parallel_path_ids": ["PATH-A"],
            "anchor_paths": ["src/a.py"],
            "review_lenses": LENSES,
            "scope_rationale": "Inspect the first disjoint primary path.",
        },
        {
            "sweep_id": "SWEEP-B",
            "direction": "code_to_design",
            "document_keys": [],
            "section_ids": [],
            "architecture_boundaries": ["BOUNDARY-A", "BOUNDARY-B"],
            "implementation_planes": ["PLANE-A", "PLANE-B"],
            "parallel_path_ids": ["PATH-A", "PATH-B"],
            "anchor_paths": ["src/b.py"],
            "review_lenses": LENSES,
            "scope_rationale": "Inspect the second disjoint primary path.",
        },
    ]

    errors, _index = _validate(plan, architecture)

    assert errors == []


def test_rejects_plan_that_does_not_assign_every_portfolio_lens() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    for item in plan["slices"]:
        item["review_lenses"] = [LENSES[0]]

    errors, _index = _validate(plan, architecture)

    assert any(
        "review_lenses must equal the complete contract portfolio" in error
        and LENSES[1] in error
        for error in errors
    )


def test_allows_more_than_six_planes_when_one_non_overlapping_code_scope_owns_them() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    for suffix in ("C", "D", "E", "F", "G", "H"):
        plane_id = f"PLANE-{suffix}"
        code_path = f"src/{suffix.lower()}.py"
        architecture["implementation_planes"].append({
            "plane_id": plane_id, "paths": [code_path],
        })
        plan["required_coverage"]["plane_ids"].append(plane_id)
        plan["slices"][1]["implementation_planes"].append(plane_id)
        plan["slices"][1]["anchor_paths"].append(code_path)

    errors, _index = _validate(plan, architecture)

    assert errors == []


def test_rejects_anchor_unrelated_to_assigned_architecture_paths() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["slices"][1]["anchor_paths"] = ["unrelated.py"]

    errors, _index = _validate(plan, architecture)

    assert any(
        "unrelated.py is unrelated to assigned architecture" in error
        for error in errors
    )


def test_allows_nested_anchor_paths_within_one_sweep() -> None:
    architecture = _architecture()
    architecture["implementation_planes"][0]["paths"] = ["lib"]
    architecture["integration_boundaries"][0]["paths"] = ["lib/foo.c"]
    plan = _valid_plan(architecture)

    errors, _index = _validate(plan, architecture)

    assert not any("anchor_paths" in error and "overlaps" in error for error in errors)


def test_rejects_nested_anchor_paths_across_sweeps() -> None:
    architecture = _architecture(distinct_boundary_paths=True)
    architecture["implementation_planes"][0]["paths"] = ["lib"]
    architecture["implementation_planes"][1]["paths"] = ["lib/foo.c"]
    plan = _valid_plan(architecture)

    errors, _index = _validate(plan, architecture)

    assert any(
        "anchor_paths" in error and "lib" in error and "lib/foo.c" in error
        for error in errors
    )


def test_observation_allows_architecture_refinement_but_rejects_code_scope_escape(
    tmp_path: Path,
) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    observation = _observation(
        state_root,
        sweep="SWEEP-A",
        boundary="BOUNDARY-B",
        plane="PLANE-A",
        path_id="PATH-A",
        code_file="src/b.py",
        lens=LENSES[0],
    )

    errors = validator.validate_observation_against_plan(
        observation, state_root, "risk (RISK-ESCAPE)",
    )

    assert not any("architecture_boundaries" in error for error in errors)
    assert any("code_evidence[1] is outside assigned primary paths" in error for error in errors)


def test_observation_code_evidence_must_stay_in_owned_anchor(tmp_path: Path) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    observation = _observation(
        state_root,
        sweep="SWEEP-A",
        boundary="BOUNDARY-A",
        plane="PLANE-A",
        path_id="PATH-A",
        code_file="src/b.py",
        lens=LENSES[0],
    )

    errors = validator.validate_observation_against_plan(
        observation, state_root, "risk (RISK-WRONG-LOCAL)",
    )

    assert any("code_evidence[1] is outside assigned primary paths" in error for error in errors)
    assert not any("BOUNDARY-A" in error or "PLANE-A" in error for error in errors)


def test_observation_rejects_unknown_sweep_id(tmp_path: Path) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    observation = _observation(
        state_root,
        sweep="SWEEP-UNKNOWN",
        boundary="BOUNDARY-A",
        plane="PLANE-A",
        path_id="PATH-A",
        code_file="src/a.py",
        lens=LENSES[0],
    )

    errors = validator.validate_observation_against_plan(
        observation, state_root, "risk (RISK-UNKNOWN)",
    )

    assert any("unknown sweep_id 'SWEEP-UNKNOWN'" in error for error in errors)


def test_risk_observations_may_be_sparse_within_each_completed_sweep(tmp_path: Path) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    risks = {
        "RISK-A-BOUNDARY": _observation(
            state_root,
            sweep="SWEEP-A",
            boundary="BOUNDARY-A",
            plane="PLANE-A",
            path_id="",
            code_file="src/a.py",
            lens=LENSES[1],
        ),
        "RISK-A-PLANE-PATH": _observation(
            state_root,
            sweep="SWEEP-A",
            boundary="",
            plane="PLANE-A",
            path_id="PATH-A",
            code_file="src/a.py",
            lens=LENSES[0],
        ),
        "RISK-B": _observation(
            state_root,
            sweep="SWEEP-B",
            boundary="BOUNDARY-B",
            plane="PLANE-B",
            path_id="PATH-B",
            code_file="src/b.py",
            lens=LENSES[1],
        ),
    }
    _sync_receipt_candidates(state_root, risks)
    errors, metrics = validator.validate_risk_coverage(risks, state_root)

    assert errors == []
    assert metrics["expected_sweeps"] == ["DESIGN-SCOUT", "SWEEP-A", "SWEEP-B"]
    assert metrics["completed_sweeps"] == ["DESIGN-SCOUT", "SWEEP-A", "SWEEP-B"]
    assert metrics["observed_sweeps"] == ["SWEEP-A", "SWEEP-B"]
    assert metrics["closed"] is True

    sparse = deepcopy(risks)
    del sparse["RISK-A-PLANE-PATH"]
    _sync_receipt_candidates(state_root, sparse)
    errors, sparse_metrics = validator.validate_risk_coverage(sparse, state_root)

    assert errors == []
    assert sparse_metrics["observed_sweeps"] == ["SWEEP-A", "SWEEP-B"]
    assert sparse_metrics["unobserved_paths"] == ["PATH-A"]
    assert sparse_metrics["unobserved_planes"] == []


def test_risk_coverage_requires_receipt_not_observation_per_planned_sweep(
    tmp_path: Path,
) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    risks = {
        "RISK-A": _observation(
            state_root,
            sweep="SWEEP-A",
            boundary="BOUNDARY-A",
            plane="PLANE-A",
            path_id="",
            code_file="src/a.py",
            lens=LENSES[0],
        ),
    }

    _write_receipts(state_root, _plan, ["DESIGN-SCOUT", "SWEEP-A"])
    _sync_receipt_candidates(state_root, risks)
    errors, metrics = validator.validate_risk_coverage(risks, state_root)

    assert any("receipts do not include completed sweeps: ['SWEEP-B']" in error for error in errors)
    assert metrics["missing_sweeps"] == ["SWEEP-B"]
    assert metrics["closed"] is False


def test_zero_candidates_are_closed_by_complete_receipts(tmp_path: Path) -> None:
    state_root, _plan = _write_valid_state(tmp_path)

    errors, metrics = validator.validate_risk_coverage({}, state_root)

    assert errors == []
    assert metrics["observed_sweeps"] == []
    assert metrics["completed_sweeps"] == ["DESIGN-SCOUT", "SWEEP-A", "SWEEP-B"]
    assert metrics["closed"] is True


def test_code_to_design_observation_rejects_informational_source(
    tmp_path: Path,
) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    observation = _observation(
        state_root,
        sweep="SWEEP-A",
        boundary="BOUNDARY-A",
        plane="PLANE-A",
        path_id="PATH-A",
        code_file="src/a.py",
        lens=LENSES[0],
    )
    observation["design_section_ids"] = ["SECTION-SUPPORTING"]
    observation["design_requirement"]["source_ref"] = {
        "path": "supporting.md", "line_start": 1, "line_end": 1,
    }

    errors = validator.validate_observation_against_plan(
        observation, state_root, "risk (RISK-DYNAMIC-DESIGN)",
    )

    assert any("candidate source is not required/in_scope" in error for error in errors)


def test_test_surface_metadata_is_not_a_semantic_candidate_gate(
    tmp_path: Path,
) -> None:
    state_root, plan = _write_valid_state(tmp_path)
    architecture = ac.load_json(state_root / validator.ARCHITECTURE_NAME)
    architecture["implementation_planes"][0]["paths"] = ["impl/a"]
    architecture["integration_boundaries"][0]["paths"] = ["impl/a"]
    architecture["test_surfaces"] = [{"path": "impl/a/test_case.py"}]
    ac.save_json(state_root / validator.ARCHITECTURE_NAME, architecture)
    plan["architecture_map_sha256"] = ac.sha256_file(
        state_root / validator.ARCHITECTURE_NAME
    )
    plan["slices"][1]["anchor_paths"] = ["impl/a"]
    ac.save_json(state_root / validator.PLAN_NAME, plan)
    _write_receipts(state_root, plan)
    observation = _observation(
        state_root,
        sweep="SWEEP-A", boundary="BOUNDARY-A", plane="PLANE-A",
        path_id="PATH-A", code_file="impl/a/test_case.py", lens=LENSES[0],
    )

    errors = validator.validate_observation_against_plan(
        observation, state_root, "risk (RISK-TEST-ONLY)",
    )

    assert errors == []


def test_design_to_code_observation_stays_with_assigned_document_group(
    tmp_path: Path,
) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    observation = {
        "sweep_id": "DESIGN-SCOUT",
        "direction": "design_to_code",
        "risk_sweep_plan_sha256": ac.sha256_file(state_root / validator.PLAN_NAME),
        "architecture_boundaries": [],
        "implementation_planes": [],
        "parallel_path_ids": [],
        "review_lenses": [LENSES[0]],
        "design_section_ids": ["SECTION-SUPPORTING"],
        "design_requirement": {"source_ref": {
            "path": "supporting.md", "line_start": 1, "line_end": 1,
        }},
        "code_evidence": [{"file": "src/b.py", "line_start": 1, "line_end": 1}],
    }

    errors = validator.validate_observation_against_plan(
        observation, state_root, "risk (RISK-WRONG-DOCUMENT)",
    )

    assert any("escape assigned section range" in error for error in errors)


def test_design_to_code_observation_may_cross_repository_code_scopes(
    tmp_path: Path,
) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    observation = {
        "sweep_id": "DESIGN-SCOUT",
        "direction": "design_to_code",
        "risk_sweep_plan_sha256": ac.sha256_file(state_root / validator.PLAN_NAME),
        "architecture_boundaries": ["BOUNDARY-B"],
        "implementation_planes": ["PLANE-B"],
        "parallel_path_ids": ["PATH-B"],
        "review_lenses": [LENSES[0]],
        "design_section_ids": ["SECTION-A"],
        "design_requirement": {"source_ref": {
            "path": "design.md", "line_start": 1, "line_end": 1,
        }},
        "code_evidence": [{"file": "src/b.py", "line_start": 1, "line_end": 1}],
    }

    errors = validator.validate_observation_against_plan(
        observation, state_root, "risk (RISK-CROSS-REPO)",
    )

    assert errors == []


def test_canonical_sweep_accepts_blind_review_expansion_beyond_raw_limit(
    tmp_path: Path,
) -> None:
    state_root, _plan = _write_valid_state(tmp_path)
    items = []
    for index in range(13):
        item = _observation(
            state_root,
            sweep="SWEEP-A",
            boundary="BOUNDARY-A",
            plane="PLANE-A",
            path_id="PATH-A",
            code_file="src/a.py",
            lens=LENSES[index % len(LENSES)],
        )
        item["observation_id"] = f"RISK-A-{index}"
        items.append(item)

    errors = validator.validate_sweep_coverage(items, state_root, "SWEEP-A")

    assert errors == []
