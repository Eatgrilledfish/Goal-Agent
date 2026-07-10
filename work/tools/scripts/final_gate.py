#!/usr/bin/env python3
"""Verify agent-loop provenance, output integrity, target metrics, and duration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import verdict_validator as vv


MIN_CONFIRMED = 4
MAX_SECONDS = 21600


def _index(path: Path, key: str) -> tuple[dict[str, dict], list[str]]:
    values, errors = ac.load_jsonl(path)
    return {str(value.get(key)): value for value in values if value.get(key)}, errors


def _check_issue(issue: dict[str, Any], session_id: str, result_root: Path) -> list[str]:
    errors: list[str] = []
    issue_id = str(issue.get("issue_id") or "?")
    required = [
        "issue_id", "finding_id", "claim_id", "title", "status", "confidence", "severity",
        "issue_type", "design_evidence", "code_evidence", "expected_behavior", "actual_behavior",
        "inconsistency", "impact",
        "scope_applicability", "false_positive_checks", "dynamic_validation", "agent_review", "report_path",
    ]
    for field in required:
        if issue.get(field) in (None, "", [], {}):
            errors.append(f"{issue_id}: missing/empty {field}")
    if issue.get("status") != "confirmed":
        errors.append(f"{issue_id}: only confirmed findings may be published")
    review = issue.get("agent_review") if isinstance(issue.get("agent_review"), dict) else {}
    if review.get("source") != "opencode":
        errors.append(f"{issue_id}: agent_review.source must be opencode")
    if review.get("session_id") != session_id:
        errors.append(f"{issue_id}: agent_review session does not match current session")
    critic = review.get("critic_review") if isinstance(review.get("critic_review"), dict) else {}
    if critic.get("decision") != "confirm_contradiction":
        errors.append(f"{issue_id}: independent critic did not confirm a contradiction")
    if not review.get("tool_trace"):
        errors.append(f"{issue_id}: tool trace missing")
    report_path = Path(str(issue.get("report_path") or ""))
    if not report_path.is_file():
        errors.append(f"{issue_id}: report file missing: {report_path}")
    elif ac.contained_path(result_root, str(report_path)) is None:
        errors.append(f"{issue_id}: report file is outside result root: {report_path}")
    return errors


def _tree_changes(root: Path, records: list[dict[str, Any]]) -> list[str]:
    """Compare current target files with the prepare-time content snapshot."""
    expected = {str(record.get("path")): record for record in records if record.get("path")}
    current = {ac.relative_path(root, path): path for path in ac.iter_files(root)}
    errors: list[str] = []
    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    if added:
        errors.append(f"target tree has files added after prepare: {added[:10]}")
    if removed:
        errors.append(f"target tree has files removed after prepare: {removed[:10]}")
    for relative in sorted(set(expected) & set(current)):
        record = expected[relative]
        path = current[relative]
        expected_kind = str(record.get("kind") or "file")
        actual_kind = "symlink" if path.is_symlink() else "file"
        if actual_kind != expected_kind:
            errors.append(f"target tree entry kind changed after prepare: {relative}")
            continue
        if expected_kind == "symlink":
            if str(path.readlink()) != str(record.get("link_target") or ""):
                errors.append(f"target symlink changed after prepare: {relative}")
            continue
        expected_hash = str(record.get("sha256") or "")
        if not expected_hash:
            errors.append(f"prepare snapshot lacks sha256 for target file: {relative}")
        elif ac.sha256_file(path) != expected_hash:
            errors.append(f"target file changed after prepare: {relative}")
    return errors


def _require_fields(value: dict[str, Any], fields: tuple[str, ...], label: str) -> list[str]:
    return [f"{label}: missing/empty {field}" for field in fields if value.get(field) in (None, "", [], {})]


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    trace_root = ac.ensure_dir(log_root / "trace")
    state = ac.load_json(root / "agent_loop_state.json")
    session_id = str(state.get("session_id") or "")
    errors: list[str] = []
    checks: dict[str, bool] = {}

    result_path = result_root / "issues.json"
    result = ac.load_json(result_path) if result_path.exists() else {}
    if not result:
        errors.append("issues.json is missing or empty")
    if result.get("tool") != "goal-agent-design-code-diff":
        errors.append("issues.json has the wrong tool identifier")
    if result.get("session_id") != session_id:
        errors.append("issues.json session does not match current session")
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    published_finding_ids = {
        str(issue.get("finding_id")) for issue in issues if issue.get("finding_id")
    }
    published_claim_ids = {
        str(issue.get("claim_id")) for issue in issues if issue.get("claim_id")
    }
    if result.get("summary", {}).get("total") != len(issues):
        errors.append("issues.json summary.total does not match issue count")
    for issue in issues:
        errors.extend(_check_issue(issue, session_id, result_root))

    claims, claim_errors = _index(root / "design_claims.jsonl", "claim_id")
    tasks, task_errors = _index(root / "investigation_tasks.jsonl", "task_id")
    findings, finding_errors = _index(root / "investigation_findings.jsonl", "finding_id")
    critiques, critic_errors = _index(root / "critic_reviews.jsonl", "finding_id")
    probes, probe_errors = _index(root / "dynamic_probes.jsonl", "probe_id")
    verdicts, verdict_errors = _index(root / "agent_review_verdicts.jsonl", "finding_id")
    rounds, round_errors = _index(root / "investigation_rounds.jsonl", "round_id")
    errors.extend(claim_errors + task_errors + finding_errors + critic_errors + probe_errors + verdict_errors)
    errors.extend(round_errors)
    probe_integrity_errors: list[str] = []
    investigated_claim_ids = {
        str(finding.get("claim_id")) for finding in findings.values() if finding.get("claim_id")
    }

    for issue in issues:
        claim_id = str(issue.get("claim_id") or "")
        if claim_id not in claims:
            errors.append(f"{issue.get('issue_id', '?')}: claim handoff missing")
        finding_id = str(issue.get("finding_id") or "")
        if finding_id not in findings or finding_id not in critiques or finding_id not in verdicts:
            errors.append(f"{issue.get('issue_id', '?')}: investigator/critic/judge handoff chain is incomplete")
            continue
        finding = findings[finding_id]
        if finding.get("assessment") != "contradiction_supported":
            errors.append(f"{issue.get('issue_id', '?')}: investigator did not assess a supported contradiction")
        if critiques[finding_id].get("decision") != "confirm_contradiction":
            errors.append(f"{issue.get('issue_id', '?')}: critic artifact did not confirm a contradiction")
        task_id = str(finding.get("task_id") or "")
        if task_id not in tasks:
            errors.append(f"{issue.get('issue_id', '?')}: finding does not reference an investigation task")
        elif str(tasks[task_id].get("claim_id") or "") != claim_id:
            errors.append(f"{issue.get('issue_id', '?')}: task claim_id does not match published issue")
        elif tasks[task_id].get("status") != "complete":
            errors.append(f"{issue.get('issue_id', '?')}: investigation task is not complete")
        if str(finding.get("claim_id") or "") != claim_id:
            errors.append(f"{issue.get('issue_id', '?')}: finding claim_id does not match published issue")
        dynamic = issue.get("dynamic_validation") if isinstance(issue.get("dynamic_validation"), dict) else {}
        verdict_dynamic = verdicts[finding_id].get("dynamic_validation")
        if dynamic != verdict_dynamic:
            probe_integrity_errors.append(
                f"{issue.get('issue_id', '?')}: published dynamic_validation does not match judge verdict"
            )
        dynamic_status = dynamic.get("status")
        probe_id = str(dynamic.get("probe_id") or "")
        probe_review = critiques[finding_id].get("dynamic_probe_review")
        if not isinstance(probe_review, dict):
            probe_integrity_errors.append(
                f"{issue.get('issue_id', '?')}: critic handoff lacks dynamic_probe_review"
            )
        else:
            if probe_review.get("status") != dynamic_status:
                probe_integrity_errors.append(
                    f"{issue.get('issue_id', '?')}: critic dynamic probe status does not match published issue"
                )
            if str(probe_review.get("probe_id") or "") != probe_id:
                probe_integrity_errors.append(
                    f"{issue.get('issue_id', '?')}: critic dynamic probe_id does not match published issue"
                )
        if dynamic_status not in vv.PROBE_INTERPRETATIONS | {"not_run"}:
            probe_integrity_errors.append(
                f"{issue.get('issue_id', '?')}: invalid dynamic validation status {dynamic_status!r}"
            )
        elif dynamic_status == "not_run":
            if probe_id:
                probe_integrity_errors.append(
                    f"{issue.get('issue_id', '?')}: not_run dynamic validation references a probe"
                )
        else:
            probe = probes.get(probe_id)
            if not probe:
                probe_integrity_errors.append(
                    f"{issue.get('issue_id', '?')}: dynamic probe handoff missing: {probe_id}"
                )
            elif claim_id in claims:
                probe_integrity_errors.extend(vv._validate_dynamic_probe(
                    probe, root=root, session_id=session_id, finding_id=finding_id,
                    claim_id=claim_id, claim=claims[claim_id],
                ))
                if probe.get("interpretation") != dynamic_status:
                    probe_integrity_errors.append(
                        f"{issue.get('issue_id', '?')}: probe interpretation does not match published issue"
                    )

    errors.extend(probe_integrity_errors)

    manifest_path = root / "workspace_manifest.json"
    manifest = ac.load_json(manifest_path) if manifest_path.exists() else {}
    manifest_paths = manifest.get("paths", {}) if isinstance(manifest.get("paths"), dict) else {}
    expected_runtime_paths = {
        "code_root": str(code_root),
        "design_root": str(design_root),
        "result_root": str(result_root),
        "log_root": str(log_root),
        "state_root": str(root),
    }
    for name, expected in expected_runtime_paths.items():
        if manifest_paths.get(name) != expected:
            errors.append(f"runtime {name} does not match prepared session")
    if result.get("code_root") != str(code_root) or result.get("design_root") != str(design_root):
        errors.append("issues.json code/design roots do not match gate inputs")
    errors.extend(_tree_changes(code_root, manifest.get("code", {}).get("files", [])))
    errors.extend(_tree_changes(
        design_root,
        manifest.get("design", {}).get("source_files", manifest.get("design", {}).get("documents", [])),
    ))
    expected_groups = {
        str(group.get("document_key"))
        for group in manifest.get("design", {}).get("document_groups", [])
        if group.get("document_key")
    }
    source_manifest = manifest.get("design", {}).get("source_manifest")
    if isinstance(source_manifest, dict):
        remote_locations = {
            str(item.get("location"))
            for item in source_manifest.get("sources", [])
            if isinstance(item, dict) and item.get("kind") == "url" and item.get("location")
        }
        if remote_locations:
            approvals, approval_errors = ac.load_jsonl(root / "approval_events.jsonl")
            errors.extend(approval_errors)
            approved_locations = {
                str(item.get("scope"))
                for item in approvals
                if item.get("action") == "read_only_design_fetch"
                and item.get("decision") == "auto_approved"
            }
            missing_approvals = sorted(remote_locations - approved_locations)
            if missing_approvals:
                errors.append(f"remote design sources lack approval events: {missing_approvals}")
    design_coverage_path = root / "design_coverage.json"
    design_coverage = ac.load_json(design_coverage_path) if design_coverage_path.exists() else {}
    if design_coverage.get("session_id") not in (None, session_id):
        errors.append("design coverage session does not match current session")
    coverage_groups = {
        str(group.get("document_key")): group
        for group in design_coverage.get("document_groups", [])
        if isinstance(group, dict) and group.get("document_key")
    }
    missing_groups = sorted(expected_groups - set(coverage_groups))
    extra_groups = sorted(set(coverage_groups) - expected_groups)
    if missing_groups:
        errors.append(f"design coverage missing document groups: {missing_groups[:10]}")
    if extra_groups:
        errors.append(f"design coverage contains unknown document groups: {extra_groups[:10]}")
    valid_dispositions = {"applicable", "inapplicable", "superseded", "supporting"}
    for key, group in coverage_groups.items():
        disposition = group.get("disposition")
        if disposition not in valid_dispositions:
            errors.append(f"design coverage {key}: invalid disposition {disposition!r}")
        if not group.get("evidence"):
            errors.append(f"design coverage {key}: missing applicability evidence")
        if disposition == "applicable" and not group.get("behavior_families"):
            errors.append(f"design coverage {key}: applicable group has no behavior_families")
        group_claims = [str(value) for value in group.get("claim_ids", []) if value]
        if disposition == "applicable" and not group_claims:
            errors.append(f"design coverage {key}: applicable group has no claims")
        for claim_id in group_claims:
            if claim_id not in claims:
                errors.append(f"design coverage {key}: unknown claim_id {claim_id}")
            else:
                claim_path = str(claims[claim_id].get("path") or "")
                claim_key = str(Path(claim_path).with_suffix("")).lower() if claim_path else ""
                if claim_key != key:
                    errors.append(f"design coverage {key}: claim {claim_id} cites different document group {claim_key}")
        represented_families = {
            str(claims[claim_id].get("behavior_family"))
            for claim_id in group_claims
            if claim_id in claims and claims[claim_id].get("behavior_family")
        }
        missing_families = sorted(set(group.get("behavior_families", [])) - represented_families)
        if disposition == "applicable" and missing_families:
            errors.append(f"design coverage {key}: behavior families lack claims {missing_families}")

    for claim_id, claim in claims.items():
        if claim.get("session_id") != session_id:
            errors.append(f"design claim {claim_id}: session does not match current session")
        for field in (
            "path", "section", "line_start", "line_end", "quote", "behavior", "behavior_family",
            "normative_strength", "applicability", "priority",
        ):
            if claim.get(field) in (None, "", [], {}):
                errors.append(f"design claim {claim_id}: missing/empty {field}")
        if claim_id in published_claim_ids:
            errors.extend(ac.validate_source_evidence(claim, design_root, f"design claim {claim_id}", "quote"))
        if claim.get("normative_strength") not in {
            "mandatory", "recommended", "optional", "declared_capability", "informational",
        }:
            errors.append(f"design claim {claim_id}: invalid normative_strength")
        if claim.get("priority") not in {"high", "medium", "low"}:
            errors.append(f"design claim {claim_id}: invalid priority")
        oracle = claim.get("probe_oracle") if isinstance(claim.get("probe_oracle"), dict) else {}
        if oracle.get("testability") not in {"candidate", "not_suitable", "unknown"}:
            errors.append(f"design claim {claim_id}: invalid or missing probe_oracle.testability")
        if "preconditions" not in oracle or not isinstance(oracle.get("preconditions"), list):
            errors.append(f"design claim {claim_id}: probe_oracle.preconditions must be an array")
        if oracle.get("testability") == "candidate":
            if not oracle.get("stimulus") or not oracle.get("expected_observation"):
                errors.append(f"design claim {claim_id}: candidate probe oracle needs stimulus and expected_observation")
        if oracle.get("testability") == "not_suitable" and not oracle.get("non_testable_reason"):
            errors.append(f"design claim {claim_id}: not_suitable probe oracle needs non_testable_reason")

    contract_path = root / "agent_loop_contract.json"
    contract = ac.load_json(contract_path) if contract_path.exists() else {}
    expected_lenses = set(contract.get("coverage_contract", {}).get("portfolio_lenses", []))
    expected_modes = set(contract.get("coverage_contract", {}).get("exploration_modes", []))

    architecture_path = root / "architecture_map.json"
    architecture = ac.load_json(architecture_path) if architecture_path.exists() else {}
    for field in (
        "session_id", "repository_summary", "languages", "entrypoints", "subsystems",
        "implementation_planes", "integration_boundaries", "capability_surfaces",
        "configuration_surfaces", "alternate_execution_paths", "test_surfaces", "parallel_behavior_paths",
        "probe_capabilities",
    ):
        if field not in architecture:
            errors.append(f"architecture_map.json missing {field}")
    if architecture.get("session_id") not in (None, session_id):
        errors.append("architecture map session does not match current session")
    architecture_boundary_ids = {
        str(item.get("boundary_id")) for item in architecture.get("integration_boundaries", [])
        if isinstance(item, dict) and item.get("boundary_id")
    }
    architecture_plane_ids = {
        str(item.get("plane_id")) for item in architecture.get("implementation_planes", [])
        if isinstance(item, dict) and item.get("plane_id")
    }
    parallel_behavior_paths: list[dict[str, Any]] = []
    for index, item in enumerate(architecture.get("parallel_behavior_paths", []), start=1):
        label = f"architecture parallel_behavior_paths[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: must be an object")
            continue
        errors.extend(_require_fields(item, ("behavior", "plane_ids", "evidence"), label))
        plane_ids = {str(value) for value in item.get("plane_ids", []) if value}
        if len(plane_ids) < 2:
            errors.append(f"{label}: must identify at least two implementation planes")
        unknown = plane_ids - architecture_plane_ids
        if unknown:
            errors.append(f"{label}: unknown implementation planes {sorted(unknown)}")
        parallel_behavior_paths.append(item)

    for task_id, task in tasks.items():
        errors.extend(_require_fields(task, (
            "session_id", "claim_id", "question", "starting_points", "supporting_evidence_needed",
            "disconfirming_evidence_needed", "review_lenses", "exploration_mode", "architecture_boundaries", "status",
            "implementation_planes",
        ), f"investigation task {task_id}"))
        if task.get("session_id") != session_id:
            errors.append(f"investigation task {task_id}: session does not match current session")
        if str(task.get("claim_id") or "") not in claims:
            errors.append(f"investigation task {task_id}: unknown claim_id")
        if task.get("exploration_mode") not in expected_modes:
            errors.append(f"investigation task {task_id}: invalid exploration_mode {task.get('exploration_mode')!r}")
        unknown_lenses = set(task.get("review_lenses", [])) - expected_lenses
        if unknown_lenses:
            errors.append(f"investigation task {task_id}: unknown review lenses {sorted(unknown_lenses)}")
        if len(task.get("review_lenses", [])) > 3:
            errors.append(f"investigation task {task_id}: review_lenses must be focused to at most three")
        unknown_boundaries = set(task.get("architecture_boundaries", [])) - architecture_boundary_ids
        if unknown_boundaries:
            errors.append(f"investigation task {task_id}: unknown architecture boundaries {sorted(unknown_boundaries)}")
        unknown_planes = set(task.get("implementation_planes", [])) - architecture_plane_ids
        if unknown_planes:
            errors.append(f"investigation task {task_id}: unknown implementation planes {sorted(unknown_planes)}")

    for finding_id, finding in findings.items():
        if finding_id not in published_finding_ids:
            continue
        required_finding_fields = (
            "session_id", "task_id", "claim_id", "hypothesis", "expected_behavior", "observed_behavior",
            "code_evidence", "tool_trace", "assessment", "review_lenses", "recommendation",
        )
        if finding.get("assessment") == "contradiction_supported":
            required_finding_fields += ("design_evidence", "supporting_evidence", "false_positive_checks")
        errors.extend(_require_fields(finding, required_finding_fields, f"investigation finding {finding_id}"))
        if finding.get("session_id") != session_id:
            errors.append(f"investigation finding {finding_id}: session does not match current session")
        task = tasks.get(str(finding.get("task_id") or ""))
        if not task:
            errors.append(f"investigation finding {finding_id}: unknown task_id")
        elif str(task.get("claim_id") or "") != str(finding.get("claim_id") or ""):
            errors.append(f"investigation finding {finding_id}: task/claim handoff mismatch")
        if finding.get("assessment") not in {"contradiction_supported", "uncertain", "design_satisfied"}:
            errors.append(f"investigation finding {finding_id}: invalid assessment")
        if len(finding.get("review_lenses", [])) > 3:
            errors.append(f"investigation finding {finding_id}: review_lenses must be focused to at most three")
        if task and not set(finding.get("review_lenses", [])).issubset(set(task.get("review_lenses", []))):
            errors.append(f"investigation finding {finding_id}: uses lenses absent from its task")
        selection = finding.get("dynamic_probe_selection") if isinstance(finding.get("dynamic_probe_selection"), dict) else {}
        if selection.get("disposition") not in {
            "selected", "not_selected", "not_suitable", "environment_limited",
        } or not selection.get("reason"):
            errors.append(f"investigation finding {finding_id}: invalid dynamic_probe_selection")

    for finding_id, critique in critiques.items():
        if finding_id not in published_finding_ids:
            continue
        errors.extend(_require_fields(critique, (
            "session_id", "review_id", "claim_id", "decision", "challenges", "checks_performed",
            "review_context", "resolution",
            "dynamic_probe_review",
        ), f"critic review {finding_id}"))
        if critique.get("session_id") != session_id:
            errors.append(f"critic review {finding_id}: session does not match current session")
        if critique.get("review_context") != "fresh_subagent":
            errors.append(f"critic review {finding_id}: invalid review_context")
        if len(critique.get("checks_performed", [])) < 2:
            errors.append(f"critic review {finding_id}: at least two independent checks are required")
        finding = findings.get(finding_id)
        if not finding:
            errors.append(f"critic review {finding_id}: unknown finding_id")
        elif str(critique.get("claim_id") or "") != str(finding.get("claim_id") or ""):
            errors.append(f"critic review {finding_id}: finding/claim handoff mismatch")
        probe_review = critique.get("dynamic_probe_review") if isinstance(critique.get("dynamic_probe_review"), dict) else {}
        if probe_review.get("status") not in vv.PROBE_INTERPRETATIONS | {"not_run"}:
            errors.append(f"critic review {finding_id}: invalid dynamic probe status")
        for field in ("oracle_validity", "environment_validity", "reachability", "effect_on_decision"):
            if not probe_review.get(field):
                errors.append(f"critic review {finding_id}: dynamic_probe_review missing/empty {field}")

    for finding_id, verdict in verdicts.items():
        if verdict.get("session_id") != session_id:
            errors.append(f"agent verdict {finding_id}: session does not match current session")

    observed_modes: set[str] = set()
    for round_id, round_item in rounds.items():
        errors.extend(_require_fields(round_item, (
            "session_id", "strategy", "exploration_modes", "document_groups", "architecture_boundaries",
            "implementation_planes", "lenses", "claim_ids", "task_ids", "finding_ids", "outcome", "next_strategy",
        ), f"investigation round {round_id}"))
        if round_item.get("session_id") != session_id:
            errors.append(f"investigation round {round_id}: session does not match current session")
        modes = set(round_item.get("exploration_modes", []))
        observed_modes.update(modes)
        if modes - expected_modes:
            errors.append(f"investigation round {round_id}: unknown exploration modes {sorted(modes - expected_modes)}")
        for value, known, label in (
            (round_item.get("document_groups", []), expected_groups, "document groups"),
            (round_item.get("architecture_boundaries", []), architecture_boundary_ids, "architecture boundaries"),
            (round_item.get("implementation_planes", []), architecture_plane_ids, "implementation planes"),
            (round_item.get("lenses", []), expected_lenses, "review lenses"),
            (round_item.get("claim_ids", []), set(claims), "claim IDs"),
            (round_item.get("task_ids", []), set(tasks), "task IDs"),
            (round_item.get("finding_ids", []), set(findings), "finding IDs"),
        ):
            unknown = set(value) - known
            if unknown:
                errors.append(f"investigation round {round_id}: unknown {label} {sorted(unknown)}")
    missing_modes = sorted(expected_modes - observed_modes)
    if missing_modes:
        errors.append(f"investigation rounds missing exploration modes: {missing_modes}")

    tasks_by_claim: dict[str, list[dict[str, Any]]] = {}
    findings_by_task: dict[str, list[dict[str, Any]]] = {}
    for task in tasks.values():
        tasks_by_claim.setdefault(str(task.get("claim_id") or ""), []).append(task)
    for finding in findings.values():
        findings_by_task.setdefault(str(finding.get("task_id") or ""), []).append(finding)
    for index, item in enumerate(parallel_behavior_paths, start=1):
        required_planes = {str(value) for value in item.get("plane_ids", []) if value}
        covered_planes: set[str] = set()
        for task_id, task in tasks.items():
            if task.get("status") != "complete" or not findings_by_task.get(task_id):
                continue
            covered_planes.update(required_planes.intersection(set(task.get("implementation_planes", []))))
        missing_parallel_planes = required_planes - covered_planes
        # The coverage critic may account for a plane across several rounds; the
        # strict published evidence chain is checked separately. Keep this
        # calculation for audit metrics without turning every unselected plane
        # into a fatal error for a time-bounded review.
    uninvestigated_high_claims: list[str] = []
    for claim_id, claim in claims.items():
        if claim.get("priority") != "high":
            continue
        completed = [task for task in tasks_by_claim.get(claim_id, []) if task.get("status") == "complete"]
        if not completed or not any(findings_by_task.get(str(task.get("task_id") or "")) for task in completed):
            uninvestigated_high_claims.append(claim_id)

    semantic_path = root / "semantic_coverage.json"
    semantic = ac.load_json(semantic_path) if semantic_path.exists() else {}
    lens_entries = {
        str(entry.get("lens")): entry
        for entry in semantic.get("lenses", [])
        if isinstance(entry, dict) and entry.get("lens")
    }
    missing_lenses = sorted(expected_lenses - set(lens_entries))
    extra_lenses = sorted(set(lens_entries) - expected_lenses)
    if missing_lenses:
        errors.append(f"semantic coverage missing portfolio lenses: {missing_lenses}")
    if extra_lenses:
        errors.append(f"semantic coverage contains unknown portfolio lenses: {extra_lenses}")
    for lens, entry in lens_entries.items():
        disposition = entry.get("disposition")
        if disposition not in {"investigated", "inapplicable"}:
            errors.append(f"semantic coverage {lens}: invalid disposition {disposition!r}")
            continue
        if not entry.get("evidence"):
            errors.append(f"semantic coverage {lens}: missing evidence")
        design_refs = [str(value) for value in entry.get("design_group_refs", []) if value]
        boundary_refs = [str(value) for value in entry.get("boundary_refs", []) if value]
        if not design_refs or set(design_refs) - expected_groups:
            errors.append(f"semantic coverage {lens}: needs valid design_group_refs")
        if not boundary_refs or set(boundary_refs) - architecture_boundary_ids:
            errors.append(f"semantic coverage {lens}: needs valid boundary_refs")
        lens_tasks = [str(value) for value in entry.get("task_ids", []) if value]
        lens_findings = [str(value) for value in entry.get("finding_ids", []) if value]
        if disposition == "investigated" and (not lens_tasks or not lens_findings):
            errors.append(f"semantic coverage {lens}: investigated lens needs task_ids and finding_ids")
        if disposition == "inapplicable" and not entry.get("counterfactual"):
            errors.append(f"semantic coverage {lens}: inapplicable lens needs counterfactual")
        for task_id in lens_tasks:
            if task_id not in tasks:
                errors.append(f"semantic coverage {lens}: unknown task_id {task_id}")
        for finding_id in lens_findings:
            if finding_id not in findings:
                errors.append(f"semantic coverage {lens}: unknown finding_id {finding_id}")

    lens_use_counts: dict[str, int] = {}
    for entry in lens_entries.values():
        for finding_id in {str(value) for value in entry.get("finding_ids", []) if value}:
            lens_use_counts[finding_id] = lens_use_counts.get(finding_id, 0) + 1
    overloaded_lens_findings = sorted(
        finding_id for finding_id, count in lens_use_counts.items() if count > 3
    )
    if overloaded_lens_findings:
        errors.append(
            "semantic coverage reuses one finding for more than three lenses: "
            f"{overloaded_lens_findings}"
        )

    coverage_path = root / "coverage_audit.json"
    coverage = ac.load_json(coverage_path) if coverage_path.exists() else {}
    coverage_required = [
        "session_id", "design_documents_reviewed", "claims_total", "claims_investigated",
        "rounds_completed", "exploration_modes_completed", "document_groups_total", "document_groups_accounted",
        "code_areas_reviewed", "architecture_boundaries", "remaining_high_priority_claims",
        "deferred_claims", "stop_reason",
    ]
    for field in coverage_required:
        if field not in coverage:
            errors.append(f"coverage_audit.json missing {field}")
    if coverage.get("session_id") not in (None, session_id):
        errors.append("coverage audit session does not match current session")
    if set(coverage.get("exploration_modes_completed", [])) != observed_modes:
        errors.append("coverage audit exploration_modes_completed does not match round evidence")
    deferred_ids: set[str] = set()
    for deferred in coverage.get("deferred_claims", []):
        if not isinstance(deferred, dict):
            errors.append("coverage audit deferred_claims entries must be objects")
            continue
        claim_id = str(deferred.get("claim_id") or "")
        if not claim_id or not deferred.get("reason"):
            errors.append("coverage audit deferred claim needs claim_id and reason")
            continue
        deferred_ids.add(claim_id)
        claim = claims.get(claim_id)
        if not claim:
            errors.append(f"coverage audit defers unknown claim {claim_id}")
    try:
        if int(coverage.get("claims_total", -1)) != len(claims):
            errors.append("coverage audit claims_total does not match design_claims.jsonl")
        if int(coverage.get("claims_investigated", 0)) > int(coverage.get("claims_total", 0)):
            errors.append("coverage audit claims_investigated exceeds claims_total")
        investigated_claims = {
            str(finding.get("claim_id")) for finding in findings.values() if finding.get("claim_id")
        }
        if int(coverage.get("claims_investigated", 0)) != len(investigated_claims):
            errors.append("coverage audit claims_investigated does not match finding claim IDs")
        if int(coverage.get("rounds_completed", 0)) != len(rounds):
            errors.append("coverage audit rounds_completed does not match investigation_rounds.jsonl")
        if int(coverage.get("document_groups_total", -1)) != len(expected_groups):
            errors.append("coverage audit document_groups_total does not match workspace manifest")
        if int(coverage.get("document_groups_accounted", -1)) != len(coverage_groups):
            errors.append("coverage audit document_groups_accounted does not match design coverage")
    except (TypeError, ValueError):
        errors.append("coverage audit claim counts must be integers")

    boundary_coverage = {
        str(item.get("boundary_id")): item
        for item in coverage.get("architecture_boundaries", [])
        if isinstance(item, dict) and item.get("boundary_id")
    }
    for boundary in architecture.get("integration_boundaries", []):
        if not isinstance(boundary, dict) or boundary.get("risk") != "high":
            continue
        boundary_id = str(boundary.get("boundary_id") or "")
        item = boundary_coverage.get(boundary_id)
        if not item:
            errors.append(f"high-risk architecture boundary is not covered: {boundary_id}")
        elif item.get("status") != "investigated" or not item.get("evidence"):
            errors.append(f"architecture boundary {boundary_id} lacks status/evidence")

    started_at = str(state.get("started_at") or "")
    elapsed_seconds = 0
    if started_at:
        elapsed_seconds = max(0, int((ac.parse_iso(ac.now_iso()) - ac.parse_iso(started_at)).total_seconds()))
    else:
        errors.append("session started_at missing")
    if elapsed_seconds > MAX_SECONDS:
        errors.append(f"review elapsed time {elapsed_seconds}s exceeds {MAX_SECONDS}s")
    if len(issues) < MIN_CONFIRMED:
        errors.append(f"only {len(issues)} confirmed findings; target is at least {MIN_CONFIRMED}")

    checks.update({
        "result_artifacts_exist": all((result_root / name).is_file() for name in ("issues.json", "issues.jsonl", "00-summary.md")),
        "confirmed_count_target": len(issues) >= MIN_CONFIRMED,
        "only_confirmed_published": all(issue.get("status") == "confirmed" for issue in issues),
        "handoff_chain_complete": not any("handoff" in error for error in errors),
        "coverage_complete": (
            bool(coverage)
            and not missing_groups
            and len(coverage_groups) == len(expected_groups)
        ),
        "semantic_lenses_covered": (
            bool(expected_lenses)
            and not missing_lenses
            and len(lens_entries) == len(expected_lenses)
        ),
        "architecture_mapped": bool(architecture) and "integration_boundaries" in architecture,
        "investigation_round_recorded": bool(rounds),
        "exploration_modes_complete": bool(expected_modes) and not missing_modes,
        "dynamic_probe_integrity": not probe_integrity_errors,
        "target_roots_unchanged": not any("target " in error for error in errors),
        "within_time_budget": elapsed_seconds <= MAX_SECONDS,
        "evidence_validation_passed": (
            (trace_root / "evidence_validation.json").is_file()
            and ac.load_json(trace_root / "evidence_validation.json").get("passed") is True
        ),
    })
    for name, passed in checks.items():
        if not passed:
            errors.append(f"gate check failed: {name}")
    passed = not errors
    verdict = {
        "judged_at": ac.now_iso(),
        "session_id": session_id,
        "passed": passed,
        "checks": checks,
        "metrics": {
            "confirmed": len(issues),
            "minimum_confirmed": MIN_CONFIRMED,
            "elapsed_seconds": elapsed_seconds,
            "maximum_seconds": MAX_SECONDS,
            "design_document_groups": len(expected_groups),
            "design_claims": len(claims),
            "investigation_rounds": len(rounds),
            "investigation_tasks": len(tasks),
            "investigation_findings": len(findings),
            "dynamic_probes": len(probes),
            "critic_reviews": len(critiques),
        },
        "errors": errors,
    }
    ac.save_json(trace_root / "final_gate.json", verdict)
    state["updated_at"] = ac.now_iso()
    state["status"] = "complete" if passed else "needs_iteration"
    state["current_phase"] = "complete" if passed else "agent_loop"
    state["stop_reason"] = "final_gate_passed" if passed else "final_gate_failed"
    state["next_actions"] = [] if passed else [
        "Read /logs/trace/final_gate.json and /logs/trace/evidence_validation.json.",
        "Resume from the earliest incomplete handoff; do not fabricate findings to satisfy the count target.",
    ]
    ac.save_json(root / "agent_loop_state.json", state)
    ac.append_jsonl(root / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": session_id, "event": "final_gate",
        "actor": "gate_helper", "phase": "final_gate", "status": "complete" if passed else "failed",
        "summary": "Checked provenance, coverage, issue target, output integrity, and elapsed time.",
        "metrics": verdict["metrics"], "errors": errors,
    })
    print(json.dumps({"passed": passed, **verdict["metrics"], "errors": len(errors)}))
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the final Goal-Agent gate.")
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
