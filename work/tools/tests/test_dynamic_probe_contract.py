from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import handoff_merge as hm  # noqa: E402


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8",
    )


def _claim(session_id: str) -> dict:
    return {
        "claim_id": "CLAIM-1",
        "session_id": session_id,
        "source_ref": {
            "path": "design.md",
            "line_start": 4,
            "line_end": 4,
            "source_sha256": "a" * 64,
        },
        "probe_oracle": {
            "preconditions": ["The supported public entrypoint is available."],
            "stimulus": "Invoke the entrypoint with the design-scoped input.",
            "expected_observation": "The documented behavior is observable.",
        },
    }


def _probe(state: Path, claim: dict, *, second_oracle: bool = True) -> dict:
    workspace = state / "probes" / "PROBE-1" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    secondary = {
        "kind": "negative_control" if second_oracle else "not_available",
        "status": "passed" if second_oracle else "not_run",
        "command": "python focused_probe.py --negative-control" if second_oracle else "",
        "result": (
            "The negative control rejected the deliberately invalid observation."
            if second_oracle else
            "No independent reference path exists in the supplied repository."
        ),
    }
    return {
        "probe_id": "PROBE-1",
        "session_id": claim["session_id"],
        "finding_id": "FINDING-1",
        "claim_id": claim["claim_id"],
        "oracle": {
            "source": "design_claim",
            "claim_id": claim["claim_id"],
            "claim_sha256": hm.canonical_digest(claim),
            "source_sha256": claim["source_ref"]["source_sha256"],
            "preconditions": claim["probe_oracle"]["preconditions"],
            "stimulus": claim["probe_oracle"]["stimulus"],
            "expected_observation": claim["probe_oracle"]["expected_observation"],
        },
        "oracle_validation": {
            "non_triviality": {
                "status": "passed",
                "method": "Run a deliberately satisfiable and deliberately failing input.",
                "result": "The probe distinguishes the two controls.",
            },
            "secondary_oracle": secondary,
            "evidence_role": "corroborating" if second_oracle else "auxiliary",
        },
        "selection_reason": "The candidate has a focused observable entrypoint.",
        "isolation": {
            "kind": "session_copy",
            "workspace": str(workspace),
            "command_cwd": str(workspace),
            "original_target_unchanged": True,
        },
        "baseline": {
            "status": "passed",
            "command": "python -m pytest -q tests/test_focused.py",
            "result": "The focused baseline passed.",
        },
        "execution": {
            "status": "completed",
            "command": "python focused_probe.py",
            "exit_code": 1,
            "observed": "The target path was reached and contradicted the oracle.",
            "target_reached": True,
        },
        "interpretation": "supports_contradiction",
        "limitations": [],
        "tool_trace": [
            {
                "seq": 1,
                "kind": "test",
                "tool": "shell",
                "target": "focused baseline",
                "purpose": "Establish the local baseline.",
                "result": "Baseline passed.",
            },
            {
                "seq": 2,
                "kind": "test",
                "tool": "shell",
                "target": "focused candidate path",
                "purpose": "Exercise the design-derived oracle and controls.",
                "result": "Captured the scoped observation and control result.",
            },
        ],
    }


@pytest.mark.parametrize("second_oracle", [True, False])
def test_probe_contract_accepts_dual_oracle_or_explicit_auxiliary_role(
    tmp_path: Path, second_oracle: bool,
) -> None:
    claim = _claim("session-probe")
    probe = _probe(tmp_path / "state", claim, second_oracle=second_oracle)
    assert hm.validate_probe_contract(probe, "probe") == []


def test_probe_contract_rejects_support_from_invalid_oracle_checks(tmp_path: Path) -> None:
    claim = _claim("session-probe")
    probe = _probe(tmp_path / "state", claim)
    probe["oracle_validation"]["non_triviality"]["status"] = "failed"
    errors = hm.validate_probe_contract(probe, "probe")
    assert any("must be inconclusive" in error for error in errors)
    assert any("must be auxiliary" in error for error in errors)

    probe = _probe(tmp_path / "state-2", claim)
    probe["oracle_validation"]["secondary_oracle"]["status"] = "failed"
    errors = hm.validate_probe_contract(probe, "probe")
    assert any("failed secondary oracle" in error for error in errors)


def test_probe_merge_binds_current_claim_source_and_session_workspace(tmp_path: Path) -> None:
    state = tmp_path / "state"
    handoffs = state / "handoffs" / "probes"
    handoffs.mkdir(parents=True)
    claim = _claim("session-probe")
    _write_jsonl(state / "design_claims.jsonl", [claim])
    _write_jsonl(state / "investigation_findings.jsonl", [{
        "finding_id": "FINDING-1",
        "claim_id": claim["claim_id"],
        "session_id": claim["session_id"],
    }])
    probe = _probe(state, claim)
    ac.save_json(handoffs / "probe.json", probe)
    merged = hm.merge(
        handoffs,
        state / "dynamic_probes.jsonl",
        "probe_id",
        artifact_type="probe",
        session_id=claim["session_id"],
        context_root=state,
    )
    assert merged["validated_ids"] == ["PROBE-1"]

    bad = deepcopy(probe)
    bad["oracle"]["source_sha256"] = "b" * 64
    ac.save_json(handoffs / "probe.json", bad)
    with pytest.raises(hm.HandoffValidationError) as raised:
        hm.merge(
            handoffs,
            state / "dynamic_probes.jsonl",
            "probe_id",
            artifact_type="probe",
            session_id=claim["session_id"],
            context_root=state,
        )
    assert any("source_sha256" in error for error in raised.value.errors)


def test_probe_merge_requires_a_real_non_symlink_workspace(tmp_path: Path) -> None:
    state = tmp_path / "state"
    handoffs = state / "handoffs" / "probes"
    handoffs.mkdir(parents=True)
    claim = _claim("session-probe")
    _write_jsonl(state / "design_claims.jsonl", [claim])
    _write_jsonl(state / "investigation_findings.jsonl", [{
        "finding_id": "FINDING-1",
        "claim_id": claim["claim_id"],
        "session_id": claim["session_id"],
    }])
    probe = _probe(state, claim)
    workspace = Path(probe["isolation"]["workspace"])
    workspace.rmdir()
    ac.save_json(handoffs / "probe.json", probe)

    with pytest.raises(hm.HandoffValidationError) as missing:
        hm.merge(
            handoffs,
            state / "dynamic_probes.jsonl",
            "probe_id",
            artifact_type="probe",
            session_id=claim["session_id"],
            context_root=state,
        )
    assert any("must exist as a non-symlink directory" in error for error in missing.value.errors)

    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.symlink_to(outside, target_is_directory=True)
    with pytest.raises(hm.HandoffValidationError) as symlinked:
        hm.merge(
            handoffs,
            state / "dynamic_probes.jsonl",
            "probe_id",
            artifact_type="probe",
            session_id=claim["session_id"],
            context_root=state,
        )
    assert any("path contains a symlink" in error for error in symlinked.value.errors)


def test_probe_chain_requires_exactly_one_selected_probe_and_critic_binding() -> None:
    findings = {
        "FINDING-1": {
            "finding_id": "FINDING-1",
            "dynamic_probe_selection": {"disposition": "selected", "reason": "focused"},
        }
    }
    critiques = {
        "FINDING-1": {
            "dynamic_probe_review": {
                "status": "supports_contradiction", "probe_id": "PROBE-1",
            }
        }
    }
    probe = {
        "probe_id": "PROBE-1", "finding_id": "FINDING-1",
        "interpretation": "supports_contradiction",
    }
    assert hm.validate_probe_chain(findings, {"PROBE-1": probe}, critiques) == []

    assert any(
        "requires exactly one" in error
        for error in hm.validate_probe_chain(findings, {}, critiques)
    )
    duplicate = {**probe, "probe_id": "PROBE-2"}
    assert any(
        "requires exactly one" in error
        for error in hm.validate_probe_chain(
            findings, {"PROBE-1": probe, "PROBE-2": duplicate}, critiques,
        )
    )


def test_probe_chain_rejects_orphan_probe_for_unselected_finding() -> None:
    findings = {
        "FINDING-1": {
            "finding_id": "FINDING-1",
            "dynamic_probe_selection": {"disposition": "not_selected", "reason": "static"},
        }
    }
    probes = {
        "PROBE-1": {
            "probe_id": "PROBE-1", "finding_id": "FINDING-1",
            "interpretation": "inconclusive",
        }
    }
    critiques = {
        "FINDING-1": {
            "dynamic_probe_review": {"status": "not_run", "probe_id": ""},
        }
    }
    errors = hm.validate_probe_chain(findings, probes, critiques)
    assert any("cannot have probe artifacts" in error for error in errors)
