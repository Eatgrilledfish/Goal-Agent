from __future__ import annotations

from copy import deepcopy
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
LENSES = ["normative behavior", "alternate execution path"]


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
        "required_coverage": {
            "boundary_ids": ["BOUNDARY-A", "BOUNDARY-B"],
            "plane_ids": ["PLANE-A", "PLANE-B"],
            "parallel_path_ids": ["PATH-A", "PATH-B"],
        },
        "slices": [
            {
                "sweep_id": "SWEEP-A",
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
    )


def _write_valid_state(tmp_path: Path) -> tuple[Path, dict]:
    state_root = tmp_path / "state"
    state_root.mkdir()
    architecture = _architecture()
    ac.save_json(state_root / validator.ARCHITECTURE_NAME, architecture)
    architecture_digest = ac.sha256_file(state_root / validator.ARCHITECTURE_NAME)
    plan = _valid_plan(architecture, architecture_digest)
    ac.save_json(state_root / validator.PLAN_NAME, plan)
    ac.save_json(state_root / validator.CONTRACT_NAME, _contract())
    ac.save_json(
        state_root / "agent_loop_state.json",
        {"session_id": SESSION_ID},
    )
    return state_root, plan


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
    return {
        "sweep_id": sweep,
        "risk_sweep_plan_sha256": ac.sha256_file(state_root / validator.PLAN_NAME),
        "architecture_boundaries": [boundary] if boundary else [],
        "implementation_planes": [plane] if plane else [],
        "parallel_path_ids": [path_id] if path_id else [],
        "review_lenses": [lens],
        "code_evidence": [{"file": code_file, "line_start": 1, "line_end": 1}],
    }


def test_accepts_exactly_two_independent_architecture_components() -> None:
    architecture = _architecture()
    errors, index = _validate(_valid_plan(architecture), architecture)

    assert errors == []
    assert set(index["slices"]) == {"SWEEP-A", "SWEEP-B"}
    assert len(index["components"]) == 2
    assert all(len(component) == 3 for component in index["components"])


def test_accepts_nonempty_valid_lens_subset_per_slice() -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    plan["slices"][0]["review_lenses"] = [LENSES[0]]
    plan["slices"][1]["review_lenses"] = [LENSES[1]]

    errors, index = _validate(plan, architecture)

    assert errors == []
    assert index["slices"]["SWEEP-A"]["review_lenses"] == [LENSES[0]]
    assert index["slices"]["SWEEP-B"]["review_lenses"] == [LENSES[1]]


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
            "architecture_boundaries": [boundary_id],
            "implementation_planes": [plane_id],
            "parallel_path_ids": [path_id],
            "anchor_paths": [code_path],
            "review_lenses": LENSES,
            "scope_rationale": f"Own focused component {suffix} ({lens}).",
        })

    errors, index = _validate(plan, architecture)

    assert errors == []
    assert set(index["slices"]) == {"SWEEP-A", "SWEEP-B", "SWEEP-C", "SWEEP-D"}


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
        plan["slices"], ["PLANE-A", "PLANE-B"], ["src/a.py", "src/b.py"],
    ):
        item["architecture_boundaries"] = []
        item["implementation_planes"] = [plane_id]
        item["parallel_path_ids"] = []
        item["anchor_paths"] = [path]

    errors, index = _validate(plan, architecture)

    assert errors == []
    assert index["required_planes"] == {"PLANE-A", "PLANE-B"}


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


def test_repository_root_scope_allows_one_true_connected_component() -> None:
    architecture = _architecture(distinct_boundary_paths=True)
    architecture["implementation_planes"][0]["paths"] = ["."]
    plan = _valid_plan(architecture)
    plan["slices"] = [{
        "sweep_id": "SWEEP-ALL",
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
    assert errors == []
    assert set(index["slices"]) == {"SWEEP-ALL"}


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
    plan["slices"] = [{
        "sweep_id": "SWEEP-SHARED",
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
    assert set(index["slices"]) == {"SWEEP-SHARED"}


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("overlap", "BOUNDARY-A overlaps sweep SWEEP-A"),
        ("missing", "boundary ownership must exactly cover required IDs"),
        ("unknown", "unknown IDs ['PLANE-UNKNOWN']"),
        ("derived_coverage", "must equal architecture-derived IDs"),
    ],
)
def test_rejects_overlap_missing_unknown_or_inexact_coverage(
    case: str, expected_error: str,
) -> None:
    architecture = _architecture()
    plan = _valid_plan(architecture)
    if case == "overlap":
        plan["slices"][1]["architecture_boundaries"].append("BOUNDARY-A")
    elif case == "missing":
        plan["slices"][0]["architecture_boundaries"] = []
    elif case == "unknown":
        plan["slices"][0]["implementation_planes"].append("PLANE-UNKNOWN")
    else:
        plan["required_coverage"]["plane_ids"] = ["PLANE-A", "PLANE-UNKNOWN"]

    errors, _index = _validate(plan, architecture)

    assert any(expected_error in error for error in errors), errors


def test_rejects_boundary_and_path_plane_components_split_across_sweeps() -> None:
    architecture = _architecture(distinct_boundary_paths=True)
    plan = _valid_plan(architecture)
    plan["slices"] = [
        {
            "sweep_id": "SWEEP-A",
            "architecture_boundaries": ["BOUNDARY-A"],
            "implementation_planes": ["PLANE-B"],
            "parallel_path_ids": ["PATH-B"],
            "anchor_paths": ["entry/a.py", "impl/b.py"],
            "review_lenses": [LENSES[0]],
            "scope_rationale": "Intentionally invalid cross-component assignment.",
        },
        {
            "sweep_id": "SWEEP-B",
            "architecture_boundaries": ["BOUNDARY-B"],
            "implementation_planes": ["PLANE-A"],
            "parallel_path_ids": ["PATH-A"],
            "anchor_paths": ["entry/b.py", "impl/a.py"],
            "review_lenses": [LENSES[1]],
            "scope_rationale": "Intentionally invalid cross-component assignment.",
        },
    ]

    errors, _index = _validate(plan, architecture)

    split_errors = [
        error for error in errors if "coupled risk component is split across sweeps" in error
    ]
    assert len(split_errors) == 2
    assert any("boundary:BOUNDARY-A" in error and "path:PATH-A" in error for error in split_errors)
    assert any("boundary:BOUNDARY-B" in error and "path:PATH-B" in error for error in split_errors)


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


def test_observation_rejects_out_of_slice_id_and_code_evidence(tmp_path: Path) -> None:
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

    assert any("architecture_boundaries escapes assigned sweep" in error for error in errors)
    assert any("code_evidence[1] is outside assigned primary paths" in error for error in errors)


def test_observation_cannot_use_another_local_file_for_owned_ids(tmp_path: Path) -> None:
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

    assert any("BOUNDARY-A lacks local code evidence" in error for error in errors)
    assert any("PLANE-A lacks local code evidence" in error for error in errors)


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
    errors, metrics = validator.validate_risk_coverage(risks, state_root)

    assert errors == []
    assert metrics["expected_sweeps"] == ["SWEEP-A", "SWEEP-B"]
    assert metrics["observed_sweeps"] == ["SWEEP-A", "SWEEP-B"]

    sparse = deepcopy(risks)
    del sparse["RISK-A-PLANE-PATH"]
    errors, sparse_metrics = validator.validate_risk_coverage(sparse, state_root)

    assert errors == []
    assert sparse_metrics["observed_sweeps"] == ["SWEEP-A", "SWEEP-B"]
    assert sparse_metrics["unobserved_paths"] == ["PATH-A"]
    assert sparse_metrics["unobserved_planes"] == []


def test_risk_coverage_still_requires_one_observation_per_planned_sweep(
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

    errors, _metrics = validator.validate_risk_coverage(risks, state_root)

    assert any("do not include completed sweeps: ['SWEEP-B']" in error for error in errors)
