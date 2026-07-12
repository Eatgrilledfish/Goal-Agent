from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import design_source_materializer  # noqa: E402
import handoff_merge  # noqa: E402
import handoff_template  # noqa: E402
import report_writer  # noqa: E402
import session_event  # noqa: E402
import stage_artifact_validator  # noqa: E402
import stage_replay  # noqa: E402
import verdict_validator  # noqa: E402


def run_runner(command: str, code: Path, design: Path, result: Path, logs: Path, check: bool = True):
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "goal_runner.py"), command,
            "--code-root", str(code), "--design-root", str(design),
            "--result-root", str(result), "--log-root", str(logs),
        ],
        text=True,
        capture_output=True,
    )
    if check and proc.returncode:
        raise AssertionError(f"command failed: {proc.args}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path | str]:
    code = tmp_path / "service"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    code.mkdir()
    design.mkdir()
    (design / "contract.md").write_text(
        "# Service contract\n"
        "\n"
        "The service must reject negative amounts.\n"
        "The service must expire sessions after 30 minutes.\n"
        "The service must deny exports for guest users.\n"
        "The service must preserve all submitted audit events.\n",
        encoding="utf-8",
    )
    (code / "service.py").write_text(
        "def charge(amount):\n"
        "    return {\"accepted\": True}\n"
        "\n"
        "def session_expired(minutes):\n"
        "    return minutes > 60\n"
        "\n"
        "def can_export(role):\n"
        "    return True\n"
        "\n"
        "def record_event(events, event):\n"
        "    return events[-9:] + [event]\n",
        encoding="utf-8",
    )
    (code / "audit.py").write_text(
        "def publish_event(event):\n"
        "    return {\"published\": True, \"event\": event}\n",
        encoding="utf-8",
    )
    run_runner("prepare", code, design, result, logs)
    state = ac.load_json(logs / "state" / "agent_loop_state.json")
    return {
        "code": code,
        "design": design,
        "result": result,
        "logs": logs,
        "state": logs / "state",
        "session_id": state["session_id"],
    }


def prepare_materialized_workspace(
    workspace: dict[str, Path | str], tmp_path: Path,
) -> dict[str, Path | str]:
    source = workspace["design"]
    code = workspace["code"]
    assert isinstance(source, Path)
    assert isinstance(code, Path)
    (source / "catalog.list").write_text(
        "Use contract.md as the supplied service design.\n", encoding="utf-8"
    )
    result = tmp_path / "materialized-result"
    logs = tmp_path / "materialized-logs"
    state = logs / "state"
    bundle = state / "design-sources"
    plan = state / "design_source_plan.json"
    source_manifest = logs / "trace" / "design_source_materialization.json"
    clock = subprocess.run([
        sys.executable, str(SCRIPTS / "goal_runner.py"), "start-clock",
        "--log-root", str(logs), "--state-root", str(state),
    ], text=True, capture_output=True)
    assert clock.returncode == 0, clock.stdout + clock.stderr
    ac.save_json(plan, {
        "catalog_path": "catalog.list",
        "sources": [{
            "source_id": "service-contract",
            "kind": "local",
            "location": "contract.md",
            "output_path": "contract.md",
            "catalog_evidence": {
                "path": "catalog.list", "line_start": 1, "line_end": 1,
                "quote": "Use contract.md as the supplied service design.",
            },
        }],
    })
    materialize = subprocess.run([
        sys.executable, str(SCRIPTS / "design_source_materializer.py"),
        "--source-root", str(source), "--plan", str(plan),
        "--output-root", str(bundle), "--manifest", str(source_manifest),
    ], text=True, capture_output=True)
    assert materialize.returncode == 0, materialize.stderr
    prepared = subprocess.run([
        sys.executable, str(SCRIPTS / "goal_runner.py"), "prepare",
        "--code-root", str(code), "--design-root", str(bundle),
        "--result-root", str(result), "--log-root", str(logs),
        "--source-manifest", str(source_manifest),
    ], text=True, capture_output=True)
    assert prepared.returncode == 0, prepared.stderr
    session = ac.load_json(state / "agent_loop_state.json")
    return {
        "code": code,
        "design": bundle,
        "source": source,
        "result": result,
        "logs": logs,
        "state": state,
        "session_id": session["session_id"],
    }


def test_prepare_rejects_materialized_bundle_without_source_manifest(workspace, tmp_path):
    source = Path(workspace["design"])
    code = Path(workspace["code"])
    (source / "catalog.list").write_text(
        "Use contract.md as the supplied service design.\n", encoding="utf-8",
    )
    result = tmp_path / "omitted-manifest-result"
    logs = tmp_path / "omitted-manifest-logs"
    state = logs / "state"
    bundle = state / "design-sources"
    plan = state / "design_source_plan.json"
    manifest = logs / "trace" / "design_source_materialization.json"
    clock = subprocess.run([
        sys.executable, str(SCRIPTS / "goal_runner.py"), "start-clock",
        "--log-root", str(logs), "--state-root", str(state),
    ], text=True, capture_output=True)
    assert clock.returncode == 0, clock.stdout + clock.stderr
    ac.save_json(plan, {
        "catalog_path": "catalog.list",
        "sources": [{
            "source_id": "service-contract", "kind": "local",
            "location": "contract.md", "output_path": "contract.md",
            "catalog_evidence": {
                "path": "catalog.list", "line_start": 1, "line_end": 1,
                "quote": "Use contract.md as the supplied service design.",
            },
        }],
    })
    materialize = subprocess.run([
        sys.executable, str(SCRIPTS / "design_source_materializer.py"),
        "--source-root", str(source), "--plan", str(plan),
        "--output-root", str(bundle), "--manifest", str(manifest),
    ], text=True, capture_output=True)
    assert materialize.returncode == 0, materialize.stderr

    prepared = run_runner(
        "prepare", code, bundle, result, logs, check=False,
    )

    assert prepared.returncode != 0
    preflight = ac.load_json(logs / "trace" / "preflight.json")
    assert any(
        "requires --source-manifest" in problem
        for problem in preflight["problems"]
    )
    assert not (state / "agent_loop_state.json").exists()

    corrected = subprocess.run([
        sys.executable, str(SCRIPTS / "goal_runner.py"), "prepare",
        "--code-root", str(code), "--design-root", str(bundle),
        "--result-root", str(result), "--log-root", str(logs),
        "--source-manifest", str(manifest),
    ], text=True, capture_output=True)
    assert corrected.returncode == 0, corrected.stdout + corrected.stderr


def append(path: Path, value: dict) -> None:
    ac.append_jsonl(path, value)


def record_critic_history(state: Path, critic: dict) -> None:
    ac.append_jsonl(state / "critic_review_history.jsonl", {
        "recorded_at": ac.now_iso(),
        "session_id": critic["session_id"],
        "finding_id": critic["finding_id"],
        "review_key": handoff_merge._critic_history_key(critic),
        "input_digests": critic["input_digests"],
        "evidence_critic_prompt_version": critic["evidence_critic_prompt_version"],
        "critic_sha256": handoff_merge.canonical_digest(critic),
    })


def record_trace_checkpoint(
    state: Path, session_id: str, phase: str, role: str,
    *, task_id: str | None = None, scope_id: str | None = None,
    provider_session_id: str | None = None,
) -> None:
    input_path = state / "workspace_manifest.json"
    records = [{
        "path": str(input_path.resolve()),
        "sha256": ac.sha256_file(input_path),
        "size_bytes": input_path.stat().st_size,
    }]
    artifact_name = {
        "architecture_mapping": "architecture_map.json",
        "design_inventory": "design_inventory.json",
        "code_risk_backtracking": "risk_observations.jsonl",
        "design_claim_resolution": "design_claims.jsonl",
        "design_claim_review": "design_claim_review.json",
        "investigation_planning": "investigation_tasks.jsonl",
        "investigation": "investigation_findings.jsonl",
        "dynamic_probe": "dynamic_probes.jsonl",
        "critic_review": "critic_reviews.jsonl",
        "coverage_audit": "coverage_audit.json",
        "final_judgement": "agent_review_verdicts.jsonl",
    }[phase]
    artifact_path = state / artifact_name
    artifact_records = [{
        "path": str(artifact_path.resolve()),
        "sha256": ac.sha256_file(artifact_path),
        "size_bytes": artifact_path.stat().st_size,
    }]
    timestamp = ac.now_iso()
    stable_scope_id = task_id or scope_id or f"{phase}-portfolio"
    event = {
        "recorded_at": timestamp,
        "session_id": session_id,
        "event": f"{phase}.checkpoint",
        "actor": f"fixture-{role}",
        "role": role,
        "phase": phase,
        "status": "complete",
        "outcome": "fixture_complete",
        "summary": "Fixture phase completed with deterministic artifacts.",
        "metrics": {},
        "artifacts": [str(artifact_path.resolve())],
        "artifact_snapshots": artifact_records,
        "artifact_sha256": session_event.input_artifacts_sha256(artifact_records),
        "next_actions": [],
        "scope_id": stable_scope_id,
        "scope": f"fixture:{phase}:{stable_scope_id}",
        "input_artifacts": records,
        "input_sha256": session_event.input_artifacts_sha256(records),
        "started_at": timestamp,
        "ended_at": timestamp,
        "wall_time_seconds": 0.0,
        "provider_attempt": 1,
        "provider_session_id": provider_session_id or (
            f"fixture-provider-{phase}-{stable_scope_id}"
        ),
        "output_count": 1,
        "repair_count": 0,
        "stop_reason": "phase_handoff",
    }
    if task_id is not None:
        event["task_id"] = task_id
    ac.append_jsonl(state / "agent_run_ledger.jsonl", event)


def populate_handoffs(workspace: dict[str, Path | str], count: int = 4, bad_quote: bool = False) -> None:
    state = workspace["state"]
    assert isinstance(state, Path)
    ac.save_json(state / "architecture_map.json", {
        "session_id": workspace["session_id"],
        "repository_summary": "A small service implementation.",
        "languages": ["Python"],
        "entrypoints": [{"path": "service.py", "purpose": "service API", "evidence": "top-level functions"}],
        "subsystems": [{"subsystem_id": "SUBSYSTEM-SERVICE", "name": "service", "paths": ["service.py"], "role": "business behavior"}],
        "implementation_planes": [
            {
                "plane_id": "PLANE-SERVICE", "kind": "owned", "paths": ["service.py"],
                "reachable_evidence": "The public service functions execute directly.",
            },
            {
                "plane_id": "PLANE-AUDIT", "kind": "adapter", "paths": ["audit.py"],
                "reachable_evidence": "The audit publishing adapter executes independently.",
            },
        ],
        "integration_boundaries": [
            {
                "boundary_id": "BOUNDARY-API", "name": "callers to service",
                "paths": ["service.py"], "plane_ids": ["PLANE-SERVICE"],
                "risk": "high", "why": "externally visible behavior",
            },
            {
                "boundary_id": "BOUNDARY-AUDIT", "name": "audit publisher",
                "paths": ["audit.py"], "plane_ids": ["PLANE-AUDIT"],
                "risk": "high", "why": "externally visible audit behavior",
            },
        ],
        "capability_surfaces": [{
            "surface_id": "CAPABILITY-API", "paths": ["service.py"],
            "declares_or_registers": "Public service functions.",
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
    inventory = design_source_materializer.materialize_inventory({
        "session_id": workspace["session_id"],
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "scope_relation": "required",
            "scope_evidence": {
                "source_ref": {"path": "contract.md", "line_start": 1, "line_end": 3},
            },
            "sections": [{
                "section_id": "SECTION-CONTRACT",
                "source_ref": {"path": "contract.md", "line_start": 1, "line_end": 6},
                "behavior_families": ["externally visible service contract"],
                "ambiguities": [],
            }],
        }],
    }, Path(workspace["design"]))
    ac.save_json(state / "design_inventory.json", inventory)
    contract = ac.load_json(state / "agent_loop_contract.json")
    lenses = contract["coverage_contract"]["portfolio_lenses"]
    architecture_digest = ac.sha256_file(state / "architecture_map.json")
    ac.save_json(state / "risk_sweep_plan.json", {
        "session_id": workspace["session_id"],
        "plan_id": "RISK-PLAN-001",
        "architecture_map_sha256": architecture_digest,
        "design_inventory_sha256": ac.sha256_file(state / "design_inventory.json"),
        "required_coverage": {
            "boundary_ids": ["BOUNDARY-API", "BOUNDARY-AUDIT"],
            "plane_ids": ["PLANE-SERVICE", "PLANE-AUDIT"],
            "parallel_path_ids": [],
        },
        "slices": [
            {
                "sweep_id": "RISK-SWEEP-API",
                "architecture_boundaries": ["BOUNDARY-API"],
                "implementation_planes": ["PLANE-SERVICE"],
                "parallel_path_ids": [],
                "anchor_paths": ["service.py"],
                "review_lenses": lenses,
                "design_section_ids": ["SECTION-CONTRACT"],
                "scope_rationale": "Own the public service API component.",
            },
            {
                "sweep_id": "RISK-SWEEP-AUDIT",
                "architecture_boundaries": ["BOUNDARY-AUDIT"],
                "implementation_planes": ["PLANE-AUDIT"],
                "parallel_path_ids": [],
                "anchor_paths": ["audit.py"],
                "review_lenses": lenses,
                "design_section_ids": ["SECTION-CONTRACT"],
                "scope_rationale": "Own the independent audit publishing component.",
            },
        ],
    })
    risk_plan_digest = ac.sha256_file(state / "risk_sweep_plan.json")
    risk = {
        "observation_id": "RISK-API-001",
        "session_id": workspace["session_id"],
        "sweep_id": "RISK-SWEEP-API",
        "risk_sweep_plan_sha256": risk_plan_digest,
        "behavior_question": "What behavior is exposed when the public charge entry point is called?",
        "observed_code_behavior": "The public entry point accepts an amount and returns an accepted result without a guard.",
        "design_section_ids": ["SECTION-CONTRACT"],
        "design_alignment": "The section defines the behavior of the same public service entry point.",
        "review_lenses": lenses[:3],
        "architecture_boundaries": ["BOUNDARY-API"],
        "implementation_planes": ["PLANE-SERVICE"],
        "parallel_path_ids": [],
        "code_evidence": [{
            "file": "service.py", "line_start": 1, "line_end": 2,
            "symbol": "charge", "snippet": 'def charge(amount):\n    return {"accepted": True}',
        }],
        "false_positive_checks": [
            {
                "question": "Is a guard called before returning?", "method": "control-flow read",
                "target": "charge", "result": "The function returns directly.",
            },
            {
                "question": "Does another public charge entry point replace this one?", "method": "symbol search",
                "target": "service.py", "result": "No alternate charge entry point exists.",
            },
        ],
        "design_lookup_questions": [
            "Does the service contract constrain acceptance behavior for amount inputs?",
        ],
        "tool_trace": [
            {
                "seq": 1, "kind": "design_read", "tool": "read",
                "target": "contract.md:1-6", "purpose": "Read the assigned behavior section.",
                "result": "The section defines externally visible service behavior.",
            },
            {
                "seq": 2, "kind": "code_search", "tool": "search", "target": "charge",
                "purpose": "Locate the public entry point.", "result": "Found service.py:1.",
            },
            {
                "seq": 3, "kind": "code_read", "tool": "read", "target": "service.py:1-2",
                "purpose": "Derive the reachable behavior.", "result": "The function returns accepted directly.",
            },
            {
                "seq": 4, "kind": "reverse_check", "tool": "search", "target": "charge callers and alternatives",
                "purpose": "Check for a compensating path.", "result": "No alternate enforcement path exists.",
            },
        ],
    }
    audit_risk = {
        "observation_id": "RISK-AUDIT-001",
        "session_id": workspace["session_id"],
        "sweep_id": "RISK-SWEEP-AUDIT",
        "risk_sweep_plan_sha256": risk_plan_digest,
        "behavior_question": "What behavior is exposed when an audit event is published?",
        "observed_code_behavior": "The independent adapter immediately reports the event as published.",
        "design_section_ids": ["SECTION-CONTRACT"],
        "design_alignment": "The section defines externally visible service and audit behavior.",
        "review_lenses": lenses[:3],
        "architecture_boundaries": ["BOUNDARY-AUDIT"],
        "implementation_planes": ["PLANE-AUDIT"],
        "parallel_path_ids": [],
        "code_evidence": [{
            "file": "audit.py", "line_start": 1, "line_end": 2,
            "symbol": "publish_event",
            "snippet": 'def publish_event(event):\n    return {"published": True, "event": event}',
        }],
        "false_positive_checks": [
            {
                "question": "Is another publisher called first?", "method": "control-flow read",
                "target": "publish_event", "result": "The adapter returns directly.",
            },
            {
                "question": "Is this adapter unreachable?", "method": "entry review",
                "target": "audit.py", "result": "It is a public adapter function.",
            },
        ],
        "design_lookup_questions": [
            "Does the service contract constrain how audit publication is acknowledged?",
        ],
        "tool_trace": [
            {
                "seq": 1, "kind": "design_read", "tool": "read",
                "target": "contract.md:1-6", "purpose": "Read the assigned behavior section.",
                "result": "The section defines externally visible audit behavior.",
            },
            {
                "seq": 2, "kind": "code_search", "tool": "search",
                "target": "publish_event", "purpose": "Locate the adapter entry point.",
                "result": "Found audit.py:1.",
            },
            {
                "seq": 3, "kind": "code_read", "tool": "read",
                "target": "audit.py:1-2", "purpose": "Derive the adapter behavior.",
                "result": "The adapter reports publication directly.",
            },
            {
                "seq": 4, "kind": "reverse_check", "tool": "search",
                "target": "audit publisher alternatives", "purpose": "Check compensation paths.",
                "result": "No alternate publisher exists.",
            },
        ],
    }
    risk_observations: list[dict] = []
    risk_sources: list[tuple[str, dict]] = []
    if count >= 2:
        risk_sources.append(("RISK-API", risk))
    if count >= 3:
        risk_sources.append(("RISK-AUDIT", audit_risk))
    for prefix, base in risk_sources:
        for chunk_index, start in enumerate(range(0, len(lenses), 3), start=1):
            item = dict(base)
            item["observation_id"] = f"{prefix}-{chunk_index:03d}"
            item["review_lenses"] = lenses[start:start + 3]
            item["behavior_question"] = (
                f"Under the assigned lens group {chunk_index}, "
                + str(base["behavior_question"])
            )
            risk_observations.append(item)
            append(state / "risk_observations.jsonl", item)
    inventory = design_source_materializer.materialize_inventory({
        "session_id": workspace["session_id"],
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "scope_relation": "required",
            "scope_evidence": {
                "source_ref": {
                    "path": "contract.md", "line_start": 1, "line_end": 3,
                },
            },
            "sections": [{
                "section_id": "SECTION-CONTRACT",
                "source_ref": {
                    "path": "contract.md", "line_start": 1, "line_end": 6,
                },
                "behavior_families": ["externally visible service contract"],
                "ambiguities": [],
            }],
        }],
    }, Path(workspace["design"]))
    ac.save_json(state / "design_inventory.json", inventory)
    specs = [
        (3, "The service must reject negative amounts.", 1, 2, "charge", 'def charge(amount):\n    return {"accepted": True}'),
        (4, "The service must expire sessions after 30 minutes.", 4, 5, "session_expired", "def session_expired(minutes):\n    return minutes > 60"),
        (5, "The service must deny exports for guest users.", 7, 8, "can_export", "def can_export(role):\n    return True"),
        (6, "The service must preserve all submitted audit events.", 10, 11, "record_event", "def record_event(events, event):\n    return events[-9:] + [event]"),
    ]
    for index, (design_line, quote, code_start, code_end, symbol, snippet) in enumerate(specs[:count], start=1):
        claim_id = f"CLAIM-{index:03d}"
        task_id = f"TASK-{index:03d}"
        finding_id = f"FINDING-{task_id}"
        review_id = f"CRITIC-{index:03d}"
        claim = design_source_materializer.materialize_claims([{
            "claim_id": claim_id,
            "session_id": workspace["session_id"],
            "source_ref": {
                "path": "contract.md",
                "line_start": design_line,
                "line_end": design_line,
            },
            "document_key": "contract",
            "subject": "The public service implementation",
            "trigger": f"A caller invokes {symbol} with an input covered by the contract.",
            "obligation": quote,
            "exceptions": [],
            "observable_result": quote,
            "behavior_family": "externally visible service contract",
            "normative_strength": "mandatory",
            "applicability": "service implementation",
            "priority": "high",
            "ambiguities": [],
            "probe_oracle": {
                "testability": "candidate",
                "preconditions": ["The public service function is callable."],
                "stimulus": f"Call {symbol} with an input covered by the requirement.",
                "expected_observation": quote,
                "non_testable_reason": "",
            },
        }], Path(workspace["design"]))[0]
        append(state / "design_claims.jsonl", claim)
        task_lenses = (
            lenses[index - 1::count]
            if count >= 3 else lenses[(index - 1) * 2:index * 2]
        )
        audit_scope = index >= 3
        task_boundary = "BOUNDARY-AUDIT" if audit_scope else "BOUNDARY-API"
        task_plane = "PLANE-AUDIT" if audit_scope else "PLANE-SERVICE"
        task_risk = "RISK-AUDIT-001" if audit_scope else "RISK-API-001"
        exploration_modes = contract["coverage_contract"]["exploration_modes"]
        task_mode = [
            exploration_modes[0], exploration_modes[1],
            exploration_modes[2], exploration_modes[1],
        ][index - 1]
        task_boundaries = [task_boundary]
        task_planes = [task_plane]
        task_risk_ids = [task_risk] if (
            task_mode == "code-to-design risk backtracking"
        ) else []
        if count == 3 and index == 3:
            task_mode = "code-to-design risk backtracking"
            task_risk_ids = ["RISK-AUDIT-001"]
        hypothesis = ac.canonical_claim_hypothesis(claim)
        obligation_sha256 = stage_artifact_validator.claim_obligation_sha256(claim)
        append(state / "investigation_tasks.jsonl", {
            "task_id": task_id,
            "session_id": workspace["session_id"],
            "claim_id": claim_id,
            "claim_branch": ac.canonical_claim_branch(claim),
            "hypothesis": hypothesis,
            "obligation_sha256": obligation_sha256,
            "starting_points": ["public service entry point"],
            "supporting_evidence_needed": ["reachable implementation"],
            "disconfirming_evidence_needed": ["alternate enforcement path"],
            "status": "complete",
            "defer_reason": "",
            "review_lenses": task_lenses,
            "exploration_mode": task_mode,
            "architecture_boundaries": task_boundaries,
            "implementation_planes": task_planes,
            "parallel_path_ids": [],
            "risk_observation_ids": task_risk_ids,
        })
        finding = {
            "finding_id": finding_id,
            "session_id": workspace["session_id"],
            "task_id": task_id,
            "claim_id": claim_id,
            "claim_branch": ac.canonical_claim_branch(claim),
            "obligation_sha256": obligation_sha256,
            "hypothesis": hypothesis,
            "expected_behavior": f"{quote} Observable result: {quote}",
            "observed_behavior": "The cited implementation permits behavior the design forbids.",
            "design_evidence": [{
                "document": claim["document"], "path": "contract.md", "section": claim["section"],
                "line_start": design_line, "line_end": design_line, "quote": quote,
            }],
            "code_evidence": [{
                "file": "service.py", "line_start": code_start, "line_end": code_end,
                "symbol": symbol, "snippet": snippet,
            }],
            "supporting_evidence": ["reachable source branch"],
            "disconfirming_evidence": [],
            "false_positive_checks": [
                {"question": "Is there another enforcement path?", "method": "call search", "target": symbol, "result": "No alternate path."},
                {"question": "Is the branch unreachable?", "method": "entry review", "target": "service.py", "result": "The function is directly reachable."},
            ],
            "tool_trace": [
                {"seq": 1, "kind": "design_read", "tool": "read", "target": f"contract.md:{design_line}", "purpose": "Verify requirement.", "result": quote},
                {"seq": 2, "kind": "code_search", "tool": "search", "target": symbol, "purpose": "Locate implementation.", "result": "Found service.py."},
                {"seq": 3, "kind": "code_read", "tool": "read", "target": f"service.py:{code_start}-{code_end}", "purpose": "Derive behavior.", "result": "Observed contradictory return."},
                {"seq": 4, "kind": "reverse_check", "tool": "search", "target": symbol, "purpose": "Find alternate enforcement.", "result": "No alternate path."},
            ],
            "dynamic_probe_selection": {
                "disposition": "not_selected",
                "reason": "Static source and reachability evidence is already direct in this small fixture.",
            },
            "recommendation": "critic_review",
            "assessment": "contradiction_supported",
            "review_lenses": task_lenses,
        }
        append(state / "investigation_findings.jsonl", finding)
        critic = {
            "review_id": review_id,
            "session_id": workspace["session_id"],
            "finding_id": finding_id,
            "claim_id": claim_id,
            "decision": "confirm_contradiction",
            "normative_assessment": {
                "claim_strength": "mandatory",
                "applicability": "supported",
                "obligation_status": "binding_required",
                "actual_conflict": "yes",
                "rationale": "The applicable mandatory contract directly conflicts with the reachable implementation.",
            },
            "challenges": [
                "Could another reachable path enforce the claim?",
                "Could configuration or scope make the cited branch inapplicable?",
            ],
            "checks_performed": ["Reviewed callers and adjacent implementation.", "Re-read the cited contract lines."],
            "review_context": "fresh_subagent",
            "dynamic_probe_review": {
                "status": "not_run",
                "probe_id": "",
                "oracle_validity": "The claim oracle is design-derived but no probe was selected.",
                "environment_validity": "No dynamic environment result was used.",
                "reachability": "Reachability was checked statically through the public function.",
                "effect_on_decision": "The decision relies on independently reviewed design and source evidence.",
            },
            "resolution": "No alternate enforcement path exists in the fixture.",
            "remaining_risks": [],
        }
        critic = handoff_merge.materialize_critic_bindings(
            critic, state, f"critic ({finding_id})",
        )
        append(state / "critic_reviews.jsonl", critic)
        record_critic_history(state, critic)
        design_quote = "invented quote" if bad_quote and index == 1 else quote
        append(state / "agent_review_verdicts.jsonl", {
            "finding_id": finding_id,
            "session_id": workspace["session_id"],
            "claim_id": claim_id,
            "status": "confirmed",
            "title": f"Contract behavior {index} is not enforced",
            "confidence": 0.92,
            "severity": "high",
            "issue_type": "contradictory_behavior",
            "design_evidence": ([{
                **finding["design_evidence"][0], "quote": design_quote,
            }]),
            "code_evidence": [{
                "file": "service.py", "line_start": code_start, "line_end": code_end,
                "symbol": symbol, "snippet": snippet,
            }],
            "expected_behavior": finding["expected_behavior"],
            "actual_behavior": finding["observed_behavior"],
            "inconsistency": "The implementation's reachable return value contradicts the stated requirement.",
            "impact": "A caller observes behavior forbidden by the design.",
            "scope_applicability": "The design names the service behavior implemented by this entry point.",
            "false_positive_checks": [
                {"question": "Is there another enforcement path?", "method": "call search", "target": symbol, "result": "No alternate path."},
                {"question": "Is the branch unreachable?", "method": "entry review", "target": "service.py", "result": "The function is directly reachable."},
            ],
            "dynamic_validation": {
                "status": "not_run",
                "probe_id": "",
                "reason": "A probe was not selected; the static design/code contradiction and reachability evidence are sufficient.",
            },
            "critic_review": {
                "review_id": review_id, "decision": "confirm_contradiction",
                "normative_assessment": critic["normative_assessment"],
                "challenges": critic["challenges"], "resolution": critic["resolution"],
                "review_context": critic["review_context"],
            },
            "tool_trace": [
                {"seq": 1, "kind": "design_read", "tool": "read", "target": f"contract.md:{design_line}", "purpose": "Verify requirement.", "result": quote},
                {"seq": 2, "kind": "code_search", "tool": "search", "target": symbol, "purpose": "Locate implementation.", "result": "Found service.py."},
                {"seq": 3, "kind": "code_read", "tool": "read", "target": f"service.py:{code_start}-{code_end}", "purpose": "Derive behavior.", "result": "Observed contradictory return."},
                {"seq": 4, "kind": "reverse_check", "tool": "search", "target": symbol, "purpose": "Find alternate enforcement.", "result": "No alternate path."},
            ],
            "generalization_rationale": "The finding is derived from supplied design and source evidence only.",
        })
    tasks_for_templates, _ = ac.load_jsonl(state / "investigation_tasks.jsonl")
    claims_for_templates, _ = ac.load_jsonl(state / "design_claims.jsonl")
    claims_by_id = {item["claim_id"]: item for item in claims_for_templates}
    for task in tasks_for_templates:
        ac.save_json(
            state / "handoff-templates" / "investigators" / f"{task['task_id']}.json",
            handoff_template.finding_template(task, claims_by_id[task["claim_id"]]),
        )
    ac.save_json(state / "design_coverage.json", {
        "session_id": workspace["session_id"],
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "disposition": "applicable",
            "evidence": "The supplied contract defines the service's observable behavior.",
            "claim_ids": [f"CLAIM-{index:03d}" for index in range(1, count + 1)],
            "behavior_families": ["externally visible service contract"],
        }],
    })
    ac.save_json(state / "claim_review_scope.json", {
        "session_id": workspace["session_id"],
        "round_id": "ROUND-001",
        "claim_ids": [f"CLAIM-{index:03d}" for index in range(1, count + 1)],
    })
    ac.save_json(state / "design_claim_review.json", {
        "session_id": workspace["session_id"],
        "input_digests": {
            "design_claims.jsonl": ac.sha256_file(state / "design_claims.jsonl"),
            "design_coverage.json": ac.sha256_file(state / "design_coverage.json"),
            "design_inventory.json": ac.sha256_file(state / "design_inventory.json"),
            "design_agent_manifest.json": ac.sha256_file(state / "design_agent_manifest.json"),
            "claim_review_scope.json": ac.sha256_file(state / "claim_review_scope.json"),
        },
        "claim_reviews": [{
            "session_id": workspace["session_id"],
            "claim_id": claim["claim_id"],
            "claim_sha256": handoff_merge.canonical_digest(claim),
            "source_sha256": claim["source_ref"]["source_sha256"],
            "spec_critic_prompt_version": "spec-critic-v2",
            "quote_entailment": {
                "assessment": "entailed", "rationale": "The quoted contract states this behavior directly.",
            },
            "normative_strength": {
                "assessment": "correct", "stated_strength": claim["normative_strength"],
                "recommended_strength": claim["normative_strength"],
                "rationale": "The fixture uses an explicit must requirement.",
            },
            "atomicity": {
                "assessment": "atomic", "obligations": [claim["obligation"]],
                "rationale": "The claim contains one independently observable obligation.",
            },
            "applicability": {
                "assessment": "supported", "rationale": "The supplied contract defines the service behavior.",
            },
            "decision": "accept", "repair_actions": [],
        } for claim in claims_for_templates],
        "group_reviews": [{
            "session_id": workspace["session_id"], "document_key": "contract",
            "group_sha256": inventory["document_groups"][0]["group_sha256"],
            "behavior_families": {
                "assessment": "complete", "missing_items": [],
                "rationale": "The fixture behavior family is represented.",
            },
            "roles": {
                "assessment": "complete", "missing_items": [],
                "rationale": "The fixture defines a single service role.",
            },
            "branches": {
                "assessment": "complete", "missing_items": [],
                "rationale": "Each fixture branch has its own claim.",
            },
            "decision": "accept", "repair_actions": [],
        }],
        "decision": "accept",
        "summary": "All fixture claims and the document group passed design-only review.",
    })
    lens_task_ids = {
        lens: str(task["task_id"])
        for task in tasks_for_templates for lens in task.get("review_lenses", [])
    }
    tasks_by_id = {str(task["task_id"]): task for task in tasks_for_templates}
    actual_modes = list(dict.fromkeys(
        str(task["exploration_mode"]) for task in tasks_for_templates
    ))
    missing_mode_gaps = [{
        "gap_id": f"GAP-MODE-{index:03d}",
        "kind": "exploration_mode",
        "ref_id": mode,
        "reason": "The bounded fixture frontier did not require this mode.",
        "evidence": "No fixture task selected this exploration mode.",
    } for index, mode in enumerate(
        sorted(set(contract["coverage_contract"]["exploration_modes"]) - set(actual_modes)),
        start=1,
    )]
    ac.save_json(state / "semantic_coverage.json", {
        "session_id": workspace["session_id"],
        "lenses": [{
            "lens": lens,
            "disposition": "investigated",
            "evidence": "The service fixture exposes this concern at its public API boundary.",
            "task_ids": [lens_task_ids.get(lens, "TASK-001")],
            "finding_ids": [f"FINDING-{lens_task_ids.get(lens, 'TASK-001')}"],
            "design_group_refs": ["contract"],
            "boundary_refs": tasks_by_id.get(
                lens_task_ids.get(lens, "TASK-001"), {},
            ).get("architecture_boundaries", ["BOUNDARY-API"]),
            "counterfactual": "",
        } for index, lens in enumerate(lenses)],
    })
    append(state / "investigation_rounds.jsonl", {
        "session_id": workspace["session_id"],
        "round_id": "ROUND-001",
        "strategy": "Check externally visible service behaviors.",
        "exploration_modes": actual_modes,
        "document_groups": ["contract"],
        "architecture_boundaries": (
            ["BOUNDARY-API", "BOUNDARY-AUDIT"] if count >= 3 else ["BOUNDARY-API"]
        ),
        "implementation_planes": (
            ["PLANE-SERVICE", "PLANE-AUDIT"] if count >= 3 else ["PLANE-SERVICE"]
        ),
        "lenses": lenses,
        "claim_ids": [f"CLAIM-{index:03d}" for index in range(1, count + 1)],
        "task_ids": [f"TASK-{index:03d}" for index in range(1, count + 1)],
        "finding_ids": [f"FINDING-TASK-{index:03d}" for index in range(1, count + 1)],
        "outcome": "Four contradictions independently verified.",
        "next_strategy": "finalize",
    })
    ac.save_json(state / "coverage_audit.json", {
        "session_id": workspace["session_id"],
        "design_documents_reviewed": ["contract.md"],
        "claims_total": count,
        "claims_investigated": count,
        "rounds_completed": 1,
        "exploration_modes_completed": actual_modes,
        "document_groups_total": 1,
        "document_groups_accounted": 1,
        "code_areas_reviewed": ["service.py", "audit.py"],
        "architecture_boundaries": [
            {
                "boundary_id": "BOUNDARY-API", "status": "investigated",
                "evidence": "The public service entry points were inspected.",
            },
            {
                "boundary_id": "BOUNDARY-AUDIT", "status": "investigated",
                "evidence": "The independent audit adapter was inspected.",
            },
        ],
        "remaining_scoped_claims": [],
        "deferred_claims": [],
        "false_positive_samples_rechecked": [f"FINDING-TASK-{index:03d}" for index in range(1, count + 1)],
        "next_round_tasks": [],
        "supplement_rounds": 0,
        "remaining_gaps": missing_mode_gaps,
        "stop_reason": "All high-priority fixture claims were investigated and independently reviewed.",
    })
    task_ids = [f"TASK-{index:03d}" for index in range(1, count + 1)]
    finding_ids = [f"FINDING-TASK-{index:03d}" for index in range(1, count + 1)]
    trace = Path(workspace["logs"]) / "trace"
    task_report = trace / "task-handoff-merge.json"
    finding_report = trace / "finding-merge-TEST.json"
    critic_report = trace / "critic-handoff-merge.json"
    risk_report = trace / "risk-handoff-merge.json"
    ac.save_json(task_report, {
        "passed": True, "artifact_type": "task", "errors": [], "validated_ids": task_ids,
        "ledger_sha256": ac.sha256_file(state / "investigation_tasks.jsonl"),
        "task_plan_ledger_sha256": handoff_merge.task_plan_ledger_sha256(
            {task["task_id"]: task for task in tasks_for_templates}
        ),
    })
    ac.save_json(finding_report, {
        "passed": True, "artifact_type": "finding", "errors": [], "validated_ids": finding_ids,
        "expected_ids": finding_ids, "missing_ids": [],
        "ledger_sha256": ac.sha256_file(state / "investigation_findings.jsonl"),
    })
    ac.save_json(critic_report, {
        "passed": True, "artifact_type": "critic", "errors": [], "validated_ids": finding_ids,
        "ledger_sha256": ac.sha256_file(state / "critic_reviews.jsonl"),
    })
    ac.save_json(risk_report, {
        "passed": True, "artifact_type": "risk", "errors": [],
        "validated_ids": [item["observation_id"] for item in risk_observations],
        "expected_sweep_ids": ["RISK-SWEEP-API", "RISK-SWEEP-AUDIT"],
        "submitted_sweep_ids": ["RISK-SWEEP-API", "RISK-SWEEP-AUDIT"],
        "validated_sweep_ids": ["RISK-SWEEP-API", "RISK-SWEEP-AUDIT"],
        "completed_sweep_ids": ["RISK-SWEEP-API", "RISK-SWEEP-AUDIT"],
        "missing_sweep_ids": [],
        "closed": True,
        "global_coverage_validated": True,
        "risk_sweep_plan_sha256": risk_plan_digest,
        "architecture_map_sha256": architecture_digest,
        "ledger_sha256": ac.sha256_file(state / "risk_observations.jsonl"),
    })
    provenance = {
        "task": (task_ids, state / "investigation_tasks.jsonl", task_report, "investigation_planning"),
        "risk": (
            [item["observation_id"] for item in risk_observations],
            state / "risk_observations.jsonl", risk_report, "code_risk_backtracking",
        ),
        "finding": (finding_ids, state / "investigation_findings.jsonl", finding_report, "investigation"),
        "critic": (finding_ids, state / "critic_reviews.jsonl", critic_report, "critic_review"),
    }
    for artifact_type, (validated_ids, ledger_path, report_path, phase) in provenance.items():
        event = {
            "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
            "event": "handoff_merge", "actor": "fixture_handoff_merge",
            "phase": phase,
            "status": "complete", "artifact_type": artifact_type,
            "validated_ids": validated_ids, "report": str(report_path),
            "report_sha256": ac.sha256_file(report_path),
            "ledger_sha256": ac.sha256_file(ledger_path),
        }
        if artifact_type == "task":
            event["task_plan_ledger_sha256"] = handoff_merge.task_plan_ledger_sha256(
                {task["task_id"]: task for task in tasks_for_templates}
            )
        ac.append_jsonl(state / "agent_run_ledger.jsonl", event)
    record_trace_checkpoint(
        state, str(workspace["session_id"]), "architecture_mapping", "orchestrator",
        scope_id="ARCHITECTURE-MAP",
    )
    record_trace_checkpoint(
        state, str(workspace["session_id"]), "design_inventory", "spec-analyst",
        scope_id="DESIGN-INVENTORY",
    )
    for round_id in sorted({item["round_id"] for item in ac.load_jsonl(
        state / "investigation_rounds.jsonl",
    )[0]}):
        record_trace_checkpoint(
            state, str(workspace["session_id"]),
            "design_claim_resolution", "spec-analyst", scope_id=round_id,
        )
        record_trace_checkpoint(
            state, str(workspace["session_id"]),
            "design_claim_review", "spec-critic", scope_id=round_id,
        )
        record_trace_checkpoint(
            state, str(workspace["session_id"]),
            "investigation_planning", "orchestrator", scope_id=round_id,
        )
    record_trace_checkpoint(
        state, str(workspace["session_id"]), "coverage_audit", "coverage-critic",
        scope_id="COVERAGE-AUDIT-FINAL",
    )
    record_trace_checkpoint(
        state, str(workspace["session_id"]), "final_judgement", "final-judge",
        scope_id="FINAL-JUDGEMENT",
    )
    for slice_item in ac.load_json(state / "risk_sweep_plan.json")["slices"]:
        record_trace_checkpoint(
            state, str(workspace["session_id"]),
            "code_risk_backtracking", "risk-explorer",
            task_id=slice_item["sweep_id"],
        )
    for task in tasks_for_templates:
        record_trace_checkpoint(
            state, str(workspace["session_id"]),
            "investigation", "code-investigator", task_id=task["task_id"],
        )
    for finding_id in finding_ids:
        record_trace_checkpoint(
            state, str(workspace["session_id"]),
            "critic_review", "evidence-critic", task_id=finding_id,
        )
    validation_commands = [
        "architecture-check", "risk-plan-check", "design-check", "claim-check", "task-check",
    ]
    if count >= 3:
        validation_commands.append("coverage-check")
    for command in validation_commands:
        run_runner(
            command, Path(workspace["code"]), Path(workspace["design"]),
            Path(workspace["result"]), Path(workspace["logs"]),
        )


def attach_dynamic_probe(
    workspace: dict[str, Path | str],
    *,
    interpretation: str = "supports_contradiction",
    baseline_status: str = "passed",
    execution_status: str = "completed",
    target_reached: bool = True,
    validate_coverage: bool = True,
) -> dict:
    state = workspace["state"]
    assert isinstance(state, Path)
    claims, _ = ac.load_jsonl(state / "design_claims.jsonl")
    claim = claims[0]
    finding_id = "FINDING-TASK-001"
    probe_id = "PROBE-001"
    probe_workspace = state / "probes" / probe_id / "workspace"
    probe_workspace.mkdir(parents=True)
    findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
    review_code_root = Path(
        ac.load_json(state / "workspace_manifest.json")["paths"]["review_code_root"]
    )
    for evidence in findings[0]["code_evidence"]:
        relative = Path(evidence["file"])
        target = probe_workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((review_code_root / relative).read_bytes())
    probe = {
        "probe_id": probe_id,
        "session_id": workspace["session_id"],
        "finding_id": finding_id,
        "claim_id": claim["claim_id"],
        "oracle": {
            "source": "design_claim",
            "claim_id": claim["claim_id"],
            "claim_sha256": handoff_merge.canonical_digest(claim),
            "source_sha256": claim["source_ref"]["source_sha256"],
            "preconditions": claim["probe_oracle"]["preconditions"],
            "stimulus": claim["probe_oracle"]["stimulus"],
            "expected_observation": claim["probe_oracle"]["expected_observation"],
        },
        "oracle_validation": {
            "non_triviality": {
                "status": "passed",
                "method": "Run a deliberately satisfying and deliberately failing control input.",
                "result": "The focused oracle distinguishes the two controls.",
            },
            "secondary_oracle": {
                "kind": "negative_control",
                "status": "passed",
                "command": "python focused_probe.py --negative-control",
                "result": "The negative control rejected the deliberately invalid observation.",
            },
            "evidence_role": "corroborating",
        },
        "selection_reason": "The public function is observable and the existing Python runtime is available.",
        "isolation": {
            "kind": "session_copy",
            "workspace": str(probe_workspace),
            "command_cwd": str(probe_workspace),
            "original_target_unchanged": True,
        },
        "baseline": {
            "status": baseline_status,
            "command": "python -m pytest -q" if baseline_status != "not_available" else "",
            "result": "Baseline completed with the requested status.",
        },
        "execution": {
            "status": execution_status,
            "command": "python focused_probe.py" if execution_status == "completed" else "",
            "exit_code": 1 if execution_status == "completed" else None,
            "observed": "The focused observation was captured without writing the original target.",
            "target_reached": target_reached,
        },
        "interpretation": interpretation,
        "limitations": [],
        "tool_trace": [
            {
                "seq": 1, "kind": "build_read", "tool": "read", "target": "project test metadata",
                "purpose": "Select an existing baseline.", "result": "Python test entrypoint is available.",
            },
            {
                "seq": 2, "kind": "test", "tool": "bash", "target": str(probe_workspace),
                "purpose": "Run the design-grounded focused probe.", "result": "Captured the scoped observation.",
            },
        ],
    }
    append(state / "dynamic_probes.jsonl", probe)

    findings[0]["dynamic_probe_selection"] = {
        "disposition": "selected",
        "reason": "The candidate is high-value, observable, low-cost, and runnable with existing dependencies.",
    }
    (state / "investigation_findings.jsonl").write_text(
        "\n".join(json.dumps(item) for item in findings) + "\n", encoding="utf-8"
    )

    critiques, _ = ac.load_jsonl(state / "critic_reviews.jsonl")
    critiques[0]["dynamic_probe_review"] = {
        "status": interpretation,
        "probe_id": probe_id,
        "oracle_validity": "The oracle exactly matches the design claim.",
        "environment_validity": "The recorded baseline used the repository's available runtime.",
        "reachability": "The probe records whether the mapped target path was reached.",
        "effect_on_decision": "The scoped result supplements but does not replace static evidence.",
    }
    critiques[0].pop("input_digests", None)
    critiques[0].pop("evidence_critic_prompt_version", None)
    critiques[0] = handoff_merge.materialize_critic_bindings(
        critiques[0], state, f"critic ({finding_id})",
    )
    (state / "critic_reviews.jsonl").write_text(
        "\n".join(json.dumps(item) for item in critiques) + "\n", encoding="utf-8"
    )
    record_critic_history(state, critiques[0])

    verdicts, _ = ac.load_jsonl(state / "agent_review_verdicts.jsonl")
    verdicts[0]["dynamic_validation"] = {
        "status": interpretation,
        "probe_id": probe_id,
        "reason": "A design-grounded isolated probe was independently reviewed as scoped supporting evidence.",
    }
    (state / "agent_review_verdicts.jsonl").write_text(
        "\n".join(json.dumps(item) for item in verdicts) + "\n", encoding="utf-8"
    )
    trace = Path(workspace["logs"]) / "trace"
    for artifact_type, ledger_name, report_name, validated_ids in (
        (
            "finding", "investigation_findings.jsonl", "finding-merge-TEST.json",
            [f"FINDING-TASK-{index:03d}" for index in range(1, len(findings) + 1)],
        ),
        (
            "critic", "critic_reviews.jsonl", "critic-handoff-merge.json",
            [f"FINDING-TASK-{index:03d}" for index in range(1, len(critiques) + 1)],
        ),
    ):
        ledger_path = state / ledger_name
        report_path = trace / report_name
        report = ac.load_json(report_path)
        report["ledger_sha256"] = ac.sha256_file(ledger_path)
        ac.save_json(report_path, report)
        ac.append_jsonl(state / "agent_run_ledger.jsonl", {
            "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
            "event": "handoff_merge", "actor": "fixture_handoff_merge",
            "phase": "investigation" if artifact_type == "finding" else "critic_review",
            "status": "complete", "artifact_type": artifact_type,
            "validated_ids": validated_ids, "report": str(report_path),
            "report_sha256": ac.sha256_file(report_path),
            "ledger_sha256": ac.sha256_file(ledger_path),
        })
    probe_report = trace / "probe-handoff-merge.json"
    ac.save_json(probe_report, {
        "passed": True, "artifact_type": "probe", "errors": [], "validated_ids": [probe_id],
        "ledger_sha256": ac.sha256_file(state / "dynamic_probes.jsonl"),
    })
    ac.append_jsonl(state / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
        "event": "handoff_merge", "actor": "fixture_handoff_merge",
        "phase": "dynamic_probe", "status": "complete", "artifact_type": "probe",
        "validated_ids": [probe_id], "report": str(probe_report),
        "report_sha256": ac.sha256_file(probe_report),
        "ledger_sha256": ac.sha256_file(state / "dynamic_probes.jsonl"),
    })
    current_critic_report = trace / "critic-handoff-merge.json"
    ac.append_jsonl(state / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
        "event": "handoff_merge", "actor": "fixture_handoff_merge",
        "phase": "critic_review", "status": "complete", "artifact_type": "critic",
        "validated_ids": [
            f"FINDING-TASK-{index:03d}" for index in range(1, len(critiques) + 1)
        ],
        "report": str(current_critic_report),
        "report_sha256": ac.sha256_file(current_critic_report),
        "ledger_sha256": ac.sha256_file(state / "critic_reviews.jsonl"),
    })
    ac.append_jsonl(state / "approval_events.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
        "actor": "fixture_handoff_merge", "action": "focused_dynamic_probe",
        "scope": str(state / "probes"), "decision": "auto_approved",
        "rationale": "The fixture probe is confined to a session-owned copy.",
    })
    record_trace_checkpoint(
        state, str(workspace["session_id"]), "dynamic_probe", "code-investigator",
        task_id=finding_id,
    )
    if validate_coverage:
        run_runner(
            "coverage-check", Path(workspace["code"]), Path(workspace["design"]),
            Path(workspace["result"]), Path(workspace["logs"]),
        )
    return probe


def test_prepare_is_semantic_neutral_and_writes_agent_contract(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    manifest = ac.load_json(state / "workspace_manifest.json")
    design_manifest = ac.load_json(state / "design_agent_manifest.json")
    contract = ac.load_json(state / "agent_loop_contract.json")
    assert manifest["semantic_analysis_performed"] is False
    assert manifest["design"]["document_count"] == 1
    assert manifest["code"]["suffix_counts"] == {".py": 2}
    assert design_manifest["session_id"] == manifest["session_id"]
    assert design_manifest["design"]["document_groups"] == manifest["design"]["document_groups"]
    assert "code" not in design_manifest
    assert "paths" not in design_manifest
    assert "code_root" not in json.dumps(design_manifest)
    assert contract["execution_model"] == "opencode-owned-model-driven-loop"
    assert contract["contract_version"] == 19
    assert contract["handoff_integrity"]["max_concurrent_subagent_tasks"] == 2
    assert contract["tool_protocol"]["agent_event_contract"]["required_fields"] == [
        "event", "role", "phase", "scope_id", "scope",
        "input_artifacts", "input_sha256", "artifacts",
        "artifact_snapshots", "artifact_sha256",
        "started_at", "ended_at", "wall_time_seconds",
        "provider_attempt", "provider_session_id", "output_count",
        "repair_count", "outcome", "stop_reason",
    ]
    assert len(contract["coverage_contract"]["exploration_modes"]) == 3
    assert "dynamic_probe" in contract["coverage_contract"]
    assert [phase["owner"] for phase in contract["phases"]][:10] == [
        "orchestrator", "spec-analyst", "risk-explorer",
        "spec-analyst", "spec-critic", "orchestrator",
        "code-investigator", "code-investigator", "evidence-critic",
        "coverage-critic",
    ]
    assert (state / "risk_observations.jsonl").is_file()
    assert (state / "dynamic_probes.jsonl").is_file()
    loop_state = ac.load_json(state / "agent_loop_state.json")
    run_clock = ac.load_json(state / "run_clock.json")
    assert loop_state["started_at"] == run_clock["started_at"]
    assert loop_state["deadline_at"] == run_clock["deadline_at"]
    assert int((
        ac.parse_iso(loop_state["deadline_at"]) - ac.parse_iso(loop_state["started_at"])
    ).total_seconds()) == 21600
    assert not (state / "candidate_issues.json").exists()
    approvals, approval_errors = ac.load_jsonl(state / "approval_events.jsonl")
    assert approval_errors == []
    assert {(item["action"], item["decision"]) for item in approvals} >= {
        ("review_snapshot_read", "auto_approved"),
        ("session_artifact_write", "auto_approved"),
        ("target_source_write", "denied"),
        ("external_side_effect", "external_approval_required"),
    }


def test_risk_plan_check_revalidates_repaired_architecture(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    architecture_path = state / "architecture_map.json"
    architecture = ac.load_json(architecture_path)
    architecture["integration_boundaries"][0]["plane_ids"] = ["PLANE-UNKNOWN"]
    ac.save_json(architecture_path, architecture)
    plan_path = state / "risk_sweep_plan.json"
    plan = ac.load_json(plan_path)
    plan["architecture_map_sha256"] = ac.sha256_file(architecture_path)
    ac.save_json(plan_path, plan)

    proc = run_runner(
        "risk-plan-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    trace = ac.load_json(Path(workspace["logs"]) / "trace" / "architecture_validation.json")
    assert any("unknown plane_ids ['PLANE-UNKNOWN']" in error for error in trace["errors"])


def test_goal_runner_stops_helpers_after_persisted_session_deadline(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    loop_state = ac.load_json(state / "agent_loop_state.json")
    run_clock = ac.load_json(state / "run_clock.json")
    run_clock["started_at"] = "1999-12-31T18:00:00Z"
    run_clock["deadline_at"] = "2000-01-01T00:00:00Z"
    ac.save_json(state / "run_clock.json", run_clock)
    loop_state["started_at"] = run_clock["started_at"]
    loop_state["deadline_at"] = "2000-01-01T00:00:00Z"
    ac.save_json(state / "agent_loop_state.json", loop_state)

    proc = run_runner(
        "design-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )
    assert proc.returncode == 124
    blocked = ac.load_json(state / "agent_loop_state.json")
    assert blocked["status"] == "blocked"
    assert blocked["stop_reason"] == "hard_deadline_reached"
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    assert ledger[-1]["event"] == "helper_timeout"


def test_prepare_ignores_binary_design_when_text_export_is_available(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    code.mkdir()
    design.mkdir()
    (code / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (design / "spec.md").write_text("The value must be one.\n", encoding="utf-8")
    (design / "appendix.md").write_text(
        "Text export of the appendix with stable lines.\n", encoding="utf-8",
    )
    (design / "appendix.pdf").write_bytes(b"%PDF-1.4\nnot-a-text-evidence-source")
    proc = run_runner("prepare", code, design, result, logs, check=False)
    assert proc.returncode == 0
    manifest = ac.load_json(logs / "state" / "workspace_manifest.json")
    assert {item["path"] for item in manifest["design"]["documents"]} == {
        "appendix.md", "spec.md",
    }


def test_prepare_rejects_design_root_with_only_binary_documents(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    code.mkdir()
    design.mkdir()
    (code / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (design / "spec.pdf").write_bytes(b"%PDF-1.4\nnot-a-text-evidence-source")
    proc = run_runner(
        "prepare", code, design, tmp_path / "result", tmp_path / "logs", check=False,
    )
    assert proc.returncode == 1
    preflight = ac.load_json(tmp_path / "logs" / "trace" / "session_prepared.json")
    assert any("only binary PDF/DOCX" in item for item in preflight["problems"])


def test_prepare_rejects_invalid_utf8_design_disguised_as_text(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    code.mkdir()
    design.mkdir()
    (code / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (design / "spec.md").write_bytes(b"design text\xff\xfe")
    proc = run_runner("prepare", code, design, tmp_path / "result", tmp_path / "logs", check=False)
    assert proc.returncode == 1
    trace = ac.load_json(tmp_path / "logs" / "trace" / "session_prepared.json")
    assert any("not valid UTF-8 text" in item for item in trace["problems"])


def test_goal_runner_refuses_ambiguous_automatic_input_roots(tmp_path):
    assets = tmp_path / "assets"
    (assets / "code" / "one").mkdir(parents=True)
    (assets / "code" / "two").mkdir()
    (assets / "design").mkdir()
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "goal_runner.py"), "prepare",
        "--asset-root", str(assets), "--result-root", str(tmp_path / "result"),
        "--log-root", str(tmp_path / "logs"),
    ], text=True, capture_output=True)
    assert proc.returncode == 2
    assert "requires exactly one project directory" in proc.stderr


def test_goal_runner_does_not_prefer_one_named_design_directory_over_another(tmp_path):
    assets = tmp_path / "assets"
    (assets / "code" / "project").mkdir(parents=True)
    (assets / "design").mkdir()
    (assets / "appendices").mkdir()
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "goal_runner.py"), "prepare",
        "--asset-root", str(assets), "--result-root", str(tmp_path / "result"),
        "--log-root", str(tmp_path / "logs"),
    ], text=True, capture_output=True)
    assert proc.returncode == 2
    assert "automatic design-root discovery is ambiguous" in proc.stderr


def test_prepare_materializes_session_local_review_inputs(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    manifest = ac.load_json(state / "workspace_manifest.json")
    contract = ac.load_json(state / "agent_loop_contract.json")
    review_code = Path(manifest["paths"]["review_code_root"])
    review_design = Path(manifest["paths"]["review_design_root"])
    assert review_code.is_relative_to(state)
    assert review_design.is_relative_to(state)
    assert (review_code / "service.py").read_bytes() == (workspace["code"] / "service.py").read_bytes()
    assert (review_design / "contract.md").read_bytes() == (workspace["design"] / "contract.md").read_bytes()
    assert contract["guardrails"]["agent_read_roots"] == [str(review_code), str(review_design)]
    assert (review_code / ".git").is_file()
    git_probe = subprocess.run(
        ["git", "-C", str(review_code), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
    )
    assert git_probe.returncode != 0


def test_review_copy_keeps_source_directories_with_generic_build_names(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    code.mkdir()
    design.mkdir()
    (code / "build").mkdir()
    (code / "build" / "recipe.txt").write_text("build recipe\n", encoding="utf-8")
    (code / "vendor").mkdir()
    (code / "vendor" / "library.c").write_text("int imported(void);\n", encoding="utf-8")
    (code / "target").mkdir()
    (code / "target" / "generated.c").write_text("int generated(void);\n", encoding="utf-8")
    (design / "spec.md").write_text("The recipes must be preserved.\n", encoding="utf-8")
    run_runner("prepare", code, design, result, logs)
    manifest = ac.load_json(logs / "state" / "workspace_manifest.json")
    review_code = Path(manifest["paths"]["review_code_root"])
    assert (review_code / "build" / "recipe.txt").read_text(encoding="utf-8") == "build recipe\n"
    assert (review_code / "vendor" / "library.c").read_text(encoding="utf-8") == "int imported(void);\n"
    assert (review_code / "target" / "generated.c").read_text(encoding="utf-8") == "int generated(void);\n"


def test_prepare_preserves_internal_directory_symlinks_in_review_copy(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    (code / "actual").mkdir(parents=True)
    design.mkdir()
    (code / "actual" / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (code / "alias").symlink_to("actual", target_is_directory=True)
    (design / "spec.md").write_text("The value must be one.\n", encoding="utf-8")
    run_runner("prepare", code, design, result, logs)
    manifest = ac.load_json(logs / "state" / "workspace_manifest.json")
    records = {item["path"]: item for item in manifest["code"]["files"]}
    review_code = Path(manifest["paths"]["review_code_root"])
    assert records["alias"]["kind"] == "symlink"
    assert (review_code / "alias").is_symlink()
    assert (review_code / "alias").resolve() == review_code / "actual"


def test_prepare_does_not_follow_preexisting_review_parent_symlink(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    outside = tmp_path / "outside"
    code.mkdir()
    design.mkdir()
    outside.mkdir()
    (code / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (design / "spec.md").write_text("The value must be one.\n", encoding="utf-8")
    (outside / "marker.txt").write_text("keep\n", encoding="utf-8")
    (logs / "state").mkdir(parents=True)
    (logs / "state" / "review-inputs").symlink_to(outside, target_is_directory=True)
    proc = run_runner("prepare", code, design, result, logs, check=False)
    assert proc.returncode != 0
    assert (outside / "marker.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (outside / "code").exists()


def test_design_check_rejects_wrong_claim_schema_before_investigation(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    append(state / "design_claims.jsonl", {
        "claim_id": "CLAIM-BAD",
        "normative_level": "mandatory",
        "section_ref": "Section 1",
        "quote": "The service must reject negative amounts.",
        "quote_lines": "3",
        "probe_oracle": "call the service and expect rejection",
    })
    ac.save_json(state / "design_coverage.json", {
        "session_id": workspace["session_id"],
        "document_groups": [{
            "document_key": "contract", "members": ["contract.md"],
            "disposition": "applicable", "evidence": "The contract defines service behavior.",
            "claim_ids": ["CLAIM-BAD"], "behavior_families": ["service behavior"],
        }],
    })
    proc = run_runner(
        "design-check", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "design_validation.json")
    assert trace["passed"] is False
    assert any("probe_oracle must be an object" in error for error in trace["errors"])
    assert any("missing/empty session_id" in error for error in trace["errors"])


def test_design_check_accepts_valid_spec_analyst_artifacts(workspace):
    populate_handoffs(workspace, count=1)
    proc = run_runner(
        "design-check", workspace["code"], workspace["design"], workspace["result"], workspace["logs"]
    )
    trace = ac.load_json(workspace["logs"] / "trace" / "design_validation.json")
    assert trace["passed"] is True
    assert trace["metrics"]["claims"] == 1


def test_prepare_records_broken_symlinks_without_following_them(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    code.mkdir()
    design.mkdir()
    (code / "missing-link").symlink_to("does-not-exist")
    (design / "spec.md").write_text("A behavior is required.\n", encoding="utf-8")
    run_runner("prepare", code, design, result, logs)
    manifest = ac.load_json(logs / "state" / "workspace_manifest.json")
    assert manifest["code"]["files"] == [
        {
            "path": "missing-link", "suffix": "(none)", "bytes": len("does-not-exist"),
            "kind": "symlink", "link_target": "does-not-exist",
        }
    ]


def test_prepare_groups_multiple_formats_of_the_same_design_document(tmp_path):
    code = tmp_path / "code"
    design = tmp_path / "design"
    result = tmp_path / "result"
    logs = tmp_path / "logs"
    code.mkdir()
    design.mkdir()
    (code / "main.go").write_text("package main\n", encoding="utf-8")
    (design / "service.md").write_text("Service contract.\n", encoding="utf-8")
    (design / "service.txt").write_text("Service contract text mirror.\n", encoding="utf-8")
    run_runner("prepare", code, design, result, logs)
    manifest = ac.load_json(logs / "state" / "workspace_manifest.json")
    assert manifest["design"]["document_count"] == 2
    assert manifest["design"]["document_group_count"] == 1
    assert manifest["design"]["document_groups"] == [{
        "document_key": "service",
        "members": ["service.md", "service.txt"],
        "explicit_entry": False,
    }]


def test_design_source_materializer_only_copies_model_selected_sources(tmp_path):
    source = tmp_path / "input-design"
    bundle = tmp_path / "bundle"
    source.mkdir()
    (source / "benchmark.md").write_text(
        "Use service-contract.md as the service contract.\n", encoding="utf-8",
    )
    (source / "service-contract.md").write_text("Requests must be authenticated.\n", encoding="utf-8")
    plan = tmp_path / "design-source-plan.json"
    manifest = tmp_path / "materialization.json"
    ac.save_json(plan, {
        "catalog_path": "benchmark.md",
        "sources": [{
            "source_id": "service-contract",
            "kind": "local",
            "location": "service-contract.md",
            "output_path": "sources/service-contract.md",
            "catalog_evidence": {
                "path": "benchmark.md", "line_start": 1, "line_end": 1,
                "quote": "Use service-contract.md as the service contract.",
            },
        }],
    })
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "design_source_materializer.py"),
        "--source-root", str(source), "--plan", str(plan),
        "--output-root", str(bundle), "--manifest", str(manifest),
    ], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    report = ac.load_json(manifest)
    assert report["passed"] is True
    assert report["semantic_analysis_performed"] is False
    assert (bundle / "catalog" / "benchmark.md").is_file()
    assert (bundle / "sources" / "service-contract.md").read_text(encoding="utf-8") == (
        "Requests must be authenticated.\n"
    )
    assert {item["source_id"] for item in report["sources"]} == {"catalog", "service-contract"}


def test_design_source_materializer_normalizes_html_to_visible_text():
    raw = (
        b"<!doctype html><html><head><style>hidden{}</style><script>ignore()</script></head>"
        b"<body><h1>Service Contract</h1><pre>Requests MUST preserve\nall events.</pre></body></html>"
    )
    rendered, normalization = design_source_materializer._normalise_document_bytes(raw, "text/html")
    text = rendered.decode("utf-8")
    assert normalization == "html_visible_text"
    assert "Service Contract" in text
    assert "Requests MUST preserve\nall events." in text
    assert "hidden" not in text
    assert "ignore" not in text


def test_prepare_freezes_materialization_source_and_gate_accepts_unchanged_catalog(
    workspace, tmp_path,
):
    materialized = prepare_materialized_workspace(workspace, tmp_path)
    state = materialized["state"]
    source = materialized["source"]
    assert isinstance(state, Path)
    assert isinstance(source, Path)
    manifest = ac.load_json(state / "workspace_manifest.json")
    snapshot = manifest["design"]["materialization_source"]
    assert snapshot["source_root"] == str(source.resolve())
    assert snapshot["catalog_path"] == "catalog.list"
    assert snapshot["plan_path"] == str((state / "design_source_plan.json").resolve())
    assert snapshot["plan_sha256"] == ac.sha256_file(state / "design_source_plan.json")
    assert snapshot["file_count"] == len(snapshot["files"])
    assert {record["path"] for record in snapshot["files"]} == {
        "catalog.list", "contract.md",
    }
    assert all(
        record.get("sha256") for record in snapshot["files"]
        if record["kind"] == "file"
    )

    populate_handoffs(materialized)
    run_runner(
        "review", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    run_runner(
        "report", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    gate = run_runner(
        "gate", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    assert gate.returncode == 0
    verdict = ac.load_json(materialized["logs"] / "trace" / "final_gate.json")
    assert verdict["checks"]["supplied_design_source_unchanged"] is True
    assert verdict["checks"]["target_roots_unchanged"] is True


def test_design_source_materializer_rejects_unverifiable_catalog_evidence(tmp_path):
    source = tmp_path / "input-design"
    bundle = tmp_path / "bundle"
    source.mkdir()
    (source / "catalog.md").write_text("Use contract-a.md.\n", encoding="utf-8")
    (source / "contract-b.md").write_text("A different contract.\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    manifest = tmp_path / "manifest.json"
    ac.save_json(plan, {
        "catalog_path": "catalog.md",
        "sources": [{
            "source_id": "invented-source",
            "kind": "local",
            "location": "contract-b.md",
            "output_path": "sources/contract-b.md",
            "catalog_evidence": {
                "path": "catalog.md", "line_start": 1, "line_end": 1,
                "quote": "Use contract-b.md.",
            },
        }],
    })
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "design_source_materializer.py"),
        "--source-root", str(source), "--plan", str(plan),
        "--output-root", str(bundle), "--manifest", str(manifest),
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    report = ac.load_json(manifest)
    assert report["passed"] is False
    assert any("quote does not match" in error for error in report["errors"])


def test_design_source_materializer_requires_session_approval_log_for_network(tmp_path):
    source = tmp_path / "input-design"
    bundle = tmp_path / "state" / "design-sources"
    source.mkdir()
    (source / "catalog.md").write_text("Use https://example.invalid/spec.txt.\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    manifest = tmp_path / "manifest.json"
    ac.save_json(plan, {
        "catalog_path": "catalog.md",
        "sources": [{
            "source_id": "remote-spec",
            "kind": "url",
            "location": "https://example.invalid/spec.txt",
            "output_path": "sources/spec.txt",
            "catalog_evidence": {
                "path": "catalog.md", "line_start": 1, "line_end": 1,
                "quote": "Use https://example.invalid/spec.txt.",
            },
        }],
    })
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "design_source_materializer.py"),
        "--source-root", str(source), "--plan", str(plan),
        "--output-root", str(bundle), "--manifest", str(manifest),
        "--allow-network",
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    report = ac.load_json(manifest)
    assert any("requires --approval-log" in error for error in report["errors"])


def test_handoff_merge_atomically_combines_isolated_subagent_outputs(tmp_path):
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    output = tmp_path / "state" / "findings.jsonl"
    ac.save_json(handoffs / "task-b.json", {"finding_id": "FINDING-B", "assessment": "design_satisfied"})
    ac.save_json(handoffs / "task-a.json", {"finding_id": "FINDING-A", "assessment": "contradiction_supported"})
    metrics = handoff_merge.merge(handoffs, output, "finding_id")
    values, errors = ac.load_jsonl(output)
    assert errors == []
    assert metrics == {
        "files": 2, "imported": 2, "ledger_entries": 2,
        "validated_ids": ["FINDING-A", "FINDING-B"],
    }
    assert [item["finding_id"] for item in values] == ["FINDING-A", "FINDING-B"]


def test_handoff_merge_does_not_clobber_ledger_on_invalid_input(tmp_path):
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    output = tmp_path / "findings.jsonl"
    output.write_text('{"finding_id":"FINDING-KEEP"}\n', encoding="utf-8")
    (handoffs / "broken.json").write_text('{"finding_id":', encoding="utf-8")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        handoff_merge.merge(handoffs, output, "finding_id")
    assert output.read_text(encoding="utf-8") == '{"finding_id":"FINDING-KEEP"}\n'


def test_handoff_merge_rejects_issue_shaped_object_in_critic_handoff(tmp_path):
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    output = tmp_path / "critic_reviews.jsonl"
    output.write_text('', encoding="utf-8")
    ac.save_json(handoffs / "bad.json", {
        "finding_id": "FINDING-001",
        "session_id": "session-test",
        "status": "confirmed",
        "title": "This is a final issue, not a critic review",
    })
    with pytest.raises(ValueError, match="missing/empty review_id"):
        handoff_merge.merge(
            handoffs, output, "finding_id", artifact_type="critic", session_id="session-test"
        )
    assert output.read_text(encoding="utf-8") == ""


def test_typed_handoff_merge_rejects_an_incompatible_explicit_key(tmp_path):
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--input-dir", str(handoffs), "--output", str(tmp_path / "critics.jsonl"),
        "--artifact-type", "critic", "--key", "review_id", "--session-id", "session-test",
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    assert "requires key finding_id" in proc.stdout


def test_handoff_merge_rejects_malformed_finding_before_shared_ledger(tmp_path):
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    output = tmp_path / "investigation_findings.jsonl"
    ac.save_json(handoffs / "bad.json", {
        "finding_id": "FINDING-001",
        "session_id": "session-test",
        "task_id": "TASK-001",
        "claim_id": "CLAIM-001",
        "assessment": "contradiction_supported",
        "recommendation": "critic_review",
    })
    with pytest.raises(ValueError, match="dynamic_probe_selection"):
        handoff_merge.merge(
            handoffs, output, "finding_id", artifact_type="finding", session_id="session-test"
        )
    assert not output.exists()


def test_handoff_merge_rejects_false_source_excerpt_before_shared_ledger(workspace, tmp_path):
    state = workspace["state"]
    assert isinstance(state, Path)
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    output = tmp_path / "investigation_findings.jsonl"
    contract = ac.load_json(state / "agent_loop_contract.json")
    lens = contract["coverage_contract"]["portfolio_lenses"][0]
    ac.save_json(handoffs / "bad.json", {
        "finding_id": "FINDING-BAD", "session_id": workspace["session_id"],
        "task_id": "TASK-BAD", "claim_id": "CLAIM-BAD",
        "hypothesis": "The implementation differs.",
        "expected_behavior": "Reject negative amounts.",
        "observed_behavior": "Negative amounts are accepted.",
        "design_evidence": [{"path": "contract.md", "line_start": 3, "line_end": 3, "quote": "invented"}],
        "code_evidence": [{"file": "service.py", "line_start": 1, "line_end": 2, "snippet": "invented"}],
        "supporting_evidence": ["The entry point is reachable."],
        "disconfirming_evidence": [],
        "false_positive_checks": [
            {"question": "Alternate path?", "method": "search", "target": "service", "result": "None."},
            {"question": "Configuration?", "method": "read", "target": "service", "result": "None."},
        ],
        "tool_trace": [
            {"seq": 1, "kind": "design_read", "tool": "read", "target": "contract", "purpose": "read", "result": "read"},
            {"seq": 2, "kind": "code_search", "tool": "search", "target": "service", "purpose": "find", "result": "found"},
            {"seq": 3, "kind": "code_read", "tool": "read", "target": "service", "purpose": "read", "result": "read"},
            {"seq": 4, "kind": "reverse_check", "tool": "search", "target": "service", "purpose": "check", "result": "none"},
        ],
        "dynamic_probe_selection": {"disposition": "not_selected", "reason": "Static evidence should suffice."},
        "assessment": "contradiction_supported", "review_lenses": [lens], "recommendation": "critic_review",
    })
    with pytest.raises(ValueError, match="does not match cited source lines"):
        handoff_merge.merge(
            handoffs, output, "finding_id", artifact_type="finding",
            session_id=str(workspace["session_id"]), code_root=workspace["code"], design_root=workspace["design"],
        )
    assert not output.exists()


def test_finding_template_copies_only_task_and_claim_contract_fields(workspace, tmp_path):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, _ = ac.load_jsonl(state / "investigation_tasks.jsonl")
    claims, _ = ac.load_jsonl(state / "design_claims.jsonl")
    template = handoff_template.finding_template(tasks[0], claims[0])
    assert template["finding_id"] == "FINDING-TASK-001"
    assert template["expected_behavior"] == (
        f"{claims[0]['obligation']} Observable result: {claims[0]['observable_result']}"
    )
    assert template["claim_branch"] == tasks[0]["claim_branch"]
    assert template["obligation_sha256"] == tasks[0]["obligation_sha256"]
    assert template["design_evidence"] == [{
        "document": claims[0]["document"], "path": claims[0]["path"],
        "section": claims[0]["section"], "line_start": claims[0]["line_start"],
        "line_end": claims[0]["line_end"], "quote": claims[0]["quote"],
    }]
    assert template["review_lenses"] == tasks[0]["review_lenses"]
    assert template["false_positive_checks"] == [
        {"question": "", "method": "", "target": "", "result": ""},
        {"question": "", "method": "", "target": "", "result": ""},
    ]
    assert [step["kind"] for step in template["tool_trace"]] == [
        "design_read", "code_search", "code_read", "reverse_check",
    ]
    assert template["assessment"] == ""


def test_handoff_check_file_writes_machine_readable_failure_report(workspace, tmp_path):
    bad = tmp_path / "bad-finding.json"
    report = tmp_path / "finding-check.json"
    ac.save_json(bad, {
        "finding_id": "FINDING-BAD", "session_id": workspace["session_id"],
        "task_id": "TASK-BAD", "claim_id": "CLAIM-BAD",
    })
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--check-file", str(bad), "--artifact-type", "finding",
        "--session-id", str(workspace["session_id"]), "--report", str(report),
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    result = ac.load_json(report)
    assert result["passed"] is False
    assert result["invalid_ids"] == ["FINDING-BAD"]
    assert result["errors"]


def test_handoff_check_file_accepts_valid_finding_without_writing_ledger(workspace, tmp_path):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
    handoff = tmp_path / "finding.json"
    report = tmp_path / "finding-check.json"
    ac.save_json(handoff, findings[0])
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--check-file", str(handoff), "--artifact-type", "finding",
        "--session-id", str(workspace["session_id"]),
        "--code-root", str(workspace["code"]), "--design-root", str(workspace["design"]),
        "--report", str(report),
    ], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = ac.load_json(report)
    assert result["passed"] is True
    assert result["validated_ids"] == [findings[0]["finding_id"]]
    assert list(tmp_path.glob("*.jsonl")) == []


def test_finding_template_keeps_two_task_frontier_without_failed_peer_lock(workspace):
    populate_handoffs(workspace, count=4)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, _ = ac.load_jsonl(state / "investigation_tasks.jsonl")
    original_findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
    for task in tasks:
        task["status"] = "pending"
    (state / "investigation_tasks.jsonl").write_text(
        "".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8",
    )
    (state / "investigation_findings.jsonl").write_text("", encoding="utf-8")
    rounds, _ = ac.load_jsonl(state / "investigation_rounds.jsonl")
    rounds[0]["finding_ids"] = []
    (state / "investigation_rounds.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in rounds), encoding="utf-8",
    )
    run_runner(
        "task-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]),
    )
    template_root = state / "handoff-templates" / "investigators"
    for path in template_root.glob("*.json"):
        path.unlink()

    def generate(task_id: str, force: bool = False):
        command = [
            sys.executable, str(SCRIPTS / "handoff_template.py"),
            "--tasks", str(state / "investigation_tasks.jsonl"),
            "--claims", str(state / "design_claims.jsonl"),
            "--task-id", task_id,
            "--output", str(template_root / f"{task_id}.json"),
        ]
        if force:
            command.append("--force")
        return subprocess.run(command, text=True, capture_output=True)

    assert generate("TASK-001").returncode == 0
    assert generate("TASK-001").returncode == 2
    assert generate("TASK-001", force=True).returncode == 0
    assert generate("TASK-002").returncode == 0
    blocked = generate("TASK-003")
    assert blocked.returncode == 1
    assert "outside the ordered two-task frontier" in blocked.stdout

    # Candidate 1 closes while candidate 2 remains pending.  A failed report
    # for candidate 2 is audit evidence for that candidate, not a global lock.
    tasks[0]["status"] = "complete"
    (state / "investigation_tasks.jsonl").write_text(
        "".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8",
    )
    (state / "investigation_findings.jsonl").write_text(
        json.dumps(original_findings[0]) + "\n",
        encoding="utf-8",
    )
    rounds[0]["finding_ids"] = ["FINDING-TASK-001"]
    (state / "investigation_rounds.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in rounds), encoding="utf-8",
    )
    run_runner(
        "task-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]),
    )
    ac.save_json(state / "investigator_batch_gate.json", {
        "passed": False,
        "invalid_ids": ["FINDING-TASK-002"],
        "errors": ["invalid handoff"],
    })
    third = generate("TASK-003")
    assert third.returncode == 0, third.stdout + third.stderr


def test_failed_finding_merge_writes_batch_gate_and_detailed_stdout(workspace, tmp_path):
    handoffs = tmp_path / "handoffs"
    state = tmp_path / "state"
    handoffs.mkdir()
    report = tmp_path / "finding-merge.json"
    ac.save_json(handoffs / "bad.json", {
        "finding_id": "FINDING-BAD", "session_id": workspace["session_id"],
        "task_id": "TASK-BAD", "claim_id": "CLAIM-BAD",
    })
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--input-dir", str(handoffs),
        "--output", str(state / "investigation_findings.jsonl"),
        "--artifact-type", "finding", "--session-id", str(workspace["session_id"]),
        "--report", str(report),
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    stdout = json.loads(proc.stdout)
    assert stdout["invalid_ids"] == ["FINDING-BAD"]
    assert stdout["errors"]
    gate = ac.load_json(state / "investigator_batch_gate.json")
    assert gate["passed"] is False
    assert gate["invalid_ids"] == ["FINDING-BAD"]


def test_finding_merge_isolates_valid_candidate_from_invalid_peer(workspace, tmp_path):
    populate_handoffs(workspace, count=3)
    source_state = workspace["state"]
    assert isinstance(source_state, Path)
    tasks, _ = ac.load_jsonl(source_state / "investigation_tasks.jsonl")
    claims, _ = ac.load_jsonl(source_state / "design_claims.jsonl")
    findings, _ = ac.load_jsonl(source_state / "investigation_findings.jsonl")
    claims_by_id = {item["claim_id"]: item for item in claims}

    state = source_state
    handoffs = state / "handoffs" / "investigators"
    templates = state / "handoff-templates" / "investigators"
    for path in handoffs.rglob("*.json"):
        path.unlink()
    for path in templates.glob("*.json"):
        path.unlink()
    (state / "investigation_findings.jsonl").write_text("", encoding="utf-8")
    for task in tasks:
        task["status"] = "pending"
    ac.save_json(templates / "TASK-001.json", handoff_template.finding_template(tasks[0], claims_by_id[tasks[0]["claim_id"]]))
    ac.save_json(templates / "TASK-002.json", handoff_template.finding_template(tasks[1], claims_by_id[tasks[1]["claim_id"]]))
    valid_dir = handoffs / "TASK-001"
    invalid_dir = handoffs / "TASK-002"
    valid_dir.mkdir(parents=True)
    invalid_dir.mkdir(parents=True)
    ac.save_json(valid_dir / "finding.json", findings[0])
    invalid_peer = dict(findings[1])
    invalid_peer["observed_behavior"] = ""
    ac.save_json(invalid_dir / "finding.json", invalid_peer)
    (state / "investigation_tasks.jsonl").write_text(
        "\n".join(json.dumps(item) for item in tasks) + "\n", encoding="utf-8"
    )
    (state / "design_claims.jsonl").write_text(
        "\n".join(json.dumps(item) for item in claims) + "\n", encoding="utf-8"
    )
    rounds, _ = ac.load_jsonl(source_state / "investigation_rounds.jsonl")
    rounds[0]["finding_ids"] = []
    (state / "investigation_rounds.jsonl").write_text(
        "\n".join(json.dumps(item) for item in rounds) + "\n", encoding="utf-8"
    )
    invalid_report = tmp_path / "invalid-candidate-report.json"
    invalid = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--input-dir", str(invalid_dir), "--output", str(state / "investigation_findings.jsonl"),
        "--artifact-type", "finding", "--session-id", str(workspace["session_id"]),
        "--code-root", str(workspace["code"]), "--design-root", str(workspace["design"]),
        "--report", str(invalid_report),
    ], text=True, capture_output=True)
    assert invalid.returncode == 1
    assert (state / "investigation_findings.jsonl").read_bytes() == b""
    failed = ac.load_json(invalid_report)
    assert failed["expected_ids"] == ["FINDING-TASK-002"]
    assert failed["invalid_ids"] == ["FINDING-TASK-002"]
    assert ac.load_json(state / "investigator_batch_gate.json")["passed"] is False

    # The peer is independently invalid in both its stable plan and lifecycle.
    # Those candidate-local errors must remain visible without rolling back the
    # valid candidate's finding/lifecycle transition.
    tasks[1]["obligation_sha256"] = "0" * 64
    tasks[1]["status"] = "invalid-peer-status"
    (state / "investigation_tasks.jsonl").write_text(
        "\n".join(json.dumps(item) for item in tasks) + "\n", encoding="utf-8"
    )
    plan = run_runner(
        "task-plan-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]), check=False,
    )
    lifecycle = run_runner(
        "task-lifecycle-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]), check=False,
    )
    assert plan.returncode == 1
    assert lifecycle.returncode == 1
    plan_digest_before = stage_artifact_validator.task_plan_digest(
        tasks[0], claims_by_id[tasks[0]["claim_id"]], rounds,
    )
    lifecycle_digest_before = stage_artifact_validator.task_lifecycle_digest(tasks[0], [])
    trace_root = Path(workspace["logs"]) / "trace"
    plan_trace_before = (trace_root / "task_plan_validation.json").read_bytes()
    lifecycle_trace_before = (trace_root / "task_lifecycle_validation.json").read_bytes()
    plan_trace = ac.load_json(trace_root / "task_plan_validation.json")
    lifecycle_trace = ac.load_json(trace_root / "task_lifecycle_validation.json")
    assert plan_trace["global_passed"] is True
    assert lifecycle_trace["global_passed"] is True
    assert "TASK-001" in plan_trace["valid_task_ids"]
    assert "TASK-001" in lifecycle_trace["valid_task_ids"]
    assert "TASK-002" in plan_trace["invalid_task_ids"]
    assert "TASK-002" in lifecycle_trace["invalid_task_ids"]

    report = tmp_path / "valid-candidate-report.json"
    valid = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--input-dir", str(valid_dir), "--output", str(state / "investigation_findings.jsonl"),
        "--artifact-type", "finding", "--session-id", str(workspace["session_id"]),
        "--code-root", str(workspace["code"]), "--design-root", str(workspace["design"]),
        "--report", str(report),
    ], text=True, capture_output=True)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    result = ac.load_json(report)
    assert result["expected_ids"] == ["FINDING-TASK-001"]
    assert result["missing_ids"] == []
    assert result["validated_ids"] == ["FINDING-TASK-001"]
    merged_tasks, _ = ac.load_jsonl(state / "investigation_tasks.jsonl")
    merged_rounds, _ = ac.load_jsonl(state / "investigation_rounds.jsonl")
    merged_findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert stage_artifact_validator.task_plan_digest(
        merged_tasks[0], claims_by_id[merged_tasks[0]["claim_id"]], merged_rounds,
    ) == plan_digest_before
    assert stage_artifact_validator.task_lifecycle_digest(
        merged_tasks[0], [merged_findings[0]],
    ) != lifecycle_digest_before
    assert merged_tasks[0]["status"] == "complete"
    assert merged_tasks[1]["status"] == "invalid-peer-status"
    assert merged_rounds[0]["finding_ids"] == ["FINDING-TASK-001"]
    assert (trace_root / "task_plan_validation.json").read_bytes() == plan_trace_before
    assert (trace_root / "task_lifecycle_validation.json").read_bytes() != lifecycle_trace_before
    refreshed_lifecycle = ac.load_json(trace_root / "task_lifecycle_validation.json")
    assert refreshed_lifecycle["global_passed"] is True
    assert refreshed_lifecycle["passed"] is False
    assert "TASK-001" in refreshed_lifecycle["valid_task_ids"]
    assert "TASK-002" in refreshed_lifecycle["invalid_task_ids"]

    # The next frontier candidate can start even though candidate 2's isolated
    # validation failed and its template remains unresolved.
    allowed = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_template.py"),
        "--tasks", str(state / "investigation_tasks.jsonl"),
        "--claims", str(state / "design_claims.jsonl"), "--task-id", "TASK-003",
        "--output", str(templates / "TASK-003.json"),
    ], text=True, capture_output=True)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


def test_finding_merge_does_not_block_on_a_stale_retained_candidate(workspace):
    populate_handoffs(workspace, count=2)
    state = workspace["state"]
    assert isinstance(state, Path)
    findings, errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert errors == []
    findings[0]["code_evidence"][0]["snippet"] = "not the cited source"
    (state / "investigation_findings.jsonl").write_text(
        "".join(json.dumps(finding) + "\n" for finding in findings),
        encoding="utf-8",
    )
    candidate_dir = state / "handoffs" / "investigators" / "TASK-002"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    ac.save_json(candidate_dir / "TASK-002.json", findings[1])
    report_path = workspace["logs"] / "trace" / "finding-merge-TASK-002.json"

    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--input-dir", str(candidate_dir),
        "--output", str(state / "investigation_findings.jsonl"),
        "--artifact-type", "finding", "--session-id", str(workspace["session_id"]),
        "--code-root", str(workspace["code"]), "--design-root", str(workspace["design"]),
        "--report", str(report_path),
    ], text=True, capture_output=True)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = ac.load_json(report_path)
    assert report["validated_ids"] == ["FINDING-TASK-002"]
    assert report["retained_invalid_ids"] == ["FINDING-TASK-001"]
    merged, errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert errors == []
    assert {finding["finding_id"] for finding in merged} == {
        "FINDING-TASK-001", "FINDING-TASK-002",
    }


def test_finding_merge_does_not_block_on_retained_stale_template_binding(workspace):
    populate_handoffs(workspace, count=2)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, task_errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    findings, finding_errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert task_errors == []
    assert finding_errors == []

    # Make only the retained peer's upstream task/template contract stale.  The
    # currently submitted candidate remains unchanged and must still merge.
    tasks[0]["claim_id"] = tasks[1]["claim_id"]
    (state / "investigation_tasks.jsonl").write_text(
        "".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8",
    )
    candidate_dir = state / "handoffs" / "investigators" / "TASK-002"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    ac.save_json(candidate_dir / "TASK-002.json", findings[1])
    report_path = workspace["logs"] / "trace" / "finding-merge-stale-template.json"

    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--input-dir", str(candidate_dir),
        "--output", str(state / "investigation_findings.jsonl"),
        "--artifact-type", "finding", "--session-id", str(workspace["session_id"]),
        "--code-root", str(workspace["code"]), "--design-root", str(workspace["design"]),
        "--report", str(report_path),
    ], text=True, capture_output=True)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = ac.load_json(report_path)
    assert report["validated_ids"] == ["FINDING-TASK-002"]
    assert report["retained_invalid_ids"] == ["FINDING-TASK-001"]
    merged, merge_errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert merge_errors == []
    assert {finding["finding_id"] for finding in merged} == {
        "FINDING-TASK-001", "FINDING-TASK-002",
    }


def test_finding_self_check_rejects_changes_to_pristine_template_fields(workspace):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
    handoff = state / "handoffs" / "investigators" / "TASK-001.json"
    changed = dict(findings[0])
    changed["expected_behavior"] = "A model-rewritten expectation."
    ac.save_json(handoff, changed)
    report = Path(workspace["logs"]) / "trace" / "immutable-template-check.json"
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--check-file", str(handoff), "--artifact-type", "finding",
        "--session-id", str(workspace["session_id"]),
        "--code-root", str(workspace["code"]), "--design-root", str(workspace["design"]),
        "--report", str(report),
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    assert any("expected_behavior does not match the pristine" in error for error in ac.load_json(report)["errors"])


def test_finding_self_check_reconstructs_template_instead_of_trusting_the_file(workspace):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
    handoff = state / "handoffs" / "investigators" / "TASK-001.json"
    template_path = state / "handoff-templates" / "investigators" / "TASK-001.json"
    changed_finding = dict(findings[0])
    changed_finding["expected_behavior"] = "A synchronized but ungrounded rewrite."
    changed_template = ac.load_json(template_path)
    changed_template["expected_behavior"] = changed_finding["expected_behavior"]
    ac.save_json(template_path, changed_template)
    ac.save_json(handoff, changed_finding)
    report = Path(workspace["logs"]) / "trace" / "reconstructed-template-check.json"
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--check-file", str(handoff), "--artifact-type", "finding",
        "--session-id", str(workspace["session_id"]),
        "--code-root", str(workspace["code"]), "--design-root", str(workspace["design"]),
        "--report", str(report),
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    assert any(
        "template differs from current task/claim contract" in error
        for error in ac.load_json(report)["errors"]
    )


def test_instruction_allows_valid_candidate_to_merge_without_waiting_for_its_peer():
    instruction = (ROOT / "INSTRUCTION.md").read_text(encoding="utf-8")
    assert "--check-file" in instruction
    assert "--report" in instruction
    assert "handoffs/investigators/${TASK_ID}/${TASK_ID}.json" in instruction
    assert "--input-dir ${STATE_ROOT}/handoffs/investigators/${TASK_ID}" in instruction
    assert "不阻塞已验证 candidate" in instruction
    assert "valid_task_ids" in instruction
    assert "全部 handoff self-check 通过后原子 merge" not in instruction
    assert "部分批次不得继续" not in instruction
    assert "${REVIEW_CODE_ROOT}" in instruction
    assert "goal_runner.py task-lifecycle-check" in instruction


def test_instruction_requires_risk_plan_gate_before_parallel_risk_tasks():
    instruction = (ROOT / "INSTRUCTION.md").read_text(encoding="utf-8")
    gate_position = instruction.index("goal_runner.py risk-plan-check")
    launch_position = instruction.index("通过后按plan最多并发两个fresh `risk-explorer`")
    assert gate_position < launch_position
    assert "primary code scope" in instruction[:launch_position]
    assert "互斥primary code scope" in instruction[:launch_position]


def test_discovery_policy_uses_design_guided_trace_candidates_without_quotas():
    instruction = (ROOT / "INSTRUCTION.md").read_text(encoding="utf-8")
    skill = (ROOT / "work" / "skill" / "SKILL.md").read_text(encoding="utf-8")
    orchestrator = (ROOT / "work" / "skills" / "orchestrator.md").read_text(
        encoding="utf-8"
    )
    risk = (ROOT / "work" / "skills" / "risk-explorer.md").read_text(
        encoding="utf-8"
    )

    assert "设计引导" in instruction
    assert "双入口" in instruction
    assert "design section" in instruction
    assert "设计入口可直接产生candidate" in orchestrator
    assert "design_section_ids" in risk
    assert "最多12条" in orchestrator
    assert "不做每文档或每sweep配额" in orchestrator
    assert "passed=true,closed=true" in orchestrator
    assert "不得为了填充数量制造 observation" in risk
    assert "supplied design" in instruction
    assert "evidence" in skill.lower()


def test_prepare_resumes_same_session_without_erasing_handoffs(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    append(state / "design_claims.jsonl", {"claim_id": "CLAIM-KEEP"})
    proc = run_runner("prepare", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    assert '"resumed": true' in proc.stdout.lower()
    assert ac.load_jsonl(state / "design_claims.jsonl")[0] == [{"claim_id": "CLAIM-KEEP"}]
    assert ac.load_json(state / "agent_loop_state.json")["session_id"] == workspace["session_id"]


def test_prepare_resume_rejects_original_mutation_without_rebaselining(workspace):
    state = workspace["state"]
    code = workspace["code"]
    assert isinstance(state, Path)
    assert isinstance(code, Path)
    manifest_path = state / "workspace_manifest.json"
    manifest_before = manifest_path.read_bytes()
    review_code = Path(ac.load_json(manifest_path)["paths"]["review_code_root"])
    review_before = (review_code / "service.py").read_bytes()
    (code / "service.py").write_text("VALUE = 'mutated'\n", encoding="utf-8")
    proc = run_runner("prepare", code, workspace["design"], workspace["result"], workspace["logs"], check=False)
    assert proc.returncode == 2
    assert manifest_path.read_bytes() == manifest_before
    assert (review_code / "service.py").read_bytes() == review_before


def test_prepare_resume_refuses_to_recreate_deleted_supplement_history(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    history = state / "coverage_supplement_history.json"
    history.unlink()

    proc = run_runner(
        "prepare", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    assert not history.exists()
    prepared = ac.load_json(workspace["logs"] / "trace" / "session_prepared.json")
    assert any(
        "refusing to reset the one-supplement ledger" in problem
        for problem in prepared["problems"]
    )


def test_prepare_resume_rejects_snapshot_mutation_without_repairing_it(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    manifest_path = state / "workspace_manifest.json"
    manifest_before = manifest_path.read_bytes()
    review_code = Path(ac.load_json(manifest_path)["paths"]["review_code_root"])
    contaminated = b"VALUE = 'contaminated'\n"
    (review_code / "service.py").write_bytes(contaminated)
    proc = run_runner(
        "prepare", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 2
    assert manifest_path.read_bytes() == manifest_before
    assert (review_code / "service.py").read_bytes() == contaminated


def test_prepare_with_different_roots_has_no_snapshot_side_effects(workspace, tmp_path):
    state = workspace["state"]
    assert isinstance(state, Path)
    manifest_path = state / "workspace_manifest.json"
    manifest_before = manifest_path.read_bytes()
    review_code = Path(ac.load_json(manifest_path)["paths"]["review_code_root"])
    review_before = (review_code / "service.py").read_bytes()
    other_code = tmp_path / "other-code"
    other_design = tmp_path / "other-design"
    other_code.mkdir()
    other_design.mkdir()
    (other_code / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    (other_design / "spec.md").write_text("The value must be two.\n", encoding="utf-8")
    proc = run_runner(
        "prepare", other_code, other_design, workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 2
    assert manifest_path.read_bytes() == manifest_before
    assert (review_code / "service.py").read_bytes() == review_before


def test_validator_re_reads_cited_source_and_rejects_fabricated_quote(workspace):
    populate_handoffs(workspace, count=1, bad_quote=True)
    proc = run_runner(
        "review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any("quote does not match cited source lines" in error for error in trace["errors"])


@pytest.mark.parametrize(
    ("ledger_name", "expected_error"),
    (
        ("critic_reviews.jsonl", "findings lack critic handoffs"),
        ("agent_review_verdicts.jsonl", "findings lack final-judge verdicts"),
    ),
)
def test_validator_requires_every_candidate_to_reach_critic_and_judge(
    workspace, ledger_name, expected_error,
):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    (state / ledger_name).write_text("", encoding="utf-8")

    proc = run_runner(
        "review", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any(expected_error in error for error in trace["errors"])


def test_validator_cannot_publish_a_design_satisfied_finding_as_confirmed(workspace):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    finding = ac.load_jsonl(state / "investigation_findings.jsonl")[0][0]
    finding["assessment"] = "design_satisfied"
    (state / "investigation_findings.jsonl").write_text(json.dumps(finding) + "\n", encoding="utf-8")
    critic = ac.load_jsonl(state / "critic_reviews.jsonl")[0][0]
    critic["decision"] = "reject_issue"
    (state / "critic_reviews.jsonl").write_text(json.dumps(critic) + "\n", encoding="utf-8")
    verdict = ac.load_jsonl(state / "agent_review_verdicts.jsonl")[0][0]
    verdict["critic_review"]["decision"] = "reject_issue"
    (state / "agent_review_verdicts.jsonl").write_text(json.dumps(verdict) + "\n", encoding="utf-8")
    proc = run_runner(
        "review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any("critic artifact decision is incompatible with confirmed" in error for error in trace["errors"])
    assert any("investigator assessment must be" in error for error in trace["errors"])


def test_validator_rejects_final_evidence_not_handed_off_by_investigator(workspace):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    finding = ac.load_jsonl(state / "investigation_findings.jsonl")[0][0]
    finding["code_evidence"] = [{
        "file": "service.py", "line_start": 4, "line_end": 5,
        "symbol": "session_expired", "snippet": "def session_expired(minutes):\n    return minutes > 60",
    }]
    (state / "investigation_findings.jsonl").write_text(json.dumps(finding) + "\n", encoding="utf-8")
    proc = run_runner(
        "review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any("final code evidence was not handed off" in error for error in trace["errors"])


def test_validator_requires_judge_to_copy_investigator_evidence_exactly(workspace):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    verdict = ac.load_jsonl(state / "agent_review_verdicts.jsonl")[0][0]
    verdict["code_evidence"].append({
        "file": "service.py", "line_start": 7, "line_end": 8,
        "symbol": "can_export", "snippet": "def can_export(role):\n    return True",
    })
    (state / "agent_review_verdicts.jsonl").write_text(json.dumps(verdict) + "\n", encoding="utf-8")
    proc = run_runner(
        "review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any("code_evidence must exactly match" in error for error in trace["errors"])


def test_validator_rejects_critic_bound_to_an_older_finding_snapshot(workspace):
    populate_handoffs(workspace, count=1)
    state = workspace["state"]
    assert isinstance(state, Path)
    findings, errors = ac.load_jsonl(state / "investigation_findings.jsonl")
    assert errors == []
    findings[0]["supporting_evidence"].append(
        "A newly discovered caller changes the evidence snapshot."
    )
    (state / "investigation_findings.jsonl").write_text(
        json.dumps(findings[0]) + "\n", encoding="utf-8",
    )

    proc = run_runner(
        "review", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any(
        "critic (FINDING-TASK-001): input_digests do not match current"
        in error for error in trace["errors"]
    )


def test_full_handoff_review_report_and_gate(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    result = ac.load_json(workspace["result"] / "issues.json")
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert result["tool"] == "goal-agent-design-code-diff"
    assert result["summary"]["confirmed"] == 4
    assert all(issue["status"] == "confirmed" for issue in result["issues"])
    assert all(issue["normative_strength"] == "mandatory" for issue in result["issues"])
    assert all(issue["agent_review"]["critic_review"]["decision"] == "confirm_contradiction" for issue in result["issues"])
    assert gate["passed"] is True
    assert gate["checks"]["handoff_chain_complete"] is True


def test_competition_review_then_finalize_path_uses_current_traces(workspace):
    populate_handoffs(workspace)
    run_runner(
        "review", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )

    proc = run_runner(
        "finalize", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )

    assert proc.returncode == 0
    assert "design_artifact_validator.py" not in proc.stdout
    assert "stage_artifact_validator.py" not in proc.stdout
    assert "verdict_validator.py" not in proc.stdout
    assert ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")[
        "passed"
    ] is True


def test_gate_rejects_risk_merge_report_bound_to_stale_plan(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    report_path = Path(workspace["logs"]) / "trace" / "risk-handoff-merge.json"
    report = ac.load_json(report_path)
    report["risk_sweep_plan_sha256"] = "stale-plan-digest"
    ac.save_json(report_path, report)
    risks, errors = ac.load_jsonl(state / "risk_observations.jsonl")
    assert errors == []
    ac.append_jsonl(state / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
        "event": "handoff_merge", "actor": "fixture_handoff_merge",
        "phase": "code_risk_backtracking", "status": "complete",
        "artifact_type": "risk",
        "validated_ids": [risk["observation_id"] for risk in risks],
        "report": str(report_path), "report_sha256": ac.sha256_file(report_path),
        "ledger_sha256": ac.sha256_file(state / "risk_observations.jsonl"),
    })

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any(
        "risk handoff merge trace does not validate the current ledger" in error
        for error in gate["errors"]
    )


def test_coverage_passed_is_not_closed_while_next_round_tasks_exist(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    contract = ac.load_json(state / "agent_loop_contract.json")
    claims, _ = ac.load_jsonl(state / "design_claims.jsonl")
    coverage = ac.load_json(state / "coverage_audit.json")
    coverage["remaining_gaps"] = [{
        "gap_id": "GAP-SUPPLEMENT-001",
        "kind": "frontier_claim",
        "ref_id": "CLAIM-001",
        "reason": "An alternate reachable path has not been tested.",
        "evidence": "The current finding covers only the primary entry path.",
    }]
    coverage["next_round_tasks"] = [{
        "claim_id": "CLAIM-001",
        "claim_branch": "CLAIM-001: alternate reachable evidence path",
        "hypothesis": "Does an independent entry path enforce the same obligation?",
        "obligation_sha256": stage_artifact_validator.claim_obligation_sha256(claims[0]),
        "exploration_mode": "design-to-code obligation tracing",
        "review_lenses": [contract["coverage_contract"]["portfolio_lenses"][0]],
        "architecture_boundaries": ["BOUNDARY-API"],
        "implementation_planes": ["PLANE-SERVICE"],
        "parallel_path_ids": [],
        "risk_observation_ids": [],
        "source_gap_ids": ["GAP-SUPPLEMENT-001"],
        "priority_reason": "The coverage critic identified a concrete alternate-path gap.",
    }]
    ac.save_json(state / "coverage_audit.json", coverage)
    run_runner(
        "coverage-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]),
    )
    trace = ac.load_json(Path(workspace["logs"]) / "trace" / "coverage_validation.json")
    assert trace["passed"] is True
    assert trace["closed"] is False
    assert trace["metrics"]["supplement_rounds"] == 0
    report = run_runner(
        "report", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]), check=False,
    )
    assert report.returncode != 0


def test_coverage_rejects_uninvestigated_accepted_claims_as_a_stop_condition(
    workspace,
):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    coverage = ac.load_json(state / "coverage_audit.json")
    coverage["remaining_scoped_claims"] = [{
        "claim_id": "CLAIM-001",
        "reason": "The accepted claim has not been investigated.",
    }]
    ac.save_json(state / "coverage_audit.json", coverage)

    proc = run_runner(
        "coverage-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]), check=False,
    )

    assert proc.returncode == 1
    trace = ac.load_json(Path(workspace["logs"]) / "trace" / "coverage_validation.json")
    assert any(
        "remaining_scoped_claims is not a stop condition" in error
        for error in trace["errors"]
    )


def test_new_on_demand_claim_must_join_complete_review_scope(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    review_path = state / "design_claim_review.json"
    review_before = review_path.read_bytes()
    on_demand_claim = design_source_materializer.materialize_claims([{
        "claim_id": "CLAIM-INDEX-ONLY",
        "session_id": workspace["session_id"],
        "source_ref": {
            "path": "contract.md", "line_start": 3, "line_end": 3,
        },
        "document_key": "contract",
        "subject": "A secondary service entry point",
        "trigger": "A caller supplies an amount covered by the service contract.",
        "obligation": "The service must reject negative amounts.",
        "exceptions": [],
        "observable_result": "The negative request is rejected.",
        "behavior_family": "externally visible service contract",
        "normative_strength": "mandatory",
        "applicability": "service implementation",
        "priority": "high",
        "ambiguities": [],
        "probe_oracle": {
            "testability": "candidate",
            "preconditions": ["The service accepts an amount input."],
            "stimulus": "Call the service with a negative amount.",
            "expected_observation": "The request is rejected.",
            "non_testable_reason": "",
        },
    }], Path(workspace["design"]))[0]
    append(state / "design_claims.jsonl", on_demand_claim)
    design_coverage = ac.load_json(state / "design_coverage.json")
    design_coverage["document_groups"][0]["claim_ids"].append("CLAIM-INDEX-ONLY")
    ac.save_json(state / "design_coverage.json", design_coverage)
    run_runner(
        "design-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]),
    )
    proc = run_runner(
        "claim-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]), check=False,
    )
    assert proc.returncode == 1
    trace = ac.load_json(Path(workspace["logs"]) / "trace" / "claim_review_validation.json")
    assert any(
        "must include every materialized claim" in error
        and "CLAIM-INDEX-ONLY" in error
        for error in trace["errors"]
    )
    assert review_path.read_bytes() == review_before


def test_complete_session_gate_can_be_replayed_in_isolation(workspace, tmp_path):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    replay = tmp_path / "gate-replay"
    stage_replay.prepare_replay(
        source_state=Path(workspace["state"]), replay_root=replay,
        stage="gate", run_local=True,
    )

    assert stage_replay.run_local(replay) == 0
    assert ac.load_json(replay / "logs" / "trace" / "final_gate.json")["passed"] is True
    assert ac.load_json(replay / "logs" / "trace" / "stage_replay_local.json")[
        "returncode"
    ] == 0


def test_materialized_catalog_gate_replay_copies_and_protects_source(workspace, tmp_path):
    materialized = prepare_materialized_workspace(workspace, tmp_path)
    populate_handoffs(materialized)
    run_runner(
        "review", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    run_runner(
        "report", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    run_runner(
        "gate", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    replay = tmp_path / "materialized-gate-replay"
    stage_replay.prepare_replay(
        source_state=Path(materialized["state"]), replay_root=replay,
        stage="gate", run_local=True,
    )
    replay_manifest = ac.load_json(replay / "state" / "workspace_manifest.json")
    replay_source = Path(
        replay_manifest["design"]["materialization_source"]["source_root"]
    )
    replay_plan = Path(
        replay_manifest["design"]["materialization_source"]["plan_path"]
    )
    assert replay_source.is_relative_to(replay / "state" / "materialization-inputs")
    assert replay_plan == replay / "state" / "design_source_plan.json"
    assert (replay_source / "catalog.list").is_file()

    original_source = Path(materialized["source"])
    (original_source / "catalog.list").write_text(
        "Original source changed after replay preparation.\n", encoding="utf-8",
    )
    assert stage_replay.run_local(replay) == 0
    assert ac.load_json(replay / "logs" / "trace" / "final_gate.json")[
        "passed"
    ] is True


def test_gate_replay_force_cannot_delete_materialization_source(workspace, tmp_path):
    materialized = prepare_materialized_workspace(workspace, tmp_path)
    source = Path(materialized["source"])
    replay = source / "unsafe-replay-root"
    replay.mkdir()
    marker = replay / "keep.txt"
    marker.write_text("must survive\n", encoding="utf-8")

    with pytest.raises(stage_replay.ReplayError, match="protected source/session root"):
        stage_replay.prepare_replay(
            source_state=Path(materialized["state"]), replay_root=replay,
            stage="gate", force=True,
        )

    assert marker.read_text(encoding="utf-8") == "must survive\n"


def test_complete_coverage_stage_can_be_replayed_in_isolation(workspace, tmp_path):
    populate_handoffs(workspace)
    replay = tmp_path / "coverage-replay"
    stage_replay.prepare_replay(
        source_state=Path(workspace["state"]), replay_root=replay,
        stage="coverage", run_local=True,
    )

    assert stage_replay.run_local(replay) == 0
    assert ac.load_json(replay / "logs" / "trace" / "claim_review_validation.json")[
        "passed"
    ] is True
    assert ac.load_json(replay / "logs" / "trace" / "coverage_validation.json")[
        "passed"
    ] is True


def test_dynamic_probe_session_can_replay_coverage_and_gate(workspace, tmp_path):
    populate_handoffs(workspace)
    attach_dynamic_probe(workspace)
    run_runner(
        "review", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )
    run_runner(
        "report", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )
    run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )

    coverage_replay = tmp_path / "dynamic-coverage-replay"
    stage_replay.prepare_replay(
        source_state=Path(workspace["state"]), replay_root=coverage_replay,
        stage="coverage", run_local=True,
    )
    replay_probe = ac.load_jsonl(
        coverage_replay / "state" / "dynamic_probes.jsonl"
    )[0][0]
    probe_workspace = Path(replay_probe["isolation"]["workspace"])
    assert probe_workspace.is_relative_to(coverage_replay / "state" / "probes")
    assert (probe_workspace / "service.py").is_file()
    assert stage_replay.run_local(coverage_replay) == 0

    gate_replay = tmp_path / "dynamic-gate-replay"
    stage_replay.prepare_replay(
        source_state=Path(workspace["state"]), replay_root=gate_replay,
        stage="gate", run_local=True,
    )
    assert stage_replay.run_local(gate_replay) == 0
    assert ac.load_json(gate_replay / "logs" / "trace" / "final_gate.json")[
        "passed"
    ] is True


def test_gate_revalidates_verdicts_instead_of_trusting_stale_validated_issues(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    verdicts, _ = ac.load_jsonl(state / "agent_review_verdicts.jsonl")
    verdicts[0]["status"] = "rejected"
    verdicts[0]["rejection_reason"] = "The verdict was changed after the prior review."
    (state / "agent_review_verdicts.jsonl").write_text(
        "\n".join(json.dumps(item) for item in verdicts) + "\n", encoding="utf-8"
    )
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    validated = ac.load_json(state / "validated_issues.json")
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert validated["confirmed"] == 3
    assert any("published finding IDs do not exactly match" in error for error in gate["errors"])


def test_report_refuses_stale_evidence_validation_after_verdict_change(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    verdicts, errors = ac.load_jsonl(state / "agent_review_verdicts.jsonl")
    assert errors == []
    verdicts[0]["title"] = "Changed after evidence validation"
    (state / "agent_review_verdicts.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in verdicts), encoding="utf-8",
    )

    proc = run_runner(
        "report", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )
    assert proc.returncode == 2
    assert "evidence validation input digests are stale" in proc.stdout


def test_gate_rejects_approval_policy_events_from_another_session(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    approvals, _ = ac.load_jsonl(state / "approval_events.jsonl")
    for item in approvals:
        if item.get("action") in {
            "review_snapshot_read", "session_artifact_write", "target_source_write", "external_side_effect",
        }:
            item["session_id"] = "session-old"
    (state / "approval_events.jsonl").write_text(
        "\n".join(json.dumps(item) for item in approvals) + "\n", encoding="utf-8"
    )
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("approval policy trace is incomplete" in error for error in gate["errors"])


def test_gate_rejects_duplicate_non_revision_ledger_ids(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    claims, _ = ac.load_jsonl(state / "design_claims.jsonl")
    append(state / "design_claims.jsonl", claims[0])
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("duplicate claim_id" in error for error in gate["errors"])


def test_parallel_path_can_be_covered_by_linked_split_plane_tasks(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    architecture = ac.load_json(state / "architecture_map.json")
    architecture["implementation_planes"].append({
        "plane_id": "PLANE-ALTERNATE", "kind": "adapter", "paths": ["service.py"],
        "reachable_evidence": "A second entry plane reaches the same contract.",
    })
    architecture["parallel_behavior_paths"] = [{
        "path_id": "PARALLEL-SERVICE-CONTRACT",
        "behavior": "The same contract is reachable through two implementation planes.",
        "plane_ids": ["PLANE-SERVICE", "PLANE-ALTERNATE"],
        "evidence": "Both public entry planes expose the designed behavior.",
    }]
    ac.save_json(state / "architecture_map.json", architecture)
    plan = ac.load_json(state / "risk_sweep_plan.json")
    plan["architecture_map_sha256"] = ac.sha256_file(state / "architecture_map.json")
    plan["required_coverage"]["plane_ids"] = [
        "PLANE-SERVICE", "PLANE-AUDIT", "PLANE-ALTERNATE",
    ]
    plan["required_coverage"]["parallel_path_ids"] = [
        "PARALLEL-SERVICE-CONTRACT",
    ]
    plan["slices"][0]["implementation_planes"].append("PLANE-ALTERNATE")
    plan["slices"][0]["parallel_path_ids"] = ["PARALLEL-SERVICE-CONTRACT"]
    ac.save_json(state / "risk_sweep_plan.json", plan)
    risk_plan_digest = ac.sha256_file(state / "risk_sweep_plan.json")
    tasks, _ = ac.load_jsonl(state / "investigation_tasks.jsonl")
    tasks[0]["parallel_path_ids"] = ["PARALLEL-SERVICE-CONTRACT"]
    tasks[1]["parallel_path_ids"] = ["PARALLEL-SERVICE-CONTRACT"]
    tasks[1]["implementation_planes"] = ["PLANE-ALTERNATE"]
    tasks[1]["exploration_mode"] = "code-to-design risk backtracking"
    tasks[1]["risk_observation_ids"] = ["RISK-API-001"]
    (state / "investigation_tasks.jsonl").write_text(
        "\n".join(json.dumps(item) for item in tasks) + "\n", encoding="utf-8"
    )
    risks, _ = ac.load_jsonl(state / "risk_observations.jsonl")
    risks[0]["implementation_planes"] = ["PLANE-SERVICE", "PLANE-ALTERNATE"]
    risks[0]["parallel_path_ids"] = ["PARALLEL-SERVICE-CONTRACT"]
    for risk in risks:
        risk["risk_sweep_plan_sha256"] = risk_plan_digest
    (state / "risk_observations.jsonl").write_text(
        "\n".join(json.dumps(item) for item in risks) + "\n", encoding="utf-8"
    )
    report_path = Path(workspace["logs"]) / "trace" / "task-handoff-merge.json"
    report = ac.load_json(report_path)
    report["ledger_sha256"] = ac.sha256_file(state / "investigation_tasks.jsonl")
    current_task_plan_sha256 = handoff_merge.task_plan_ledger_sha256(
        {task["task_id"]: task for task in tasks}
    )
    report["task_plan_ledger_sha256"] = current_task_plan_sha256
    ac.save_json(report_path, report)
    ac.append_jsonl(state / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
        "event": "handoff_merge", "actor": "fixture_handoff_merge",
        "phase": "investigation_planning", "status": "complete", "artifact_type": "task",
        "validated_ids": [task["task_id"] for task in tasks], "report": str(report_path),
        "report_sha256": ac.sha256_file(report_path),
        "ledger_sha256": ac.sha256_file(state / "investigation_tasks.jsonl"),
        "task_plan_ledger_sha256": current_task_plan_sha256,
    })
    risk_report_path = Path(workspace["logs"]) / "trace" / "risk-handoff-merge.json"
    risk_report = ac.load_json(risk_report_path)
    risk_report["ledger_sha256"] = ac.sha256_file(state / "risk_observations.jsonl")
    risk_report["risk_sweep_plan_sha256"] = risk_plan_digest
    risk_report["architecture_map_sha256"] = plan["architecture_map_sha256"]
    ac.save_json(risk_report_path, risk_report)
    ac.append_jsonl(state / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": workspace["session_id"],
        "event": "handoff_merge", "actor": "fixture_handoff_merge",
        "phase": "code_risk_backtracking", "status": "complete", "artifact_type": "risk",
            "validated_ids": [risk["observation_id"] for risk in risks],
            "report": str(risk_report_path),
        "report_sha256": ac.sha256_file(risk_report_path),
        "ledger_sha256": ac.sha256_file(state / "risk_observations.jsonl"),
    })
    for command in ("architecture-check", "task-check", "coverage-check"):
        run_runner(
            command, Path(workspace["code"]), Path(workspace["design"]),
            Path(workspace["result"]), Path(workspace["logs"]),
        )
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])


def test_gate_counts_only_unique_findings_and_binds_published_results(workspace):
    populate_handoffs(workspace, count=3)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    result_root = Path(workspace["result"])
    result = ac.load_json(result_root / "issues.json")
    duplicate = dict(result["issues"][0])
    duplicate["issue_id"] = "ISSUE-004"
    duplicate["report_path"] = str(result_root / f"04-{ac.slugify(duplicate['title'])}.md")
    result["issues"].append(duplicate)
    result["summary"].update({"total": 4, "confirmed": 4})
    ac.save_json(result_root / "issues.json", result)
    (result_root / "issues.jsonl").write_text(
        "\n".join(json.dumps(item) for item in result["issues"]) + "\n", encoding="utf-8"
    )
    Path(duplicate["report_path"]).write_text(report_writer.render_issue(duplicate), encoding="utf-8")
    (result_root / "00-summary.md").write_text(
        report_writer.render_summary(result, 0), encoding="utf-8"
    )
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert gate["metrics"]["confirmed"] == 3
    assert any("duplicate finding_id" in error for error in gate["errors"])


def test_gate_accepts_fewer_than_four_validated_issues_for_a_generic_project(workspace):
    populate_handoffs(workspace, count=3)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])

    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )

    assert proc.returncode == 0
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert gate["passed"] is True
    assert gate["metrics"]["confirmed"] == 3


def test_gate_requires_recorded_handoff_merge_lifecycle(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    ledger, _ = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    ledger = [item for item in ledger if item.get("event") != "handoff_merge"]
    (state / "agent_run_ledger.jsonl").write_text(
        "\n".join(json.dumps(item) for item in ledger) + "\n", encoding="utf-8"
    )
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("digest-bound finding handoff merge event" in error for error in gate["errors"])
    assert any("digest-bound critic handoff merge event" in error for error in gate["errors"])
    assert any("digest-bound task handoff merge event" in error for error in gate["errors"])


def test_gate_requires_rich_checkpoint_for_each_semantic_phase(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    ledger = [
        event for event in ledger
        if not (
            event.get("phase") == "final_judgement"
            and event.get("role") == "final-judge"
        )
    ]
    (state / "agent_run_ledger.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in ledger), encoding="utf-8",
    )
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["rich_trace_complete"] is False
    assert any(
        "final_judgement/final-judge" in error for error in gate["errors"]
    )


def test_gate_rejects_reused_provider_session_across_fresh_semantic_phases(workspace):
    populate_handoffs(workspace)
    state = Path(workspace["state"])
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    inventory_session = next(
        event["provider_session_id"] for event in ledger
        if event.get("phase") == "design_inventory"
    )
    for event in ledger:
        if event.get("phase") == "design_claim_resolution":
            event["provider_session_id"] = inventory_session
    (state / "agent_run_ledger.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in ledger), encoding="utf-8",
    )
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("fresh semantic tasks reuse provider session" in error for error in gate["errors"])


def test_gate_rejects_third_identical_no_progress_checkpoint(workspace):
    populate_handoffs(workspace)
    state = Path(workspace["state"])
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    checkpoint = next(
        event for event in ledger if event.get("phase") == "design_inventory"
    )
    second = dict(
        checkpoint,
        outcome="same_inputs_different_wording",
        scope="same stable scope with different wording",
    )
    third = dict(
        checkpoint,
        outcome="another_outcome_wording_only",
        scope="same stable scope with a third wording",
    )
    ac.append_jsonl(state / "agent_run_ledger.jsonl", second)
    ac.append_jsonl(state / "agent_run_ledger.jsonl", third)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("repeated a third time without progress" in error for error in gate["errors"])


def test_gate_rejects_unbound_portfolio_scope_id(workspace):
    populate_handoffs(workspace)
    state = Path(workspace["state"])
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    checkpoint = next(
        event for event in ledger if event.get("phase") == "design_inventory"
    )
    checkpoint["scope_id"] = "DESIGN-INVENTORY-RETRY-RENAMED"
    (state / "agent_run_ledger.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in ledger), encoding="utf-8",
    )
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("unbound portfolio trace scope IDs" in error for error in gate["errors"])


def test_gate_rejects_same_candidate_semantic_repair_reusing_provider_session(
    workspace,
):
    populate_handoffs(workspace)
    state = Path(workspace["state"])
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    original = next(
        event for event in ledger
        if event.get("phase") == "investigation"
        and event.get("task_id") == "TASK-001"
    )
    changed_input = state / "design_claims.jsonl"
    changed_input_records = [{
        "path": str(changed_input.resolve()),
        "sha256": ac.sha256_file(changed_input),
        "size_bytes": changed_input.stat().st_size,
    }]
    repaired = dict(
        original,
        provider_attempt=1,
        repair_count=1,
        outcome="semantic_repair_complete",
        input_artifacts=changed_input_records,
        input_sha256=session_event.input_artifacts_sha256(changed_input_records),
    )
    ac.append_jsonl(state / "agent_run_ledger.jsonl", repaired)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any(
        "semantic repair" in error and "provider session" in error
        for error in gate["errors"]
    )


def test_gate_rejects_unregistered_current_deterministic_helper_report(workspace):
    populate_handoffs(workspace)
    state = Path(workspace["state"])
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    ledger = [
        event for event in ledger
        if not (
            event.get("event") == "deterministic_helper_trace"
            and str(event.get("report") or "").endswith("/coverage_validation.json")
        )
    ]
    (state / "agent_run_ledger.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in ledger), encoding="utf-8",
    )

    proc = run_runner(
        "finalize", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any(
        "deterministic helper report" in error and "coverage_validation.json" in error
        for error in gate["errors"]
    )


def test_gate_rejects_provider_session_reuse_between_candidates(workspace):
    populate_handoffs(workspace)
    state = Path(workspace["state"])
    ledger, errors = ac.load_jsonl(state / "agent_run_ledger.jsonl")
    assert errors == []
    first_provider = next(
        event["provider_session_id"] for event in ledger
        if event.get("phase") == "investigation" and event.get("task_id") == "TASK-001"
    )
    for event in ledger:
        if event.get("phase") == "investigation" and event.get("task_id") == "TASK-002":
            event["provider_session_id"] = first_provider
    (state / "agent_run_ledger.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in ledger), encoding="utf-8",
    )
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("fresh semantic tasks reuse provider session" in error for error in gate["errors"])


def test_gate_rejects_task_plan_edited_after_handoff_merge(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    tasks[0]["starting_points"].append("A directly edited unmerged plan entry.")
    (state / "investigation_tasks.jsonl").write_text(
        "".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8",
    )
    for command in ("task-check", "coverage-check", "review", "report"):
        run_runner(
            command, workspace["code"], workspace["design"],
            workspace["result"], workspace["logs"],
        )

    proc = run_runner(
        "gate", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert any(
        "task handoff merge trace does not validate the current ledger" in error
        for error in gate["errors"]
    )


def test_design_grounded_dynamic_probe_survives_review_report_and_gate(workspace):
    populate_handoffs(workspace)
    attach_dynamic_probe(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    result = ac.load_json(workspace["result"] / "issues.json")
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert result["issues"][0]["dynamic_validation"]["status"] == "supports_contradiction"
    assert gate["checks"]["dynamic_probe_integrity"] is True
    assert gate["metrics"]["dynamic_probes"] == 1


def test_validator_rejects_probe_oracle_rewritten_from_implementation(workspace):
    populate_handoffs(workspace)
    probe = attach_dynamic_probe(workspace)
    probe["oracle"]["expected_observation"] = "Match the current implementation instead of the design."
    state = workspace["state"]
    assert isinstance(state, Path)
    (state / "dynamic_probes.jsonl").write_text(json.dumps(probe) + "\n", encoding="utf-8")
    proc = run_runner(
        "review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any("oracle.expected_observation does not match the design claim" in error for error in trace["errors"])


def test_validator_forces_environment_failure_to_inconclusive(workspace):
    populate_handoffs(workspace)
    attach_dynamic_probe(
        workspace,
        interpretation="supports_contradiction",
        baseline_status="failed",
        execution_status="environment_failed",
        target_reached=False,
        validate_coverage=False,
    )
    proc = run_runner(
        "review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any("environment/baseline/reachability limitations must be inconclusive" in error for error in trace["errors"])


def test_unselected_inventory_section_does_not_require_synthetic_gap(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    inventory = design_source_materializer.materialize_inventory({
        "session_id": workspace["session_id"],
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "scope_relation": "required",
            "scope_evidence": {
                "source_ref": {"path": "contract.md", "line_start": 1, "line_end": 3},
            },
            "sections": [
                {
                    "section_id": "SECTION-CONTRACT-API",
                    "source_ref": {"path": "contract.md", "line_start": 1, "line_end": 3},
                    "behavior_families": ["externally visible service contract"],
                    "ambiguities": [],
                },
                {
                    "section_id": "SECTION-CONTRACT-LIFECYCLE",
                    "source_ref": {"path": "contract.md", "line_start": 4, "line_end": 6},
                    "behavior_families": ["session lifecycle"],
                    "ambiguities": [],
                },
                {
                    "section_id": "SECTION-NOT-MATERIALIZED",
                    "source_ref": {"path": "contract.md", "line_start": 2, "line_end": 2},
                    "behavior_families": ["session lifecycle"],
                    "ambiguities": ["This obligation was not selected for the current frontier."],
                },
            ],
        }],
    }, Path(workspace["design"]))
    ac.save_json(state / "design_inventory.json", inventory)
    plan = ac.load_json(state / "risk_sweep_plan.json")
    plan["design_inventory_sha256"] = ac.sha256_file(state / "design_inventory.json")
    for item in plan["slices"]:
        item["design_section_ids"] = [
            "SECTION-CONTRACT-API", "SECTION-CONTRACT-LIFECYCLE",
            "SECTION-NOT-MATERIALIZED",
        ]
    ac.save_json(state / "risk_sweep_plan.json", plan)
    plan_digest = ac.sha256_file(state / "risk_sweep_plan.json")
    risks, risk_errors = ac.load_jsonl(state / "risk_observations.jsonl")
    assert risk_errors == []
    for item in risks:
        item["risk_sweep_plan_sha256"] = plan_digest
        item["design_section_ids"] = ["SECTION-CONTRACT-API"]
    (state / "risk_observations.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in risks), encoding="utf-8",
    )
    review = ac.load_json(state / "design_claim_review.json")
    review["group_reviews"][0]["group_sha256"] = inventory["document_groups"][0]["group_sha256"]
    ac.save_json(state / "design_claim_review.json", review)
    for command in ("design-check", "claim-check", "task-check"):
        run_runner(
            command, Path(workspace["code"]), Path(workspace["design"]),
            Path(workspace["result"]), Path(workspace["logs"]),
        )
    run_runner(
        "coverage-check", Path(workspace["code"]), Path(workspace["design"]),
        Path(workspace["result"]), Path(workspace["logs"]),
    )
    trace = ac.load_json(Path(workspace["logs"]) / "trace" / "coverage_validation.json")
    assert trace["passed"] is True
    assert trace["closed"] is True
    assert trace["metrics"]["claims"] == 4
    assert trace["metrics"]["remaining_gaps"] == 0


def test_gap_recorded_lens_requires_matching_gap_evidence(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    semantic = ac.load_json(state / "semantic_coverage.json")
    lens = semantic["lenses"][0]
    lens.update({
        "disposition": "gap_recorded",
        "evidence": "The coverage critic identified this unexamined semantic dimension.",
        "task_ids": [],
        "finding_ids": [],
        "counterfactual": "If this lens applied, a focused task would be required.",
    })
    ac.save_json(state / "semantic_coverage.json", semantic)
    proc = run_runner(
        "coverage-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )
    assert proc.returncode == 1
    validation = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert validation["passed"] is False
    assert any("gap_recorded lacks remaining_gaps entry" in error for error in validation["errors"])


def test_coverage_rejects_second_supplement_round(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    coverage = ac.load_json(state / "coverage_audit.json")
    coverage["supplement_rounds"] = 2
    ac.save_json(state / "coverage_audit.json", coverage)
    proc = run_runner(
        "coverage-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "coverage_validation.json")
    assert any("supplement_rounds must be 0 or 1" in error for error in trace["errors"])


def test_gate_rejects_semantic_lens_refs_unrelated_to_task_and_finding(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    semantic = ac.load_json(state / "semantic_coverage.json")
    for entry in semantic["lenses"]:
        entry["task_ids"] = ["TASK-001"]
        entry["finding_ids"] = ["FINDING-TASK-001"]
    ac.save_json(state / "semantic_coverage.json", semantic)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    report = run_runner(
        "report", workspace["code"], workspace["design"], workspace["result"],
        workspace["logs"], check=False,
    )
    assert report.returncode != 0
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("does not declare this lens" in error for error in gate["errors"])


def test_gate_rejects_uninvestigated_parallel_execution_path(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    architecture = ac.load_json(state / "architecture_map.json")
    architecture["implementation_planes"].append({
        "plane_id": "PLANE-ALTERNATE", "kind": "adapter", "paths": ["alternate/"],
        "reachable_evidence": "A second public adapter reaches the same behavior.",
    })
    architecture["parallel_behavior_paths"] = [{
        "path_id": "PARALLEL-DIRECT-ALTERNATE",
        "behavior": "The same contract through direct and alternate entrypoints.",
        "plane_ids": ["PLANE-SERVICE", "PLANE-ALTERNATE"],
        "evidence": "Both entrypoints expose the designed behavior.",
    }]
    ac.save_json(state / "architecture_map.json", architecture)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    report = run_runner(
        "report", workspace["code"], workspace["design"], workspace["result"],
        workspace["logs"], check=False,
    )
    assert report.returncode != 0
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(Path(workspace["logs"]) / "trace" / "final_gate.json")
    assert any("parallel behavior path" in error for error in gate["errors"])


def test_gate_rejects_actionable_uninvestigated_scoped_claim(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    claim = design_source_materializer.materialize_claims([{
        "claim_id": "CLAIM-UNINVESTIGATED",
        "session_id": workspace["session_id"],
        "source_ref": {
            "path": "contract.md", "line_start": 3, "line_end": 3,
        },
        "document_key": "contract",
        "subject": "The public service implementation",
        "trigger": "A caller supplies a negative amount.",
        "obligation": "The service must reject negative amounts.",
        "exceptions": [],
        "observable_result": "The negative request is rejected.",
        "behavior_family": "externally visible service contract",
        "normative_strength": "mandatory",
        "applicability": "service implementation",
        "priority": "high",
        "ambiguities": [],
        "probe_oracle": {
            "testability": "candidate",
            "preconditions": ["The service accepts an amount input."],
            "stimulus": "Call the service with a negative amount.",
            "expected_observation": "The request is rejected.",
            "non_testable_reason": "",
        },
    }], Path(workspace["design"]))[0]
    append(state / "design_claims.jsonl", claim)
    design_coverage = ac.load_json(state / "design_coverage.json")
    design_coverage["document_groups"][0]["claim_ids"].append("CLAIM-UNINVESTIGATED")
    ac.save_json(state / "design_coverage.json", design_coverage)
    scope = ac.load_json(state / "claim_review_scope.json")
    scope["claim_ids"].append("CLAIM-UNINVESTIGATED")
    ac.save_json(state / "claim_review_scope.json", scope)
    claim_review = ac.load_json(state / "design_claim_review.json")
    claim_review["claim_reviews"].append({
        "session_id": workspace["session_id"],
        "claim_id": "CLAIM-UNINVESTIGATED",
        "claim_sha256": handoff_merge.canonical_digest(claim),
        "source_sha256": claim["source_ref"]["source_sha256"],
        "spec_critic_prompt_version": "spec-critic-v2",
        "quote_entailment": {
            "assessment": "entailed", "rationale": "The quoted contract states rejection directly.",
        },
        "normative_strength": {
            "assessment": "correct", "stated_strength": "mandatory",
            "recommended_strength": "mandatory", "rationale": "The source uses must.",
        },
        "atomicity": {
            "assessment": "atomic", "obligations": [claim["obligation"]],
            "rationale": "This is one independently observable behavior.",
        },
        "applicability": {
            "assessment": "supported", "rationale": "The service contract is supplied for this implementation.",
        },
        "decision": "accept", "repair_actions": [],
    })
    claim_review["input_digests"] = {
        "design_claims.jsonl": ac.sha256_file(state / "design_claims.jsonl"),
        "design_coverage.json": ac.sha256_file(state / "design_coverage.json"),
        "design_inventory.json": ac.sha256_file(state / "design_inventory.json"),
        "design_agent_manifest.json": ac.sha256_file(state / "design_agent_manifest.json"),
        "claim_review_scope.json": ac.sha256_file(state / "claim_review_scope.json"),
    }
    ac.save_json(state / "design_claim_review.json", claim_review)
    coverage = ac.load_json(state / "coverage_audit.json")
    coverage["claims_total"] = 5
    coverage["remaining_scoped_claims"] = [{
        "claim_id": "CLAIM-UNINVESTIGATED",
        "reason": "Outside the completed risk-diverse portfolio for this bounded review.",
    }]
    coverage["remaining_gaps"] = [{
        "gap_id": "GAP-SUPPLEMENT-UNINVESTIGATED",
        "kind": "frontier_claim",
        "ref_id": "CLAIM-UNINVESTIGATED",
        "reason": "The accepted claim has no completed investigation.",
        "evidence": "The task/finding ledgers contain no entry for this claim.",
    }]
    contract = ac.load_json(state / "agent_loop_contract.json")
    coverage["next_round_tasks"] = [{
        "claim_id": "CLAIM-UNINVESTIGATED",
        "claim_branch": "CLAIM-UNINVESTIGATED: public negative-amount branch",
        "hypothesis": "Does the public path enforce the negative-amount obligation?",
        "obligation_sha256": stage_artifact_validator.claim_obligation_sha256(claim),
        "exploration_mode": "design-to-code obligation tracing",
        "review_lenses": [contract["coverage_contract"]["portfolio_lenses"][0]],
        "architecture_boundaries": ["BOUNDARY-API"],
        "implementation_planes": ["PLANE-SERVICE"],
        "parallel_path_ids": [],
        "risk_observation_ids": [],
        "source_gap_ids": ["GAP-SUPPLEMENT-UNINVESTIGATED"],
        "priority_reason": "This high-priority claim has no completed investigation.",
    }]
    ac.save_json(state / "coverage_audit.json", coverage)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    report = run_runner(
        "report", workspace["code"], workspace["design"], workspace["result"],
        workspace["logs"], check=False,
    )
    assert report.returncode != 0
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["passed"] is False
    assert any("scoped design claims remain uninvestigated" in error for error in gate["errors"])


def test_gate_rejects_unsupported_inapplicable_lens(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    semantic = ac.load_json(state / "semantic_coverage.json")
    semantic["lenses"][0].update({
        "disposition": "inapplicable",
        "design_group_refs": [],
        "boundary_refs": [],
    })
    ac.save_json(state / "semantic_coverage.json", semantic)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    report = run_runner(
        "report", workspace["code"], workspace["design"], workspace["result"],
        workspace["logs"], check=False,
    )
    assert report.returncode != 0
    proc = run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False)
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert any("needs valid design_group_refs" in error for error in gate["errors"])
    assert any("needs counterfactual" in error for error in gate["errors"])


def test_gate_rejects_target_code_mutation_after_prepare(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    code = workspace["code"]
    assert isinstance(code, Path)
    (code / "service.py").write_text("def changed():\n    return True\n", encoding="utf-8")
    proc = run_runner("gate", code, workspace["design"], workspace["result"], workspace["logs"], check=False)
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["target_roots_unchanged"] is False
    assert any("target file changed after prepare" in error for error in gate["errors"])


def test_gate_rejects_original_catalog_mutation_after_materialization(workspace, tmp_path):
    materialized = prepare_materialized_workspace(workspace, tmp_path)
    populate_handoffs(materialized)
    run_runner(
        "review", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    run_runner(
        "report", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    source = materialized["source"]
    assert isinstance(source, Path)
    (source / "catalog.list").write_text(
        "The supplied catalog was changed after prepare.\n", encoding="utf-8"
    )
    proc = run_runner(
        "gate", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"], check=False,
    )
    assert proc.returncode == 1
    gate = ac.load_json(materialized["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["supplied_design_source_unchanged"] is False
    assert gate["checks"]["target_roots_unchanged"] is False
    assert any(
        "supplied design source file changed after prepare: catalog.list" in error
        for error in gate["errors"]
    )


def test_catalog_source_integrity_includes_normally_ignored_directories(workspace, tmp_path):
    source = Path(workspace["design"])
    hidden = source / ".cache-supplied-design" / "nested.txt"
    hidden.parent.mkdir()
    hidden.write_text("Frozen supplied context.\n", encoding="utf-8")
    materialized = prepare_materialized_workspace(workspace, tmp_path)
    snapshot = ac.load_json(
        Path(materialized["state"]) / "workspace_manifest.json"
    )["design"]["materialization_source"]
    assert ".cache-supplied-design/nested.txt" in {
        record["path"] for record in snapshot["files"]
    }
    populate_handoffs(materialized)
    run_runner(
        "review", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    run_runner(
        "report", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    hidden.write_text("Changed after prepare.\n", encoding="utf-8")

    proc = run_runner(
        "gate", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(Path(materialized["logs"]) / "trace" / "final_gate.json")
    assert any(
        "supplied design source file changed after prepare: "
        ".cache-supplied-design/nested.txt" in error
        for error in gate["errors"]
    )


def test_gate_rejects_materialization_plan_mutation_after_prepare(workspace, tmp_path):
    materialized = prepare_materialized_workspace(workspace, tmp_path)
    populate_handoffs(materialized)
    run_runner(
        "review", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    run_runner(
        "report", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"],
    )
    plan = materialized["state"] / "design_source_plan.json"
    plan.write_text('{"catalog_path":"changed-after-prepare"}\n', encoding="utf-8")

    proc = run_runner(
        "gate", materialized["code"], materialized["design"],
        materialized["result"], materialized["logs"], check=False,
    )

    assert proc.returncode == 1
    gate = ac.load_json(materialized["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["supplied_design_source_unchanged"] is False
    assert any(
        "design materialization plan changed after prepare" in error
        for error in gate["errors"]
    )


def test_gate_rejects_review_snapshot_mutation(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    manifest = ac.load_json(state / "workspace_manifest.json")
    review_code = Path(manifest["paths"]["review_code_root"])
    (review_code / "service.py").write_text("def contaminated():\n    return True\n", encoding="utf-8")
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["target_roots_unchanged"] is True
    assert gate["checks"]["review_snapshots_unchanged"] is False
    assert any("review snapshot file changed after prepare" in error for error in gate["errors"])


def test_gate_rejects_added_review_directory_symlink(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    review_code = Path(ac.load_json(state / "workspace_manifest.json")["paths"]["review_code_root"])
    (review_code / "unexpected-link").symlink_to(".", target_is_directory=True)
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["review_snapshots_unchanged"] is False
    assert any("review snapshot tree has files added" in error for error in gate["errors"])


def test_gate_rejects_missing_review_git_barrier(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    review_code = Path(ac.load_json(state / "workspace_manifest.json")["paths"]["review_code_root"])
    (review_code / ".git").unlink()
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["review_snapshots_unchanged"] is False
    assert any("Git isolation barrier is missing" in error for error in gate["errors"])


def test_gate_rejects_symlinked_review_parent_even_with_identical_files(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    review_parent = state / "review-inputs"
    backup = state / "review-inputs-backup"
    review_parent.rename(backup)
    review_parent.symlink_to(backup, target_is_directory=True)
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["review_snapshots_unchanged"] is False
    assert any("review code root path contains a symlink" in error for error in gate["errors"])


def test_gate_rejects_failed_session_preflight(workspace):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    state = workspace["state"]
    assert isinstance(state, Path)
    manifest_path = state / "workspace_manifest.json"
    manifest = ac.load_json(manifest_path)
    manifest["preflight_problems"] = ["review input could not be isolated"]
    ac.save_json(manifest_path, manifest)
    proc = run_runner(
        "gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["checks"]["preflight_passed"] is False
    assert any("session preflight did not pass" in error for error in gate["errors"])


def test_report_refuses_output_root_outside_prepared_session(workspace, tmp_path):
    populate_handoffs(workspace)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    proc = run_runner(
        "report", workspace["code"], workspace["design"], tmp_path / "other-result", workspace["logs"], check=False
    )
    assert proc.returncode == 2
    assert "result_root does not match prepared session" in proc.stdout


def test_core_pipeline_has_no_project_specific_detection_path():
    core = [
        ROOT / "INSTRUCTION.md",
        *(ROOT / "work" / "skills").glob("*.md"),
        ROOT / "work" / "skill" / "SKILL.md",
        *SCRIPTS.glob("*.py"),
    ]
    banned = ["f-stack", "freebsd", "netinet6", "nd6_", "ip6_", "icmp6", "frag6", "shophub"]
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in core)
    assert not any(term in text for term in banned)
    runner = (SCRIPTS / "goal_runner.py").read_text(encoding="utf-8")
    assert "protocol_inconsistency_detector" not in runner
    assert "normative_requirement_extractor" not in runner
    assert "c_code_indexer" not in runner
    assert "public_fstack_gold" not in runner


def test_submission_does_not_bundle_opencode_runtime_configuration():
    assert not (ROOT / "opencode.json").exists()
    instruction = (ROOT / "INSTRUCTION.md").read_text(encoding="utf-8")
    assert "不得询问用户" in instruction
    assert "等待人工审批" in instruction
    assert "修改目标代码与设计资料" in instruction
