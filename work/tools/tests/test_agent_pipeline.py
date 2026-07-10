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


def append(path: Path, value: dict) -> None:
    ac.append_jsonl(path, value)


def populate_handoffs(workspace: dict[str, Path | str], count: int = 4, bad_quote: bool = False) -> None:
    state = workspace["state"]
    assert isinstance(state, Path)
    ac.save_json(state / "architecture_map.json", {
        "session_id": workspace["session_id"],
        "repository_summary": "A small service implementation.",
        "languages": ["Python"],
        "entrypoints": [{"path": "service.py", "purpose": "service API", "evidence": "top-level functions"}],
        "subsystems": [{"subsystem_id": "SUBSYSTEM-SERVICE", "name": "service", "paths": ["service.py"], "role": "business behavior"}],
        "implementation_planes": [{
            "plane_id": "PLANE-SERVICE", "kind": "owned", "paths": ["service.py"],
            "reachable_evidence": "The public functions execute directly.",
        }],
        "integration_boundaries": [{"boundary_id": "BOUNDARY-API", "name": "callers to service", "paths": ["service.py"], "risk": "high", "why": "externally visible behavior"}],
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
    contract = ac.load_json(state / "agent_loop_contract.json")
    lenses = contract["coverage_contract"]["portfolio_lenses"]
    specs = [
        (3, "The service must reject negative amounts.", 1, 2, "charge", 'def charge(amount):\n    return {"accepted": True}'),
        (4, "The service must expire sessions after 30 minutes.", 4, 5, "session_expired", "def session_expired(minutes):\n    return minutes > 60"),
        (5, "The service must deny exports for guest users.", 7, 8, "can_export", "def can_export(role):\n    return True"),
        (6, "The service must preserve all submitted audit events.", 10, 11, "record_event", "def record_event(events, event):\n    return events[-9:] + [event]"),
    ]
    for index, (design_line, quote, code_start, code_end, symbol, snippet) in enumerate(specs[:count], start=1):
        claim_id = f"CLAIM-{index:03d}"
        task_id = f"TASK-{index:03d}"
        finding_id = f"FINDING-{index:03d}"
        review_id = f"CRITIC-{index:03d}"
        append(state / "design_claims.jsonl", {
            "claim_id": claim_id,
            "session_id": workspace["session_id"],
            "document": "Service contract",
            "path": "contract.md",
            "section": "Service contract",
            "line_start": design_line,
            "line_end": design_line,
            "quote": quote,
            "behavior": quote,
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
        })
        task_lenses = lenses[(index - 1) * 2:index * 2]
        append(state / "investigation_tasks.jsonl", {
            "task_id": task_id,
            "session_id": workspace["session_id"],
            "claim_id": claim_id,
            "question": "Does the implementation enforce the stated behavior?",
            "starting_points": ["public service entry point"],
            "supporting_evidence_needed": ["reachable implementation"],
            "disconfirming_evidence_needed": ["alternate enforcement path"],
            "status": "complete",
            "defer_reason": "",
            "review_lenses": task_lenses,
            "exploration_mode": contract["coverage_contract"]["exploration_modes"][(index - 1) % 3],
            "architecture_boundaries": ["BOUNDARY-API"],
            "implementation_planes": ["PLANE-SERVICE"],
        })
        finding = {
            "finding_id": finding_id,
            "session_id": workspace["session_id"],
            "task_id": task_id,
            "claim_id": claim_id,
            "hypothesis": "The implementation contradicts the design claim.",
            "expected_behavior": quote,
            "observed_behavior": "The cited implementation permits behavior the design forbids.",
            "design_evidence": [{
                "document": "Service contract", "path": "contract.md", "section": "Service contract",
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
            "challenges": ["Could another reachable path enforce the claim?"],
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
        append(state / "critic_reviews.jsonl", critic)
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
            "design_evidence": [{
                "document": "Service contract", "path": "contract.md", "section": "Service contract",
                "line_start": design_line, "line_end": design_line, "quote": design_quote,
            }],
            "code_evidence": [{
                "file": "service.py", "line_start": code_start, "line_end": code_end,
                "symbol": symbol, "snippet": snippet,
            }],
            "expected_behavior": quote,
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
    ac.save_json(state / "design_coverage.json", {
        "session_id": workspace["session_id"],
        "document_groups": [{
            "document_key": "contract",
            "members": ["contract.md"],
            "disposition": "applicable",
            "evidence": "The document explicitly defines the service implementation contract.",
            "claim_ids": [f"CLAIM-{index:03d}" for index in range(1, count + 1)],
            "behavior_families": ["externally visible service contract"],
        }],
    })
    ac.save_json(state / "semantic_coverage.json", {
        "session_id": workspace["session_id"],
        "lenses": [{
            "lens": lens,
            "disposition": "investigated",
            "evidence": "The service fixture exposes this concern at its public API boundary.",
            "task_ids": [f"TASK-{index // 2 + 1:03d}"],
            "finding_ids": [f"FINDING-{index // 2 + 1:03d}"],
            "design_group_refs": ["contract"],
            "boundary_refs": ["BOUNDARY-API"],
        } for index, lens in enumerate(lenses)],
    })
    append(state / "investigation_rounds.jsonl", {
        "session_id": workspace["session_id"],
        "round_id": "ROUND-001",
        "strategy": "Check externally visible service behaviors.",
        "exploration_modes": contract["coverage_contract"]["exploration_modes"],
        "document_groups": ["contract"],
        "architecture_boundaries": ["BOUNDARY-API"],
        "implementation_planes": ["PLANE-SERVICE"],
        "lenses": lenses,
        "claim_ids": [f"CLAIM-{index:03d}" for index in range(1, count + 1)],
        "task_ids": [f"TASK-{index:03d}" for index in range(1, count + 1)],
        "finding_ids": [f"FINDING-{index:03d}" for index in range(1, count + 1)],
        "outcome": "Four contradictions independently verified.",
        "next_strategy": "finalize",
    })
    ac.save_json(state / "coverage_audit.json", {
        "session_id": workspace["session_id"],
        "design_documents_reviewed": ["contract.md"],
        "claims_total": count,
        "claims_investigated": count,
        "rounds_completed": 1,
        "exploration_modes_completed": contract["coverage_contract"]["exploration_modes"],
        "document_groups_total": 1,
        "document_groups_accounted": 1,
        "code_areas_reviewed": ["service.py"],
        "architecture_boundaries": [{
            "boundary_id": "BOUNDARY-API", "status": "investigated",
            "evidence": "All public service entry points were inspected.",
        }],
        "remaining_high_priority_claims": [],
        "deferred_claims": [],
        "false_positive_samples_rechecked": [f"FINDING-{index:03d}" for index in range(1, count + 1)],
        "stop_reason": "All high-priority fixture claims were investigated and independently reviewed.",
    })


def attach_dynamic_probe(
    workspace: dict[str, Path | str],
    *,
    interpretation: str = "supports_contradiction",
    baseline_status: str = "passed",
    execution_status: str = "completed",
    target_reached: bool = True,
) -> dict:
    state = workspace["state"]
    assert isinstance(state, Path)
    claims, _ = ac.load_jsonl(state / "design_claims.jsonl")
    claim = claims[0]
    finding_id = "FINDING-001"
    probe_id = "PROBE-001"
    probe_workspace = state / "probes" / probe_id / "workspace"
    probe_workspace.mkdir(parents=True)
    probe = {
        "probe_id": probe_id,
        "session_id": workspace["session_id"],
        "finding_id": finding_id,
        "claim_id": claim["claim_id"],
        "oracle": {
            "source": "design_claim",
            "preconditions": claim["probe_oracle"]["preconditions"],
            "stimulus": claim["probe_oracle"]["stimulus"],
            "expected_observation": claim["probe_oracle"]["expected_observation"],
        },
        "selection_reason": "The public function is observable and the existing Python runtime is available.",
        "isolation": {
            "kind": "session_copy",
            "workspace": str(probe_workspace),
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

    findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
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
    (state / "critic_reviews.jsonl").write_text(
        "\n".join(json.dumps(item) for item in critiques) + "\n", encoding="utf-8"
    )

    verdicts, _ = ac.load_jsonl(state / "agent_review_verdicts.jsonl")
    verdicts[0]["dynamic_validation"] = {
        "status": interpretation,
        "probe_id": probe_id,
        "reason": "A design-grounded isolated probe was independently reviewed as scoped supporting evidence.",
    }
    (state / "agent_review_verdicts.jsonl").write_text(
        "\n".join(json.dumps(item) for item in verdicts) + "\n", encoding="utf-8"
    )
    return probe


def test_prepare_is_semantic_neutral_and_writes_agent_contract(workspace):
    state = workspace["state"]
    assert isinstance(state, Path)
    manifest = ac.load_json(state / "workspace_manifest.json")
    contract = ac.load_json(state / "agent_loop_contract.json")
    assert manifest["semantic_analysis_performed"] is False
    assert manifest["design"]["document_count"] == 1
    assert manifest["code"]["suffix_counts"] == {".py": 1}
    assert contract["execution_model"] == "opencode-owned-model-driven-loop"
    assert contract["contract_version"] == 7
    assert contract["handoff_integrity"]["max_concurrent_subagent_tasks"] == 2
    assert len(contract["coverage_contract"]["exploration_modes"]) == 3
    assert "dynamic_probe" in contract["coverage_contract"]
    assert [phase["owner"] for phase in contract["phases"]][:7] == [
        "orchestrator", "spec_analyst", "orchestrator", "code_investigator",
        "orchestrator_then_code_investigator", "evidence_critic", "final_judge",
    ]
    assert (state / "dynamic_probes.jsonl").is_file()
    assert not (state / "candidate_issues.json").exists()


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
    (source / "benchmark.md").write_text("Use the linked service contract.\n", encoding="utf-8")
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
                "quote": "Use the linked service contract.",
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
    assert template["expected_behavior"] == claims[0]["behavior"]
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


def test_finding_template_enforces_two_unmerged_items_and_failed_batch_lock(workspace):
    populate_handoffs(workspace, count=4)
    state = workspace["state"]
    assert isinstance(state, Path)
    template_root = state / "handoff-templates" / "investigators"

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
    assert blocked.returncode == 3
    assert "two investigator templates are already unresolved" in blocked.stdout

    ac.save_json(state / "investigator_batch_gate.json", {
        "passed": True,
        "validated_ids": ["FINDING-TASK-001", "FINDING-TASK-002"],
        "errors": [],
    })
    assert generate("TASK-003").returncode == 0

    ac.save_json(state / "investigator_batch_gate.json", {
        "passed": False,
        "invalid_ids": ["FINDING-TASK-003"],
        "errors": ["invalid handoff"],
    })
    locked = generate("TASK-004")
    assert locked.returncode == 3
    assert "previous investigator batch has not passed merge" in locked.stdout


def test_failed_finding_merge_writes_batch_gate_and_detailed_stdout(workspace, tmp_path):
    handoffs = tmp_path / "handoffs"
    state = tmp_path / "state"
    handoffs.mkdir()
    ac.save_json(handoffs / "bad.json", {
        "finding_id": "FINDING-BAD", "session_id": workspace["session_id"],
        "task_id": "TASK-BAD", "claim_id": "CLAIM-BAD",
    })
    proc = subprocess.run([
        sys.executable, str(SCRIPTS / "handoff_merge.py"),
        "--input-dir", str(handoffs),
        "--output", str(state / "investigation_findings.jsonl"),
        "--artifact-type", "finding", "--session-id", str(workspace["session_id"]),
    ], text=True, capture_output=True)
    assert proc.returncode == 1
    stdout = json.loads(proc.stdout)
    assert stdout["invalid_ids"] == ["FINDING-BAD"]
    assert stdout["errors"]
    gate = ac.load_json(state / "investigator_batch_gate.json")
    assert gate["passed"] is False
    assert gate["invalid_ids"] == ["FINDING-BAD"]


def test_instruction_makes_successful_batch_report_a_hard_progression_gate():
    instruction = (ROOT / "INSTRUCTION.md").read_text(encoding="utf-8")
    assert "--check-file" in instruction
    assert "--report" in instruction
    assert "merge 返回非 0 时，**禁止启动下一批**" in instruction
    assert "validated_ids" in instruction
    assert "${REVIEW_CODE_ROOT}" in instruction
    assert "investigator_batch_gate.json" in instruction


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
    )
    proc = run_runner(
        "review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False
    )
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "evidence_validation.json")
    assert any("environment/baseline/reachability limitations must be inconclusive" in error for error in trace["errors"])


def test_gate_rejects_unaccounted_design_groups(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    ac.save_json(state / "design_coverage.json", {
        "session_id": workspace["session_id"], "document_groups": [],
    })
    proc = run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False)
    assert proc.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "design_validation.json")
    assert trace["passed"] is False
    assert any("design coverage missing document groups" in error for error in trace["errors"])


def test_gate_rejects_behavior_family_without_a_claim(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    coverage = ac.load_json(state / "design_coverage.json")
    coverage["document_groups"][0]["behavior_families"].append("unrepresented behavior family")
    ac.save_json(state / "design_coverage.json", coverage)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    proc = run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False)
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert any("behavior families lack claims" in error for error in gate["errors"])


def test_gate_rejects_one_finding_claiming_every_review_lens(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    contract = ac.load_json(state / "agent_loop_contract.json")
    lenses = contract["coverage_contract"]["portfolio_lenses"]
    tasks, _ = ac.load_jsonl(state / "investigation_tasks.jsonl")
    findings, _ = ac.load_jsonl(state / "investigation_findings.jsonl")
    tasks[0]["review_lenses"] = lenses
    findings[0]["review_lenses"] = lenses
    (state / "investigation_tasks.jsonl").write_text(
        "\n".join(json.dumps(item) for item in tasks) + "\n", encoding="utf-8"
    )
    (state / "investigation_findings.jsonl").write_text(
        "\n".join(json.dumps(item) for item in findings) + "\n", encoding="utf-8"
    )
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    proc = run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"], check=False)
    assert proc.returncode == 1
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert sum("focused to at most three" in error for error in gate["errors"]) >= 2


def test_gate_allows_audited_uninvestigated_claims_outside_the_risk_portfolio(workspace):
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    append(state / "design_claims.jsonl", {
        "claim_id": "CLAIM-UNINVESTIGATED",
        "session_id": workspace["session_id"],
        "document": "Service contract",
        "path": "contract.md",
        "section": "Service contract",
        "line_start": 3,
        "line_end": 3,
        "quote": "The service must reject negative amounts.",
        "behavior": "Negative amounts are rejected.",
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
    })
    design_coverage = ac.load_json(state / "design_coverage.json")
    design_coverage["document_groups"][0]["claim_ids"].append("CLAIM-UNINVESTIGATED")
    ac.save_json(state / "design_coverage.json", design_coverage)
    coverage = ac.load_json(state / "coverage_audit.json")
    coverage["claims_total"] = 5
    coverage["remaining_high_priority_claims"] = [{
        "claim_id": "CLAIM-UNINVESTIGATED",
        "reason": "Outside the completed risk-diverse portfolio for this bounded review.",
    }]
    ac.save_json(state / "coverage_audit.json", coverage)
    run_runner("review", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    run_runner("gate", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
    gate = ac.load_json(workspace["logs"] / "trace" / "final_gate.json")
    assert gate["passed"] is True


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
    run_runner("report", workspace["code"], workspace["design"], workspace["result"], workspace["logs"])
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
    assert "不要询问用户" in instruction
    assert "不要修改目标代码或设计文档" in instruction
