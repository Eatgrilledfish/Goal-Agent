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
        "normative_assessment": {
            "claim_strength": "mandatory",
            "applicability": "supported",
            "obligation_status": "binding_required",
            "actual_conflict": "yes",
            "rationale": "The applicable mandatory behavior directly conflicts with the reachable implementation behavior.",
        },
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
        {"claim_id": "CLAIM-1", "session_id": session_id, "normative_strength": "mandatory"},
        {"claim_id": "CLAIM-2", "session_id": session_id, "normative_strength": "mandatory"},
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
    (state / "critic_review_history.jsonl").touch()
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
    assert merged == [{
        **critic,
        "input_digests": {
            "claim_sha256": hm.canonical_digest({
                "claim_id": "CLAIM-1", "session_id": critic_state["session_id"],
                "normative_strength": "mandatory",
            }),
            "finding_sha256": hm.canonical_digest({
                "finding_id": "FINDING-1", "claim_id": "CLAIM-1",
                "session_id": critic_state["session_id"],
            }),
            "probe_sha256": "",
        },
        "evidence_critic_prompt_version": hm.EVIDENCE_CRITIC_PROMPT_VERSION,
    }]


def test_critic_becomes_stale_when_finding_changes_and_fresh_merge_rebinds_it(
    critic_state,
):
    critic = _critic(str(critic_state["session_id"]))
    _merge(critic_state, critic)
    state = Path(critic_state["state"])
    findings, errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert errors == []
    findings[0]["new_evidence"] = "A newly discovered reachable path changes the evidence."
    _write_jsonl(state / "investigation_findings.jsonl", findings)
    retained = ac.load_jsonl(state / "critic_reviews.jsonl")[0][0]

    binding_errors = hm.validate_critic_bindings(
        retained, state, "critic (FINDING-1)",
    )
    assert any("input_digests do not match current" in error for error in binding_errors)

    _merge(critic_state, critic)
    rebound = ac.load_jsonl(state / "critic_reviews.jsonl")[0][0]
    assert rebound["input_digests"]["finding_sha256"] == hm.canonical_digest(
        findings[0]
    )


def test_critic_binding_includes_the_exact_reviewed_probe_snapshot(critic_state):
    critic = _critic(
        str(critic_state["session_id"]),
        probe_id="PROBE-1",
        probe_status="supports_contradiction",
    )
    _merge(critic_state, critic)
    state = Path(critic_state["state"])
    merged = ac.load_jsonl(state / "critic_reviews.jsonl")[0][0]
    probes, errors = ac.load_jsonl(state / "dynamic_probes.jsonl")
    assert errors == []
    probe = next(item for item in probes if item["probe_id"] == "PROBE-1")
    assert merged["input_digests"]["probe_sha256"] == hm.canonical_digest(probe)

    probe["new_observation"] = "The environment changed after critic review."
    _write_jsonl(state / "dynamic_probes.jsonl", probes)
    binding_errors = hm.validate_critic_bindings(
        merged, state, "critic (FINDING-1)",
    )
    assert any("input_digests do not match current" in error for error in binding_errors)


def test_critic_rejects_supplied_stale_tool_owned_bindings(critic_state):
    critic = _critic(str(critic_state["session_id"]))
    critic["input_digests"] = {
        "claim_sha256": "0" * 64,
        "finding_sha256": "1" * 64,
        "probe_sha256": "",
    }
    critic["evidence_critic_prompt_version"] = "evidence-critic-v1"

    errors = _errors(critic_state, critic)

    assert any("supplied input_digests do not match current evidence" in error for error in errors)
    assert any("prompt_version is stale or unsupported" in error for error in errors)


def test_same_evidence_cannot_be_recriticized_to_change_the_decision(critic_state):
    critic = _critic(str(critic_state["session_id"]))
    _merge(critic_state, critic)
    changed_vote = _critic(str(critic_state["session_id"]))
    changed_vote["review_id"] = "CRITIC-2"
    changed_vote["decision"] = "reject_issue"
    changed_vote["resolution"] = "The same evidence was reinterpreted without new facts."

    errors = _errors(critic_state, changed_vote)

    assert any(
        "current evidence snapshot was already reviewed" in error
        and "new claim/finding/probe evidence is required" in error
        for error in errors
    )


def test_critic_history_rejects_changed_vote_after_current_ledger_is_deleted(
    critic_state,
):
    critic = _critic(str(critic_state["session_id"]))
    _merge(critic_state, critic)
    (Path(critic_state["state"]) / "critic_reviews.jsonl").unlink()
    changed_vote = _critic(str(critic_state["session_id"]))
    changed_vote["review_id"] = "CRITIC-RECREATED"
    changed_vote["decision"] = "reject_issue"
    changed_vote["resolution"] = "Changed after deleting the current ledger."

    errors = _errors(critic_state, changed_vote)

    assert any("already reviewed in critic history" in error for error in errors)


def test_exact_critic_retry_is_idempotent_not_a_second_review(critic_state):
    critic = _critic(str(critic_state["session_id"]))
    _merge(critic_state, critic)

    result = _merge(critic_state, critic)

    assert result["imported"] == 0
    merged, errors = ac.load_jsonl(
        Path(critic_state["state"]) / "critic_reviews.jsonl"
    )
    assert errors == []
    assert len(merged) == 1


def test_stale_peer_critic_is_invalidated_without_blocking_current_candidate(
    critic_state,
):
    state = Path(critic_state["state"])
    handoffs = Path(critic_state["handoffs"])
    first = _critic(str(critic_state["session_id"]))
    second = _critic(
        str(critic_state["session_id"]),
        finding_id="FINDING-2",
        claim_id="CLAIM-2",
    )
    second["review_id"] = "CRITIC-2"
    ac.save_json(handoffs / "critic-1.json", first)
    ac.save_json(handoffs / "critic-2.json", second)
    hm.merge(
        handoffs,
        state / "critic_reviews.jsonl",
        "finding_id",
        artifact_type="critic",
        session_id=str(critic_state["session_id"]),
        context_root=state,
    )

    findings, errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert errors == []
    findings[0]["new_evidence"] = "Only the first candidate obtained new evidence."
    _write_jsonl(state / "investigation_findings.jsonl", findings)
    for path in handoffs.iterdir():
        path.unlink()
    ac.save_json(handoffs / "critic-2-retry.json", second)

    result = hm.merge(
        handoffs,
        state / "critic_reviews.jsonl",
        "finding_id",
        artifact_type="critic",
        session_id=str(critic_state["session_id"]),
        context_root=state,
    )

    assert result["invalidated_ids"] == ["FINDING-1"]
    assert result["imported"] == 0
    merged, errors = ac.load_jsonl(state / "critic_reviews.jsonl")
    assert errors == []
    assert [item["finding_id"] for item in merged] == ["FINDING-2"]


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("applicability", "unsupported"),
        ("applicability", "ambiguous"),
        ("actual_conflict", "no"),
        ("actual_conflict", "uncertain"),
        ("obligation_status", "optional_not_adopted"),
        ("obligation_status", "informational"),
    ],
)
def test_confirm_requires_supported_direct_binding_conflict(
    critic_state, field, value,
):
    critic = _critic(str(critic_state["session_id"]))
    critic["normative_assessment"][field] = value
    errors = _errors(critic_state, critic)
    assert any(
        "confirm_contradiction requires supported applicability" in error
        for error in errors
    )


def test_critic_normative_assessment_must_match_claim_strength(critic_state):
    critic = _critic(str(critic_state["session_id"]))
    critic["normative_assessment"]["claim_strength"] = "recommended"
    critic["normative_assessment"]["obligation_status"] = "binding_recommended"
    errors = _errors(critic_state, critic)
    assert any("claim_strength does not match" in error for error in errors)
    assert any("incompatible with claim strength" in error for error in errors)


def test_optional_claim_requires_adoption_to_confirm(critic_state):
    state = Path(critic_state["state"])
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert errors == []
    claims[0]["normative_strength"] = "optional"
    _write_jsonl(state / "design_claims.jsonl", claims)

    not_adopted = _critic(str(critic_state["session_id"]))
    not_adopted["normative_assessment"].update({
        "claim_strength": "optional",
        "obligation_status": "optional_not_adopted",
    })
    errors = _errors(critic_state, not_adopted)
    assert any("binding/adopted obligation" in error for error in errors)

    adopted = _critic(str(critic_state["session_id"]))
    adopted["normative_assessment"].update({
        "claim_strength": "optional",
        "obligation_status": "optional_adopted",
    })
    result = _merge(critic_state, adopted)
    assert result["validated_ids"] == ["FINDING-1"]


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
