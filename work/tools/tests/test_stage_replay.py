from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import stage_replay  # noqa: E402


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


@pytest.fixture
def replay_source(tmp_path: Path) -> dict[str, Path | str]:
    root = tmp_path / "source"
    state = root / "logs" / "state"
    trace = root / "logs" / "trace"
    result = root / "result"
    code = root / "code"
    design = root / "design"
    review_code = state / "review-inputs" / "code"
    review_design = state / "review-inputs" / "design"
    for path in (state, trace, result, code, design, review_code, review_design):
        path.mkdir(parents=True, exist_ok=True)
    (code / "service.py").write_text("def accept(value):\n    return True\n", encoding="utf-8")
    (design / "contract.md").write_text("The service must reject invalid values.\n", encoding="utf-8")
    (review_code / "service.py").write_text("def accept(value):\n    return True\n", encoding="utf-8")
    (review_design / "contract.md").write_text(
        "The service must reject invalid values.\n", encoding="utf-8",
    )
    session_id = "session-replay-test"
    paths = {
        "code_root": str(code.resolve()),
        "design_root": str(design.resolve()),
        "result_root": str(result.resolve()),
        "log_root": str((root / "logs").resolve()),
        "state_root": str(state.resolve()),
        "review_code_root": str(review_code.resolve()),
        "review_design_root": str(review_design.resolve()),
    }
    ac.save_json(state / "workspace_manifest.json", {
        "prepared_at": "2026-01-01T00:00:00Z",
        "session_id": session_id,
        "paths": paths,
        "design": {
            "document_count": 1,
            "document_group_count": 1,
            "documents": [],
            "document_groups": [{
                "document_key": "contract",
                "members": ["contract.md"],
            }],
            "source_manifest": None,
        },
        "code": {"files": []},
        "review_workspace": {},
        "preflight_problems": [],
    })
    ac.save_json(state / "design_agent_manifest.json", {
        "session_id": session_id,
        "prepared_at": "2026-01-01T00:00:00Z",
        "review_design_root": str(review_design.resolve()),
        "design": {
            "document_count": 1,
            "document_group_count": 1,
            "documents": [],
            "document_groups": [{
                "document_key": "contract", "members": ["contract.md"],
            }],
            "source_manifest": None,
        },
        "preflight_problems": [],
    })
    artifacts = {
        "architecture_map": str(state / "architecture_map.json"),
        "design_coverage": str(state / "design_coverage.json"),
        "design_claims": str(state / "design_claims.jsonl"),
        "investigation_tasks": str(state / "investigation_tasks.jsonl"),
        "investigation_findings": str(state / "investigation_findings.jsonl"),
        "critic_reviews": str(state / "critic_reviews.jsonl"),
        "verdicts": str(state / "agent_review_verdicts.jsonl"),
        "state": str(state / "agent_loop_state.json"),
    }
    ac.save_json(state / "agent_loop_contract.json", {
        "contract_version": 10,
        "execution_model": "opencode-owned-model-driven-loop",
        "session": {"session_id": session_id, "artifacts": artifacts},
        "coverage_contract": {
            "portfolio_lenses": ["input acceptance"],
            "exploration_modes": ["design-to-code obligation tracing"],
        },
        "guardrails": {"allowed_writes": [str(state), str(result), str(root / "logs")]},
    })
    ac.save_json(state / "agent_loop_state.json", {
        "session_id": session_id,
        "started_at": "2026-01-01T00:00:00Z",
        "status": "agent_loop",
        "artifacts": artifacts,
    })
    ac.save_json(state / "architecture_map.json", {
        "session_id": session_id,
        "repository_summary": "Small service.",
        "implementation_planes": [{
            "plane_id": "PLANE-SERVICE", "kind": "owned", "paths": ["service.py"],
            "reachable_evidence": "The public function executes directly.",
        }],
        "integration_boundaries": [{
            "boundary_id": "BOUNDARY-SERVICE", "name": "service API",
            "paths": ["service.py"], "risk": "high",
            "why": "The behavior is externally visible.",
        }],
        "parallel_behavior_paths": [],
    })
    claims = [
        {
            "claim_id": "CLAIM-1",
            "session_id": session_id,
            "document": "Contract",
            "path": "contract.md",
            "section": "contract",
            "line_start": 1,
            "line_end": 1,
            "quote": "The service must reject invalid values.",
            "behavior": "Reject invalid values.",
            "behavior_family": "input acceptance",
            "normative_strength": "mandatory",
            "applicability": "The supplied service contract is applicable.",
            "priority": "high",
            "ambiguities": [],
            "probe_oracle": {
                "testability": "candidate",
                "preconditions": ["The service is running."],
                "stimulus": "Submit an invalid value.",
                "expected_observation": "The value is rejected.",
            },
        },
        {
            "claim_id": "CLAIM-2",
            "session_id": session_id,
            "document": "Contract",
            "path": "contract.md",
            "section": "contract",
            "line_start": 1,
            "line_end": 1,
            "quote": "The service must reject invalid values.",
            "behavior": "Reject invalid values on the adapter path.",
            "behavior_family": "adapter acceptance",
            "normative_strength": "mandatory",
            "applicability": "The supplied service contract is applicable.",
            "priority": "medium",
            "ambiguities": [],
            "probe_oracle": {
                "testability": "candidate",
                "preconditions": ["The adapter is running."],
                "stimulus": "Submit an invalid value through the adapter.",
                "expected_observation": "The value is rejected.",
            },
        },
    ]
    _write_jsonl(state / "design_claims.jsonl", claims)
    ac.save_json(state / "design_coverage.json", {
        "session_id": session_id,
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "disposition": "applicable",
            "evidence": "The file declares the service contract.",
            "claim_ids": ["CLAIM-1", "CLAIM-2"],
            "behavior_families": ["input acceptance", "adapter acceptance"],
        }],
    })
    claim_reviews = []
    for claim in claims:
        claim_reviews.append({
            "session_id": session_id,
            "claim_id": claim["claim_id"],
            "quote_entailment": {
                "assessment": "entailed", "rationale": "The quote directly states the behavior.",
            },
            "normative_strength": {
                "assessment": "correct", "stated_strength": "mandatory",
                "recommended_strength": "mandatory",
                "rationale": "The design uses mandatory language.",
            },
            "atomicity": {
                "assessment": "atomic", "obligations": [claim["behavior"]],
                "rationale": "The claim contains one independently testable obligation.",
            },
            "applicability": {
                "assessment": "supported", "rationale": "The supplied contract is applicable.",
            },
            "decision": "accept", "repair_actions": [],
        })
    ac.save_json(state / "design_claim_review.json", {
        "session_id": session_id,
        "input_digests": {
            name: ac.sha256_file(state / name)
            for name in (
                "design_claims.jsonl", "design_coverage.json", "design_agent_manifest.json",
            )
        },
        "claim_reviews": claim_reviews,
        "group_reviews": [{
            "session_id": session_id,
            "document_key": "contract",
            "behavior_families": {
                "assessment": "complete", "missing_items": [],
                "rationale": "Both declared behavior families are represented.",
            },
            "roles": {
                "assessment": "complete", "missing_items": [],
                "rationale": "The contract defines one service role.",
            },
            "branches": {
                "assessment": "complete", "missing_items": [],
                "rationale": "No additional branch is declared.",
            },
            "decision": "accept", "repair_actions": [],
        }],
        "decision": "accept",
        "summary": "All frozen claims are faithful to the supplied contract.",
    })
    tasks = [
        {
            "task_id": "TASK-1", "session_id": session_id, "claim_id": "CLAIM-1",
            "question": "Does the direct path reject invalid values?", "status": "pending",
        },
        {
            "task_id": "TASK-2", "session_id": session_id, "claim_id": "CLAIM-2",
            "question": "Does the adapter path reject invalid values?", "status": "pending",
            "risk_observation_ids": ["OBS-2"],
        },
    ]
    _write_jsonl(state / "investigation_tasks.jsonl", tasks)
    _write_jsonl(state / "risk_observations.jsonl", [
        {"observation_id": "OBS-1", "session_id": session_id, "summary": "Direct path."},
        {"observation_id": "OBS-2", "session_id": session_id, "summary": "Adapter path."},
    ])
    findings = [
        {
            "finding_id": "FINDING-1", "session_id": session_id,
            "task_id": "TASK-1", "claim_id": "CLAIM-1", "assessment": "uncertain",
        },
        {
            "finding_id": "FINDING-2", "session_id": session_id,
            "task_id": "TASK-2", "claim_id": "CLAIM-2",
            "assessment": "contradiction_supported",
        },
    ]
    _write_jsonl(state / "investigation_findings.jsonl", findings)
    _write_jsonl(state / "dynamic_probes.jsonl", [
        {
            "probe_id": "PROBE-2", "session_id": session_id,
            "finding_id": "FINDING-2", "claim_id": "CLAIM-2",
        },
    ])
    _write_jsonl(state / "critic_reviews.jsonl", [
        {
            "review_id": "REVIEW-1", "session_id": session_id,
            "finding_id": "FINDING-1", "claim_id": "CLAIM-1",
            "decision": "needs_more_evidence",
        },
        {
            "review_id": "REVIEW-2", "session_id": session_id,
            "finding_id": "FINDING-2", "claim_id": "CLAIM-2",
            "decision": "confirm_contradiction",
        },
    ])
    _write_jsonl(state / "agent_review_verdicts.jsonl", [])
    _write_jsonl(state / "investigation_rounds.jsonl", [])
    _write_jsonl(state / "agent_run_ledger.jsonl", [])
    _write_jsonl(state / "approval_events.jsonl", [])
    ac.save_json(state / "coverage_audit.json", {"session_id": session_id})
    ac.save_json(state / "semantic_coverage.json", {"session_id": session_id})
    ac.save_json(state / "validated_issues.json", {
        "session_id": session_id, "issues": [], "confirmed": 0, "probable": 0,
    })
    ac.save_json(result / "issues.json", {
        "generated_at": "2026-01-01T00:00:00Z",
        "tool": "goal-agent-design-code-diff",
        "session_id": session_id,
        "code_root": str(code),
        "design_root": str(design),
        "summary": {"total": 0, "confirmed": 0, "probable": 0, "high_confidence": 0},
        "issues": [],
    })
    (result / "issues.jsonl").write_text("", encoding="utf-8")
    (result / "00-summary.md").write_text("# Empty replay fixture\n", encoding="utf-8")
    (state / "unrelated-evaluation-data.json").write_text('{"expected": true}\n', encoding="utf-8")
    return {"root": root, "state": state, "session_id": session_id}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_claims_prepare_writes_frozen_manifest_and_no_llm(replay_source, tmp_path):
    replay = tmp_path / "replay-claims"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay, stage="claims",
        provider="provider-a", model="model-b",
    )

    assert manifest["development_only"] is True
    assert manifest["llm_invoked"] is False
    assert manifest["runtime"] == {"provider": "provider-a", "model": "model-b"}
    assert manifest["schema"]["contract_version"] == 10
    assert len(manifest["source_digest"]) == 64
    assert len(manifest["replay_input_digest"]) == 64
    assert len(manifest["prompt"]["sha256"]) == 64
    assert len(manifest["skills"]["combined_sha256"]) == 64
    assert (replay / "prompt_envelope.json").is_file()
    assert not (replay / "state" / "architecture_map.json").exists()
    assert not (replay / "state" / "design_claims.jsonl").exists()
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert envelope["mode"] == "external_llm_required"
    assert envelope["inputs"] == ["state/design_agent_manifest.json"]
    assert envelope["read_only_source_roots"] == {
        "design": str(replay_source["state"] / "review-inputs" / "design")
    }
    assert not (replay / "prompt-assets" / "output_schema.json").exists()


def test_claim_review_and_risk_replays_enforce_opposite_source_boundaries(
    replay_source, tmp_path,
):
    claim_replay = tmp_path / "replay-claim-review"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=claim_replay,
        stage="claim-review",
    )
    claim_envelope = ac.load_json(claim_replay / "prompt_envelope.json")
    assert claim_envelope["read_only_source_roots"] == {
        "design": str(replay_source["state"] / "review-inputs" / "design")
    }
    assert (claim_replay / "state" / "design_claims.jsonl").is_file()
    assert not (claim_replay / "state" / "architecture_map.json").exists()
    assert not (claim_replay / "state" / "design_claim_review.json").exists()

    risk_replay = tmp_path / "replay-risk"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=risk_replay, stage="risk",
    )
    risk_envelope = ac.load_json(risk_replay / "prompt_envelope.json")
    assert risk_envelope["read_only_source_roots"] == {
        "code": str(replay_source["state"] / "review-inputs" / "code")
    }
    assert risk_envelope["inputs"] == [
        "state/agent_loop_contract.json", "state/architecture_map.json",
    ]
    assert risk_envelope["selection"] == {
        "architecture_boundaries": ["BOUNDARY-SERVICE"],
        "implementation_planes": ["PLANE-SERVICE"],
        "review_lenses": ["input acceptance"],
    }
    assert (risk_replay / "state" / "architecture_map.json").is_file()
    assert not (risk_replay / "state" / "design_claims.jsonl").exists()
    assert not (risk_replay / "state" / "design_coverage.json").exists()
    assert not (risk_replay / "prompt-assets" / "output_schema.json").exists()


def test_claim_review_local_validation_rebinds_frozen_input_digests(
    replay_source, tmp_path,
):
    replay = tmp_path / "replay-claim-review-local"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="claim-review", run_local=True,
    )

    assert stage_replay.run_local(replay) == 0
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "claim_review_validation"
    assert execution["returncode"] == 0
    validation = ac.load_json(replay / "logs" / "trace" / "claim_review_validation.json")
    assert validation["passed"] is True
    review = ac.load_json(replay / "state" / "design_claim_review.json")
    assert review["input_digests"] == {
        name: ac.sha256_file(replay / "state" / name)
        for name in (
            "design_claims.jsonl", "design_coverage.json", "design_agent_manifest.json",
        )
    }


def test_investigator_replay_copies_only_selected_task_context(replay_source, tmp_path):
    replay = tmp_path / "replay-investigator"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="investigator", item_id="TASK-2",
    )

    assert manifest["selection"] == {
        "task_id": "TASK-2", "claim_id": "CLAIM-2", "risk_observation_ids": ["OBS-2"],
    }
    assert [item["task_id"] for item in _jsonl(
        replay / "state" / "investigation_tasks.jsonl",
    )] == ["TASK-2"]
    assert [item["claim_id"] for item in _jsonl(
        replay / "state" / "design_claims.jsonl",
    )] == ["CLAIM-2"]
    assert [item["observation_id"] for item in _jsonl(
        replay / "state" / "risk_observations.jsonl",
    )] == ["OBS-2"]
    assert not (replay / "state" / "investigation_findings.jsonl").exists()
    assert not (replay / "state" / "unrelated-evaluation-data.json").exists()
    assert (replay / "state" / "handoff-templates" / "investigators" / "TASK-2.json").is_file()


@pytest.mark.parametrize(
    ("stage", "has_critic"), (("critic", False), ("judge", True)),
)
def test_finding_replays_slice_claim_task_finding_probe_and_critic(
    replay_source, tmp_path, stage, has_critic,
):
    replay = tmp_path / f"replay-{stage}"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage=stage, item_id="FINDING-2",
    )

    assert manifest["selection"] == {
        "finding_id": "FINDING-2", "task_id": "TASK-2", "claim_id": "CLAIM-2",
        "probe_ids": ["PROBE-2"],
    }
    assert [item["finding_id"] for item in _jsonl(
        replay / "state" / "investigation_findings.jsonl",
    )] == ["FINDING-2"]
    assert [item["probe_id"] for item in _jsonl(
        replay / "state" / "dynamic_probes.jsonl",
    )] == ["PROBE-2"]
    assert (replay / "state" / "critic_reviews.jsonl").exists() is has_critic
    envelope = ac.load_json(replay / "prompt_envelope.json")
    if stage == "judge":
        assert envelope["read_only_source_roots"] == {}


def test_claims_local_schema_replay_is_isolated(replay_source, tmp_path):
    source_claims = replay_source["state"] / "design_claims.jsonl"
    before = ac.sha256_file(source_claims)
    replay = tmp_path / "replay-schema"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="claims", run_local=True,
    )

    assert stage_replay.run_local(replay) == 0
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "design_schema_validation"
    assert execution["returncode"] == 0
    assert ac.load_json(replay / "logs" / "trace" / "design_validation.json")["passed"] is True
    assert ac.sha256_file(source_claims) == before


def test_gate_local_replay_uses_existing_deterministic_runner(
    replay_source, tmp_path, monkeypatch,
):
    replay = tmp_path / "replay-gate"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay, stage="gate",
    )
    replay_workspace = ac.load_json(replay / "state" / "workspace_manifest.json")
    assert replay_workspace["paths"]["review_code_root"] == str(
        (replay / "state" / "review-inputs" / "code").resolve()
    )
    assert (replay / "state" / "review-inputs" / "code" / "service.py").is_file()
    assert (replay / "state" / "review-inputs" / "design" / "contract.md").is_file()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="gate output", stderr="")

    monkeypatch.setattr(stage_replay.subprocess, "run", fake_run)
    assert stage_replay.run_local(replay) == 1
    assert Path(calls[0][1]).name == "goal_runner.py"
    assert calls[0][2] == "gate"
    assert manifest["llm_invoked"] is False
    assert manifest["schema"]["publication_schema_applies_to_stage"] is False
    assert not (replay / "prompt-assets" / "output_schema.json").exists()
    assert ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")["returncode"] == 1


def test_coverage_local_replay_uses_coverage_gate_only(
    replay_source, tmp_path, monkeypatch,
):
    replay = tmp_path / "replay-coverage"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="coverage", run_local=True,
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="coverage output", stderr="")

    monkeypatch.setattr(stage_replay.subprocess, "run", fake_run)
    assert stage_replay.run_local(replay) == 1
    assert Path(calls[0][1]).name == "goal_runner.py"
    assert calls[0][2] == "coverage-check"
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert envelope["read_only_source_roots"] == {}
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "coverage_validation"


def test_cli_rejects_ambiguous_or_reused_replay_roots(replay_source, tmp_path):
    replay = tmp_path / "occupied"
    replay.mkdir()
    (replay / "keep.txt").write_text("user data", encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "stage_replay.py"), "investigator",
            "--source-state", str(replay_source["state"]),
            "--replay-root", str(replay),
            "--item-id", "TASK-2",
        ],
        text=True, capture_output=True,
    )

    assert process.returncode == 2
    assert "not empty" in process.stderr
    assert (replay / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_force_replay_cannot_delete_or_nest_inside_protected_inputs(replay_source):
    protected = Path(replay_source["root"]) / "code" / "replay"
    protected.mkdir()
    marker = protected / "keep.txt"
    marker.write_text("source data", encoding="utf-8")

    with pytest.raises(stage_replay.ReplayError, match="disjoint"):
        stage_replay.prepare_replay(
            source_state=replay_source["state"], replay_root=protected,
            stage="claims", force=True,
        )
    assert marker.read_text(encoding="utf-8") == "source data"


def test_force_replay_cannot_delete_submission_tools_or_custom_prompt(
    replay_source, tmp_path,
):
    skill_before = (ROOT / "work" / "skill" / "SKILL.md").read_bytes()
    with pytest.raises(stage_replay.ReplayError, match="disjoint"):
        stage_replay.prepare_replay(
            source_state=replay_source["state"], replay_root=ROOT / "work",
            stage="claims", force=True,
        )
    assert (ROOT / "work" / "skill" / "SKILL.md").read_bytes() == skill_before

    prompt_root = tmp_path / "prompt-root"
    prompt_root.mkdir()
    prompt = prompt_root / "custom-prompt.txt"
    prompt.write_text("Use this frozen prompt.\n", encoding="utf-8")
    with pytest.raises(stage_replay.ReplayError, match="disjoint"):
        stage_replay.prepare_replay(
            source_state=replay_source["state"], replay_root=prompt_root,
            stage="claims", prompt_file=prompt, force=True,
        )
    assert prompt.read_text(encoding="utf-8") == "Use this frozen prompt.\n"
