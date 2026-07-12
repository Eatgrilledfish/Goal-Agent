#!/usr/bin/env python3
"""Verify agent-loop provenance, output integrity, target metrics, and duration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import agent_common as ac
import handoff_merge as hm
import report_writer as rw
import run_clock as rc
import session_event as se
import risk_sweep_plan_validator as rpv
import stage_artifact_validator as sav
import verdict_validator as vv


MAX_SECONDS = 21600


def _index(
    path: Path, key: str, *, allow_revisions: bool = False,
) -> tuple[dict[str, dict], list[str]]:
    values, errors = ac.load_jsonl(path)
    indexed: dict[str, dict] = {}
    seen: set[str] = set()
    for line_number, value in enumerate(values, start=1):
        identifier = str(value.get(key) or "")
        if not identifier:
            errors.append(f"{path.name}:{line_number}: missing {key}")
            continue
        if identifier in seen and not allow_revisions:
            errors.append(f"{path.name}:{line_number}: duplicate {key} {identifier}")
        seen.add(identifier)
        indexed[identifier] = value
    return indexed, errors


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


def _tree_changes(
    root: Path, records: list[dict[str, Any]], label: str = "target",
    *, complete_tree: bool = False,
) -> list[str]:
    """Compare current target files with the prepare-time content snapshot."""
    expected = {str(record.get("path")): record for record in records if record.get("path")}
    iterator = ac.iter_integrity_files(root) if complete_tree else ac.iter_files(root)
    current = {ac.relative_path(root, path): path for path in iterator}
    errors: list[str] = []
    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    if added:
        errors.append(f"{label} tree has files added after prepare: {added[:10]}")
    if removed:
        errors.append(f"{label} tree has files removed after prepare: {removed[:10]}")
    for relative in sorted(set(expected) & set(current)):
        record = expected[relative]
        path = current[relative]
        expected_kind = str(record.get("kind") or "file")
        actual_kind = "symlink" if path.is_symlink() else "file"
        if actual_kind != expected_kind:
            errors.append(f"{label} tree entry kind changed after prepare: {relative}")
            continue
        if expected_kind == "symlink":
            if str(path.readlink()) != str(record.get("link_target") or ""):
                errors.append(f"{label} symlink changed after prepare: {relative}")
            continue
        expected_hash = str(record.get("sha256") or "")
        if not expected_hash:
            errors.append(f"prepare snapshot lacks sha256 for {label} file: {relative}")
        elif ac.sha256_file(path) != expected_hash:
            errors.append(f"{label} file changed after prepare: {relative}")
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
    run_clock_path = root / "run_clock.json"
    run_clock = ac.load_json(run_clock_path) if run_clock_path.is_file() else {}
    run_clock_trace_path = trace_root / "run_clock.json"
    run_clock_trace = (
        ac.load_json(run_clock_trace_path)
        if run_clock_trace_path.is_file() else {}
    )
    clock_errors: list[str] = []
    if run_clock_path.is_symlink() or not isinstance(run_clock, dict) or not run_clock:
        clock_errors.append("immutable run_clock.json is missing or invalid")
    else:
        clock_errors.extend(
            f"immutable run_clock.json: {error}"
            for error in rc.validate_clock(run_clock)
        )
        if (
            state.get("started_at") != run_clock.get("started_at")
            or state.get("deadline_at") != run_clock.get("deadline_at")
            or run_clock.get("maximum_seconds") != MAX_SECONDS
        ):
            clock_errors.append("session timing does not match immutable run_clock.json")
        if (
            run_clock_trace_path.is_symlink()
            or not isinstance(run_clock_trace, dict)
            or run_clock_trace != run_clock
        ):
            clock_errors.append("run_clock.json differs from its original trace baseline")
    errors.extend(clock_errors)

    result_path = result_root / "issues.json"
    result = ac.load_json(result_path) if result_path.exists() else {}
    if not result:
        errors.append("issues.json is missing or empty")
    if result.get("tool") != "goal-agent-design-code-diff":
        errors.append("issues.json has the wrong tool identifier")
    if result.get("session_id") != session_id:
        errors.append("issues.json session does not match current session")
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    issue_ids = [str(issue.get("issue_id") or "") for issue in issues]
    finding_id_list = [str(issue.get("finding_id") or "") for issue in issues]
    report_paths = [str(issue.get("report_path") or "") for issue in issues]
    if len(set(issue_ids)) != len(issue_ids):
        errors.append("issues.json contains duplicate issue_id values")
    if len(set(finding_id_list)) != len(finding_id_list):
        errors.append("issues.json contains duplicate finding_id values")
    if len(set(report_paths)) != len(report_paths):
        errors.append("issues.json contains duplicate report_path values")
    published_finding_ids = {
        str(issue.get("finding_id")) for issue in issues if issue.get("finding_id")
    }
    published_claim_ids = {
        str(issue.get("claim_id")) for issue in issues if issue.get("claim_id")
    }
    if result.get("summary", {}).get("total") != len(issues):
        errors.append("issues.json summary.total does not match issue count")
    jsonl_issues, jsonl_errors = ac.load_jsonl(result_root / "issues.jsonl")
    errors.extend(jsonl_errors)
    if jsonl_issues != issues:
        errors.append("issues.jsonl does not exactly match issues.json issues")

    validated_path = root / "validated_issues.json"
    validated = ac.load_json(validated_path) if validated_path.is_file() else {}
    validated_confirmed = [
        issue for issue in validated.get("issues", [])
        if isinstance(issue, dict) and issue.get("status") == "confirmed"
    ]
    validated_probable = [
        issue for issue in validated.get("issues", [])
        if isinstance(issue, dict) and issue.get("status") == "probable"
    ]
    validated_by_finding = {
        str(issue.get("finding_id")): issue
        for issue in validated_confirmed if issue.get("finding_id")
    }
    if len(validated_by_finding) != len(validated_confirmed):
        errors.append("validated_issues.json contains missing or duplicate confirmed finding IDs")
    if published_finding_ids != set(validated_by_finding):
        errors.append("published finding IDs do not exactly match validated confirmed findings")
    confirmed_count = len(published_finding_ids.intersection(validated_by_finding))
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    expected_summary = {
        "total": len(validated_confirmed),
        "confirmed": len(validated_confirmed),
        "probable": len(validated_probable),
        "high_confidence": sum(
            float(issue.get("confidence") or 0) >= 0.8 for issue in validated_confirmed
        ),
    }
    if summary != expected_summary:
        errors.append("issues.json summary does not match validated issue counts")
    for index, issue in enumerate(issues, start=1):
        finding_id = str(issue.get("finding_id") or "")
        expected = validated_by_finding.get(finding_id)
        core = {key: value for key, value in issue.items() if key not in {"issue_id", "report_path"}}
        if expected is not None and core != expected:
            errors.append(f"{issue.get('issue_id', '?')}: published issue differs from validated issue")
        expected_issue_id = f"ISSUE-{index:03d}"
        expected_report_path = result_root / f"{index:02d}-{ac.slugify(str(issue.get('title') or 'issue'))}.md"
        if issue.get("issue_id") != expected_issue_id:
            errors.append(f"{issue.get('issue_id', '?')}: issue_id does not match deterministic report order")
        if str(issue.get("report_path") or "") != str(expected_report_path):
            errors.append(f"{issue.get('issue_id', '?')}: report_path does not match deterministic report order")
        errors.extend(_check_issue(issue, session_id, result_root))
        if expected_report_path.is_file() and expected_report_path.read_text(encoding="utf-8") != rw.render_issue(issue):
            errors.append(f"{issue.get('issue_id', '?')}: single-issue Markdown does not match published JSON")
    summary_path = result_root / "00-summary.md"
    if summary_path.is_file() and summary_path.read_text(encoding="utf-8") != rw.render_summary(result, len(validated_probable)):
        errors.append("00-summary.md does not match published JSON")

    claims, claim_errors = _index(root / "design_claims.jsonl", "claim_id")
    risks, risk_errors = _index(root / "risk_observations.jsonl", "observation_id")
    tasks, task_errors = _index(root / "investigation_tasks.jsonl", "task_id")
    findings, finding_errors = _index(root / "investigation_findings.jsonl", "finding_id")
    critiques, critic_errors = _index(root / "critic_reviews.jsonl", "finding_id")
    probes, probe_errors = _index(root / "dynamic_probes.jsonl", "probe_id")
    verdicts, verdict_errors = _index(
        root / "agent_review_verdicts.jsonl", "finding_id", allow_revisions=True,
    )
    rounds, round_errors = _index(root / "investigation_rounds.jsonl", "round_id")
    errors.extend(
        claim_errors + risk_errors + task_errors + finding_errors
        + critic_errors + probe_errors + verdict_errors
    )
    errors.extend(round_errors)
    if not set(findings).issubset(critiques) or set(critiques) - set(findings):
        missing = sorted(set(findings) - set(critiques))
        extra = sorted(set(critiques) - set(findings))
        errors.append(
            "critic coverage does not cover every finding: "
            f"missing={missing}, extra={extra}"
        )
    if set(verdicts) != set(findings):
        missing = sorted(set(findings) - set(verdicts))
        extra = sorted(set(verdicts) - set(findings))
        errors.append(
            "final-judge verdict coverage does not exactly match findings: "
            f"missing={missing}, extra={extra}"
        )
    probe_integrity_errors: list[str] = []
    investigated_claim_ids = {
        str(finding.get("claim_id")) for finding in findings.values() if finding.get("claim_id")
    }

    design_validation = ac.load_json(trace_root / "design_validation.json") if (trace_root / "design_validation.json").is_file() else {}
    if design_validation.get("passed") is not True or design_validation.get("mode") != "all":
        errors.append("passed design-validation trace is missing")
    expected_design_digests = {
        "design_inventory.json": ac.sha256_file(root / "design_inventory.json")
        if (root / "design_inventory.json").is_file() else "",
        "design_claims.jsonl": ac.sha256_file(root / "design_claims.jsonl"),
        "design_coverage.json": ac.sha256_file(root / "design_coverage.json"),
        "workspace_manifest.json": ac.sha256_file(root / "workspace_manifest.json"),
    }
    if design_validation.get("input_digests") != expected_design_digests:
        errors.append("design-validation trace is stale for current design artifacts")

    scope_path = root / "claim_review_scope.json"
    scope = ac.load_json(scope_path) if scope_path.is_file() else {}
    raw_scope_claim_ids = scope.get("claim_ids", []) if isinstance(scope, dict) else []
    scoped_claim_ids = {
        str(value) for value in raw_scope_claim_ids
        if isinstance(value, str) and value
    }
    if not scope_path.is_file():
        errors.append("claim_review_scope.json is missing")
    elif scope.get("session_id") != session_id:
        errors.append("claim review scope does not match current session")
    if not scoped_claim_ids or len(scoped_claim_ids) != len(raw_scope_claim_ids):
        errors.append("claim review scope must contain unique non-empty claim IDs")
    unknown_scoped_claims = scoped_claim_ids - set(claims)
    if unknown_scoped_claims:
        errors.append(f"claim review scope contains unknown claims: {sorted(unknown_scoped_claims)}")

    claim_review_path = root / "design_claim_review.json"
    claim_review = ac.load_json(claim_review_path) if claim_review_path.is_file() else {}
    claim_review_validation_path = trace_root / "claim_review_validation.json"
    claim_review_validation = (
        ac.load_json(claim_review_validation_path)
        if claim_review_validation_path.is_file() else {}
    )
    if not (root / "design_agent_manifest.json").is_file():
        errors.append("design_agent_manifest.json is missing")
    if (
        claim_review_validation.get("passed") is not True
        or claim_review_validation.get("session_id") != session_id
    ):
        errors.append("passed per-claim-bound claim-review validation trace is missing")
    accepted_claim_ids = {
        str(value) for value in claim_review_validation.get("accepted_claim_ids", [])
        if isinstance(value, str) and value
    }
    repaired_claim_ids = {
        str(value) for value in claim_review_validation.get("repaired_claim_ids", [])
        if isinstance(value, str) and value
    }
    if accepted_claim_ids.intersection(repaired_claim_ids):
        errors.append("claim review validation accepts and repairs the same claim")
    if accepted_claim_ids.union(repaired_claim_ids) != scoped_claim_ids:
        errors.append("claim review validation does not account for every scoped claim")
    used_claim_ids = {
        str(item.get("claim_id") or "") for item in [*tasks.values(), *findings.values()]
        if item.get("claim_id")
    }
    unreviewed_used_claims = used_claim_ids - accepted_claim_ids
    if unreviewed_used_claims:
        errors.append(f"tasks/findings use claims without a current accepted review: {sorted(unreviewed_used_claims)}")

    def stage_validation_current(stage: str) -> bool:
        trace_name = {
            "task-plan": "task_plan_validation.json",
            "task-lifecycle": "task_lifecycle_validation.json",
        }.get(stage, f"{stage}_validation.json")
        path = trace_root / trace_name
        if not path.is_file():
            return False
        report = ac.load_json(path)
        if stage == "task-plan":
            contract_value = ac.load_json(root / "agent_loop_contract.json")
            architecture_value = ac.load_json(root / "architecture_map.json")
            expected_plan_sha256 = sav.task_plan_snapshot_sha256(
                root,
                contract=contract_value,
                architecture=architecture_value,
                claims=claims,
                risks=risks,
                tasks=tasks,
                rounds=list(rounds.values()),
            )
            return (
                report.get("passed") is True
                and report.get("session_id") == session_id
                and report.get("task_plan_sha256") == expected_plan_sha256
            )
        if stage == "task-lifecycle":
            expected_lifecycle_sha256 = sav.task_lifecycle_snapshot_sha256(
                tasks=tasks, findings=findings, rounds=list(rounds.values()),
            )
            return (
                report.get("passed") is True
                and report.get("session_id") == session_id
                and report.get("task_lifecycle_sha256") == expected_lifecycle_sha256
            )
        expected_inputs, expected_combined = sav._input_digests(
            root, sav._stage_inputs(root, stage),
        )
        current = (
            report.get("passed") is True
            and report.get("session_id") == session_id
            and report.get("input_digests") == expected_inputs
            and report.get("combined_input_sha256") == expected_combined
        )
        if stage == "coverage":
            current = (
                current
                and report.get("closed") is True
                and report.get("coverage_provenance_sha256")
                == sav.coverage_provenance_sha256(root)
                and report.get("claim_review_provenance_sha256")
                == sav.claim_review_provenance_sha256(root)
            )
        return current

    evidence_validation_path = trace_root / "evidence_validation.json"
    evidence_validation = (
        ac.load_json(evidence_validation_path)
        if evidence_validation_path.is_file() else {}
    )
    evidence_validation_current = (
        evidence_validation.get("passed") is True
        and evidence_validation.get("session_id") == session_id
        and evidence_validation.get("input_digests") == vv.evidence_input_digests(root)
        and evidence_validation.get("validated_issues_sha256")
        == (ac.sha256_file(validated_path) if validated_path.is_file() else "")
    )
    if not evidence_validation_current:
        errors.append("passed digest-bound evidence validation trace is missing or stale")

    for stage in ("architecture", "task-plan", "task-lifecycle", "coverage"):
        if not stage_validation_current(stage):
            errors.append(f"passed digest-bound {stage} validation trace is missing or stale")
    published_task_ids = {
        str(findings[finding_id].get("task_id") or "")
        for finding_id in published_finding_ids.intersection(findings)
        if findings[finding_id].get("task_id")
    }
    for stage, trace_name in (
        ("task-plan", "task_plan_validation.json"),
        ("task-lifecycle", "task_lifecycle_validation.json"),
    ):
        trace = ac.load_json(trace_root / trace_name) if (trace_root / trace_name).is_file() else {}
        missing_valid_tasks = published_task_ids - set(trace.get("valid_task_ids", []))
        if missing_valid_tasks:
            errors.append(
                f"published issue tasks are invalid in {stage}: {sorted(missing_valid_tasks)}"
            )

    def valid_merge_report(
        path: Path, artifact_type: str, expected_ids: set[str], ledger_path: Path,
    ) -> bool:
        if not path.is_file() or not ledger_path.is_file():
            return False
        report = ac.load_json(path)
        current = (
            report.get("passed") is True
            and set(report.get("validated_ids", [])) == expected_ids
        )
        if artifact_type == "task":
            current = current and report.get("task_plan_ledger_sha256") == \
                hm.task_plan_ledger_sha256(tasks)
        else:
            current = current and report.get("ledger_sha256") == ac.sha256_file(ledger_path)
        if artifact_type == "risk":
            plan_path = root / "risk_sweep_plan.json"
            architecture_path = root / "architecture_map.json"
            loaded_plan = ac.load_json(plan_path) if plan_path.is_file() else {}
            plan = loaded_plan if isinstance(loaded_plan, dict) else {}
            expected_sweeps = {
                str(item.get("sweep_id"))
                for item in plan.get("slices", [])
                if isinstance(item, dict) and item.get("sweep_id")
            }
            current = current and (
                report.get("risk_sweep_plan_sha256")
                == (ac.sha256_file(plan_path) if plan_path.is_file() else "")
                and report.get("architecture_map_sha256")
                == (
                    ac.sha256_file(architecture_path)
                    if architecture_path.is_file() else ""
                )
                and set(report.get("expected_sweep_ids", [])) == expected_sweeps
                and set(report.get("completed_sweep_ids", [])) == expected_sweeps
                and report.get("missing_sweep_ids", []) == []
                and report.get("closed") is True
                and report.get("global_coverage_validated") is True
                and set(report.get("submitted_sweep_ids", []))
                == set(report.get("validated_sweep_ids", []))
                and set(report.get("submitted_sweep_ids", [])).issubset(
                    expected_sweeps
                )
            )
        return current

    def current_merge_reports(
        artifact_type: str, expected_ids: set[str], ledger_path: Path,
    ) -> set[Path]:
        reports: set[Path] = set()
        for path in trace_root.glob("*merge*.json"):
            if not path.is_file():
                continue
            report = ac.load_json(path)
            if report.get("artifact_type") != artifact_type:
                continue
            if valid_merge_report(path, artifact_type, expected_ids, ledger_path):
                reports.add(path.resolve())
        return reports

    merge_requirements = {
        "task": (set(tasks), root / "investigation_tasks.jsonl"),
        "finding": (set(findings), root / "investigation_findings.jsonl"),
        "risk": (set(risks), root / "risk_observations.jsonl"),
        "critic": (set(critiques), root / "critic_reviews.jsonl"),
        "probe": (set(probes), root / "dynamic_probes.jsonl"),
    }
    valid_report_paths: dict[str, set[Path]] = {}
    for artifact_type, (expected_ids, ledger_path) in merge_requirements.items():
        paths = current_merge_reports(artifact_type, expected_ids, ledger_path)
        valid_report_paths[artifact_type] = paths
        if expected_ids and not paths:
            errors.append(
                f"{artifact_type} handoff merge trace does not validate the current ledger"
            )
    run_ledger, run_ledger_errors = ac.load_jsonl(root / "agent_run_ledger.jsonl")
    errors.extend(run_ledger_errors)
    required_helper_reports = (
        "session_prepared.json",
        "architecture_validation.json",
        "risk_sweep_plan_validation.json",
        "design_validation.json",
        "claim_review_validation.json",
        "task_plan_validation.json",
        "task_lifecycle_validation.json",
        "coverage_validation.json",
        "evidence_validation.json",
    )
    for report_name in required_helper_reports:
        report_path = trace_root / report_name
        report_sha256 = ac.sha256_file(report_path) if report_path.is_file() else ""
        if not any(
            event.get("event") == "deterministic_helper_trace"
            and event.get("session_id") == session_id
            and event.get("status") == "complete"
            and event.get("report") == str(report_path.resolve())
            and event.get("report_sha256") == report_sha256
            for event in run_ledger
        ):
            errors.append(
                f"current deterministic helper report is not registered in the run ledger: "
                f"{report_name}"
            )
    contract_value = ac.load_json(root / "agent_loop_contract.json")
    event_contract = contract_value.get("tool_protocol", {}).get(
        "agent_event_contract", {}
    ) if isinstance(contract_value, dict) else {}
    required_phase_roles = event_contract.get("required_phase_roles", []) \
        if isinstance(event_contract, dict) else []
    trace_contract_errors: list[str] = []
    required_pairs = {
        (str(item.get("phase") or ""), str(item.get("role") or ""))
        for item in required_phase_roles if isinstance(item, dict)
    }
    if probes:
        required_pairs.add(("dynamic_probe", "code-investigator"))
    claim_round_scope_ids = {
        str(round_value.get("round_id") or "")
        for round_value in rounds.values()
        if round_value.get("round_id") and round_value.get("claim_ids")
    }
    review_round_id = str(scope.get("round_id") or "")
    if review_round_id:
        claim_round_scope_ids.add(review_round_id)
    if not claim_round_scope_ids:
        claim_round_scope_ids = {"DESIGN-ROUND"}
    planning_scope_ids = set(rounds) or {"INVESTIGATION-PLANNING"}
    portfolio_scope_requirements: dict[
        tuple[str, str], tuple[set[str], set[str]]
    ] = {
        ("architecture_mapping", "orchestrator"): (
            {"ARCHITECTURE-MAP"}, {"ARCHITECTURE-MAP"},
        ),
        ("design_inventory", "spec-analyst"): (
            {"DESIGN-INVENTORY"}, {"DESIGN-INVENTORY"},
        ),
        ("design_claim_resolution", "spec-analyst"): (
            claim_round_scope_ids, claim_round_scope_ids,
        ),
        ("design_claim_review", "spec-critic"): (
            claim_round_scope_ids, claim_round_scope_ids,
        ),
        ("investigation_planning", "orchestrator"): (
            planning_scope_ids, planning_scope_ids,
        ),
        ("coverage_audit", "coverage-critic"): (
            {"COVERAGE-AUDIT-FINAL"},
            {"COVERAGE-AUDIT-INITIAL", "COVERAGE-AUDIT-FINAL"},
        ),
        ("final_judgement", "final-judge"): (
            {"FINAL-JUDGEMENT"}, {"FINAL-JUDGEMENT"},
        ),
    }
    valid_checkpoints_by_pair: dict[
        tuple[str, str], list[dict[str, Any]]
    ] = {}
    all_valid_checkpoints_by_pair: dict[
        tuple[str, str], list[dict[str, Any]]
    ] = {pair: [] for pair in required_pairs}
    fresh_provider_uses: dict[str, set[tuple[str, str, str]]] = {}
    semantic_events_by_use: dict[
        tuple[str, str, str], list[dict[str, Any]]
    ] = {}
    for event in run_ledger:
        pair = (str(event.get("phase") or ""), str(event.get("role") or ""))
        if not (
            pair in required_pairs
            and event.get("session_id") == session_id
            and isinstance(event.get("event"), str)
            and str(event.get("event")).endswith(".checkpoint")
        ):
            continue
        event_errors = se.checkpoint_event_errors(
            event, session_id=session_id, role=pair[1], phase=pair[0],
            require_complete=False,
        )
        trace_contract_errors.extend(event_errors)
        if not event_errors:
            all_valid_checkpoints_by_pair[pair].append(event)
    for phase, role in sorted(required_pairs):
        valid_candidates = [
            event for event in all_valid_checkpoints_by_pair[(phase, role)]
            if event.get("status") == "complete"
        ]
        valid_checkpoints_by_pair[(phase, role)] = valid_candidates
        if not valid_candidates:
            trace_contract_errors.append(
                f"missing valid complete trace checkpoint for {phase}/{role}"
            )
        elif not any(event.get("output_count", 0) > 0 for event in valid_candidates):
            trace_contract_errors.append(
                f"complete trace checkpoint for {phase}/{role} records no output"
            )
        for event in all_valid_checkpoints_by_pair[(phase, role)]:
            provider_session_id = str(event.get("provider_session_id") or "")
            use = (phase, role, str(event.get("scope_id") or ""))
            if role != "orchestrator" and provider_session_id:
                fresh_provider_uses.setdefault(provider_session_id, set()).add(use)
            semantic_events_by_use.setdefault(use, []).append(event)
    checkpoint_risk_plan_path = root / "risk_sweep_plan.json"
    checkpoint_risk_plan = (
        ac.load_json(checkpoint_risk_plan_path)
        if checkpoint_risk_plan_path.is_file() else {}
    )
    checkpoint_risk_slices = checkpoint_risk_plan.get("slices", []) \
        if isinstance(checkpoint_risk_plan, dict) else []
    candidate_checkpoint_requirements = {
        ("code_risk_backtracking", "risk-explorer"): {
            str(item.get("sweep_id") or "")
            for item in checkpoint_risk_slices
            if isinstance(item, dict) and item.get("sweep_id")
        },
        ("investigation", "code-investigator"): {
            str(finding.get("task_id") or "")
            for finding in findings.values() if finding.get("task_id")
        },
        ("critic_review", "evidence-critic"): set(findings),
    }
    if probes:
        candidate_checkpoint_requirements[("dynamic_probe", "code-investigator")] = {
            str(probe.get("finding_id") or "")
            for probe in probes.values() if probe.get("finding_id")
        }
    for pair, expected_candidate_ids in candidate_checkpoint_requirements.items():
        checkpoints = valid_checkpoints_by_pair.get(pair, [])
        all_checkpoints = all_valid_checkpoints_by_pair.get(pair, [])
        observed_candidate_ids = {
            str(event.get("task_id") or "") for event in checkpoints
            if event.get("task_id")
        }
        missing_candidate_ids = expected_candidate_ids - observed_candidate_ids
        if missing_candidate_ids:
            trace_contract_errors.append(
                f"missing candidate-level trace checkpoints for {pair[0]}/{pair[1]}: "
                f"{sorted(missing_candidate_ids)}"
            )
        for event in all_checkpoints:
            task_id = str(event.get("task_id") or "")
            scope_id = str(event.get("scope_id") or "")
            if task_id not in expected_candidate_ids:
                trace_contract_errors.append(
                    f"unexpected candidate-level trace checkpoint for {pair[0]}/{pair[1]}: "
                    f"{task_id or '<missing task_id>'}"
                )
            elif scope_id != task_id:
                trace_contract_errors.append(
                    f"candidate trace scope_id must equal task_id for {pair[0]}/{pair[1]}: "
                    f"{scope_id!r} != {task_id!r}"
                )
    for pair, (expected_scope_ids, allowed_scope_ids) in portfolio_scope_requirements.items():
        if pair not in required_pairs:
            continue
        complete_scope_ids = {
            str(event.get("scope_id") or "")
            for event in valid_checkpoints_by_pair.get(pair, [])
        }
        all_scope_ids = {
            str(event.get("scope_id") or "")
            for event in all_valid_checkpoints_by_pair.get(pair, [])
        }
        missing_scope_ids = expected_scope_ids - complete_scope_ids
        unexpected_scope_ids = all_scope_ids - allowed_scope_ids
        if missing_scope_ids:
            trace_contract_errors.append(
                f"missing portfolio trace scope IDs for {pair[0]}/{pair[1]}: "
                f"{sorted(missing_scope_ids)}"
            )
        if unexpected_scope_ids:
            trace_contract_errors.append(
                f"unbound portfolio trace scope IDs for {pair[0]}/{pair[1]}: "
                f"{sorted(unexpected_scope_ids)}"
            )
    for provider_session_id, uses in sorted(fresh_provider_uses.items()):
        if len(uses) > 1:
            trace_contract_errors.append(
                "fresh semantic tasks reuse provider session "
                f"{provider_session_id}: {sorted(uses)}"
            )

    for use, events in sorted(semantic_events_by_use.items()):
        baseline_providers: set[str] = set()
        repair_provider = ""
        prior_checkpoint_seen = False
        previous_repair_count = 0
        previous_attempt = {0: 0, 1: 0}
        provider_by_attempt: dict[tuple[int, int], str] = {}
        for event in events:
            repair_count = int(event.get("repair_count") or 0)
            provider_attempt = int(event.get("provider_attempt") or 0)
            provider_session_id = str(event.get("provider_session_id") or "")
            if repair_count < previous_repair_count:
                trace_contract_errors.append(
                    "semantic repair_count regressed for "
                    f"{use[0]}/{use[1]}/{use[2]}"
                )
            if repair_count == 1:
                if not prior_checkpoint_seen:
                    trace_contract_errors.append(
                        "semantic repair has no baseline checkpoint for "
                        f"{use[0]}/{use[1]}/{use[2]}"
                    )
                if use[1] != "orchestrator" and provider_session_id in baseline_providers:
                    trace_contract_errors.append(
                        "semantic repair reused its baseline provider session "
                        f"{provider_session_id}: {use[0]}/{use[1]}/{use[2]}"
                    )
                if repair_provider and provider_session_id != repair_provider:
                    trace_contract_errors.append(
                        "semantic scope records more than one repair provider for "
                        f"{use[0]}/{use[1]}/{use[2]}"
                    )
                repair_provider = repair_provider or provider_session_id
            elif provider_session_id:
                baseline_providers.add(provider_session_id)
            prior_attempt = previous_attempt[repair_count]
            if (
                (prior_attempt == 0 and provider_attempt != 1)
                or provider_attempt < prior_attempt
                or provider_attempt > prior_attempt + 1
            ):
                trace_contract_errors.append(
                    "provider attempt sequence is invalid for "
                    f"{use[0]}/{use[1]}/{use[2]} repair={repair_count}"
                )
            attempt_key = (repair_count, provider_attempt)
            recorded_provider = provider_by_attempt.get(attempt_key)
            if recorded_provider and recorded_provider != provider_session_id:
                trace_contract_errors.append(
                    "one provider attempt names multiple sessions for "
                    f"{use[0]}/{use[1]}/{use[2]} repair={repair_count} "
                    f"attempt={provider_attempt}"
                )
            provider_by_attempt.setdefault(attempt_key, provider_session_id)
            previous_attempt[repair_count] = max(prior_attempt, provider_attempt)
            previous_repair_count = max(previous_repair_count, repair_count)
            prior_checkpoint_seen = True

    retry_state: dict[
        tuple[str, str, str], tuple[str, int]
    ] = {}
    valid_trace_events = {
        id(event) for events in all_valid_checkpoints_by_pair.values() for event in events
    }
    for event in run_ledger:
        if id(event) not in valid_trace_events:
            continue
        base = (
            str(event.get("phase") or ""), str(event.get("role") or ""),
            # Candidate IDs and portfolio scope IDs are both bound above to
            # current artifact identities, so free-form scope text cannot
            # create a new retry identity.
            str(event.get("scope_id") or ""),
        )
        signature = json.dumps({
            "input_sha256": event.get("input_sha256"),
            "artifact_sha256": event.get("artifact_sha256"),
            "validation_error_categories": event.get(
                "validation_error_categories", {}
            ),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        previous_signature, previous_count = retry_state.get(base, ("", 0))
        count = previous_count + 1 if signature == previous_signature else 1
        retry_state[base] = (signature, count)
        if count == 3:
            trace_contract_errors.append(
                "same phase/artifact/input/error checkpoint was repeated a third time "
                f"without progress: {base[0]}/{base[1]}"
            )
    errors.extend(trace_contract_errors)
    def valid_merge_event(
        artifact_type: str, expected_ids: set[str], ledger_path: Path, report_paths: set[Path],
    ) -> bool:
        if not ledger_path.is_file():
            return False
        ledger_sha256 = ac.sha256_file(ledger_path)
        task_plan_sha256 = hm.task_plan_ledger_sha256(tasks)
        for event in run_ledger:
            if not (
                event.get("event") == "handoff_merge"
                and event.get("status") == "complete"
                and event.get("session_id") == session_id
                and event.get("artifact_type") == artifact_type
                and set(event.get("validated_ids", [])) == expected_ids
            ):
                continue
            if artifact_type == "task":
                if event.get("task_plan_ledger_sha256") != task_plan_sha256:
                    continue
            elif event.get("ledger_sha256") != ledger_sha256:
                continue
            report_path = ac.contained_path(trace_root, str(event.get("report") or ""))
            if report_path not in report_paths or not report_path or not report_path.is_file():
                continue
            if event.get("report_sha256") == ac.sha256_file(report_path):
                return True
        return False

    provenance_requirements = [
        (artifact_type, expected_ids, ledger_path, valid_report_paths[artifact_type])
        for artifact_type, (expected_ids, ledger_path) in merge_requirements.items()
        if expected_ids
    ]
    for artifact_type, expected_ids, ledger_path, report_paths in provenance_requirements:
        if not valid_merge_event(artifact_type, expected_ids, ledger_path, report_paths):
            errors.append(
                f"session ledger lacks a current, digest-bound {artifact_type} handoff merge event"
            )

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
    preflight_problems = manifest.get("preflight_problems", []) if isinstance(manifest, dict) else []
    for problem in preflight_problems:
        errors.append(f"session preflight did not pass: {problem}")
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
    target_integrity_errors = _tree_changes(code_root, manifest.get("code", {}).get("files", []))
    target_integrity_errors.extend(_tree_changes(
        design_root,
        manifest.get("design", {}).get("source_files", manifest.get("design", {}).get("documents", [])),
    ))
    errors.extend(target_integrity_errors)
    supplied_source_integrity_errors: list[str] = []
    materialization_source = manifest.get("design", {}).get("materialization_source")
    if materialization_source is not None:
        if not isinstance(materialization_source, dict):
            supplied_source_integrity_errors.append(
                "prepared design materialization source snapshot is invalid"
            )
        else:
            source_root_value = str(materialization_source.get("source_root") or "")
            source_root = (
                Path(os.path.abspath(source_root_value)) if source_root_value else None
            )
            source_records = materialization_source.get("files")
            if not isinstance(source_records, list):
                supplied_source_integrity_errors.append(
                    "prepared design materialization source snapshot lacks file records"
                )
                source_records = []
            expected_tree_sha256 = ac.stable_id(
                json.dumps(
                    source_records,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                length=64,
            )
            if materialization_source.get("file_count") != len(source_records):
                supplied_source_integrity_errors.append(
                    "prepared design materialization source file count is inconsistent"
                )
            if materialization_source.get("tree_sha256") != expected_tree_sha256:
                supplied_source_integrity_errors.append(
                    "prepared design materialization source tree digest is inconsistent"
                )
            plan_path_value = str(materialization_source.get("plan_path") or "")
            plan_path = (
                Path(os.path.abspath(plan_path_value)) if plan_path_value else None
            )
            if plan_path is None or plan_path.is_symlink() or not plan_path.is_file():
                supplied_source_integrity_errors.append(
                    "design materialization plan is missing or changed to a symlink after prepare"
                )
            elif materialization_source.get("plan_sha256") != ac.sha256_file(plan_path):
                supplied_source_integrity_errors.append(
                    "design materialization plan changed after prepare"
                )
            if source_root is None or source_root.is_symlink() or not source_root.is_dir():
                supplied_source_integrity_errors.append(
                    "supplied design source root is missing or changed to a symlink after prepare"
                )
            else:
                supplied_source_integrity_errors.extend(_tree_changes(
                    source_root,
                    source_records,
                    "supplied design source",
                    complete_tree=True,
                ))
    errors.extend(supplied_source_integrity_errors)
    review_workspace = manifest.get("review_workspace", {})
    review_integrity_errors: list[str] = []
    review_code_value = str(manifest_paths.get("review_code_root") or "")
    review_design_value = str(manifest_paths.get("review_design_root") or "")
    review_code_root = Path(os.path.abspath(review_code_value)) if review_code_value else None
    review_design_root = Path(os.path.abspath(review_design_value)) if review_design_value else None
    expected_review_code_root = Path(os.path.abspath(str(root / "review-inputs" / "code")))
    expected_review_design_root = Path(os.path.abspath(str(root / "review-inputs" / "design")))
    code_path_errors = (
        ac.lexical_path_errors(root, review_code_root, "review code root") if review_code_root else []
    )
    design_path_errors = (
        ac.lexical_path_errors(root, review_design_root, "review design root") if review_design_root else []
    )
    if review_code_root != expected_review_code_root:
        code_path_errors.append("review code root is not the fixed session review path")
    if review_design_root != expected_review_design_root:
        design_path_errors.append("review design root is not the fixed session review path")
    review_integrity_errors.extend(code_path_errors + design_path_errors)
    if code_path_errors or review_code_root is None or not review_code_root.is_dir():
        review_integrity_errors.append("review code root is missing or outside session state")
    else:
        barrier_errors = ac.review_git_barrier_errors(review_code_root)
        review_integrity_errors.extend(barrier_errors)
        barrier_record = review_workspace.get("git_isolation_barrier", {})
        if barrier_record.get("path") != ".git":
            review_integrity_errors.append("review code Git isolation barrier manifest is missing")
        elif not barrier_errors and barrier_record.get("sha256") != ac.sha256_file(review_code_root / ".git"):
            review_integrity_errors.append("review code Git isolation barrier hash changed")
        review_integrity_errors.extend(_tree_changes(
            review_code_root, review_workspace.get("code", {}).get("files", []), "review snapshot"
        ))
    if design_path_errors or review_design_root is None or not review_design_root.is_dir():
        review_integrity_errors.append("review design root is missing or outside session state")
    else:
        review_integrity_errors.extend(_tree_changes(
            review_design_root, review_workspace.get("design_source_files", []), "review snapshot"
        ))
    errors.extend(review_integrity_errors)
    expected_groups = {
        str(group.get("document_key"))
        for group in manifest.get("design", {}).get("document_groups", [])
        if group.get("document_key")
    }
    approvals, approval_errors = ac.load_jsonl(root / "approval_events.jsonl")
    errors.extend(approval_errors)
    session_approvals = [item for item in approvals if item.get("session_id") == session_id]
    approval_decisions = {
        (str(item.get("action") or ""), str(item.get("decision") or ""))
        for item in session_approvals
    }
    required_approval_decisions = {
        ("review_snapshot_read", "auto_approved"),
        ("session_artifact_write", "auto_approved"),
        ("target_source_write", "denied"),
        ("external_side_effect", "external_approval_required"),
    }
    missing_policy_decisions = required_approval_decisions - approval_decisions
    if missing_policy_decisions:
        errors.append(f"approval policy trace is incomplete: {sorted(missing_policy_decisions)}")
    if probes and ("focused_dynamic_probe", "auto_approved") not in approval_decisions:
        errors.append("dynamic probes lack an isolated-probe approval event")
    source_manifest = manifest.get("design", {}).get("source_manifest")
    if isinstance(source_manifest, dict):
        remote_locations = {
            str(item.get("location"))
            for item in source_manifest.get("sources", [])
            if isinstance(item, dict) and item.get("kind") == "url" and item.get("location")
        }
        if remote_locations:
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
    member_group = {
        str(member): str(group.get("document_key"))
        for group in manifest.get("design", {}).get("document_groups", [])
        if isinstance(group, dict) and group.get("document_key")
        for member in group.get("members", [])
        if isinstance(member, str) and member
    }
    mapped_claim_ids: set[str] = set()
    for key, group in coverage_groups.items():
        raw_group_claims = group.get("claim_ids")
        if not isinstance(raw_group_claims, list):
            errors.append(f"design coverage {key}: claim_ids must be an array")
            continue
        group_claims = [str(value) for value in raw_group_claims if value]
        if len(group_claims) != len(raw_group_claims) or len(set(group_claims)) != len(group_claims):
            errors.append(f"design coverage {key}: claim_ids must be unique non-empty strings")
        for claim_id in group_claims:
            if claim_id in mapped_claim_ids:
                errors.append(f"design coverage assigns claim {claim_id} to multiple groups")
            mapped_claim_ids.add(claim_id)
            if claim_id not in claims:
                errors.append(f"design coverage {key}: unknown claim_id {claim_id}")
            else:
                claim_path = str(claims[claim_id].get("path") or "")
                claim_key = str(
                    claims[claim_id].get("document_key") or member_group.get(claim_path) or ""
                )
                if claim_key != key:
                    errors.append(f"design coverage {key}: claim {claim_id} cites different document group {claim_key}")
    unmapped_used_claims = used_claim_ids - mapped_claim_ids
    if unmapped_used_claims:
        errors.append(
            f"used claims are not mapped into design coverage groups: {sorted(unmapped_used_claims)}"
        )

    for claim_id, claim in claims.items():
        if claim.get("session_id") != session_id:
            errors.append(f"design claim {claim_id}: session does not match current session")
        for field in (
            "path", "section", "line_start", "line_end", "quote", "subject", "trigger",
            "obligation", "observable_result", "normative_strength",
            "applicability",
        ):
            if claim.get(field) in (None, "", [], {}):
                errors.append(f"design claim {claim_id}: missing/empty {field}")
        if claim_id in published_claim_ids:
            errors.extend(ac.validate_source_evidence(claim, design_root, f"design claim {claim_id}", "quote"))
        if claim.get("normative_strength") not in {
            "mandatory", "recommended", "optional", "declared_capability", "informational",
        }:
            errors.append(f"design claim {claim_id}: invalid normative_strength")
        if "priority" in claim and claim.get("priority") not in {"high", "medium", "low"}:
            errors.append(f"design claim {claim_id}: invalid priority")
        oracle = claim.get("probe_oracle") if isinstance(claim.get("probe_oracle"), dict) else {}
        if oracle:
            if oracle.get("testability") not in {"candidate", "not_suitable", "unknown"}:
                errors.append(f"design claim {claim_id}: invalid probe_oracle.testability")
            if "preconditions" not in oracle or not isinstance(oracle.get("preconditions"), list):
                errors.append(f"design claim {claim_id}: probe_oracle.preconditions must be an array")
            if oracle.get("testability") in {"candidate", "unknown"}:
                if not oracle.get("stimulus") or not oracle.get("expected_observation"):
                    errors.append(f"design claim {claim_id}: runnable/unknown probe oracle needs stimulus and expected_observation")
            if oracle.get("testability") == "not_suitable" and not oracle.get("non_testable_reason"):
                errors.append(f"design claim {claim_id}: not_suitable probe oracle needs non_testable_reason")

    contract_path = root / "agent_loop_contract.json"
    contract = ac.load_json(contract_path) if contract_path.exists() else {}
    expected_lenses = set(contract.get("coverage_contract", {}).get("portfolio_lenses", []))
    expected_modes = set(contract.get("coverage_contract", {}).get("exploration_modes", []))
    coverage_path = root / "coverage_audit.json"
    coverage = ac.load_json(coverage_path) if coverage_path.exists() else {}
    remaining_gap_refs: dict[str, set[str]] = {}
    for item in coverage.get("remaining_gaps", []):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        ref_id = item.get("ref_id")
        if isinstance(kind, str) and isinstance(ref_id, str) and kind and ref_id:
            remaining_gap_refs.setdefault(kind, set()).add(ref_id)

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
    for index, item in enumerate(architecture.get("integration_boundaries", []), start=1):
        if not isinstance(item, dict):
            continue
        label = f"architecture integration_boundaries[{index}]"
        plane_ids = item.get("plane_ids")
        if not isinstance(plane_ids, list) or not plane_ids:
            errors.append(f"{label}: plane_ids must be a non-empty array")
            continue
        unknown = {str(value) for value in plane_ids if value} - architecture_plane_ids
        if unknown:
            errors.append(f"{label}: unknown plane_ids {sorted(unknown)}")
    parallel_behavior_paths: list[dict[str, Any]] = []
    parallel_path_ids: set[str] = set()
    for index, item in enumerate(architecture.get("parallel_behavior_paths", []), start=1):
        label = f"architecture parallel_behavior_paths[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: must be an object")
            continue
        errors.extend(_require_fields(item, ("path_id", "behavior", "plane_ids", "evidence"), label))
        path_id = str(item.get("path_id") or "")
        if path_id in parallel_path_ids:
            errors.append(f"{label}: duplicate path_id {path_id}")
        elif path_id:
            parallel_path_ids.add(path_id)
        plane_ids = {str(value) for value in item.get("plane_ids", []) if value}
        if len(plane_ids) < 2:
            errors.append(f"{label}: must identify at least two implementation planes")
        unknown = plane_ids - architecture_plane_ids
        if unknown:
            errors.append(f"{label}: unknown implementation planes {sorted(unknown)}")
        parallel_behavior_paths.append(item)

    for observation_id, observation in risks.items():
        errors.extend(hm.validate_item(
            observation, artifact_type="risk", identifier=observation_id,
            session_id=session_id, code_root=code_root,
        ))
        errors.extend(hm._context_errors(
            observation, "risk", root, f"risk ({observation_id})",
        ))
    risk_partition_errors, risk_partition_metrics = rpv.validate_risk_coverage(
        risks, root,
    )
    errors.extend(risk_partition_errors)
    risk_plan_trace_path = trace_root / "risk_sweep_plan_validation.json"
    risk_plan_trace = (
        ac.load_json(risk_plan_trace_path) if risk_plan_trace_path.is_file() else {}
    )
    expected_risk_plan_inputs, expected_risk_plan_combined = rpv.plan_input_digests(root)
    risk_plan_validation_current = (
        risk_plan_trace.get("passed") is True
        and risk_plan_trace.get("session_id") == session_id
        and risk_plan_trace.get("input_digests") == expected_risk_plan_inputs
        and risk_plan_trace.get("combined_input_sha256") == expected_risk_plan_combined
        and set(risk_plan_trace.get("validated_sweep_ids", []))
        == set(risk_partition_metrics.get("expected_sweeps", []))
    )
    if not risk_plan_validation_current:
        errors.append("passed digest-bound risk sweep plan validation is missing or stale")
    for task_id, task in tasks.items():
        errors.extend(_require_fields(task, (
            "session_id", "claim_id", "claim_branch", "hypothesis", "obligation_sha256",
            "starting_points", "supporting_evidence_needed", "disconfirming_evidence_needed",
            "review_lenses", "exploration_mode", "architecture_boundaries", "status",
            "implementation_planes",
        ), f"investigation task {task_id}"))
        errors.extend(hm.validate_item(
            task, artifact_type="task", identifier=task_id, session_id=session_id,
        ))
        errors.extend(hm._context_errors(
            task, "task", root, f"investigation task {task_id}",
        ))
        if task.get("session_id") != session_id:
            errors.append(f"investigation task {task_id}: session does not match current session")
        errors.extend(hm.validate_task_defer_evidence(task, f"investigation task {task_id}"))
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
        task_parallel_ids = task.get("parallel_path_ids", [])
        if not isinstance(task_parallel_ids, list):
            errors.append(f"investigation task {task_id}: parallel_path_ids must be an array")
        else:
            unknown_parallel = set(task_parallel_ids) - parallel_path_ids
            if unknown_parallel:
                errors.append(
                    f"investigation task {task_id}: unknown parallel path IDs {sorted(unknown_parallel)}"
                )
        task_risk_ids = task.get("risk_observation_ids", [])
        if not isinstance(task_risk_ids, list):
            errors.append(f"investigation task {task_id}: risk_observation_ids must be an array")
        else:
            unknown_risks = set(task_risk_ids) - set(risks)
            if unknown_risks:
                errors.append(
                    f"investigation task {task_id}: unknown risk observation IDs {sorted(unknown_risks)}"
                )
            if task.get("exploration_mode") == "code-to-design risk backtracking" and not task_risk_ids:
                errors.append(
                    f"investigation task {task_id}: code-to-design mode lacks a risk observation"
                )
            for risk_id in set(task_risk_ids).intersection(risks):
                observation = risks[risk_id]
                if not set(task.get("architecture_boundaries", [])).intersection(
                    observation.get("architecture_boundaries", [])
                ):
                    errors.append(
                        f"investigation task {task_id}: risk observation {risk_id} shares no boundary"
                    )
                if not set(task.get("implementation_planes", [])).intersection(
                    observation.get("implementation_planes", [])
                ):
                    errors.append(
                        f"investigation task {task_id}: risk observation {risk_id} shares no plane"
                    )

    for finding_id, finding in findings.items():
        try:
            template = hm._load_template(
                root / "handoff-templates" / "investigators", finding, required=True,
            )
        except (OSError, ValueError) as exc:
            template = None
            errors.append(f"investigation finding {finding_id}: {exc}")
        errors.extend(hm.validate_item(
            finding, artifact_type="finding", identifier=finding_id,
            session_id=session_id, code_root=code_root, design_root=design_root,
            template=template if isinstance(template, dict) else None,
        ))
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
            "review_context", "resolution", "normative_assessment",
            "dynamic_probe_review",
        ), f"critic review {finding_id}"))
        errors.extend(hm.validate_artifact(
            critique, "critic", f"critic review {finding_id}",
        ))
        errors.extend(hm._context_errors(
            critique, "critic", root, f"critic review {finding_id}",
        ))
        errors.extend(hm.validate_critic_bindings(
            critique, root, f"critic review {finding_id}",
        ))
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
        round_claims = {str(value) for value in round_item.get("claim_ids", []) if value}
        round_tasks = {str(value) for value in round_item.get("task_ids", []) if value}
        round_findings = {str(value) for value in round_item.get("finding_ids", []) if value}
        for mode in modes.intersection(expected_modes):
            matching_tasks = {
                task_id for task_id in round_tasks.intersection(tasks)
                if tasks[task_id].get("exploration_mode") == mode
            }
            if not matching_tasks:
                errors.append(
                    f"investigation round {round_id}: exploration mode {mode!r} "
                    "has no task with that mode"
                )
                continue
            evidenced_tasks = {
                str(findings[finding_id].get("task_id") or "")
                for finding_id in round_findings.intersection(findings)
            }
            completed = {
                task_id for task_id in matching_tasks
                if tasks[task_id].get("status") == "complete"
            }
            if not completed.intersection(evidenced_tasks):
                errors.append(
                    f"investigation round {round_id}: exploration mode {mode!r} "
                    "lacks a completed task/finding"
                )
                continue
            observed_modes.add(mode)
        for task_id in round_tasks.intersection(tasks):
            claim_id = str(tasks[task_id].get("claim_id") or "")
            if claim_id not in round_claims:
                errors.append(f"investigation round {round_id}: task {task_id} claim is absent from claim_ids")
        for finding_id in round_findings.intersection(findings):
            finding = findings[finding_id]
            if str(finding.get("task_id") or "") not in round_tasks:
                errors.append(f"investigation round {round_id}: finding {finding_id} task is absent from task_ids")
            if str(finding.get("claim_id") or "") not in round_claims:
                errors.append(f"investigation round {round_id}: finding {finding_id} claim is absent from claim_ids")
    missing_modes = sorted(expected_modes - observed_modes)
    unrecorded_modes = set(missing_modes) - remaining_gap_refs.get("exploration_mode", set())
    if unrecorded_modes:
        errors.append(
            "investigation modes lack completed evidence or a recorded coverage gap: "
            f"{sorted(unrecorded_modes)}"
        )

    tasks_by_claim: dict[str, list[dict[str, Any]]] = {}
    findings_by_task: dict[str, list[dict[str, Any]]] = {}
    for task in tasks.values():
        tasks_by_claim.setdefault(str(task.get("claim_id") or ""), []).append(task)
    for finding in findings.values():
        findings_by_task.setdefault(str(finding.get("task_id") or ""), []).append(finding)
    for task_id, linked_findings in findings_by_task.items():
        task = tasks.get(task_id)
        if task is None:
            errors.append(f"finding ledger references unknown task {task_id!r}")
            continue
        if len(linked_findings) != 1:
            errors.append(f"investigation task {task_id} has {len(linked_findings)} findings; expected one")
        if task.get("status") != "complete":
            errors.append(f"investigation task {task_id} has a finding but is not complete")
    for task_id, task in tasks.items():
        linked_count = len(findings_by_task.get(task_id, []))
        if task.get("status") == "complete" and linked_count != 1:
            errors.append(
                f"completed investigation task {task_id} has {linked_count} findings; expected one"
            )
        if task.get("status") in {"pending", "in_progress", "deferred"} and linked_count:
            errors.append(
                f"non-complete investigation task {task_id} must not have a finding"
            )
    for index, item in enumerate(parallel_behavior_paths, start=1):
        path_id = str(item.get("path_id") or "")
        required_planes = {str(value) for value in item.get("plane_ids", []) if value}
        covering_tasks = {
            task_id: task for task_id, task in tasks.items()
            if task.get("status") == "complete"
            and findings_by_task.get(task_id)
            and path_id in task.get("parallel_path_ids", [])
        }
        covered_planes = {
            str(plane_id)
            for task in covering_tasks.values()
            for plane_id in task.get("implementation_planes", [])
            if plane_id in required_planes
        }
        missing_planes = required_planes - covered_planes
        if missing_planes and path_id not in remaining_gap_refs.get("parallel_path", set()):
            errors.append(
                f"parallel behavior path {path_id or index} lacks linked evidence or a recorded gap "
                f"for planes {sorted(missing_planes)}"
            )
    uninvestigated_scoped_claims: list[str] = []
    for claim_id in sorted(accepted_claim_ids):
        completed = [task for task in tasks_by_claim.get(claim_id, []) if task.get("status") == "complete"]
        if not completed or not any(findings_by_task.get(str(task.get("task_id") or "")) for task in completed):
            uninvestigated_scoped_claims.append(claim_id)

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
        if disposition not in {"investigated", "inapplicable", "gap_recorded"}:
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
        if disposition in {"inapplicable", "gap_recorded"} and not entry.get("counterfactual"):
            errors.append(f"semantic coverage {lens}: {disposition} lens needs counterfactual")
        if disposition == "gap_recorded":
            if lens_tasks or lens_findings:
                errors.append(
                    f"semantic coverage {lens}: gap_recorded must not claim task/finding evidence"
                )
            if lens not in remaining_gap_refs.get("lens", set()):
                errors.append(
                    f"semantic coverage {lens}: gap_recorded lacks coverage_audit remaining gap"
                )
        for task_id in lens_tasks:
            if task_id not in tasks:
                errors.append(f"semantic coverage {lens}: unknown task_id {task_id}")
        for finding_id in lens_findings:
            if finding_id not in findings:
                errors.append(f"semantic coverage {lens}: unknown finding_id {finding_id}")
        if disposition == "investigated":
            referenced_tasks = {task_id: tasks[task_id] for task_id in lens_tasks if task_id in tasks}
            referenced_findings = {
                finding_id: findings[finding_id]
                for finding_id in lens_findings if finding_id in findings
            }
            linked_task_ids = {
                str(finding.get("task_id") or "") for finding in referenced_findings.values()
            }
            for task_id, task in referenced_tasks.items():
                if task.get("status") != "complete":
                    errors.append(f"semantic coverage {lens}: task {task_id} is not complete")
                if lens not in task.get("review_lenses", []):
                    errors.append(f"semantic coverage {lens}: task {task_id} does not declare this lens")
                if task_id not in linked_task_ids:
                    errors.append(f"semantic coverage {lens}: task {task_id} has no linked finding")
            for finding_id, finding in referenced_findings.items():
                task_id = str(finding.get("task_id") or "")
                task = referenced_tasks.get(task_id)
                if task is None:
                    errors.append(f"semantic coverage {lens}: finding {finding_id} is not linked to a listed task")
                    continue
                if lens not in finding.get("review_lenses", []):
                    errors.append(f"semantic coverage {lens}: finding {finding_id} does not declare this lens")
                if str(task.get("claim_id") or "") != str(finding.get("claim_id") or ""):
                    errors.append(f"semantic coverage {lens}: task/finding claim mismatch for {finding_id}")
            task_boundaries = {
                str(boundary)
                for task in referenced_tasks.values()
                for boundary in task.get("architecture_boundaries", []) if boundary
            }
            uncovered_boundaries = set(boundary_refs) - task_boundaries
            if uncovered_boundaries:
                errors.append(
                    f"semantic coverage {lens}: boundary_refs lack linked task evidence "
                    f"{sorted(uncovered_boundaries)}"
                )
            task_groups = {
                str(
                    claims[str(task.get("claim_id"))].get("document_key")
                    or member_group.get(
                        str(claims[str(task.get("claim_id"))].get("path") or ""),
                    )
                    or ""
                )
                for task in referenced_tasks.values()
                if str(task.get("claim_id") or "") in claims
            }
            uncovered_groups = set(design_refs) - task_groups
            if uncovered_groups:
                errors.append(
                    f"semantic coverage {lens}: design_group_refs lack linked claim evidence "
                    f"{sorted(uncovered_groups)}"
                )

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

    coverage_required = [
        "session_id", "design_documents_reviewed", "claims_total", "claims_investigated",
        "rounds_completed", "exploration_modes_completed", "document_groups_total", "document_groups_accounted",
        "code_areas_reviewed", "architecture_boundaries", "remaining_scoped_claims",
        "deferred_claims", "supplement_rounds", "remaining_gaps", "stop_reason",
    ]
    for field in coverage_required:
        if field not in coverage:
            errors.append(f"coverage_audit.json missing {field}")
    if coverage.get("session_id") not in (None, session_id):
        errors.append("coverage audit session does not match current session")
    if coverage.get("supplement_rounds") not in {0, 1}:
        errors.append("coverage audit supplement_rounds must be 0 or 1")
    if not isinstance(coverage.get("remaining_gaps"), list):
        errors.append("coverage audit remaining_gaps must be an array")
    if set(coverage.get("exploration_modes_completed", [])) != observed_modes:
        errors.append("coverage audit exploration_modes_completed does not match round evidence")
    deferred_ids: set[str] = set()
    for deferred in coverage.get("deferred_claims", []):
        if not isinstance(deferred, dict):
            errors.append("coverage audit deferred_claims entries must be objects")
            continue
        claim_id = str(deferred.get("claim_id") or "")
        task_id = str(deferred.get("task_id") or "")
        if not claim_id or not task_id or not deferred.get("reason"):
            errors.append("coverage audit deferred claim needs claim_id, task_id, and reason")
            continue
        claim = claims.get(claim_id)
        if not claim:
            errors.append(f"coverage audit defers unknown claim {claim_id}")
            continue
        task = tasks.get(task_id)
        if not task or str(task.get("claim_id") or "") != claim_id:
            errors.append(f"coverage audit deferred claim {claim_id} lacks its linked task")
            continue
        defer_errors = hm.validate_task_defer_evidence(task, f"coverage deferred task {task_id}")
        if defer_errors:
            errors.extend(defer_errors)
            continue
        deferred_ids.add(claim_id)
    remaining_scoped_entries = coverage.get("remaining_scoped_claims", [])
    remaining_scoped_ids: set[str] = set()
    for remaining in remaining_scoped_entries:
        if not isinstance(remaining, dict):
            errors.append("coverage audit remaining_scoped_claims entries must be objects")
            continue
        claim_id = str(remaining.get("claim_id") or "")
        if not claim_id or not remaining.get("reason"):
            errors.append("coverage audit remaining scoped claim needs claim_id and reason")
            continue
        remaining_scoped_ids.add(claim_id)
        if claim_id not in scoped_claim_ids:
            errors.append(f"coverage audit has out-of-scope remaining claim {claim_id}")
    actionable_scoped_claims = set(uninvestigated_scoped_claims) - deferred_ids
    if remaining_scoped_ids != actionable_scoped_claims:
        errors.append(
            "coverage audit remaining_scoped_claims does not match uninvestigated, "
            "non-deferred scoped claims"
        )
    if actionable_scoped_claims:
        errors.append(
            f"scoped design claims remain uninvestigated: {sorted(actionable_scoped_claims)}"
        )
    if coverage.get("next_round_tasks"):
        errors.append("coverage audit still contains next_round_tasks")
    unfinished_tasks = sorted(
        task_id for task_id, task in tasks.items()
        if task.get("status") in {"pending", "in_progress"}
    )
    if unfinished_tasks:
        errors.append(f"investigation frontier still has unfinished tasks: {unfinished_tasks}")
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
            continue
        if item.get("status") not in {"investigated", "gap_recorded"} or not item.get("evidence"):
            errors.append(f"architecture boundary {boundary_id} lacks status/evidence")
        linked_tasks = [
            task_id for task_id, task in tasks.items()
            if task.get("status") == "complete"
            and boundary_id in task.get("architecture_boundaries", [])
            and findings_by_task.get(task_id)
        ]
        if item.get("status") == "investigated" and not linked_tasks:
            errors.append(
                f"high-risk architecture boundary lacks completed task/finding evidence: {boundary_id}"
            )
        if (
            item.get("status") == "gap_recorded"
            and boundary_id not in remaining_gap_refs.get("architecture_boundary", set())
        ):
            errors.append(
                f"high-risk architecture boundary gap is not recorded: {boundary_id}"
            )

    started_at = str(state.get("started_at") or "")
    elapsed_seconds = 0
    if started_at:
        elapsed_seconds = max(0, int((ac.parse_iso(ac.now_iso()) - ac.parse_iso(started_at)).total_seconds()))
    else:
        errors.append("session started_at missing")
    if elapsed_seconds > MAX_SECONDS:
        errors.append(f"review elapsed time {elapsed_seconds}s exceeds {MAX_SECONDS}s")
    checks.update({
        "result_artifacts_exist": all((result_root / name).is_file() for name in ("issues.json", "issues.jsonl", "00-summary.md")),
        "preflight_passed": not preflight_problems,
        "design_claim_review_passed": (
            claim_review_validation.get("passed") is True
            and not unreviewed_used_claims
        ),
        "stage_validations_current": all(
            stage_validation_current(stage)
            for stage in ("architecture", "task-plan", "task-lifecycle", "coverage")
        ),
        "only_confirmed_published": all(issue.get("status") == "confirmed" for issue in issues),
        "handoff_chain_complete": not any("handoff" in error for error in errors),
        "coverage_complete": (
            bool(coverage)
            and not missing_groups
            and len(coverage_groups) == len(expected_groups)
            and not coverage.get("next_round_tasks")
            and not actionable_scoped_claims
            and not unfinished_tasks
            and stage_validation_current("coverage")
        ),
        "semantic_lenses_covered": (
            bool(expected_lenses)
            and not missing_lenses
            and len(lens_entries) == len(expected_lenses)
        ),
        "architecture_mapped": bool(architecture) and "integration_boundaries" in architecture,
        "risk_backtracking_complete": (
            bool(risks) and not risk_partition_errors and risk_plan_validation_current
        ),
        "risk_partition_complete": not risk_partition_errors and risk_plan_validation_current,
        "investigation_round_recorded": bool(rounds),
        "exploration_modes_complete": (
            bool(expected_modes)
            and not (set(missing_modes) - remaining_gap_refs.get("exploration_mode", set()))
        ),
        "dynamic_probe_integrity": not probe_integrity_errors,
        "target_roots_unchanged": not (
            target_integrity_errors or supplied_source_integrity_errors
        ),
        "supplied_design_source_unchanged": not supplied_source_integrity_errors,
        "review_snapshots_unchanged": not review_integrity_errors,
        "within_time_budget": elapsed_seconds <= MAX_SECONDS,
        "run_clock_unchanged": not clock_errors,
        "rich_trace_complete": not trace_contract_errors,
        "evidence_validation_passed": evidence_validation_current,
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
            "confirmed": confirmed_count,
            "elapsed_seconds": elapsed_seconds,
            "maximum_seconds": MAX_SECONDS,
            "design_document_groups": len(expected_groups),
            "design_claims": len(claims),
            "risk_observations": len(risks),
            "risk_sweeps": len(risk_partition_metrics.get("observed_sweeps", [])),
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
