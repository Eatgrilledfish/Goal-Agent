from __future__ import annotations

import argparse
import hashlib
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
import claim_review_validator  # noqa: E402
import design_source_materializer as materializer  # noqa: E402
import handoff_merge  # noqa: E402
import stage_replay  # noqa: E402


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    (code / "audit.py").write_text("def record(value):\n    return value\n", encoding="utf-8")
    (design / "contract.md").write_text(
        "# Contract\nThe service must reject invalid values.\n", encoding="utf-8",
    )
    (review_code / "service.py").write_text("def accept(value):\n    return True\n", encoding="utf-8")
    (review_code / "audit.py").write_text(
        "def record(value):\n    return value\n", encoding="utf-8",
    )
    (review_design / "contract.md").write_text(
        "# Contract\nThe service must reject invalid values.\n", encoding="utf-8",
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
        "design_inventory": str(state / "design_inventory.json"),
        "design_lookup_requests": str(state / "design_lookup_requests.jsonl"),
        "design_coverage": str(state / "design_coverage.json"),
        "design_claims": str(state / "design_claims.jsonl"),
        "investigation_tasks": str(state / "investigation_tasks.jsonl"),
        "investigation_findings": str(state / "investigation_findings.jsonl"),
        "critic_reviews": str(state / "critic_reviews.jsonl"),
        "verdicts": str(state / "agent_review_verdicts.jsonl"),
        "state": str(state / "agent_loop_state.json"),
    }
    ac.save_json(state / "agent_loop_contract.json", {
        "contract_version": 19,
        "execution_model": "opencode-owned-model-driven-loop",
        "session": {"session_id": session_id, "artifacts": artifacts},
        "coverage_contract": {
            "portfolio_lenses": ["input acceptance"],
            "exploration_modes": [
                "design-to-code obligation tracing",
                "code-to-design risk backtracking",
            ],
        },
        "iteration_policy": {"max_tasks_per_round": 2},
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
        "languages": ["Python"],
        "entrypoints": [{
            "path": "service.py", "purpose": "service API",
            "evidence": "The module exposes the accept function.",
        }],
        "subsystems": [{
            "subsystem_id": "SUBSYSTEM-SERVICE", "name": "service",
            "paths": ["service.py", "audit.py"], "role": "public behavior",
        }],
        "implementation_planes": [{
            "plane_id": "PLANE-SERVICE", "kind": "owned", "paths": ["service.py"],
            "reachable_evidence": "The public function executes directly.",
        }, {
            "plane_id": "PLANE-AUDIT", "kind": "owned", "paths": ["audit.py"],
            "reachable_evidence": "The audit function executes independently.",
        }],
        "integration_boundaries": [{
            "boundary_id": "BOUNDARY-SERVICE", "name": "service API",
            "paths": ["service.py"], "plane_ids": ["PLANE-SERVICE"], "risk": "high",
            "why": "The behavior is externally visible.",
        }, {
            "boundary_id": "BOUNDARY-AUDIT", "name": "audit API",
            "paths": ["audit.py"], "plane_ids": ["PLANE-AUDIT"], "risk": "high",
            "why": "The audit behavior is independently externally visible.",
        }],
        "capability_surfaces": [{
            "surface_id": "CAPABILITY-SERVICE", "paths": ["service.py"],
            "declares_or_registers": "The public accept function.",
        }],
        "configuration_surfaces": [],
        "alternate_execution_paths": [],
        "test_surfaces": [],
        "probe_capabilities": {
            "isolated_copy_feasible": True,
            "available_runtime": ["python"],
            "constraints": [],
        },
        "parallel_behavior_paths": [],
    })
    inventory = materializer.materialize_inventory({
        "session_id": session_id,
        "document_groups": [{
            "document_key": "contract", "members": ["contract.md"],
            "scope_relation": "required",
            "scope_evidence": {"source_ref": {
                "path": "contract.md", "line_start": 1, "line_end": 2,
            }},
            "sections": [{
                "section_id": "contract-main",
                "source_ref": {"path": "contract.md", "line_start": 1, "line_end": 2},
                "behavior_families": ["input acceptance", "adapter acceptance"],
                "ambiguities": [],
            }],
        }],
    }, design)
    ac.save_json(state / "design_inventory.json", inventory)
    ac.save_json(state / "risk_sweep_plan.json", {
        "session_id": session_id,
        "plan_id": "RISK-PLAN-001",
        "architecture_map_sha256": ac.sha256_file(state / "architecture_map.json"),
        "design_inventory_sha256": ac.sha256_file(state / "design_inventory.json"),
        "required_coverage": {
            "boundary_ids": ["BOUNDARY-SERVICE", "BOUNDARY-AUDIT"],
            "plane_ids": ["PLANE-SERVICE", "PLANE-AUDIT"],
            "parallel_path_ids": [],
        },
        "slices": [{
            "sweep_id": "RISK-SWEEP-SERVICE",
            "architecture_boundaries": ["BOUNDARY-SERVICE"],
            "implementation_planes": ["PLANE-SERVICE"],
            "parallel_path_ids": [],
            "anchor_paths": ["service.py"],
            "review_lenses": ["input acceptance"],
            "design_section_ids": ["contract-main"],
            "scope_rationale": "The service API is an independent execution component.",
        }, {
            "sweep_id": "RISK-SWEEP-AUDIT",
            "architecture_boundaries": ["BOUNDARY-AUDIT"],
            "implementation_planes": ["PLANE-AUDIT"],
            "parallel_path_ids": [],
            "anchor_paths": ["audit.py"],
            "review_lenses": ["input acceptance"],
            "design_section_ids": ["contract-main"],
            "scope_rationale": "The audit API is an independent execution component.",
        }],
    })
    risk_plan_digest = ac.sha256_file(state / "risk_sweep_plan.json")
    ac.save_json(trace / "risk_sweep_plan_validation.json", {
        "validated_at": "2026-01-01T00:00:00Z",
        "session_id": session_id,
        "passed": True,
        "input_digests": {
            "architecture_map.json": ac.sha256_file(state / "architecture_map.json"),
            "risk_sweep_plan.json": risk_plan_digest,
            "agent_loop_contract.json": ac.sha256_file(state / "agent_loop_contract.json"),
        },
        "validated_sweep_ids": ["RISK-SWEEP-AUDIT", "RISK-SWEEP-SERVICE"],
        "errors": [],
    })
    inventory = materializer.materialize_inventory({
        "session_id": session_id,
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "scope_relation": "required",
            "scope_evidence": {
                "source_ref": {
                    "path": "contract.md", "line_start": 1, "line_end": 2,
                },
            },
            "sections": [{
                "section_id": "contract-main",
                "source_ref": {
                    "path": "contract.md", "line_start": 1, "line_end": 2,
                },
                "behavior_families": ["input acceptance", "adapter acceptance"],
                "ambiguities": [],
            }],
        }],
    }, design)
    ac.save_json(state / "design_inventory.json", inventory)
    _write_jsonl(state / "design_lookup_requests.jsonl", [{
        "request_id": "LOOKUP-1", "session_id": session_id,
        "source": "risk_frontier", "question": "Which paths must reject invalid values?",
    }])
    claim_drafts = []
    for claim_id, subject, trigger, obligation, family, priority in (
        (
            "CLAIM-1", "The direct service path", "When it receives an invalid value",
            "Reject the invalid value.", "input acceptance", "high",
        ),
        (
            "CLAIM-2", "The adapter path", "When it receives an invalid value",
            "Reject the invalid value on the adapter path.", "adapter acceptance", "medium",
        ),
    ):
        claim_drafts.append({
            "claim_id": claim_id,
            "session_id": session_id,
            "document_key": "contract",
            "source_ref": {"path": "contract.md", "line_start": 2, "line_end": 2},
            "subject": subject,
            "trigger": trigger,
            "obligation": obligation,
            "exceptions": [],
            "observable_result": "The invalid value is rejected.",
            "behavior_family": family,
            "normative_strength": "mandatory",
            "applicability": "The supplied service contract is applicable.",
            "priority": priority,
            "ambiguities": [],
            "probe_oracle": {
                "testability": "candidate",
                "preconditions": ["The selected path is callable."],
                "stimulus": "Submit an invalid value.",
                "expected_observation": "The value is rejected.",
            },
        })
    claims = materializer.materialize_claims(claim_drafts, design)
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
    ac.save_json(state / "claim_review_scope.json", {
        "session_id": session_id,
        "round_id": "ROUND-001",
        "claim_ids": ["CLAIM-1", "CLAIM-2"],
    })
    claim_reviews = []
    for claim in claims:
        claim_reviews.append({
            "session_id": session_id,
            "claim_id": claim["claim_id"],
            "claim_sha256": _canonical_sha256(claim),
            "source_sha256": claim["source_ref"]["source_sha256"],
            "spec_critic_prompt_version": "spec-critic-v2",
            "quote_entailment": {
                "assessment": "entailed", "rationale": "The quote directly states the behavior.",
            },
            "normative_strength": {
                "assessment": "correct", "stated_strength": "mandatory",
                "recommended_strength": "mandatory",
                "rationale": "The design uses mandatory language.",
            },
            "atomicity": {
                "assessment": "atomic", "obligations": [claim["obligation"]],
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
                "design_claims.jsonl", "design_coverage.json", "design_inventory.json",
                "design_agent_manifest.json", "claim_review_scope.json",
            )
        },
        "claim_reviews": claim_reviews,
        "group_reviews": [{
            "session_id": session_id,
            "document_key": "contract",
            "group_sha256": inventory["document_groups"][0]["group_sha256"],
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
    review_args = argparse.Namespace(
        code_root=str(code), design_root=str(design), result_root=str(result),
        log_root=str(root / "logs"), state_root=str(state),
        design_entry=[], source_manifest=None,
    )
    assert claim_review_validator.run(review_args) == 0
    tasks = [
        {
            "task_id": "TASK-1", "session_id": session_id, "claim_id": "CLAIM-1",
            "claim_branch": ac.canonical_claim_branch(claims[0]),
            "hypothesis": ac.canonical_claim_hypothesis(claims[0]),
            "obligation_sha256": _canonical_sha256({
                "claim_id": "CLAIM-1", "obligation": claims[0]["obligation"],
            }),
            "starting_points": ["service.py:accept"],
            "supporting_evidence_needed": ["reachable acceptance behavior"],
            "disconfirming_evidence_needed": ["a rejecting guard"],
            "review_lenses": ["input acceptance"],
            "exploration_mode": "design-to-code obligation tracing",
            "architecture_boundaries": ["BOUNDARY-SERVICE"],
            "implementation_planes": ["PLANE-SERVICE"],
            "parallel_path_ids": [], "risk_observation_ids": [],
            "status": "pending", "defer_reason": "",
        },
        {
            "task_id": "TASK-2", "session_id": session_id, "claim_id": "CLAIM-2",
            "claim_branch": ac.canonical_claim_branch(claims[1]),
            "hypothesis": ac.canonical_claim_hypothesis(claims[1]),
            "obligation_sha256": _canonical_sha256({
                "claim_id": "CLAIM-2", "obligation": claims[1]["obligation"],
            }),
            "starting_points": ["audit.py:record"],
            "supporting_evidence_needed": ["reachable adapter behavior"],
            "disconfirming_evidence_needed": ["an adapter validation guard"],
            "review_lenses": ["input acceptance"],
            "exploration_mode": "code-to-design risk backtracking",
            "architecture_boundaries": ["BOUNDARY-SERVICE", "BOUNDARY-AUDIT"],
            "implementation_planes": ["PLANE-SERVICE", "PLANE-AUDIT"],
            "parallel_path_ids": [], "risk_observation_ids": ["OBS-1", "OBS-2"],
            "status": "pending", "defer_reason": "",
        },
    ]
    _write_jsonl(state / "investigation_tasks.jsonl", tasks)
    _write_jsonl(state / "risk_observations.jsonl", [
        {
            "observation_id": "OBS-1", "session_id": session_id,
            "sweep_id": "RISK-SWEEP-SERVICE",
            "risk_sweep_plan_sha256": risk_plan_digest,
            "behavior_question": "What does the public path do with invalid values?",
            "observed_code_behavior": "The public path returns true without a guard.",
            "design_section_ids": ["contract-main"],
            "design_alignment": "The section defines invalid-input behavior for this public path.",
            "review_lenses": ["input acceptance"],
            "architecture_boundaries": ["BOUNDARY-SERVICE"],
            "implementation_planes": ["PLANE-SERVICE"],
            "parallel_path_ids": [],
            "code_evidence": [{
                "file": "service.py", "line_start": 1, "line_end": 2,
                "symbol": "accept", "snippet": "def accept(value):\n    return True",
            }],
            "false_positive_checks": [{
                "question": "Is a guard called first?", "method": "control-flow read",
                "target": "accept", "result": "The function returns directly.",
            }, {
                "question": "Is another public path authoritative?", "method": "symbol search",
                "target": "service.py", "result": "No alternate public path exists.",
            }],
            "design_lookup_questions": ["Must the public path reject invalid values?"],
            "tool_trace": [
                {
                    "seq": 1, "kind": "design_read", "tool": "read",
                    "target": "contract.md:1-2", "purpose": "Read the assigned section.",
                    "result": "The section defines invalid-input behavior.",
                },
                {
                    "seq": 2, "kind": "code_search", "tool": "search",
                    "target": "accept", "purpose": "Locate the public path.",
                    "result": "Found service.py:1.",
                },
                {
                    "seq": 3, "kind": "code_read", "tool": "read",
                    "target": "service.py:1-2", "purpose": "Read reachable behavior.",
                    "result": "It returns true directly.",
                },
                {
                    "seq": 4, "kind": "reverse_check", "tool": "search",
                    "target": "validation callers", "purpose": "Find compensating guards.",
                    "result": "No guard is present.",
                },
            ],
        },
        {
            "observation_id": "OBS-2", "session_id": session_id,
            "sweep_id": "RISK-SWEEP-AUDIT",
            "risk_sweep_plan_sha256": risk_plan_digest,
            "behavior_question": "What does the adapter do with invalid values?",
            "observed_code_behavior": "The adapter returns its input without a guard.",
            "design_section_ids": ["contract-main"],
            "design_alignment": "The section defines invalid-input behavior for this adapter path.",
            "review_lenses": ["input acceptance"],
            "architecture_boundaries": ["BOUNDARY-AUDIT"],
            "implementation_planes": ["PLANE-AUDIT"],
            "parallel_path_ids": [],
            "code_evidence": [{
                "file": "audit.py", "line_start": 1, "line_end": 2,
                "symbol": "record", "snippet": "def record(value):\n    return value",
            }],
            "false_positive_checks": [{
                "question": "Is a guard called first?", "method": "control-flow read",
                "target": "record", "result": "The adapter returns directly.",
            }, {
                "question": "Is the adapter unreachable?", "method": "entrypoint read",
                "target": "audit.py", "result": "The adapter is directly callable.",
            }],
            "design_lookup_questions": ["Must the adapter reject invalid values?"],
            "tool_trace": [
                {
                    "seq": 1, "kind": "design_read", "tool": "read",
                    "target": "contract.md:1-2", "purpose": "Read the assigned section.",
                    "result": "The section defines invalid-input behavior.",
                },
                {
                    "seq": 2, "kind": "code_search", "tool": "search",
                    "target": "record", "purpose": "Locate the adapter path.",
                    "result": "Found audit.py:1.",
                },
                {
                    "seq": 3, "kind": "code_read", "tool": "read",
                    "target": "audit.py:1-2", "purpose": "Read reachable behavior.",
                    "result": "It returns the value directly.",
                },
                {
                    "seq": 4, "kind": "reverse_check", "tool": "search",
                    "target": "adapter guards", "purpose": "Find compensating guards.",
                    "result": "No guard is present.",
                },
            ],
        },
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
    _write_jsonl(state / "critic_review_history.jsonl", [])
    _write_jsonl(state / "agent_review_verdicts.jsonl", [])
    _write_jsonl(state / "investigation_rounds.jsonl", [{
        "round_id": "ROUND-001", "session_id": session_id,
        "strategy": "Investigate the two atomic candidate branches.",
        "exploration_modes": [
            "design-to-code obligation tracing", "code-to-design risk backtracking",
        ],
        "document_groups": ["contract"],
        "architecture_boundaries": ["BOUNDARY-SERVICE", "BOUNDARY-AUDIT"],
        "implementation_planes": ["PLANE-SERVICE", "PLANE-AUDIT"],
        "lenses": ["input acceptance"],
        "claim_ids": ["CLAIM-1", "CLAIM-2"],
        "task_ids": ["TASK-1", "TASK-2"],
        "finding_ids": [],
        "outcome": "pending",
        "next_strategy": "Drain this frontier before opening another round.",
    }])
    _write_jsonl(state / "agent_run_ledger.jsonl", [])
    _write_jsonl(state / "approval_events.jsonl", [])
    session_prepared = trace / "session_prepared.json"
    ac.save_json(session_prepared, {
        "ok": True, "session_id": session_id, "prepared_at": "2026-01-01T00:00:00Z",
    })
    ac.append_jsonl(state / "agent_run_ledger.jsonl", {
        "recorded_at": "2026-01-01T00:00:00Z",
        "session_id": session_id,
        "event": "deterministic_helper_trace",
        "actor": "goal_runner",
        "phase": "prepare",
        "status": "complete",
        "helper": "workspace_inventory.py",
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:00:00Z",
        "returncode": 0,
        "report": str(session_prepared.resolve()),
        "report_sha256": ac.sha256_file(session_prepared),
    })
    ac.save_json(state / "coverage_audit.json", {"session_id": session_id})
    ac.save_json(state / "semantic_coverage.json", {"session_id": session_id})
    ac.save_json(state / "coverage_supplement_history.json", {
        "session_id": session_id, "requests": [],
    })
    ac.save_json(state / "validated_issues.json", {
        "session_id": session_id, "issues": [], "confirmed": 0, "probable": 0,
    })
    ac.save_json(trace / "task_plan_validation.json", {
        "stage": "task-plan", "session_id": session_id, "passed": True,
        "global_passed": True, "valid_task_ids": ["TASK-1", "TASK-2"],
        "invalid_task_ids": [], "errors_by_task": {}, "candidate_digests": {},
        "task_plan_sha256": "1" * 64, "errors": [],
    })
    ac.save_json(trace / "task_lifecycle_validation.json", {
        "stage": "task-lifecycle", "session_id": session_id, "passed": True,
        "global_passed": True, "valid_task_ids": ["TASK-1", "TASK-2"],
        "invalid_task_ids": [], "errors_by_task": {}, "candidate_digests": {},
        "task_lifecycle_sha256": "2" * 64, "errors": [],
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


def _prepare_valid_coverage_artifacts(replay_source: dict[str, Path | str]) -> None:
    """Upgrade the broad replay fixture into a validator-clean closed frontier."""
    state = Path(replay_source["state"])
    session_id = str(replay_source["session_id"])
    claims = _jsonl(state / "design_claims.jsonl")
    claims_by_id = {claim["claim_id"]: claim for claim in claims}
    tasks = _jsonl(state / "investigation_tasks.jsonl")
    for task in tasks:
        task["status"] = "complete"
        task["defer_reason"] = ""
    _write_jsonl(state / "investigation_tasks.jsonl", tasks)

    finding_specs = (
        (
            "TASK-1", "FINDING-1", "CLAIM-1", "service.py", "accept",
            "def accept(value):\n    return True",
            "The public service returns success without rejecting the invalid value.",
        ),
        (
            "TASK-2", "FINDING-2", "CLAIM-2", "audit.py", "record",
            "def record(value):\n    return value",
            "The adapter returns the invalid value without rejecting it.",
        ),
    )
    tasks_by_id = {task["task_id"]: task for task in tasks}
    findings: list[dict] = []
    for task_id, finding_id, claim_id, code_file, symbol, snippet, observed in finding_specs:
        task = tasks_by_id[task_id]
        claim = claims_by_id[claim_id]
        findings.append({
            "finding_id": finding_id,
            "session_id": session_id,
            "task_id": task_id,
            "claim_id": claim_id,
            "claim_branch": task["claim_branch"],
            "obligation_sha256": task["obligation_sha256"],
            "hypothesis": task["hypothesis"],
            "expected_behavior": claim["observable_result"],
            "observed_behavior": observed,
            "design_evidence": [{
                "file": "contract.md", "line_start": 2, "line_end": 2,
                "section": "Contract",
                "quote": "The service must reject invalid values.",
            }],
            "code_evidence": [{
                "file": code_file, "line_start": 1, "line_end": 2,
                "symbol": symbol, "snippet": snippet,
            }],
            "supporting_evidence": [
                "The selected entry point is directly reachable.",
                "No compensating validation branch exists in the function.",
            ],
            "false_positive_checks": [{
                "question": "Is a guard called before the return?",
                "method": "control-flow read", "target": symbol,
                "result": "The function reaches the return directly.",
            }, {
                "question": "Is another implementation authoritative?",
                "method": "symbol search", "target": code_file,
                "result": "No alternate enforcement path exists.",
            }],
            "tool_trace": [{
                "seq": 1, "kind": "design_read", "tool": "read",
                "target": "contract.md:2", "purpose": "Verify the obligation.",
                "result": "The design requires rejection.",
            }, {
                "seq": 2, "kind": "code_search", "tool": "search",
                "target": symbol, "purpose": "Locate the implementation.",
                "result": f"Found {code_file}:1.",
            }, {
                "seq": 3, "kind": "code_read", "tool": "read",
                "target": f"{code_file}:1-2", "purpose": "Read the reachable behavior.",
                "result": observed,
            }, {
                "seq": 4, "kind": "reverse_check", "tool": "search",
                "target": f"guards for {symbol}",
                "purpose": "Exclude compensating enforcement.",
                "result": "No guard or alternate path exists.",
            }],
            "dynamic_probe_selection": {
                "disposition": "not_selected",
                "reason": "The exact static return and direct reachability are sufficient.",
            },
            "assessment": "contradiction_supported",
            "review_lenses": task["review_lenses"],
            "recommendation": "critic_review",
        })
    _write_jsonl(state / "investigation_findings.jsonl", findings)
    _write_jsonl(state / "dynamic_probes.jsonl", [])

    critics: list[dict] = []
    for index, finding in enumerate(findings, start=1):
        claim = claims_by_id[finding["claim_id"]]
        critics.append({
            "review_id": f"REVIEW-{index}",
            "session_id": session_id,
            "finding_id": finding["finding_id"],
            "claim_id": finding["claim_id"],
            "decision": "confirm_contradiction",
            "normative_assessment": {
                "claim_strength": claim["normative_strength"],
                "applicability": "supported",
                "obligation_status": "binding_required",
                "actual_conflict": "yes",
                "rationale": "The applicable mandatory contract directly conflicts with the reachable implementation.",
            },
            "challenges": [
                "Checked whether the cited branch is reachable.",
                "Checked whether another layer enforces the design obligation.",
            ],
            "checks_performed": [
                "Read the complete selected function control flow.",
                "Searched the fixture for an alternate enforcement path.",
            ],
            "dynamic_probe_review": {
                "status": "not_run", "probe_id": "",
                "oracle_validity": "The design oracle is explicit but no probe was needed.",
                "environment_validity": "No dynamic environment evidence was used.",
                "reachability": "Direct entry-point reachability was established statically.",
                "effect_on_decision": "The decision rests on independently checked source evidence.",
            },
            "review_context": "fresh_subagent",
            "resolution": "The design and reachable implementation behavior directly conflict.",
            "remaining_risks": [],
            "input_digests": {
                "claim_sha256": _canonical_sha256(claim),
                "finding_sha256": _canonical_sha256(finding),
                "probe_sha256": "",
            },
            "evidence_critic_prompt_version": "evidence-critic-v4",
        })
    _write_jsonl(state / "critic_reviews.jsonl", critics)
    _write_jsonl(state / "critic_review_history.jsonl", [
        {
            "recorded_at": ac.now_iso(),
            "session_id": critic["session_id"],
            "finding_id": critic["finding_id"],
            "review_key": handoff_merge._critic_history_key(critic),
            "input_digests": critic["input_digests"],
            "evidence_critic_prompt_version": critic[
                "evidence_critic_prompt_version"
            ],
            "critic_sha256": handoff_merge.canonical_digest(critic),
        }
        for critic in critics
    ])
    _write_jsonl(state / "agent_run_ledger.jsonl", [
        {
            "recorded_at": ac.now_iso(), "session_id": session_id,
            "event": "handoff_merge", "status": "complete",
            "artifact_type": "finding",
            "validated_ids": [finding["finding_id"] for finding in findings],
        },
        {
            "recorded_at": ac.now_iso(), "session_id": session_id,
            "event": "handoff_merge", "status": "complete",
            "artifact_type": "critic",
            "validated_ids": [finding["finding_id"] for finding in findings],
        },
    ])

    rounds = _jsonl(state / "investigation_rounds.jsonl")
    rounds[0].update({
        "finding_ids": [finding["finding_id"] for finding in findings],
        "outcome": "Both scoped obligations were investigated and independently reviewed.",
        "next_strategy": "Finalize the drained frontier.",
    })
    _write_jsonl(state / "investigation_rounds.jsonl", rounds)

    ac.save_json(state / "semantic_coverage.json", {
        "session_id": session_id,
        "lenses": [{
            "lens": "input acceptance", "disposition": "investigated",
            "evidence": "Both directly reachable acceptance boundaries were investigated.",
            "task_ids": ["TASK-1", "TASK-2"],
            "finding_ids": ["FINDING-1", "FINDING-2"],
            "design_group_refs": ["contract"],
            "boundary_refs": ["BOUNDARY-SERVICE", "BOUNDARY-AUDIT"],
            "counterfactual": "",
        }],
    })
    ac.save_json(state / "coverage_audit.json", {
        "session_id": session_id,
        "design_documents_reviewed": ["contract.md"],
        "claims_total": 2, "claims_investigated": 2, "rounds_completed": 1,
        "exploration_modes_completed": [
            "design-to-code obligation tracing",
            "code-to-design risk backtracking",
        ],
        "document_groups_total": 1, "document_groups_accounted": 1,
        "code_areas_reviewed": ["service.py", "audit.py"],
        "architecture_boundaries": [{
            "boundary_id": "BOUNDARY-SERVICE", "status": "investigated",
            "evidence": "TASK-1 and FINDING-1 cover the service API.",
        }, {
            "boundary_id": "BOUNDARY-AUDIT", "status": "investigated",
            "evidence": "TASK-2 and FINDING-2 cover the audit API.",
        }],
        "remaining_scoped_claims": [], "deferred_claims": [],
        "false_positive_samples_rechecked": ["FINDING-1", "FINDING-2"],
        "next_round_tasks": [], "supplement_rounds": 0, "remaining_gaps": [],
        "stop_reason": "The scoped frontier is drained and all high-risk boundaries are evidenced.",
    })
    ac.save_json(state / "coverage_supplement_history.json", {
        "session_id": session_id, "requests": [],
    })


def test_claims_prepare_writes_frozen_manifest_and_no_llm(replay_source, tmp_path):
    replay = tmp_path / "replay-claims"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay, stage="claims",
        provider="provider-a", model="model-b",
    )

    assert manifest["development_only"] is True
    assert manifest["llm_invoked"] is False
    assert manifest["runtime"] == {"provider": "provider-a", "model": "model-b"}
    assert manifest["schema"]["contract_version"] == 19
    assert len(manifest["source_digest"]) == 64
    assert len(manifest["replay_input_digest"]) == 64
    assert len(manifest["prompt"]["sha256"]) == 64
    assert len(manifest["skills"]["combined_sha256"]) == 64
    assert (replay / "prompt_envelope.json").is_file()
    assert not (replay / "state" / "architecture_map.json").exists()
    assert not (replay / "state" / "design_claims.jsonl").exists()
    assert not (replay / "state" / "design_coverage.json").exists()
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert envelope["mode"] == "external_llm_required"
    assert envelope["inputs"] == [
        "state/design_agent_manifest.json", "state/design_inventory.json",
        "state/design_lookup_requests.jsonl",
    ]
    assert envelope["read_only_source_roots"] == {
        "design": str(replay_source["state"] / "review-inputs" / "design")
    }
    assert not (replay / "prompt-assets" / "output_schema.json").exists()


def test_inventory_replay_positive_and_local_validation(replay_source, tmp_path):
    replay = tmp_path / "replay-inventory"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="inventory", run_local=True,
    )

    raw_path = replay / "state" / "handoffs" / "design" / "inventory.raw.json"
    raw_inventory = ac.load_json(raw_path)
    raw_evidence = raw_inventory["document_groups"][0]["scope_evidence"]
    assert set(raw_evidence) == {"source_ref"}
    assert set(raw_evidence["source_ref"]) == {"path", "line_start", "line_end"}
    assert "group_sha256" not in raw_inventory["document_groups"][0]
    assert not (replay / "state" / "design_inventory.json").exists()
    assert stage_replay.run_local(replay) == 0
    assert ac.load_json(replay / "state" / "design_inventory.json") == ac.load_json(
        replay_source["state"] / "design_inventory.json"
    )
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert envelope["inputs"] == ["state/design_agent_manifest.json"]
    assert envelope["outputs"] == [{
        "path": "state/design_inventory.json", "write_scope": "replay_only",
    }]
    assert manifest["source_session_id"] == replay_source["session_id"]
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "design_inventory_validation"
    assert execution["preflight"]["returncode"] == 0
    assert "design_source_materializer.py" in execution["preflight"]["command"][1]
    assert ac.load_json(
        replay / "logs" / "trace" / "design_inventory_materialization.json"
    )["passed"] is True
    assert ac.load_json(replay / "logs" / "trace" / "design_validation.json")["passed"] is True


def test_inventory_local_replay_rejects_missing_or_corrupt_artifact(
    replay_source, tmp_path,
):
    inventory_path = replay_source["state"] / "design_inventory.json"
    original = inventory_path.read_bytes()
    inventory_path.unlink()
    with pytest.raises(stage_replay.ReplayError, match="design_inventory.json"):
        stage_replay.prepare_replay(
            source_state=replay_source["state"], replay_root=tmp_path / "missing-inventory",
            stage="inventory", run_local=True,
        )

    inventory_path.write_bytes(b"{broken")
    with pytest.raises(json.JSONDecodeError):
        stage_replay.prepare_replay(
            source_state=replay_source["state"], replay_root=tmp_path / "corrupt-inventory",
            stage="inventory", run_local=True,
        )
    assert inventory_path.read_bytes() == b"{broken"
    inventory_path.write_bytes(original)


def test_inventory_local_materialization_rejects_broken_source_ref_and_forged_quote(
    replay_source, tmp_path,
):
    replay = tmp_path / "replay-invalid-source-ref"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="inventory", run_local=True,
    )
    raw_path = replay / "state" / "handoffs" / "design" / "inventory.raw.json"
    raw = ac.load_json(raw_path)
    evidence = raw["document_groups"][0]["scope_evidence"]
    evidence["source_ref"]["line_end"] = 999
    evidence["quote"] = "forged quote must never become evidence"
    ac.save_json(raw_path, raw)

    assert stage_replay.run_local(replay) == 1
    assert not (replay / "state" / "design_inventory.json").exists()
    materialization = ac.load_json(
        replay / "logs" / "trace" / "design_inventory_materialization.json"
    )
    assert materialization["passed"] is False
    assert any("exceeds" in error for error in materialization["errors"])
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["command"][1].endswith("design_source_materializer.py")
    assert execution["preflight"]["returncode"] == 1


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
    assert (claim_replay / "state" / "design_claim_review.json").is_file()

    # Select the audit slice explicitly even though it is not the first slice.
    # Replay must follow the caller's validated sweep selector, not list order
    # or a boundary-risk heuristic.
    risk_plan_path = replay_source["state"] / "risk_sweep_plan.json"
    risk_replay = tmp_path / "replay-risk"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=risk_replay, stage="risk",
        item_id="RISK-SWEEP-AUDIT",
    )
    risk_envelope = ac.load_json(risk_replay / "prompt_envelope.json")
    assert risk_envelope["read_only_source_roots"] == {
        "code": str(replay_source["state"] / "review-inputs" / "code"),
        "design": str(replay_source["state"] / "review-inputs" / "design"),
    }
    assert risk_envelope["inputs"] == [
        "state/agent_loop_contract.json", "state/architecture_map.json",
        "state/design_inventory.json", "state/risk_sweep_plan.json",
    ]
    assert risk_envelope["selection"] == {
        "sweep_id": "RISK-SWEEP-AUDIT",
        "architecture_boundaries": ["BOUNDARY-AUDIT"],
        "implementation_planes": ["PLANE-AUDIT"],
        "parallel_path_ids": [],
        "anchor_paths": ["audit.py"],
        "review_lenses": ["input acceptance"],
        "design_section_ids": ["contract-main"],
        "scope_rationale": "The audit API is an independent execution component.",
        "risk_sweep_plan_sha256": ac.sha256_file(
            risk_plan_path
        ),
    }
    assert risk_envelope["outputs"] == [{
        "path": "state/handoffs/risks/RISK-SWEEP-AUDIT/RISK-SWEEP-AUDIT.json",
        "write_scope": "replay_only",
    }]
    assert (risk_replay / "state" / "architecture_map.json").is_file()
    assert (risk_replay / "state" / "risk_sweep_plan.json").is_file()
    assert not (risk_replay / "state" / "design_claims.jsonl").exists()
    assert not (risk_replay / "state" / "design_coverage.json").exists()
    assert not (risk_replay / "prompt-assets" / "output_schema.json").exists()


def test_claim_review_local_validation_preserves_item_digest_contract(
    replay_source, tmp_path,
):
    source_review_path = replay_source["state"] / "design_claim_review.json"
    source_review = ac.load_json(source_review_path)
    source_review["input_digests"] = {"audit_only": "must-not-be-rewritten"}
    ac.save_json(source_review_path, source_review)
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
    assert review["input_digests"] == {"audit_only": "must-not-be-rewritten"}
    assert review["claim_reviews"][0]["claim_sha256"] == _canonical_sha256(
        _jsonl(replay / "state" / "design_claims.jsonl")[0]
    )


@pytest.mark.parametrize("failure_kind", ("semantic", "binding"))
def test_claim_review_local_replay_rejects_semantic_or_snapshot_mismatch(
    replay_source, tmp_path, failure_kind,
):
    replay = tmp_path / f"replay-claim-review-{failure_kind}"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="claim-review", run_local=True,
    )
    review_path = replay / "state" / "design_claim_review.json"
    review = ac.load_json(review_path)
    if failure_kind == "semantic":
        review["claim_reviews"][0]["quote_entailment"]["assessment"] = "not_entailed"
        expected_error = "decision must be 'repair' for its assessments"
    else:
        review["claim_reviews"][0]["claim_sha256"] = "0" * 64
        expected_error = "claim_sha256 does not match the current claim"
    ac.save_json(review_path, review)

    assert stage_replay.run_local(replay) == 1
    validation = ac.load_json(replay / "logs" / "trace" / "claim_review_validation.json")
    assert validation["passed"] is False
    assert any(expected_error in error for error in validation["errors"])
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["returncode"] == 1
    assert execution["kind"] == "claim_review_validation"


def test_claim_review_local_replay_corrupt_review_fails_closed(
    replay_source, tmp_path,
):
    review_path = replay_source["state"] / "design_claim_review.json"
    review_path.write_bytes(b"{broken review artifact\n")
    replay = tmp_path / "replay-claim-review-corrupt"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="claim-review", run_local=True,
    )

    assert stage_replay.run_local(replay) == 1
    assert review_path.read_bytes() == b"{broken review artifact\n"
    validation = ac.load_json(replay / "logs" / "trace" / "claim_review_validation.json")
    assert validation["passed"] is False
    assert any("cannot load JSON" in error for error in validation["errors"])


def test_investigator_replay_copies_only_selected_task_context(replay_source, tmp_path):
    replay = tmp_path / "replay-investigator"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="investigator", item_id="TASK-2",
    )

    assert manifest["selection"] == {
        "task_id": "TASK-2", "claim_id": "CLAIM-2",
        "risk_observation_ids": ["OBS-1", "OBS-2"],
    }
    assert [item["task_id"] for item in _jsonl(
        replay / "state" / "investigation_tasks.jsonl",
    )] == ["TASK-2"]
    assert [item["claim_id"] for item in _jsonl(
        replay / "state" / "design_claims.jsonl",
    )] == ["CLAIM-2"]
    assert [item["observation_id"] for item in _jsonl(
        replay / "state" / "risk_observations.jsonl",
    )] == ["OBS-1", "OBS-2"]
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert "state/risk_sweep_plan.json" in envelope["inputs"]
    assert not (replay / "state" / "investigation_findings.jsonl").exists()
    assert not (replay / "state" / "unrelated-evaluation-data.json").exists()
    assert (replay / "state" / "handoff-templates" / "investigators" / "TASK-2.json").is_file()


def test_plan_replay_includes_current_claim_scope(replay_source, tmp_path):
    replay = tmp_path / "replay-plan"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay, stage="plan",
    )

    assert (replay / "state" / "claim_review_scope.json").is_file()
    assert (replay / "state" / "design_inventory.json").is_file()
    assert (replay / "state" / "design_claim_review.json").is_file()
    assert (replay / "state" / "risk_sweep_plan.json").is_file()
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert "state/claim_review_scope.json" in envelope["inputs"]
    assert "state/risk_sweep_plan.json" in envelope["inputs"]
    assert "logs/trace/claim_review_validation.json" in envelope["inputs"]
    assert not (replay / "state" / "investigation_tasks.jsonl").exists()


def test_plan_local_replay_validates_only_stable_task_plan(replay_source, tmp_path):
    source_tasks = replay_source["state"] / "investigation_tasks.jsonl"
    before = ac.sha256_file(source_tasks)
    replay = tmp_path / "replay-plan-local"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="plan", run_local=True,
    )

    assert stage_replay.run_local(replay) == 0
    assert not (replay / "state" / "investigation_findings.jsonl").exists()
    trace = ac.load_json(replay / "logs" / "trace" / "task_plan_validation.json")
    assert trace["passed"] is True
    assert trace["metrics"]["valid_task_ids"] == ["TASK-1", "TASK-2"]
    assert ac.sha256_file(source_tasks) == before
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "task_plan_validation"


def test_plan_local_replay_rejects_missing_or_corrupt_task_plan(replay_source, tmp_path):
    tasks_path = replay_source["state"] / "investigation_tasks.jsonl"
    original = tasks_path.read_bytes()
    tasks_path.unlink()
    with pytest.raises(stage_replay.ReplayError, match="investigation_tasks.jsonl"):
        stage_replay.prepare_replay(
            source_state=replay_source["state"], replay_root=tmp_path / "missing-plan",
            stage="plan", run_local=True,
        )

    tasks_path.write_bytes(b"not-json\n")
    replay = tmp_path / "corrupt-plan"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="plan", run_local=True,
    )
    assert stage_replay.run_local(replay) == 1
    assert tasks_path.read_bytes() == b"not-json\n"
    tasks_path.write_bytes(original)


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


@pytest.mark.parametrize(
    ("stage", "item_id", "expected_inputs"),
    (
        (
            "risk", "RISK-SWEEP-AUDIT", {
                "state/agent_loop_contract.json", "state/architecture_map.json",
                "state/design_inventory.json", "state/risk_sweep_plan.json",
            },
        ),
        (
            "investigator", "TASK-2", {
                "state/architecture_map.json", "state/design_coverage.json",
                "state/design_claims.jsonl", "state/investigation_tasks.jsonl",
                "state/risk_observations.jsonl", "state/risk_sweep_plan.json",
                "state/handoff-templates/investigators/TASK-2.json",
            },
        ),
        (
            "critic", "FINDING-2", {
                "state/design_claims.jsonl",
                "state/investigation_findings.jsonl", "state/dynamic_probes.jsonl",
            },
        ),
        (
            "probe", "FINDING-2", {
                "state/design_claims.jsonl", "state/investigation_findings.jsonl",
            },
        ),
        (
            "judge", "FINDING-2", {
                "state/design_claims.jsonl",
                "state/investigation_findings.jsonl", "state/dynamic_probes.jsonl",
                "state/critic_reviews.jsonl",
            },
        ),
    ),
)
def test_external_role_replay_positive_fixture_is_frozen_minimal_and_model_free(
    replay_source, tmp_path, stage, item_id, expected_inputs,
):
    replay = tmp_path / f"positive-{stage}"
    manifest = stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage=stage, item_id=item_id,
    )

    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert manifest["llm_invoked"] is False
    assert manifest["local_execution_requested"] is False
    assert manifest["local_execution"] is None
    assert envelope["mode"] == "external_llm_required"
    assert set(envelope["inputs"]) == expected_inputs
    assert not (replay / "logs" / "trace" / "stage_replay_local.json").exists()
    assert not (replay / "state" / "unrelated-evaluation-data.json").exists()


@pytest.mark.parametrize(
    ("stage", "item_id", "message"),
    (
        ("risk", "RISK-SWEEP-UNKNOWN", "risk sweep selector"),
        ("investigator", "TASK-UNKNOWN", "task selector"),
        ("probe", "FINDING-UNKNOWN", "finding selector"),
        ("critic", "FINDING-UNKNOWN", "finding selector"),
        ("judge", "FINDING-UNKNOWN", "finding selector"),
    ),
)
def test_external_role_replay_negative_fixture_rejects_unknown_item(
    replay_source, tmp_path, stage, item_id, message,
):
    with pytest.raises(stage_replay.ReplayError, match=message):
        stage_replay.prepare_replay(
            source_state=replay_source["state"],
            replay_root=tmp_path / f"negative-{stage}",
            stage=stage, item_id=item_id,
        )


@pytest.mark.parametrize(
    ("stage", "item_id", "artifact"),
    (
        ("risk", "RISK-SWEEP-AUDIT", "risk_sweep_plan.json"),
        ("investigator", "TASK-2", "investigation_tasks.jsonl"),
        ("probe", "FINDING-2", "investigation_findings.jsonl"),
        ("critic", "FINDING-2", "investigation_findings.jsonl"),
        ("judge", "FINDING-2", "critic_reviews.jsonl"),
    ),
)
def test_external_role_replay_corrupt_artifact_fixture_fails_closed(
    replay_source, tmp_path, stage, item_id, artifact,
):
    damaged = replay_source["state"] / artifact
    damaged.write_bytes(b"{broken artifact\n")

    with pytest.raises(stage_replay.ReplayError):
        stage_replay.prepare_replay(
            source_state=replay_source["state"],
            replay_root=tmp_path / f"corrupt-{stage}",
            stage=stage, item_id=item_id,
        )
    assert damaged.read_bytes() == b"{broken artifact\n"


def test_claims_local_schema_replay_is_isolated(replay_source, tmp_path):
    source_claims = replay_source["state"] / "design_claims.jsonl"
    before = ac.sha256_file(source_claims)
    replay = tmp_path / "replay-schema"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="claims", run_local=True,
    )

    raw_path = replay / "state" / "handoffs" / "design" / "claims.raw.jsonl"
    raw_claims = _jsonl(raw_path)
    assert not (replay / "state" / "design_claims.jsonl").exists()
    assert "quote" not in raw_claims[0]
    assert "source_sha256" not in raw_claims[0]["source_ref"]
    # A model-owned quote is not trusted: materialization must replace it from
    # the selected source_ref range before validation.
    raw_claims[0]["quote"] = "forged quote"
    _write_jsonl(raw_path, raw_claims)
    assert stage_replay.run_local(replay) == 0
    materialized_claims = _jsonl(replay / "state" / "design_claims.jsonl")
    assert materialized_claims[0]["quote"] == "The service must reject invalid values."
    assert materialized_claims[0]["quote"] != "forged quote"
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "design_schema_validation"
    assert execution["returncode"] == 0
    assert execution["preflight"]["returncode"] == 0
    assert ac.load_json(
        replay / "logs" / "trace" / "design_claim_materialization.json"
    )["passed"] is True
    assert ac.load_json(replay / "logs" / "trace" / "design_validation.json")["passed"] is True
    assert ac.sha256_file(source_claims) == before


def test_claims_local_replay_corrupt_raw_json_fails_closed(
    replay_source, tmp_path,
):
    replay = tmp_path / "replay-claims-corrupt-json"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="claims", run_local=True,
    )
    raw_path = replay / "state" / "handoffs" / "design" / "claims.raw.jsonl"
    raw_path.write_bytes(b"{broken claim artifact\n")

    assert stage_replay.run_local(replay) == 1
    assert not (replay / "state" / "design_claims.jsonl").exists()
    materialization = ac.load_json(
        replay / "logs" / "trace" / "design_claim_materialization.json"
    )
    assert materialization["passed"] is False
    assert materialization["errors"]
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["command"][1].endswith("design_source_materializer.py")
    assert execution["preflight"]["returncode"] == 1


def test_claims_local_replay_invalid_source_ref_fails_closed(
    replay_source, tmp_path,
):
    replay = tmp_path / "replay-claims-invalid-source-ref"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="claims", run_local=True,
    )
    raw_path = replay / "state" / "handoffs" / "design" / "claims.raw.jsonl"
    raw_claims = _jsonl(raw_path)
    raw_claims[0]["source_ref"]["line_end"] = 999
    _write_jsonl(raw_path, raw_claims)

    assert stage_replay.run_local(replay) == 1
    assert not (replay / "state" / "design_claims.jsonl").exists()
    materialization = ac.load_json(
        replay / "logs" / "trace" / "design_claim_materialization.json"
    )
    assert materialization["passed"] is False
    assert any("exceeds" in error for error in materialization["errors"])
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["command"][1].endswith("design_source_materializer.py")
    assert execution["preflight"]["returncode"] == 1


def test_gate_local_replay_uses_existing_deterministic_runner(
    replay_source, tmp_path, monkeypatch,
):
    _prepare_valid_coverage_artifacts(replay_source)
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
    assert (replay / "state" / "risk_sweep_plan.json").is_file()
    assert (replay / "state" / "design_inventory.json").is_file()
    assert (replay / "state" / "design_lookup_requests.jsonl").is_file()
    assert (replay / "state" / "coverage_supplement_history.json").is_file()
    assert (replay / "logs" / "trace" / "risk_sweep_plan_validation.json").is_file()
    assert (replay / "logs" / "trace" / "session_prepared.json").is_file()
    assert (replay / "logs" / "trace" / "claim_review_validation.json").is_file()
    assert (replay / "logs" / "trace" / "task_plan_validation.json").is_file()
    assert (replay / "logs" / "trace" / "task_lifecycle_validation.json").is_file()
    assert ac.load_json(
        replay / "logs" / "trace" / "claim_review_validation.json"
    )["expansion_requests"] == []
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
    _prepare_valid_coverage_artifacts(replay_source)
    replay = tmp_path / "replay-coverage"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="coverage", run_local=True,
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if Path(command[1]).name == "claim_review_validator.py":
            return SimpleNamespace(returncode=0, stdout="claim review ok", stderr="")
        return SimpleNamespace(returncode=1, stdout="coverage output", stderr="")

    monkeypatch.setattr(stage_replay.subprocess, "run", fake_run)
    assert stage_replay.run_local(replay) == 1
    assert Path(calls[0][1]).name == "claim_review_validator.py"
    assert Path(calls[1][1]).name == "goal_runner.py"
    assert calls[1][2] == "coverage-check"
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert envelope["read_only_source_roots"] == {}
    assert "state/claim_review_scope.json" in envelope["inputs"]
    assert "state/design_claim_review.json" in envelope["inputs"]
    assert "state/risk_sweep_plan.json" in envelope["inputs"]
    assert "state/dynamic_probes.jsonl" in envelope["inputs"]
    assert "state/critic_reviews.jsonl" in envelope["inputs"]
    assert "state/coverage_supplement_history.json" in envelope["inputs"]
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "coverage_validation"


def test_coverage_local_replay_runs_real_closed_frontier_validation(
    replay_source, tmp_path,
):
    _prepare_valid_coverage_artifacts(replay_source)
    replay = tmp_path / "replay-coverage-real"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="coverage", run_local=True,
    )

    assert stage_replay.run_local(replay) == 0
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["kind"] == "coverage_validation"
    assert execution["preflight"]["returncode"] == 0
    validation = ac.load_json(replay / "logs" / "trace" / "coverage_validation.json")
    assert validation["passed"] is True
    assert validation["closed"] is True
    assert validation["metrics"]["findings"] == 2
    assert validation["metrics"]["critics"] == 2
    assert validation["metrics"]["supplement_rounds"] == 0


@pytest.mark.parametrize("artifact", ("coverage_audit.json", "semantic_coverage.json"))
def test_coverage_local_replay_corrupt_semantic_artifact_fails_closed(
    replay_source, tmp_path, artifact,
):
    _prepare_valid_coverage_artifacts(replay_source)
    replay = tmp_path / f"replay-coverage-corrupt-{artifact}"
    stage_replay.prepare_replay(
        source_state=replay_source["state"], replay_root=replay,
        stage="coverage", run_local=True,
    )
    damaged = replay / "state" / artifact
    damaged.write_bytes(b"{broken coverage artifact\n")

    assert stage_replay.run_local(replay) == 1
    assert damaged.read_bytes() == b"{broken coverage artifact\n"
    execution = ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")
    assert execution["preflight"]["returncode"] == 0
    assert execution["returncode"] == 1
    validation = ac.load_json(replay / "logs" / "trace" / "coverage_validation.json")
    assert validation["passed"] is False
    assert any(f"{artifact}: invalid JSON" in error for error in validation["errors"])


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


def test_risk_cli_item_id_selects_non_first_sweep_and_rejects_unknown(
    replay_source, tmp_path,
):
    replay = tmp_path / "risk-cli-selected"
    selected = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "stage_replay.py"), "risk",
            "--source-state", str(replay_source["state"]),
            "--replay-root", str(replay),
            "--item-id", "RISK-SWEEP-AUDIT",
        ],
        text=True, capture_output=True,
    )

    assert selected.returncode == 0, selected.stderr
    envelope = ac.load_json(replay / "prompt_envelope.json")
    assert envelope["selection"]["sweep_id"] == "RISK-SWEEP-AUDIT"
    assert envelope["outputs"][0]["path"].endswith("/RISK-SWEEP-AUDIT.json")

    unknown = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "stage_replay.py"), "risk",
            "--source-state", str(replay_source["state"]),
            "--replay-root", str(tmp_path / "risk-cli-unknown"),
            "--item-id", "RISK-SWEEP-UNKNOWN",
        ],
        text=True, capture_output=True,
    )
    assert unknown.returncode == 2
    assert "risk sweep selector" in unknown.stderr


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
