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


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in values), encoding="utf-8",
    )


def _critic(
    session_id: str, *, finding_id: str = "FINDING-1", claim_id: str = "CLAIM-1",
    probe_id: str = "", probe_status: str = "not_run",
) -> dict:
    return {
        "review_id": "CRITIC-1",
        "session_id": session_id,
        "finding_id": finding_id,
        "claim_id": claim_id,
        "decision": "confirm_contradiction",
        "challenges": [
            "Could an alternate reachable path enforce the behavior?",
            "Could build or runtime configuration disable the cited path?",
        ],
        "checks_performed": [
            "Read callers and the alternate adapter path.",
            "Checked build registration and runtime configuration.",
        ],
        "dynamic_probe_review": {
            "status": probe_status,
            "probe_id": probe_id,
            "oracle_validity": "The oracle is copied from the current design claim.",
            "environment_validity": "The recorded environment can explain the observation.",
            "reachability": "The target path is reachable from the reviewed entrypoint.",
            "effect_on_decision": "The probe supplements the static evidence.",
        },
        "review_context": "fresh_subagent",
        "resolution": "Both alternative explanations were checked against current artifacts.",
        "remaining_risks": [],
    }


@pytest.fixture
def critic_state(tmp_path: Path) -> dict[str, object]:
    state = tmp_path / "state"
    handoffs = state / "handoffs" / "critics"
    handoffs.mkdir(parents=True)
    session_id = "session-critic-contract"
    _write_jsonl(state / "design_claims.jsonl", [
        {"claim_id": "CLAIM-1", "session_id": session_id},
        {"claim_id": "CLAIM-2", "session_id": session_id},
    ])
    _write_jsonl(state / "investigation_findings.jsonl", [
        {
            "finding_id": "FINDING-1", "claim_id": "CLAIM-1",
            "session_id": session_id,
        },
        {
            "finding_id": "FINDING-2", "claim_id": "CLAIM-2",
            "session_id": session_id,
        },
    ])
    _write_jsonl(state / "dynamic_probes.jsonl", [
        {
            "probe_id": "PROBE-1", "finding_id": "FINDING-1",
            "claim_id": "CLAIM-1", "session_id": session_id,
        },
        {
            "probe_id": "PROBE-2", "finding_id": "FINDING-2",
            "claim_id": "CLAIM-2", "session_id": session_id,
        },
        {
            "probe_id": "PROBE-OLD", "finding_id": "FINDING-1",
            "claim_id": "CLAIM-1", "session_id": "session-old",
        },
    ])
    return {"state": state, "handoffs": handoffs, "session_id": session_id}


def _merge(values: dict[str, object], critic: dict) -> dict:
    state = Path(values["state"])
    handoffs = Path(values["handoffs"])
    ac.save_json(handoffs / "critic.json", critic)
    return hm.merge(
        handoffs,
        state / "critic_reviews.jsonl",
        "finding_id",
        artifact_type="critic",
        session_id=str(values["session_id"]),
        context_root=state,
    )


def _errors(values: dict[str, object], critic: dict) -> list[str]:
    with pytest.raises(hm.HandoffValidationError) as raised:
        _merge(values, critic)
    return raised.value.errors


def test_valid_critic_is_schema_complete_and_bound_to_current_artifacts(critic_state):
    critic = _critic(str(critic_state["session_id"]))
    result = _merge(critic_state, critic)
    assert result["validated_ids"] == ["FINDING-1"]
    merged, errors = ac.load_jsonl(Path(critic_state["state"]) / "critic_reviews.jsonl")
    assert errors == []
    assert merged == [critic]


def test_critic_rejects_verdict_and_issue_fields(critic_state):
    critic = _critic(str(critic_state["session_id"]))
    critic.update({
        "issue_id": "ISSUE-1",
        "status": "confirmed",
        "confidence": 0.99,
        "severity": "high",
        "title": "Final issue fields do not belong in a critic handoff",
        "design_evidence": [{"quote": "invented"}],
    })
    errors = _errors(critic_state, critic)
    assert any("unsupported fields" in error for error in errors)
    assert any("issue_id" in error and "status" in error for error in errors)


def test_dynamic_probe_review_rejects_nested_fields_outside_its_schema(critic_state):
    critic = _critic(str(critic_state["session_id"]))
    critic["dynamic_probe_review"]["verdict"] = "confirmed"
    critic["dynamic_probe_review"]["confidence"] = 1.0
    errors = _errors(critic_state, critic)
    assert any(
        "dynamic_probe_review has unsupported fields" in error
        and "verdict" in error and "confidence" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("challenges", ["Only one challenge."], "at least 2"),
        ("challenges", ["A real challenge.", "  "], "concrete non-empty string"),
        ("checks_performed", ["Same check.", "Same check."], "distinct concrete entries"),
    ],
)
def test_critic_requires_two_distinct_concrete_challenges_and_checks(
    critic_state, field, value, message,
):
    critic = _critic(str(critic_state["session_id"]))
    critic[field] = value
    errors = _errors(critic_state, critic)
    assert any(field in error and message in error for error in errors)


@pytest.mark.parametrize("remaining_risks", [None, "none", {}])
def test_remaining_risks_must_be_an_array(critic_state, remaining_risks):
    critic = _critic(str(critic_state["session_id"]))
    if remaining_risks is None:
        critic.pop("remaining_risks")
    else:
        critic["remaining_risks"] = remaining_risks
    errors = _errors(critic_state, critic)
    assert any("remaining_risks" in error for error in errors)


def test_critic_must_reference_current_finding_and_matching_claim(critic_state):
    critic = _critic(str(critic_state["session_id"]), claim_id="CLAIM-2")
    errors = _errors(critic_state, critic)
    assert any("finding/critic claim mismatch" in error for error in errors)

    critic = _critic(str(critic_state["session_id"]), finding_id="FINDING-UNKNOWN")
    errors = _errors(critic_state, critic)
    assert any("unknown finding_id 'FINDING-UNKNOWN'" in error for error in errors)

    critic = _critic(str(critic_state["session_id"]), claim_id="CLAIM-UNKNOWN")
    errors = _errors(critic_state, critic)
    assert any("unknown claim_id 'CLAIM-UNKNOWN'" in error for error in errors)


def test_nonempty_probe_id_must_bind_probe_finding_claim_and_session(critic_state):
    valid = _critic(
        str(critic_state["session_id"]),
        probe_id="PROBE-1",
        probe_status="supports_contradiction",
    )
    assert _merge(critic_state, valid)["validated_ids"] == ["FINDING-1"]

    output = Path(critic_state["state"]) / "critic_reviews.jsonl"
    output.unlink()
    mismatched = _critic(
        str(critic_state["session_id"]),
        probe_id="PROBE-2",
        probe_status="supports_contradiction",
    )
    errors = _errors(critic_state, mismatched)
    assert any("probe/finding mismatch" in error for error in errors)
    assert any("probe/claim mismatch" in error for error in errors)

    unknown = _critic(
        str(critic_state["session_id"]),
        probe_id="PROBE-UNKNOWN",
        probe_status="inconclusive",
    )
    errors = _errors(critic_state, unknown)
    assert any("unknown probe_id 'PROBE-UNKNOWN'" in error for error in errors)

    old_session = _critic(
        str(critic_state["session_id"]),
        probe_id="PROBE-OLD",
        probe_status="inconclusive",
    )
    errors = _errors(critic_state, old_session)
    assert any("probe belongs to a different session" in error for error in errors)


def test_not_run_probe_review_cannot_reference_a_probe(critic_state):
    critic = _critic(str(critic_state["session_id"]), probe_id="PROBE-1")
    errors = _errors(critic_state, critic)
    assert any("not_run dynamic probe review must not reference probe_id" in error for error in errors)
