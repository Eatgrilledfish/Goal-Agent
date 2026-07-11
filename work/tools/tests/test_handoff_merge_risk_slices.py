from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import handoff_merge as hm  # noqa: E402
import risk_sweep_plan_validator as rpv  # noqa: E402


SWEEP_IDS = {"SWEEP-CONTROL", "SWEEP-PARALLEL"}


def _risk(
    observation_id: str,
    session_id: str,
    sweep_id: str,
    *,
    boundary_ids: list[str] | None = None,
    plane_ids: list[str] | None = None,
    path_ids: list[str] | None = None,
) -> dict:
    return {
        "observation_id": observation_id,
        "session_id": session_id,
        "sweep_id": sweep_id,
        "risk_sweep_plan_sha256": "plan-sha256",
        "behavior_question": "Can the reachable entry point bypass the expected guard?",
        "observed_code_behavior": "The reachable entry point returns without a guard.",
        "review_lenses": ["externally visible behavior"],
        "architecture_boundaries": (
            ["BOUNDARY-API"] if boundary_ids is None else boundary_ids
        ),
        "implementation_planes": (
            ["PLANE-SERVICE"] if plane_ids is None else plane_ids
        ),
        "parallel_path_ids": (
            ["PATH-ALTERNATE"] if path_ids is None else path_ids
        ),
        "code_evidence": [{
            "file": "service.py",
            "line_start": 1,
            "line_end": 2,
            "symbol": "charge",
            "snippet": "def charge(amount):\n    return {'accepted': True}",
        }],
        "false_positive_checks": [
            {
                "question": "Is a guard called first?",
                "method": "control-flow read",
                "target": "charge",
                "result": "The function returns directly.",
            },
            {
                "question": "Does an alternate adapter enforce the guard?",
                "method": "reverse call-path search",
                "target": "charge adapters",
                "result": "No compensating adapter was found.",
            },
        ],
        "design_lookup_questions": [
            "Does the service contract require a guard for amount inputs?",
        ],
        "tool_trace": [
            {
                "seq": 1,
                "kind": "code_search",
                "tool": "search",
                "target": "charge",
                "purpose": "Locate the reachable entry point.",
                "result": "Found service.py:1.",
            },
            {
                "seq": 2,
                "kind": "code_read",
                "tool": "read",
                "target": "service.py:1-2",
                "purpose": "Read the reachable behavior.",
                "result": "The function returns directly.",
            },
            {
                "seq": 3,
                "kind": "reverse_check",
                "tool": "search",
                "target": "charge callers and adapters",
                "purpose": "Check for compensating paths.",
                "result": "No compensating path was found.",
            },
        ],
    }


@pytest.fixture
def risk_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state = tmp_path / "state"
    handoffs = state / "handoffs" / "risks"
    handoffs.mkdir(parents=True)
    session_id = "session-risk-slices"
    ac.save_json(state / "agent_loop_contract.json", {
        "coverage_contract": {
            "portfolio_lenses": ["externally visible behavior"],
            "exploration_modes": [],
        },
    })
    ac.save_json(state / "architecture_map.json", {
        "integration_boundaries": [{"boundary_id": "BOUNDARY-API"}],
        "implementation_planes": [{"plane_id": "PLANE-SERVICE"}],
        "parallel_behavior_paths": [{"path_id": "PATH-ALTERNATE"}],
    })

    def plan_errors(item: dict, context_root: Path, label: str) -> list[str]:
        assert context_root == state
        assert label == f"risk ({item.get('observation_id')})"
        if item.get("sweep_id") not in SWEEP_IDS:
            return [f"{label}: sweep_id is not declared by the risk sweep plan"]
        if item.get("risk_sweep_plan_sha256") != "plan-sha256":
            return [f"{label}: risk_sweep_plan_sha256 does not match the plan"]
        return []

    monkeypatch.setattr(hm, "_risk_plan_validation_errors", plan_errors)
    monkeypatch.setattr(hm, "_expected_risk_sweep_ids", lambda root: set(SWEEP_IDS))
    monkeypatch.setattr(rpv, "validate_risk_coverage", lambda risks, root: ([], {}))
    monkeypatch.setattr(rpv, "validate_sweep_coverage", lambda items, root, sweep: [])
    return {"state": state, "handoffs": handoffs, "session_id": session_id}


def _run_check(
    risk_state: dict[str, object], path: Path, report: Path,
) -> int:
    return hm.main([
        "--check-file", str(path),
        "--artifact-type", "risk",
        "--session-id", str(risk_state["session_id"]),
        "--report", str(report),
    ])


def _run_merge(
    risk_state: dict[str, object], report: Path,
) -> tuple[int, Path]:
    state = Path(risk_state["state"])
    output = state / "risk_observations.jsonl"
    result = hm.main([
        "--input-dir", str(risk_state["handoffs"]),
        "--output", str(output),
        "--artifact-type", "risk",
        "--session-id", str(risk_state["session_id"]),
        "--report", str(report),
    ])
    return result, output


@pytest.mark.parametrize("field", [
    "sweep_id", "risk_sweep_plan_sha256", "parallel_path_ids",
])
def test_risk_contract_requires_sweep_provenance_fields(field: str) -> None:
    item = _risk("RISK-1", "session", "SWEEP-CONTROL")
    item.pop(field)
    errors = hm.validate_artifact(item, "risk", "risk (RISK-1)")
    assert any(field in error for error in errors)


@pytest.mark.parametrize(
    "field", ["architecture_boundaries", "implementation_planes", "parallel_path_ids"],
)
def test_risk_scope_fields_must_be_arrays(field: str) -> None:
    item = _risk("RISK-1", "session", "SWEEP-CONTROL")
    item[field] = "not-an-array"
    errors = hm.validate_artifact(item, "risk", "risk (RISK-1)")
    assert any(f"{field} must be an array" in error for error in errors)


def test_risk_scope_arrays_must_have_at_least_one_combined_entry() -> None:
    item = _risk(
        "RISK-1", "session", "SWEEP-CONTROL",
        boundary_ids=[], plane_ids=[], path_ids=[],
    )
    errors = hm.validate_artifact(item, "risk", "risk (RISK-1)")
    assert any("must contain at least one entry in total" in error for error in errors)

    item["parallel_path_ids"] = ["PATH-ALTERNATE"]
    errors = hm.validate_artifact(item, "risk", "risk (RISK-1)")
    assert not any("must contain at least one entry in total" in error for error in errors)


def test_risk_check_accepts_multi_observation_slice_and_checks_each_context(
    risk_state: dict[str, object], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    handoffs = Path(risk_state["handoffs"])
    path = handoffs / "SWEEP-CONTROL.json"
    observations = [
        _risk("RISK-1", str(risk_state["session_id"]), "SWEEP-CONTROL"),
        _risk("RISK-2", str(risk_state["session_id"]), "SWEEP-CONTROL"),
    ]
    ac.save_json(path, observations)
    calls: list[str] = []

    def plan_errors(item: dict, state_root: Path, label: str) -> list[str]:
        assert state_root == risk_state["state"]
        calls.append(str(item["observation_id"]))
        return []

    monkeypatch.setattr(hm, "_risk_plan_validation_errors", plan_errors)
    report = tmp_path / "risk-check.json"
    assert _run_check(risk_state, path, report) == 0
    result = ac.load_json(report)
    assert result["passed"] is True
    assert result["validated_ids"] == ["RISK-1", "RISK-2"]
    assert result["validated_sweep_ids"] == ["SWEEP-CONTROL"]
    assert calls == ["RISK-1", "RISK-2"]


def test_risk_check_rejects_duplicate_observation_ids(
    risk_state: dict[str, object], tmp_path: Path,
) -> None:
    path = Path(risk_state["handoffs"]) / "SWEEP-CONTROL.json"
    ac.save_json(path, [
        _risk("RISK-DUP", str(risk_state["session_id"]), "SWEEP-CONTROL"),
        _risk("RISK-DUP", str(risk_state["session_id"]), "SWEEP-CONTROL"),
    ])
    report = tmp_path / "duplicate-check.json"
    assert _run_check(risk_state, path, report) == 1
    result = ac.load_json(report)
    assert result["invalid_ids"] == ["RISK-DUP"]
    assert any("duplicate observation_id" in error for error in result["errors"])


@pytest.mark.parametrize("failure", ["mixed-sweep", "wrong-filename"])
def test_risk_check_requires_one_sweep_and_matching_filename(
    risk_state: dict[str, object], tmp_path: Path, failure: str,
) -> None:
    path = Path(risk_state["handoffs"]) / (
        "wrong.json" if failure == "wrong-filename" else "SWEEP-CONTROL.json"
    )
    values = [_risk("RISK-1", str(risk_state["session_id"]), "SWEEP-CONTROL")]
    if failure == "mixed-sweep":
        values.append(
            _risk("RISK-2", str(risk_state["session_id"]), "SWEEP-PARALLEL")
        )
    ac.save_json(path, values)
    report = tmp_path / f"{failure}.json"
    assert _run_check(risk_state, path, report) == 1
    errors = ac.load_json(report)["errors"]
    assert any(
        ("exactly one shared sweep_id" if failure == "mixed-sweep" else "filename must be")
        in error
        for error in errors
    )


def test_risk_merge_requires_and_records_both_planned_sweeps(
    risk_state: dict[str, object], tmp_path: Path,
) -> None:
    handoffs = Path(risk_state["handoffs"])
    ac.save_json(handoffs / "SWEEP-CONTROL.json", [
        _risk("RISK-C1", str(risk_state["session_id"]), "SWEEP-CONTROL"),
        _risk("RISK-C2", str(risk_state["session_id"]), "SWEEP-CONTROL"),
    ])
    ac.save_json(handoffs / "SWEEP-PARALLEL.json", [
        _risk("RISK-P1", str(risk_state["session_id"]), "SWEEP-PARALLEL"),
    ])
    report = tmp_path / "risk-merge.json"
    return_code, output = _run_merge(risk_state, report)
    assert return_code == 0
    result = ac.load_json(report)
    assert result["passed"] is True
    assert result["validated_sweep_ids"] == sorted(SWEEP_IDS)
    merged, errors = ac.load_jsonl(output)
    assert errors == []
    assert {item["observation_id"] for item in merged} == {
        "RISK-C1", "RISK-C2", "RISK-P1",
    }


def test_risk_merge_accepts_all_planned_focused_sweeps(
    risk_state: dict[str, object], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    sweep_ids = ["SWEEP-A", "SWEEP-B", "SWEEP-C", "SWEEP-D"]
    monkeypatch.setattr(hm, "_expected_risk_sweep_ids", lambda root: set(sweep_ids))
    monkeypatch.setattr(
        hm, "_risk_plan_validation_errors", lambda item, root, label: [],
    )
    handoffs = Path(risk_state["handoffs"])
    for index, sweep_id in enumerate(sweep_ids, start=1):
        ac.save_json(handoffs / f"{sweep_id}.json", [
            _risk(f"RISK-{index}", str(risk_state["session_id"]), sweep_id),
        ])

    report = tmp_path / "multi-sweep-merge.json"
    return_code, output = _run_merge(risk_state, report)

    assert return_code == 0
    assert ac.load_json(report)["validated_sweep_ids"] == sweep_ids
    merged, errors = ac.load_jsonl(output)
    assert errors == []
    assert len(merged) == 4


def test_risk_merge_authoritatively_replaces_stale_ledger(
    risk_state: dict[str, object], tmp_path: Path,
) -> None:
    state = Path(risk_state["state"])
    output = state / "risk_observations.jsonl"
    ac.append_jsonl(output, _risk(
        "RISK-STALE", str(risk_state["session_id"]), "SWEEP-CONTROL",
    ))
    handoffs = Path(risk_state["handoffs"])
    ac.save_json(handoffs / "SWEEP-CONTROL.json", [
        _risk("RISK-CURRENT-C", str(risk_state["session_id"]), "SWEEP-CONTROL"),
    ])
    ac.save_json(handoffs / "SWEEP-PARALLEL.json", [
        _risk("RISK-CURRENT-P", str(risk_state["session_id"]), "SWEEP-PARALLEL"),
    ])

    return_code, merged_path = _run_merge(
        risk_state, tmp_path / "replace-report.json",
    )

    assert return_code == 0
    merged, errors = ac.load_jsonl(merged_path)
    assert errors == []
    assert {item["observation_id"] for item in merged} == {
        "RISK-CURRENT-C", "RISK-CURRENT-P",
    }


@pytest.mark.parametrize("extra", [False, True])
def test_risk_merge_rejects_missing_or_extra_sweep_files_without_writing_ledger(
    risk_state: dict[str, object], tmp_path: Path, extra: bool,
) -> None:
    handoffs = Path(risk_state["handoffs"])
    ac.save_json(handoffs / "SWEEP-CONTROL.json", [
        _risk("RISK-C1", str(risk_state["session_id"]), "SWEEP-CONTROL"),
    ])
    if extra:
        ac.save_json(handoffs / "EXTRA.json", [
            _risk("RISK-X1", str(risk_state["session_id"]), "EXTRA"),
        ])
    report = tmp_path / "incomplete-risk-merge.json"
    return_code, output = _run_merge(risk_state, report)
    assert return_code == 1
    assert not output.exists()
    result = ac.load_json(report)
    assert result["expected_sweep_ids"] == sorted(SWEEP_IDS)
    assert result["validated_sweep_ids"] == []
    assert any("exactly all planned sweep JSON files" in error for error in result["errors"])


def test_risk_merge_rejects_a_file_containing_another_sweep(
    risk_state: dict[str, object], tmp_path: Path,
) -> None:
    handoffs = Path(risk_state["handoffs"])
    ac.save_json(handoffs / "SWEEP-CONTROL.json", [
        _risk("RISK-WRONG", str(risk_state["session_id"]), "SWEEP-PARALLEL"),
    ])
    ac.save_json(handoffs / "SWEEP-PARALLEL.json", [
        _risk("RISK-P1", str(risk_state["session_id"]), "SWEEP-PARALLEL"),
    ])
    report = tmp_path / "wrong-owner.json"
    return_code, output = _run_merge(risk_state, report)
    assert return_code == 1
    assert not output.exists()
    assert any(
        "filename must be SWEEP-PARALLEL.json" in error
        for error in ac.load_json(report)["errors"]
    )


def test_non_risk_check_file_still_requires_exactly_one_object(tmp_path: Path) -> None:
    path = tmp_path / "generic.json"
    path.write_text(json.dumps([{"id": "A"}, {"id": "B"}]), encoding="utf-8")
    assert hm.main([
        "--check-file", str(path), "--artifact-type", "generic", "--key", "id",
    ]) == 1
