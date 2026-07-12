#!/usr/bin/env python3
"""Validate and merge isolated subagent JSON handoffs into one JSONL ledger.

This helper validates syntax and artifact shape only. It performs no semantic
ranking, filtering, or design/code judgement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import agent_common as ac
import handoff_template as ht


ARTIFACT_TYPES = {"generic", "task", "risk", "finding", "critic", "probe"}
ARTIFACT_KEYS = {
    "task": "task_id",
    "risk": "observation_id",
    "finding": "finding_id",
    "critic": "finding_id",
    "probe": "probe_id",
}
TRACE_KINDS = {
    "design_read", "code_search", "code_navigation", "code_read", "reverse_check",
    "test", "config_read", "history_read", "build_read", "analysis",
}
DEFER_FAILURE_KINDS = {"provider_failure", "tool_failure"}
FINDING_TEMPLATE_FIELDS = (
    "finding_id", "session_id", "task_id", "claim_id", "claim_branch",
    "obligation_sha256", "hypothesis", "expected_behavior", "design_evidence",
    "review_lenses",
)
CRITIC_ALLOWED_KEYS = {
    "review_id", "session_id", "finding_id", "claim_id", "decision", "challenges",
    "checks_performed", "dynamic_probe_review", "review_context", "resolution",
    "remaining_risks", "normative_assessment", "input_digests",
    "evidence_critic_prompt_version",
}
CRITIC_INPUT_DIGEST_KEYS = {
    "claim_sha256", "finding_sha256", "probe_sha256",
}
EVIDENCE_CRITIC_PROMPT_VERSION = "evidence-critic-v4"
NORMATIVE_ASSESSMENT_ALLOWED_KEYS = {
    "claim_strength", "applicability", "obligation_status", "actual_conflict",
    "rationale",
}
NORMATIVE_APPLICABILITY = {"supported", "unsupported", "ambiguous"}
NORMATIVE_OBLIGATION_STATUS = {
    "binding_required", "binding_recommended", "declared_capability",
    "optional_adopted", "optional_not_adopted", "informational",
}
NORMATIVE_CONFLICT_STATUS = {"yes", "no", "uncertain"}
DYNAMIC_PROBE_REVIEW_ALLOWED_KEYS = {
    "status", "probe_id", "oracle_validity", "environment_validity", "reachability",
    "effect_on_decision",
}
PROBE_INTERPRETATIONS = {
    "supports_contradiction", "disconfirms_contradiction", "inconclusive",
}
PROBE_SECONDARY_ORACLE_KINDS = {
    "reference_model", "minimal_reference", "known_good_path", "negative_control",
    "not_available",
}
COVERAGE_TASK_SCALAR_FIELDS = (
    "claim_id", "claim_branch", "hypothesis", "obligation_sha256",
    "exploration_mode",
)
COVERAGE_TASK_LIST_FIELDS = (
    "review_lenses", "architecture_boundaries", "implementation_planes",
    "parallel_path_ids", "risk_observation_ids", "source_gap_ids",
)
TASK_LIFECYCLE_FIELDS = {"status", "defer_reason", "defer_evidence"}


class HandoffValidationError(ValueError):
    def __init__(self, errors_by_id: dict[str, list[str]]):
        self.errors_by_id = errors_by_id
        self.invalid_ids = sorted(errors_by_id)
        self.errors = [error for identifier in self.invalid_ids for error in errors_by_id[identifier]]
        super().__init__("; ".join(self.errors))


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def coverage_task_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable frontier fields shared by a coverage request/task."""
    projected = {field: item.get(field) for field in COVERAGE_TASK_SCALAR_FIELDS}
    for field in COVERAGE_TASK_LIST_FIELDS:
        value = item.get(field)
        projected[field] = sorted({
            entry for entry in value
            if isinstance(entry, str) and entry
        }) if isinstance(value, list) else []
    return projected


def task_plan_ledger_sha256(values: dict[str, dict[str, Any]]) -> str:
    """Digest task handoff plan fields while ignoring lifecycle transitions."""
    return canonical_digest({
        task_id: {
            key: value for key, value in task.items()
            if key not in TASK_LIFECYCLE_FIELDS
        }
        for task_id, task in sorted(values.items())
    })


def validate_task_coverage_binding(
    item: dict[str, Any], state_root: Path, label: str,
) -> list[str]:
    """Bind tasks created after coverage to the one recorded supplement request."""
    history_path = state_root / "coverage_supplement_history.json"
    if not history_path.is_file():
        return [f"{label}: coverage_supplement_history.json is missing"]
    try:
        history = ac.load_json(history_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{label}: coverage supplement history is invalid: {exc}"]
    requests = history.get("requests") if isinstance(history, dict) else None
    if not isinstance(requests, list) or len(requests) > 1:
        return [f"{label}: coverage supplement history has an invalid request ledger"]
    marker = item.get("coverage_request_sha256")
    source_gap_ids = item.get("source_gap_ids")
    if not requests:
        if marker not in (None, "") or source_gap_ids not in (None, []):
            return [f"{label}: supplement binding exists without a recorded coverage request"]
        return []
    request = requests[0]
    if not isinstance(request, dict):
        return [f"{label}: recorded coverage supplement request is invalid"]
    prior_task_ids = request.get("prior_task_ids")
    task_specs = request.get("task_specs")
    if not isinstance(prior_task_ids, list) or not isinstance(task_specs, list):
        return [f"{label}: recorded coverage supplement request lacks task bindings"]
    task_id = str(item.get("task_id") or "")
    if task_id in {value for value in prior_task_ids if isinstance(value, str)}:
        if marker not in (None, "") or source_gap_ids not in (None, []):
            return [f"{label}: pre-coverage task cannot claim a supplement binding"]
        return []
    errors: list[str] = []
    request_sha256 = request.get("request_sha256")
    if marker != request_sha256:
        errors.append(
            f"{label}: post-coverage task must bind coverage_request_sha256 to the recorded request"
        )
    projection = coverage_task_projection(item)
    if projection not in task_specs:
        errors.append(f"{label}: post-coverage task does not match a requested supplement task")
    return errors


def validate_probe_chain(
    findings: dict[str, dict[str, Any]],
    probes: dict[str, dict[str, Any]],
    critiques: dict[str, dict[str, Any]],
) -> list[str]:
    """Require the investigator selection, probe, and critic to form one chain."""
    errors: list[str] = []
    probes_by_finding: dict[str, list[dict[str, Any]]] = {}
    for probe_id, probe in probes.items():
        finding_id = str(probe.get("finding_id") or "")
        if finding_id not in findings:
            errors.append(f"dynamic probe {probe_id}: unknown finding_id {finding_id!r}")
        probes_by_finding.setdefault(finding_id, []).append(probe)
    for finding_id, finding in findings.items():
        selection = finding.get("dynamic_probe_selection")
        disposition = selection.get("disposition") if isinstance(selection, dict) else ""
        linked = probes_by_finding.get(finding_id, [])
        critique = critiques.get(finding_id, {})
        review = critique.get("dynamic_probe_review") if isinstance(critique, dict) else {}
        review = review if isinstance(review, dict) else {}
        if disposition == "selected":
            if len(linked) != 1:
                errors.append(
                    f"finding {finding_id}: selected probe requires exactly one artifact; found {len(linked)}"
                )
                continue
            probe = linked[0]
            if str(review.get("probe_id") or "") != str(probe.get("probe_id") or ""):
                errors.append(
                    f"finding {finding_id}: critic must review the selected probe artifact"
                )
            if review.get("status") != probe.get("interpretation"):
                errors.append(
                    f"finding {finding_id}: critic probe status does not match the selected probe"
                )
        else:
            if linked:
                errors.append(
                    f"finding {finding_id}: {disposition or 'invalid'} probe selection cannot have probe artifacts"
                )
            if critique and (
                review.get("status") != "not_run"
                or str(review.get("probe_id") or "")
            ):
                errors.append(
                    f"finding {finding_id}: critic must record not_run with no probe for an unselected probe"
                )
    return errors


def _require(item: dict[str, Any], fields: tuple[str, ...], label: str) -> list[str]:
    return [f"{label}: missing/empty {field}" for field in fields if not _present(item.get(field))]


def _validate_concrete_string_list(
    item: dict[str, Any], field: str, label: str, *, minimum: int,
) -> list[str]:
    value = item.get(field)
    if not isinstance(value, list) or len(value) < minimum:
        return [
            f"{label}: {field} must contain at least {minimum} concrete non-empty strings"
        ]
    errors = [
        f"{label}: {field}[{index}] must be a concrete non-empty string"
        for index, entry in enumerate(value, start=1)
        if not isinstance(entry, str) or not entry.strip()
    ]
    normalized = {
        entry.strip() for entry in value if isinstance(entry, str) and entry.strip()
    }
    if len(normalized) < minimum:
        errors.append(
            f"{label}: {field} must contain at least {minimum} distinct concrete entries"
        )
    return errors


def _validate_trace(item: dict[str, Any], label: str) -> list[str]:
    trace = item.get("tool_trace")
    if not isinstance(trace, list) or len(trace) < 4:
        return [f"{label}: tool_trace must contain at least four real steps"]
    errors: list[str] = []
    kinds: set[str] = set()
    for index, step in enumerate(trace, start=1):
        step_label = f"{label}: tool_trace[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{step_label} must be an object")
            continue
        errors.extend(_require(step, ("kind", "tool", "target", "purpose", "result"), step_label))
        if step.get("seq") != index:
            errors.append(f"{step_label}: seq must equal {index}")
        kind = str(step.get("kind") or "")
        if kind not in TRACE_KINDS:
            errors.append(f"{step_label}: unsupported kind {kind!r}")
        kinds.add(kind)
    for required, description in (
        ({"design_read"}, "design_read"),
        ({"code_search", "code_navigation"}, "code_search or code_navigation"),
        ({"code_read"}, "code_read"),
        ({"reverse_check"}, "reverse_check"),
    ):
        if not kinds.intersection(required):
            errors.append(f"{label}: tool_trace lacks {description}")
    return errors


def _validate_risk_trace(item: dict[str, Any], label: str) -> list[str]:
    trace = item.get("tool_trace")
    if not isinstance(trace, list) or len(trace) < 3:
        return [f"{label}: design-guided tool_trace must contain at least three real steps"]
    errors: list[str] = []
    kinds: set[str] = set()
    for index, step in enumerate(trace, start=1):
        step_label = f"{label}: tool_trace[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{step_label} must be an object")
            continue
        errors.extend(_require(step, ("kind", "tool", "target", "purpose", "result"), step_label))
        if step.get("seq") != index:
            errors.append(f"{step_label}: seq must equal {index}")
        kind = str(step.get("kind") or "")
        if kind not in TRACE_KINDS:
            errors.append(f"{step_label}: unsupported kind {kind!r}")
        kinds.add(kind)
    for required, description in (
        ({"design_read"}, "design_read"),
        ({"code_search", "code_navigation"}, "code_search or code_navigation"),
        ({"code_read"}, "code_read"),
    ):
        if not kinds.intersection(required):
            errors.append(f"{label}: tool_trace lacks {description}")
    return errors


def _validate_probe_trace(item: dict[str, Any], label: str) -> list[str]:
    trace = item.get("tool_trace")
    if not isinstance(trace, list) or len(trace) < 2:
        return [f"{label}: tool_trace must contain at least two real probe steps"]
    errors: list[str] = []
    kinds: set[str] = set()
    for index, step in enumerate(trace, start=1):
        step_label = f"{label}: tool_trace[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{step_label} must be an object")
            continue
        errors.extend(_require(
            step, ("kind", "tool", "target", "purpose", "result"), step_label,
        ))
        if step.get("seq") != index:
            errors.append(f"{step_label}: seq must equal {index}")
        kind = str(step.get("kind") or "")
        if kind not in TRACE_KINDS:
            errors.append(f"{step_label}: unsupported kind {kind!r}")
        kinds.add(kind)
    if "test" not in kinds:
        errors.append(f"{label}: tool_trace lacks a test step")
    return errors


def validate_probe_contract(item: dict[str, Any], label: str) -> list[str]:
    """Validate an isolated focused probe without interpreting its semantics."""
    errors = _require(item, (
        "probe_id", "session_id", "finding_id", "claim_id", "oracle",
        "oracle_validation", "selection_reason", "isolation", "baseline",
        "execution", "interpretation", "tool_trace",
    ), label)

    oracle = item.get("oracle")
    if not isinstance(oracle, dict):
        errors.append(f"{label}: oracle must be an object")
    else:
        errors.extend(_require(oracle, (
            "source", "claim_id", "claim_sha256", "source_sha256",
            "preconditions", "stimulus", "expected_observation",
        ), f"{label}: oracle"))
        if oracle.get("source") != "design_claim":
            errors.append(f"{label}: oracle.source must be design_claim")
        if not isinstance(oracle.get("preconditions"), list):
            errors.append(f"{label}: oracle.preconditions must be an array")

    validation = item.get("oracle_validation")
    if not isinstance(validation, dict):
        errors.append(f"{label}: oracle_validation must be an object")
    else:
        errors.extend(_require(validation, (
            "non_triviality", "secondary_oracle", "evidence_role",
        ), f"{label}: oracle_validation"))
        role = validation.get("evidence_role")
        if role not in {"corroborating", "auxiliary"}:
            errors.append(f"{label}: oracle_validation.evidence_role is invalid")

        non_triviality = validation.get("non_triviality")
        non_triviality_status = ""
        if not isinstance(non_triviality, dict):
            errors.append(f"{label}: oracle_validation.non_triviality must be an object")
        else:
            errors.extend(_require(
                non_triviality, ("status", "result"),
                f"{label}: oracle_validation.non_triviality",
            ))
            non_triviality_status = str(non_triviality.get("status") or "")
            if non_triviality_status not in {"passed", "failed", "not_run"}:
                errors.append(
                    f"{label}: oracle_validation.non_triviality.status is invalid"
                )
            if non_triviality_status != "not_run" and not _present(
                non_triviality.get("method")
            ):
                errors.append(
                    f"{label}: executed non-triviality validation needs a method"
                )

        secondary = validation.get("secondary_oracle")
        secondary_status = ""
        if not isinstance(secondary, dict):
            errors.append(f"{label}: oracle_validation.secondary_oracle must be an object")
        else:
            errors.extend(_require(
                secondary, ("kind", "status", "result"),
                f"{label}: oracle_validation.secondary_oracle",
            ))
            kind = secondary.get("kind")
            secondary_status = str(secondary.get("status") or "")
            if kind not in PROBE_SECONDARY_ORACLE_KINDS:
                errors.append(
                    f"{label}: oracle_validation.secondary_oracle.kind is invalid"
                )
            if kind == "not_available":
                if secondary_status != "not_run":
                    errors.append(
                        f"{label}: unavailable secondary oracle must have status not_run"
                    )
            else:
                if secondary_status not in {"passed", "failed"}:
                    errors.append(
                        f"{label}: executable secondary oracle must pass or fail"
                    )
                if not _present(secondary.get("command")):
                    errors.append(
                        f"{label}: executable secondary oracle needs a command"
                    )

        interpretation = item.get("interpretation")
        if non_triviality_status != "passed" and interpretation != "inconclusive":
            errors.append(
                f"{label}: an unvalidated/non-triviality-failing oracle must be inconclusive"
            )
        if secondary_status == "failed" and interpretation != "inconclusive":
            errors.append(
                f"{label}: a failed secondary oracle must make the probe inconclusive"
            )
        if (
            non_triviality_status != "passed" or secondary_status != "passed"
        ) and role != "auxiliary":
            errors.append(
                f"{label}: a probe without both oracle checks passed must be auxiliary"
            )

    isolation = item.get("isolation")
    if not isinstance(isolation, dict):
        errors.append(f"{label}: isolation must be an object")
    else:
        errors.extend(_require(
            isolation, (
                "kind", "workspace", "command_cwd", "original_target_unchanged",
            ),
            f"{label}: isolation",
        ))
        if isolation.get("kind") != "session_copy":
            errors.append(f"{label}: isolation.kind must be session_copy")
        if isolation.get("original_target_unchanged") is not True:
            errors.append(f"{label}: original_target_unchanged must be true")

    baseline = item.get("baseline")
    baseline_status = ""
    if not isinstance(baseline, dict):
        errors.append(f"{label}: baseline must be an object")
    else:
        errors.extend(_require(baseline, ("status", "result"), f"{label}: baseline"))
        baseline_status = str(baseline.get("status") or "")
        if baseline_status not in {"passed", "failed", "not_available"}:
            errors.append(f"{label}: baseline.status is invalid")
        if baseline_status != "not_available" and not _present(baseline.get("command")):
            errors.append(f"{label}: executed baseline needs a command")

    execution = item.get("execution")
    execution_status = ""
    target_reached = False
    if not isinstance(execution, dict):
        errors.append(f"{label}: execution must be an object")
    else:
        errors.extend(_require(execution, ("status", "observed"), f"{label}: execution"))
        execution_status = str(execution.get("status") or "")
        target_reached = execution.get("target_reached") is True
        if execution_status not in {"completed", "environment_failed", "not_executed"}:
            errors.append(f"{label}: execution.status is invalid")
        if execution_status == "completed":
            if not _present(execution.get("command")):
                errors.append(f"{label}: completed execution needs a command")
            if not isinstance(execution.get("exit_code"), int):
                errors.append(f"{label}: completed execution needs an integer exit_code")

    interpretation = item.get("interpretation")
    if interpretation not in PROBE_INTERPRETATIONS:
        errors.append(f"{label}: invalid interpretation")
    if (
        baseline_status != "passed" or execution_status != "completed" or not target_reached
    ) and interpretation != "inconclusive":
        errors.append(
            f"{label}: environment/baseline/reachability limitations must be inconclusive"
        )
    if "limitations" not in item or not isinstance(item.get("limitations"), list):
        errors.append(f"{label}: limitations must be an array")
    errors.extend(_validate_probe_trace(item, label))
    return errors


def validate_probe_workspace(
    item: dict[str, Any], state_root: Path, label: str,
) -> list[str]:
    """Require a real, non-symlinked session-owned probe workspace."""
    isolation = item.get("isolation")
    workspace = str(isolation.get("workspace") or "") if isinstance(
        isolation, dict
    ) else ""
    if not workspace:
        return [f"{label}: isolation.workspace must be a non-empty absolute path"]
    candidate = Path(workspace)
    if not candidate.is_absolute():
        return [f"{label}: isolation.workspace must be an absolute path"]
    probes_root = state_root / "probes"
    try:
        candidate.relative_to(probes_root)
    except ValueError:
        return [f"{label}: isolation.workspace must be below state/probes"]
    errors = ac.lexical_path_errors(
        state_root, candidate, f"{label}: isolation.workspace",
    )
    if errors:
        return errors
    if candidate.is_symlink() or not candidate.is_dir():
        errors.append(
            f"{label}: isolation.workspace must exist as a non-symlink directory"
        )
        return errors
    command_cwd_value = isolation.get("command_cwd")
    command_cwd = (
        Path(os.path.abspath(str(command_cwd_value)))
        if isinstance(command_cwd_value, str) and command_cwd_value else None
    )
    if command_cwd != candidate:
        errors.append(f"{label}: isolation.command_cwd must equal isolation.workspace")

    manifest_path = state_root / "workspace_manifest.json"
    finding_path = state_root / "investigation_findings.jsonl"
    if manifest_path.is_file() and finding_path.is_file():
        manifest = ac.load_json(manifest_path)
        review_root_value = manifest.get("paths", {}).get("review_code_root") \
            if isinstance(manifest, dict) else None
        review_root = (
            Path(os.path.abspath(str(review_root_value)))
            if isinstance(review_root_value, str) and review_root_value else None
        )
        findings, finding_errors = _load_index(finding_path, "finding_id")
        if finding_errors:
            errors.append(f"{label}: cannot bind workspace to invalid finding ledger")
        finding = findings.get(str(item.get("finding_id") or ""))
        evidence_paths = sorted({
            str(evidence.get("file") or evidence.get("path") or "")
            for evidence in finding.get("code_evidence", [])
            if isinstance(evidence, dict)
            and (evidence.get("file") or evidence.get("path"))
        }) if isinstance(finding, dict) else []
        if review_root is None or not review_root.is_dir():
            errors.append(f"{label}: prepared review code root is unavailable")
        elif not evidence_paths:
            errors.append(f"{label}: linked finding has no code evidence to bind the target copy")
        else:
            for relative in evidence_paths:
                source = ac.contained_path(review_root, relative)
                target = candidate / relative
                target_errors = ac.lexical_path_errors(
                    candidate, target, f"{label}: copied target {relative}",
                )
                errors.extend(target_errors)
                if source is None or source.is_symlink() or not source.is_file():
                    errors.append(f"{label}: review target file is unavailable: {relative}")
                elif target_errors or target.is_symlink() or not target.is_file():
                    errors.append(f"{label}: workspace lacks a regular copied target: {relative}")
                elif ac.sha256_file(target) != ac.sha256_file(source):
                    errors.append(f"{label}: copied target differs from review snapshot: {relative}")
    return errors


def validate_task_defer_evidence(item: dict[str, Any], label: str) -> list[str]:
    if item.get("status") != "deferred":
        return []
    errors: list[str] = []
    if not _present(item.get("defer_reason")):
        errors.append(f"{label}: deferred task requires defer_reason")
    evidence = item.get("defer_evidence")
    if not isinstance(evidence, dict):
        return errors + [f"{label}: deferred task requires structured defer_evidence"]
    if evidence.get("kind") not in DEFER_FAILURE_KINDS:
        errors.append(
            f"{label}: defer_evidence.kind must be one of {sorted(DEFER_FAILURE_KINDS)}"
        )
    attempts = evidence.get("attempts")
    if not isinstance(attempts, list) or len(attempts) < 2:
        errors.append(f"{label}: defer_evidence requires at least two failed attempts")
    else:
        for index, attempt in enumerate(attempts, start=1):
            attempt_label = f"{label}: defer_evidence.attempts[{index}]"
            if not isinstance(attempt, dict):
                errors.append(f"{attempt_label} must be an object")
            else:
                errors.extend(_require(
                    attempt, ("attempt_id", "outcome", "evidence"), attempt_label,
                ))
                if attempt.get("outcome") != "failed":
                    errors.append(f"{attempt_label}: outcome must be failed")
    return errors


def validate_artifact(item: dict[str, Any], artifact_type: str, label: str) -> list[str]:
    """Check a handoff's machine contract without judging its semantics."""
    if artifact_type == "generic":
        return []
    errors: list[str] = []
    if artifact_type == "task":
        errors.extend(_require(item, (
            "task_id", "session_id", "claim_id", "claim_branch", "hypothesis",
            "obligation_sha256", "starting_points",
            "supporting_evidence_needed", "disconfirming_evidence_needed", "review_lenses",
            "exploration_mode", "architecture_boundaries", "implementation_planes", "status",
        ), label))
        if "question" in item:
            errors.append(f"{label}: legacy question is unsupported; use hypothesis")
        obligation_digest = item.get("obligation_sha256")
        if _present(obligation_digest) and (
            not isinstance(obligation_digest, str)
            or len(obligation_digest) != 64
            or any(character not in "0123456789abcdef" for character in obligation_digest)
        ):
            errors.append(f"{label}: obligation_sha256 must be a lowercase SHA-256 digest")
        if item.get("status") not in {"pending", "in_progress", "complete", "deferred"}:
            errors.append(f"{label}: invalid status")
        coverage_request = item.get("coverage_request_sha256")
        if coverage_request not in (None, "") and (
            not isinstance(coverage_request, str)
            or len(coverage_request) != 64
            or any(character not in "0123456789abcdef" for character in coverage_request)
        ):
            errors.append(
                f"{label}: coverage_request_sha256 must be a lowercase SHA-256 digest"
            )
        if "source_gap_ids" in item:
            source_gap_ids = item.get("source_gap_ids")
            if (
                not isinstance(source_gap_ids, list)
                or not source_gap_ids
                or any(not isinstance(value, str) or not value for value in source_gap_ids)
            ):
                errors.append(f"{label}: source_gap_ids must contain non-empty gap IDs")
        errors.extend(validate_task_defer_evidence(item, label))
        lenses = item.get("review_lenses")
        if not isinstance(lenses, list) or not 1 <= len(lenses) <= 3:
            errors.append(f"{label}: review_lenses must contain one to three focused lenses")
        for field in ("parallel_path_ids", "risk_observation_ids"):
            if field not in item:
                errors.append(f"{label}: missing {field}")
            elif not isinstance(item.get(field), list):
                errors.append(f"{label}: {field} must be an array")
        return errors

    if artifact_type == "risk":
        errors.extend(_require(item, (
            "observation_id", "session_id", "sweep_id", "risk_sweep_plan_sha256",
            "behavior_question", "observed_code_behavior", "review_lenses",
            "design_section_ids", "design_alignment", "code_evidence",
            "false_positive_checks", "design_lookup_questions", "tool_trace",
        ), label))
        scoped_arrays: list[list[Any]] = []
        for field in (
            "architecture_boundaries", "implementation_planes", "parallel_path_ids",
        ):
            if field not in item:
                errors.append(f"{label}: missing {field}")
                continue
            value = item.get(field)
            if not isinstance(value, list):
                errors.append(f"{label}: {field} must be an array")
                continue
            scoped_arrays.append(value)
            for index, entry in enumerate(value, start=1):
                if not isinstance(entry, str) or not entry.strip():
                    errors.append(
                        f"{label}: {field}[{index}] must be a concrete non-empty string"
                    )
        if len(scoped_arrays) == 3 and not any(
            isinstance(entry, str) and entry.strip()
            for values in scoped_arrays for entry in values
        ):
            errors.append(
                f"{label}: architecture_boundaries, implementation_planes, and "
                "parallel_path_ids must contain at least one entry in total"
            )
        for field in ("sweep_id", "risk_sweep_plan_sha256"):
            value = item.get(field)
            if _present(value) and (not isinstance(value, str) or not value.strip()):
                errors.append(f"{label}: {field} must be a non-empty string")
        lenses = item.get("review_lenses")
        if not isinstance(lenses, list) or not 1 <= len(lenses) <= 3:
            errors.append(f"{label}: review_lenses must contain one to three focused lenses")
        checks = item.get("false_positive_checks")
        if not isinstance(checks, list) or len(checks) < 1:
            errors.append(f"{label}: false_positive_checks must contain at least one check")
        else:
            for index, check in enumerate(checks, start=1):
                if not isinstance(check, dict):
                    errors.append(f"{label}: false_positive_checks[{index}] must be an object")
                else:
                    errors.extend(_require(
                        check, ("question", "method", "target", "result"),
                        f"{label}: false_positive_checks[{index}]",
                    ))
        questions = item.get("design_lookup_questions")
        if not isinstance(questions, list) or not questions or not all(_present(value) for value in questions):
            errors.append(f"{label}: design_lookup_questions must contain non-empty questions")
        section_ids = item.get("design_section_ids")
        if (
            not isinstance(section_ids, list) or not section_ids
            or any(not isinstance(value, str) or not value for value in section_ids)
        ):
            errors.append(f"{label}: design_section_ids must contain non-empty section IDs")
        if not isinstance(item.get("design_alignment"), str) or not item.get(
            "design_alignment", "",
        ).strip():
            errors.append(f"{label}: design_alignment must be a non-empty string")
        forbidden = set(item).intersection({
            "claim_id", "design_evidence", "assessment", "recommendation", "status", "confidence",
        })
        if forbidden:
            errors.append(f"{label}: trace observation contains verdict/claim fields {sorted(forbidden)}")
        errors.extend(_validate_risk_trace(item, label))
        return errors

    if artifact_type == "finding":
        errors.extend(_require(item, (
            "finding_id", "session_id", "task_id", "claim_id", "claim_branch",
            "obligation_sha256", "hypothesis",
            "expected_behavior", "observed_behavior", "design_evidence", "code_evidence",
            "supporting_evidence", "false_positive_checks", "tool_trace",
            "dynamic_probe_selection", "assessment", "review_lenses", "recommendation",
        ), label))
        if item.get("assessment") not in {"contradiction_supported", "uncertain", "design_satisfied"}:
            errors.append(f"{label}: invalid assessment")
        obligation_digest = item.get("obligation_sha256")
        if _present(obligation_digest) and (
            not isinstance(obligation_digest, str)
            or len(obligation_digest) != 64
            or any(character not in "0123456789abcdef" for character in obligation_digest)
        ):
            errors.append(f"{label}: obligation_sha256 must be a lowercase SHA-256 digest")
        if item.get("recommendation") not in {"critic_review", "probable", "reject"}:
            errors.append(f"{label}: invalid recommendation")
        checks = item.get("false_positive_checks")
        if not isinstance(checks, list) or len(checks) < 2:
            errors.append(f"{label}: false_positive_checks must contain at least two checks")
        else:
            for index, check in enumerate(checks, start=1):
                if not isinstance(check, dict):
                    errors.append(f"{label}: false_positive_checks[{index}] must be an object")
                else:
                    errors.extend(_require(check, ("question", "method", "target", "result"), f"{label}: false_positive_checks[{index}]"))
        selection = item.get("dynamic_probe_selection")
        if not isinstance(selection, dict):
            errors.append(f"{label}: dynamic_probe_selection must be an object")
        elif selection.get("disposition") not in {
            "selected", "not_selected", "not_suitable", "environment_limited",
        } or not _present(selection.get("reason")):
            errors.append(f"{label}: invalid dynamic_probe_selection")
        lenses = item.get("review_lenses")
        if not isinstance(lenses, list) or not 1 <= len(lenses) <= 3:
            errors.append(f"{label}: review_lenses must contain one to three focused lenses")
        errors.extend(_validate_trace(item, label))
        return errors

    if artifact_type == "critic":
        unexpected = sorted(set(item) - CRITIC_ALLOWED_KEYS)
        if unexpected:
            errors.append(f"{label}: unsupported fields {unexpected}")
        errors.extend(_require(item, (
            "review_id", "session_id", "finding_id", "claim_id", "decision", "challenges",
            "checks_performed", "dynamic_probe_review", "review_context", "resolution",
            "normative_assessment", "input_digests", "evidence_critic_prompt_version",
        ), label))
        input_digests = item.get("input_digests")
        if not isinstance(input_digests, dict):
            errors.append(f"{label}: input_digests must be an object")
        else:
            digest_keys = set(input_digests)
            if digest_keys != CRITIC_INPUT_DIGEST_KEYS:
                errors.append(
                    f"{label}: input_digests keys must be exactly "
                    f"{sorted(CRITIC_INPUT_DIGEST_KEYS)}"
                )
            for field in ("claim_sha256", "finding_sha256"):
                value = input_digests.get(field)
                if (
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(character not in "0123456789abcdef" for character in value)
                ):
                    errors.append(
                        f"{label}: input_digests.{field} must be a lowercase SHA-256 digest"
                    )
            probe_digest = input_digests.get("probe_sha256")
            if probe_digest != "" and (
                not isinstance(probe_digest, str)
                or len(probe_digest) != 64
                or any(character not in "0123456789abcdef" for character in probe_digest)
            ):
                errors.append(
                    f"{label}: input_digests.probe_sha256 must be empty or a lowercase SHA-256 digest"
                )
        if item.get("evidence_critic_prompt_version") != EVIDENCE_CRITIC_PROMPT_VERSION:
            errors.append(
                f"{label}: evidence_critic_prompt_version must be "
                f"{EVIDENCE_CRITIC_PROMPT_VERSION!r}"
            )
        if item.get("decision") not in {
            "confirm_contradiction", "confirm_optional_gap", "reject_issue",
            "needs_more_evidence",
        }:
            errors.append(f"{label}: invalid decision")
        normative = item.get("normative_assessment")
        if not isinstance(normative, dict):
            errors.append(f"{label}: normative_assessment must be an object")
        else:
            unexpected_normative = sorted(
                set(normative) - NORMATIVE_ASSESSMENT_ALLOWED_KEYS
            )
            if unexpected_normative:
                errors.append(
                    f"{label}: normative_assessment has unsupported fields "
                    f"{unexpected_normative}"
                )
            errors.extend(_require(
                normative,
                (
                    "claim_strength", "applicability", "obligation_status",
                    "actual_conflict", "rationale",
                ),
                f"{label}: normative_assessment",
            ))
            if normative.get("applicability") not in NORMATIVE_APPLICABILITY:
                errors.append(f"{label}: invalid normative_assessment.applicability")
            if normative.get("obligation_status") not in NORMATIVE_OBLIGATION_STATUS:
                errors.append(f"{label}: invalid normative_assessment.obligation_status")
            if normative.get("actual_conflict") not in NORMATIVE_CONFLICT_STATUS:
                errors.append(f"{label}: invalid normative_assessment.actual_conflict")
            if item.get("decision") == "confirm_contradiction" and (
                normative.get("applicability") != "supported"
                or normative.get("actual_conflict") != "yes"
                or normative.get("obligation_status")
                in {"optional_not_adopted", "informational"}
            ):
                errors.append(
                    f"{label}: confirm_contradiction requires supported applicability, "
                    "an actual conflict, and a binding/adopted obligation"
                )
            if item.get("decision") == "confirm_optional_gap" and (
                normative.get("applicability") != "supported"
                or normative.get("actual_conflict") != "no"
                or normative.get("obligation_status") != "optional_not_adopted"
            ):
                errors.append(
                    f"{label}: confirm_optional_gap requires supported applicability, "
                    "no binding conflict, and a directly evidenced unadopted optional branch"
                )
        # This is a policy declaration in the handoff, not proof of Task identity.
        if item.get("review_context") != "fresh_subagent":
            errors.append(f"{label}: review_context must be fresh_subagent")
        errors.extend(_validate_concrete_string_list(
            item, "challenges", label, minimum=2,
        ))
        errors.extend(_validate_concrete_string_list(
            item, "checks_performed", label, minimum=2,
        ))
        if "remaining_risks" not in item:
            errors.append(f"{label}: missing remaining_risks")
        elif not isinstance(item.get("remaining_risks"), list):
            errors.append(f"{label}: remaining_risks must be an array")
        probe_review = item.get("dynamic_probe_review")
        if not isinstance(probe_review, dict):
            errors.append(f"{label}: dynamic_probe_review must be an object")
        else:
            unexpected_probe_fields = sorted(
                set(probe_review) - DYNAMIC_PROBE_REVIEW_ALLOWED_KEYS
            )
            if unexpected_probe_fields:
                errors.append(
                    f"{label}: dynamic_probe_review has unsupported fields "
                    f"{unexpected_probe_fields}"
                )
            if "probe_id" not in probe_review:
                errors.append(f"{label}: dynamic_probe_review missing probe_id")
            errors.extend(_require(probe_review, (
                "status", "oracle_validity", "environment_validity", "reachability", "effect_on_decision",
            ), f"{label}: dynamic_probe_review"))
            if probe_review.get("status") not in {
                "not_run", "supports_contradiction", "disconfirms_contradiction", "inconclusive",
            }:
                errors.append(f"{label}: invalid dynamic_probe_review.status")
            if probe_review.get("status") != "not_run" and not _present(probe_review.get("probe_id")):
                errors.append(f"{label}: executed dynamic probe review needs probe_id")
            if probe_review.get("status") == "not_run" and _present(probe_review.get("probe_id")):
                errors.append(f"{label}: not_run dynamic probe review must not reference probe_id")
        return errors

    if artifact_type == "probe":
        return validate_probe_contract(item, label)
    return [f"{label}: unknown artifact type {artifact_type!r}"]


def _read_values(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        values, errors = ac.load_jsonl(path)
        if errors:
            raise ValueError("; ".join(errors))
        return values
    value = ac.load_json(path)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise ValueError("handoff must be one JSON object, an object array, or JSONL objects")


def _template_errors(
    item: dict[str, Any], template: dict[str, Any] | None, label: str,
) -> list[str]:
    """Protect the design/task-owned portion of a finding handoff."""
    if template is None:
        return []
    errors: list[str] = []
    for field in FINDING_TEMPLATE_FIELDS:
        if item.get(field) != template.get(field):
            errors.append(f"{label}: {field} does not match the pristine finding template")
    return errors


def _template_root_for_handoff(path: Path) -> Path | None:
    # A candidate may use its own directory below handoffs/investigators so a
    # failed peer never has to share the same merge input.  Walk only the
    # resolved ancestor chain and keep the canonical state-owned template root.
    for parent in path.resolve().parents:
        if parent.name == "investigators" and parent.parent.name == "handoffs":
            return parent.parent.parent / "handoff-templates" / "investigators"
    return None


def _load_template(
    template_root: Path | None, item: dict[str, Any], *, required: bool,
) -> dict[str, Any] | None:
    if template_root is None:
        return None
    task_id = str(item.get("task_id") or "")
    path = template_root / f"{task_id}.json"
    if not task_id or not path.is_file():
        if required:
            raise ValueError(f"finding {item.get('finding_id') or '?'} lacks pristine template {path}")
        return None
    value = ac.load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"pristine template is not an object: {path}")
    state_root = template_root.parent.parent
    tasks, task_errors = ac.load_jsonl(state_root / "investigation_tasks.jsonl")
    claims, claim_errors = ac.load_jsonl(state_root / "design_claims.jsonl")
    if task_errors or claim_errors:
        raise ValueError("cannot reconstruct pristine template from invalid task/claim ledgers")
    task_matches = [task for task in tasks if task.get("task_id") == task_id]
    if len(task_matches) != 1:
        raise ValueError(f"pristine template task must occur exactly once: {task_id}")
    claim_id = str(task_matches[0].get("claim_id") or "")
    claim_matches = [claim for claim in claims if claim.get("claim_id") == claim_id]
    if len(claim_matches) != 1:
        raise ValueError(f"pristine template claim must occur exactly once: {claim_id}")
    reconstructed = ht.finding_template(task_matches[0], claim_matches[0])
    if value != reconstructed:
        raise ValueError(f"pristine template differs from current task/claim contract: {path}")
    return value


def _handoff_identifiers(input_dir: Path, key: str) -> set[str]:
    identifiers: set[str] = set()
    for path in sorted(
        path for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    ):
        for item in _read_values(path):
            identifier = str(item.get(key) or "")
            if identifier:
                identifiers.add(identifier)
    return identifiers


def _load_index(path: Path, key: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    values, errors = ac.load_jsonl(path)
    indexed: dict[str, dict[str, Any]] = {}
    for line_number, value in enumerate(values, start=1):
        identifier = str(value.get(key) or "")
        if not identifier:
            errors.append(f"{path.name}:{line_number}: missing {key}")
        elif identifier in indexed:
            errors.append(f"{path.name}:{line_number}: duplicate {key} {identifier}")
        else:
            indexed[identifier] = value
    return indexed, errors


def expected_critic_input_digests(
    item: dict[str, Any], state_root: Path, label: str,
) -> tuple[dict[str, str], list[str]]:
    """Derive the exact evidence snapshot a critic is allowed to judge."""
    findings, finding_errors = _load_index(
        state_root / "investigation_findings.jsonl", "finding_id",
    )
    claims, claim_errors = _load_index(
        state_root / "design_claims.jsonl", "claim_id",
    )
    errors = [
        *(f"{label}: finding ledger is invalid: {error}" for error in finding_errors),
        *(f"{label}: design claim ledger is invalid: {error}" for error in claim_errors),
    ]
    finding_id = str(item.get("finding_id") or "")
    claim_id = str(item.get("claim_id") or "")
    finding = findings.get(finding_id)
    claim = claims.get(claim_id)
    if finding is None:
        errors.append(f"{label}: unknown finding_id {finding_id!r}")
    if claim is None:
        errors.append(f"{label}: unknown claim_id {claim_id!r}")

    probe_review = item.get("dynamic_probe_review")
    probe_id = str(probe_review.get("probe_id") or "") if isinstance(
        probe_review, dict
    ) else ""
    probe: dict[str, Any] | None = None
    if probe_id:
        probes, probe_errors = _load_index(
            state_root / "dynamic_probes.jsonl", "probe_id",
        )
        errors.extend(
            f"{label}: dynamic probe ledger is invalid: {error}"
            for error in probe_errors
        )
        probe = probes.get(probe_id)
        if probe is None:
            errors.append(f"{label}: unknown probe_id {probe_id!r}")

    return {
        "claim_sha256": canonical_digest(claim) if claim is not None else "",
        "finding_sha256": canonical_digest(finding) if finding is not None else "",
        "probe_sha256": canonical_digest(probe) if probe is not None else "",
    }, errors


def materialize_critic_bindings(
    item: dict[str, Any], state_root: Path, label: str,
) -> dict[str, Any]:
    """Add tool-owned critic bindings without synthesizing semantic content."""
    expected, errors = expected_critic_input_digests(item, state_root, label)
    supplied = item.get("input_digests")
    if supplied is not None and supplied != expected:
        errors.append(f"{label}: supplied input_digests do not match current evidence")
    supplied_version = item.get("evidence_critic_prompt_version")
    if supplied_version is not None and supplied_version != EVIDENCE_CRITIC_PROMPT_VERSION:
        errors.append(
            f"{label}: supplied evidence_critic_prompt_version is stale or unsupported"
        )
    if errors:
        identifier = str(item.get("finding_id") or item.get("review_id") or "?")
        raise HandoffValidationError({identifier: errors})
    return {
        **item,
        "input_digests": expected,
        "evidence_critic_prompt_version": EVIDENCE_CRITIC_PROMPT_VERSION,
    }


def validate_critic_bindings(
    item: dict[str, Any], state_root: Path, label: str,
) -> list[str]:
    """Reject a retained critic after any judged evidence snapshot changes."""
    expected, errors = expected_critic_input_digests(item, state_root, label)
    if item.get("input_digests") != expected:
        errors.append(f"{label}: input_digests do not match current claim/finding/probe evidence")
    if item.get("evidence_critic_prompt_version") != EVIDENCE_CRITIC_PROMPT_VERSION:
        errors.append(
            f"{label}: evidence_critic_prompt_version does not match current critic contract"
        )
    return errors


def _critic_history_key(item: dict[str, Any]) -> str:
    return canonical_digest({
        "finding_id": item.get("finding_id"),
        "input_digests": item.get("input_digests"),
        "evidence_critic_prompt_version": item.get("evidence_critic_prompt_version"),
    })


def validate_critic_review_history(
    state_root: Path, critiques: dict[str, dict[str, Any]],
) -> list[str]:
    path = state_root / "critic_review_history.jsonl"
    if not path.is_file() or path.is_symlink():
        return ["critic_review_history.jsonl is missing or not a regular file"]
    values, parse_errors = ac.load_jsonl(path)
    errors = [f"critic review history is invalid: {error}" for error in parse_errors]
    by_key: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(values, start=1):
        label = f"critic_review_history.jsonl:{index}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        key = str(entry.get("review_key") or "")
        critic_sha256 = str(entry.get("critic_sha256") or "")
        if not key or len(key) != 64:
            errors.append(f"{label}: review_key must be a SHA-256 digest")
        if not critic_sha256 or len(critic_sha256) != 64:
            errors.append(f"{label}: critic_sha256 must be a SHA-256 digest")
        if key in by_key:
            errors.append(f"{label}: duplicate evidence review_key {key}")
        elif key:
            by_key[key] = entry
    for finding_id, critique in critiques.items():
        key = _critic_history_key(critique)
        entry = by_key.get(key)
        if entry is None:
            errors.append(f"critic {finding_id}: current evidence review is absent from history")
        elif entry.get("critic_sha256") != canonical_digest(critique):
            errors.append(f"critic {finding_id}: history critic digest does not match current review")
    return errors


def _risk_plan_validation_errors(
    item: dict[str, Any], state_root: Path, label: str,
) -> list[str]:
    """Call the shared risk-plan validator without coupling non-risk imports to it."""
    import risk_sweep_plan_validator as validator

    try:
        errors = validator.validate_observation_against_plan(item, state_root, label)
    except (KeyError, TypeError) as exc:
        return [f"{label}: risk plan context requires schema-valid values: {exc}"]
    if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
        raise ValueError("risk sweep plan validator returned a non-string error list")
    return errors


def _expected_risk_sweep_ids(state_root: Path) -> set[str]:
    """Return the sweep membership declared by the immutable risk sweep plan."""
    import risk_sweep_plan_validator as validator

    sweep_ids = validator.expected_sweep_ids(state_root)
    if not isinstance(sweep_ids, set) or not all(
        isinstance(sweep_id, str) and sweep_id.strip() for sweep_id in sweep_ids
    ):
        raise ValueError("risk sweep plan validator returned invalid sweep IDs")
    return sweep_ids


def _string_entries(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        entry for entry in value
        if isinstance(entry, str) and entry
    }


def _context_errors(
    item: dict[str, Any], artifact_type: str, state_root: Path, label: str,
) -> list[str]:
    """Validate typed references against current session artifacts."""
    if artifact_type not in {"task", "risk", "critic", "probe"}:
        return []
    if artifact_type == "probe":
        findings, finding_errors = _load_index(
            state_root / "investigation_findings.jsonl", "finding_id",
        )
        claims, claim_errors = _load_index(
            state_root / "design_claims.jsonl", "claim_id",
        )
        errors: list[str] = []
        if finding_errors:
            errors.append(
                f"{label}: finding ledger is invalid: {'; '.join(finding_errors)}"
            )
        if claim_errors:
            errors.append(
                f"{label}: design claim ledger is invalid: {'; '.join(claim_errors)}"
            )
        finding_id = str(item.get("finding_id") or "")
        claim_id = str(item.get("claim_id") or "")
        finding = findings.get(finding_id)
        claim = claims.get(claim_id)
        if finding is None:
            errors.append(f"{label}: unknown finding_id {finding_id!r}")
        elif str(finding.get("claim_id") or "") != claim_id:
            errors.append(f"{label}: finding/probe claim mismatch for {finding_id}")
        elif finding.get("session_id") != item.get("session_id"):
            errors.append(f"{label}: finding belongs to a different session")
        if claim is None:
            errors.append(f"{label}: unknown claim_id {claim_id!r}")
        elif claim.get("session_id") != item.get("session_id"):
            errors.append(f"{label}: claim belongs to a different session")
        else:
            oracle = item.get("oracle") if isinstance(item.get("oracle"), dict) else {}
            source_ref = claim.get("source_ref") if isinstance(
                claim.get("source_ref"), dict
            ) else {}
            claim_oracle = claim.get("probe_oracle") if isinstance(
                claim.get("probe_oracle"), dict
            ) else {}
            if oracle.get("claim_id") != claim_id:
                errors.append(f"{label}: oracle.claim_id does not match the probe claim")
            if oracle.get("claim_sha256") != canonical_digest(claim):
                errors.append(f"{label}: oracle.claim_sha256 does not match the current claim")
            if oracle.get("source_sha256") != source_ref.get("source_sha256"):
                errors.append(f"{label}: oracle.source_sha256 does not match the claim source")
            for field in ("preconditions", "stimulus", "expected_observation"):
                if oracle.get(field) != claim_oracle.get(field):
                    errors.append(
                        f"{label}: oracle.{field} does not match the design claim"
                    )
        errors.extend(validate_probe_workspace(item, state_root, label))
        return errors
    if artifact_type == "critic":
        findings, finding_errors = _load_index(
            state_root / "investigation_findings.jsonl", "finding_id",
        )
        claims, claim_errors = _load_index(
            state_root / "design_claims.jsonl", "claim_id",
        )
        errors: list[str] = []
        if finding_errors:
            errors.append(
                f"{label}: finding ledger is invalid: {'; '.join(finding_errors)}"
            )
        if claim_errors:
            errors.append(
                f"{label}: design claim ledger is invalid: {'; '.join(claim_errors)}"
            )

        finding_id = str(item.get("finding_id") or "")
        claim_id = str(item.get("claim_id") or "")
        finding = findings.get(finding_id)
        claim = claims.get(claim_id)
        if finding is None:
            errors.append(f"{label}: unknown finding_id {finding_id!r}")
        else:
            if str(finding.get("claim_id") or "") != claim_id:
                errors.append(f"{label}: finding/critic claim mismatch for {finding_id}")
            if finding.get("session_id") != item.get("session_id"):
                errors.append(f"{label}: finding belongs to a different session")
            finding_assessment = finding.get("assessment")
            critic_decision = item.get("decision")
            if (
                critic_decision in {"confirm_contradiction", "confirm_optional_gap"}
                and finding_assessment != "contradiction_supported"
            ):
                errors.append(
                    f"{label}: a confirmed inconsistency requires a "
                    "contradiction_supported finding"
                )
            if (
                finding_assessment == "design_satisfied"
                and critic_decision != "reject_issue"
            ):
                errors.append(
                    f"{label}: design_satisfied finding requires reject_issue; "
                    "new contradictory evidence must first revise the finding"
                )
        if claim is None:
            errors.append(f"{label}: unknown claim_id {claim_id!r}")
        elif claim.get("session_id") != item.get("session_id"):
            errors.append(f"{label}: claim belongs to a different session")
        else:
            normative = item.get("normative_assessment")
            if isinstance(normative, dict):
                strength = claim.get("normative_strength")
                if normative.get("claim_strength") != strength:
                    errors.append(
                        f"{label}: normative_assessment.claim_strength does not "
                        "match the current design claim"
                    )
                expected_statuses = {
                    "mandatory": {"binding_required"},
                    "recommended": {"binding_recommended"},
                    "declared_capability": {"declared_capability"},
                    "optional": {"optional_adopted", "optional_not_adopted"},
                    "informational": {"informational"},
                }.get(str(strength), set())
                if (
                    expected_statuses
                    and normative.get("obligation_status") not in expected_statuses
                ):
                    errors.append(
                        f"{label}: normative_assessment.obligation_status is "
                        f"incompatible with claim strength {strength!r}"
                    )

        probe_review = item.get("dynamic_probe_review")
        probe_id = str(probe_review.get("probe_id") or "") if isinstance(
            probe_review, dict
        ) else ""
        if probe_id:
            probes, probe_errors = _load_index(
                state_root / "dynamic_probes.jsonl", "probe_id",
            )
            if probe_errors:
                errors.append(
                    f"{label}: dynamic probe ledger is invalid: {'; '.join(probe_errors)}"
                )
            probe = probes.get(probe_id)
            if probe is None:
                errors.append(f"{label}: unknown probe_id {probe_id!r}")
            else:
                if str(probe.get("finding_id") or "") != finding_id:
                    errors.append(f"{label}: probe/finding mismatch for {probe_id}")
                if str(probe.get("claim_id") or "") != claim_id:
                    errors.append(f"{label}: probe/claim mismatch for {probe_id}")
                if probe.get("session_id") != item.get("session_id"):
                    errors.append(f"{label}: probe belongs to a different session")
        errors.extend(validate_critic_bindings(item, state_root, label))
        return errors

    contract_path = state_root / "agent_loop_contract.json"
    architecture_path = state_root / "architecture_map.json"
    if not contract_path.is_file() or not architecture_path.is_file():
        return _risk_plan_validation_errors(item, state_root, label) if (
            artifact_type == "risk"
        ) else []
    contract = ac.load_json(contract_path)
    architecture = ac.load_json(architecture_path)
    if not contract or not architecture:
        return _risk_plan_validation_errors(item, state_root, label) if (
            artifact_type == "risk"
        ) else []
    lenses = set(contract.get("coverage_contract", {}).get("portfolio_lenses", []))
    modes = set(contract.get("coverage_contract", {}).get("exploration_modes", []))
    boundaries = {
        str(value.get("boundary_id"))
        for value in architecture.get("integration_boundaries", [])
        if isinstance(value, dict) and value.get("boundary_id")
    }
    planes = {
        str(value.get("plane_id"))
        for value in architecture.get("implementation_planes", [])
        if isinstance(value, dict) and value.get("plane_id")
    }
    parallel_paths = {
        str(value.get("path_id"))
        for value in architecture.get("parallel_behavior_paths", [])
        if isinstance(value, dict) and value.get("path_id")
    }
    errors: list[str] = []
    if artifact_type == "risk":
        unknown_lenses = _string_entries(item.get("review_lenses")) - lenses
        unknown_boundaries = _string_entries(item.get("architecture_boundaries")) - boundaries
        unknown_planes = _string_entries(item.get("implementation_planes")) - planes
    else:
        unknown_lenses = set(item.get("review_lenses", [])) - lenses
        unknown_boundaries = set(item.get("architecture_boundaries", [])) - boundaries
        unknown_planes = set(item.get("implementation_planes", [])) - planes
    if unknown_lenses:
        errors.append(f"{label}: unknown review lenses {sorted(unknown_lenses)}")
    if unknown_boundaries:
        errors.append(f"{label}: unknown architecture boundaries {sorted(unknown_boundaries)}")
    if unknown_planes:
        errors.append(f"{label}: unknown implementation planes {sorted(unknown_planes)}")

    if artifact_type == "risk":
        unknown_paths = _string_entries(item.get("parallel_path_ids")) - parallel_paths
        if unknown_paths:
            errors.append(f"{label}: unknown parallel_path_ids {sorted(unknown_paths)}")
        errors.extend(_risk_plan_validation_errors(item, state_root, label))
        return errors

    claims, claim_errors = _load_index(state_root / "design_claims.jsonl", "claim_id")
    risks, risk_errors = _load_index(state_root / "risk_observations.jsonl", "observation_id")
    if claim_errors:
        errors.append(f"{label}: design claim ledger is invalid: {'; '.join(claim_errors)}")
    if risk_errors:
        errors.append(f"{label}: risk observation ledger is invalid: {'; '.join(risk_errors)}")
    claim_id = str(item.get("claim_id") or "")
    if claim_id not in claims:
        errors.append(f"{label}: unknown claim_id {claim_id!r}")
    else:
        claim = claims[claim_id]
        obligation = claim.get("obligation")
        expected_obligation_digest = canonical_digest({
            "claim_id": claim_id,
            "obligation": obligation,
        }) if isinstance(obligation, str) and obligation.strip() else ""
        if not expected_obligation_digest:
            errors.append(f"{label}: linked claim lacks one non-empty obligation")
        elif item.get("obligation_sha256") != expected_obligation_digest:
            errors.append(
                f"{label}: obligation_sha256 does not match the linked claim obligation"
            )
        if artifact_type in {"task", "finding"}:
            if item.get("claim_branch") != ac.canonical_claim_branch(claim):
                errors.append(
                    f"{label}: claim_branch does not match the linked claim subject/trigger"
                )
            if item.get("hypothesis") != ac.canonical_claim_hypothesis(claim):
                errors.append(
                    f"{label}: hypothesis does not match the linked claim observable result"
                )
    mode = str(item.get("exploration_mode") or "")
    if mode not in modes:
        errors.append(f"{label}: unknown exploration_mode {mode!r}")
    path_ids = item.get("parallel_path_ids", [])
    if isinstance(path_ids, list):
        unknown_paths = set(path_ids) - parallel_paths
        if unknown_paths:
            errors.append(f"{label}: unknown parallel_path_ids {sorted(unknown_paths)}")
    risk_ids = item.get("risk_observation_ids", [])
    if isinstance(risk_ids, list):
        unknown_risks = set(risk_ids) - set(risks)
        if unknown_risks:
            errors.append(f"{label}: unknown risk_observation_ids {sorted(unknown_risks)}")
        if mode == "code-to-design risk backtracking" and not risk_ids:
            errors.append(f"{label}: code-to-design task requires risk_observation_ids")
        for risk_id in set(risk_ids).intersection(risks):
            risk = risks[risk_id]
            if not set(item.get("architecture_boundaries", [])).intersection(
                risk.get("architecture_boundaries", [])
            ):
                errors.append(f"{label}: risk observation {risk_id} shares no architecture boundary")
            if not set(item.get("implementation_planes", [])).intersection(
                risk.get("implementation_planes", [])
            ):
                errors.append(f"{label}: risk observation {risk_id} shares no implementation plane")
    errors.extend(validate_task_coverage_binding(item, state_root, label))
    return errors


def _finding_batch_expectation(output: Path, input_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """Describe only the candidate handoffs submitted by this merge call.

    Pristine templates remain the immutable per-candidate contract, but sibling
    templates are deliberately not merge prerequisites.  The caller can point
    ``input_dir`` at one candidate-owned directory (the normal path) or at an
    explicitly selected subset of at most two candidates.
    """
    state_root = output.resolve().parent
    template_root = state_root / "handoff-templates" / "investigators"
    if not template_root.is_dir():
        return [], [], []
    tasks, task_errors = ac.load_jsonl(state_root / "investigation_tasks.jsonl")
    if task_errors:
        raise ValueError(f"investigation task ledger is invalid: {'; '.join(task_errors)}")
    deferred_task_ids = {
        str(item.get("task_id")) for item in tasks
        if item.get("task_id") and item.get("status") == "deferred"
        and not validate_task_defer_evidence(item, f"task ({item.get('task_id')})")
    }
    deferred_ids = sorted(
        f"FINDING-{task_id}" for task_id in deferred_task_ids
    )
    submitted = sorted(_handoff_identifiers(input_dir, "finding_id"))
    if len(submitted) > 2:
        raise ValueError(
            f"finding merge submits more than two candidate IDs: {submitted}"
        )
    # Unknown IDs and stale template contents are rejected later by
    # _load_template/_template_errors with candidate-local diagnostics.
    return submitted, [], deferred_ids


def _deferred_finding_input_errors(
    state_root: Path, input_dir: Path,
) -> dict[str, list[str]]:
    """Reject late handoffs for tasks already retired by the recovery contract."""
    task_path = state_root / "investigation_tasks.jsonl"
    if not task_path.is_file():
        return {}
    tasks, task_errors = _load_index(task_path, "task_id")
    if task_errors:
        raise ValueError(f"investigation task ledger is invalid: {'; '.join(task_errors)}")
    errors: dict[str, list[str]] = {}
    for path in sorted(
        path for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    ):
        for item in _read_values(path):
            finding_id = str(item.get("finding_id") or "?")
            task_id = str(item.get("task_id") or "")
            task = tasks.get(task_id)
            if task is not None and task.get("status") == "deferred":
                errors.setdefault(finding_id, []).append(
                    f"finding handoff targets deferred task {task_id}; retire or reopen the task first"
                )
    return errors


def _snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.is_file() else None for path in paths}


def _restore_files(snapshot: dict[Path, bytes | None]) -> None:
    """Best-effort atomic per-file rollback for a failed multi-ledger transition."""
    for path, content in snapshot.items():
        if content is None:
            if path.exists():
                path.unlink()
            continue
        temporary = path.with_suffix(path.suffix + ".rollback")
        temporary.write_bytes(content)
        temporary.replace(path)


def validate_item(
    item: dict[str, Any],
    *,
    artifact_type: str,
    identifier: str,
    session_id: str | None = None,
    code_root: Path | None = None,
    design_root: Path | None = None,
    template: dict[str, Any] | None = None,
) -> list[str]:
    label = f"{artifact_type} ({identifier})"
    errors = validate_artifact(item, artifact_type, label)
    if session_id and item.get("session_id") != session_id:
        errors.append(f"{label}: session_id does not match current session")
    if artifact_type == "finding":
        errors.extend(_template_errors(item, template, label))
    if artifact_type == "finding" and code_root and design_root:
        for index, evidence in enumerate(item.get("design_evidence", []), start=1):
            errors.extend(ac.validate_source_evidence(
                evidence, design_root, f"{label}: design_evidence[{index}]", "quote"
            ))
        for index, evidence in enumerate(item.get("code_evidence", []), start=1):
            errors.extend(ac.validate_source_evidence(
                evidence, code_root, f"{label}: code_evidence[{index}]", "snippet"
            ))
    if artifact_type == "risk" and code_root:
        for index, evidence in enumerate(item.get("code_evidence", []), start=1):
            errors.extend(ac.validate_source_evidence(
                evidence, code_root, f"{label}: code_evidence[{index}]", "snippet"
            ))
    return errors


def _typed_state_root_for_handoff(path: Path, directory: str, label: str) -> Path:
    """Locate state root for direct or candidate-owned typed handoff paths."""
    for parent in path.resolve().parents:
        if parent.name == directory and parent.parent.name == "handoffs":
            return parent.parent.parent
    raise ValueError(
        f"{label} --check-file must be located under "
        f"<state-root>/handoffs/{directory}"
    )


def _risk_state_root_for_handoff(path: Path) -> Path:
    return _typed_state_root_for_handoff(path, "risks", "risk")


def _critic_state_root_for_handoff(path: Path) -> Path:
    return _typed_state_root_for_handoff(path, "critics", "critic")


def _probe_state_root_for_handoff(path: Path) -> Path:
    return _typed_state_root_for_handoff(path, "probes", "probe")


def _validate_risk_handoff_file(
    path: Path,
    values: list[dict[str, Any]],
    *,
    state_root: Path,
    session_id: str | None,
    code_root: Path | None,
    validate_items: bool,
) -> tuple[str, list[str]]:
    """Validate one sweep-owned risk slice, optionally including every item contract."""
    if not values:
        raise ValueError(f"risk sweep handoff must contain at least one object: {path}")

    errors_by_id: dict[str, list[str]] = {}
    labels: list[str] = []
    identifiers: list[str] = []
    seen_ids: set[str] = set()
    sweep_ids: set[str] = set()
    missing_sweep = False

    for index, item in enumerate(values, start=1):
        identifier = str(item.get("observation_id") or "")
        error_id = identifier or f"{path.name}#{index}"
        labels.append(error_id)
        if identifier:
            identifiers.append(identifier)
            if identifier in seen_ids:
                errors_by_id.setdefault(error_id, []).append(
                    f"risk ({identifier}): duplicate observation_id in {path.name}"
                )
            seen_ids.add(identifier)

        sweep_id = item.get("sweep_id")
        if isinstance(sweep_id, str) and sweep_id:
            sweep_ids.add(sweep_id)
        else:
            missing_sweep = True
            errors_by_id.setdefault(error_id, []).append(
                f"risk ({error_id}): risk slice item requires a non-empty sweep_id"
            )

        if validate_items:
            item_errors = validate_item(
                item,
                artifact_type="risk",
                identifier=error_id,
                session_id=session_id,
                code_root=code_root,
            )
            item_errors.extend(_context_errors(
                item, "risk", state_root, f"risk ({error_id})",
            ))
            if item_errors:
                errors_by_id.setdefault(error_id, []).extend(item_errors)

    slice_error = ""
    if missing_sweep or len(sweep_ids) != 1:
        slice_error = (
            f"risk slice {path.name} must contain exactly one shared sweep_id; "
            f"found {sorted(sweep_ids)}"
        )
    else:
        sweep_id = next(iter(sweep_ids))
        expected_name = f"{sweep_id}.json"
        if path.name != expected_name:
            slice_error = (
                f"risk slice filename must be {expected_name}; got {path.name}"
            )
    if slice_error:
        for error_id in dict.fromkeys(labels):
            errors_by_id.setdefault(error_id, []).append(slice_error)

    if validate_items and not slice_error and len(sweep_ids) == 1:
        import risk_sweep_plan_validator as validator

        sweep_id = next(iter(sweep_ids))
        coverage_errors = validator.validate_sweep_coverage(
            values, state_root, sweep_id,
        )
        if coverage_errors:
            errors_by_id.setdefault(labels[0], []).extend(coverage_errors)

    if errors_by_id:
        raise HandoffValidationError(errors_by_id)
    return next(iter(sweep_ids)), identifiers


def _validate_risk_batch(
    input_dir: Path,
    state_root: Path,
    expected_sweep_ids: list[str] | None = None,
) -> list[str]:
    """Validate one or more plan-owned sweep files before mutating the ledger."""
    if not input_dir.is_dir():
        raise ValueError(f"handoff directory is missing: {input_dir}")
    if expected_sweep_ids is None:
        expected_sweep_ids = sorted(_expected_risk_sweep_ids(state_root))
    if not expected_sweep_ids:
        raise ValueError(
            "risk sweep plan must declare at least one sweep ID; "
            f"found {expected_sweep_ids}"
        )

    expected_paths = {
        input_dir / f"{sweep_id}.json": sweep_id for sweep_id in expected_sweep_ids
    }
    actual_paths = {
        path for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    }
    unexpected = sorted(
        str(path.relative_to(input_dir)) for path in actual_paths - set(expected_paths)
    )
    if not actual_paths:
        raise ValueError("risk merge requires at least one planned sweep JSON file")
    if unexpected:
        raise ValueError(
            "risk merge contains unplanned sweep files: " + str(unexpected)
        )

    submitted_sweep_ids: list[str] = []
    for path in sorted(actual_paths):
        sweep_id = expected_paths[path]
        file_sweep_id, _ = _validate_risk_handoff_file(
            path,
            _read_values(path),
            state_root=state_root,
            session_id=None,
            code_root=None,
            validate_items=False,
        )
        if file_sweep_id != sweep_id:
            raise ValueError(
                f"risk sweep file {path.name} contains sweep {file_sweep_id!r}"
            )
        submitted_sweep_ids.append(sweep_id)
    return submitted_sweep_ids


def _risk_ledger_sweep_ids(output: Path, state_root: Path) -> list[str]:
    """Read cumulative sweep completion from the current-plan risk ledger."""
    if not output.is_file():
        return []
    values, errors = ac.load_jsonl(output)
    if errors:
        raise ValueError(f"existing ledger is invalid: {'; '.join(errors)}")
    plan_path = state_root / "risk_sweep_plan.json"
    plan_digest = ac.sha256_file(plan_path) if plan_path.is_file() else ""
    return sorted({
        str(item.get("sweep_id") or "") for item in values
        if isinstance(item.get("sweep_id"), str)
        and item.get("sweep_id")
        and (
            not plan_digest
            or item.get("risk_sweep_plan_sha256") == plan_digest
        )
    })


def merge(
    input_dir: Path,
    output: Path,
    key: str,
    artifact_type: str = "generic",
    session_id: str | None = None,
    code_root: Path | None = None,
    design_root: Path | None = None,
    template_root: Path | None = None,
    context_root: Path | None = None,
) -> dict[str, Any]:
    if not input_dir.is_dir():
        raise ValueError(f"handoff directory is missing: {input_dir}")
    existing: list[dict[str, Any]] = []
    if output.exists():
        existing, errors = ac.load_jsonl(output)
        if errors:
            raise ValueError(f"existing ledger is invalid: {'; '.join(errors)}")
    if artifact_type == "risk" and context_root is not None:
        plan_path = context_root / "risk_sweep_plan.json"
        if plan_path.is_file():
            current_plan_digest = ac.sha256_file(plan_path)
            # A changed plan invalidates all previous sweep ownership.  Current-plan
            # observations remain independently reusable and are updated per sweep.
            existing = [
                item for item in existing
                if item.get("risk_sweep_plan_sha256") == current_plan_digest
            ]
    ordered: list[str] = []
    values: dict[str, dict[str, Any]] = {}
    for item in existing:
        identifier = str(item.get(key) or "")
        if not identifier:
            raise ValueError(f"existing ledger entry lacks {key}")
        if identifier not in values:
            ordered.append(identifier)
        values[identifier] = item

    invalidated_ids: list[str] = []
    if artifact_type == "critic" and context_root is not None:
        # Upstream evidence can change independently for one candidate.  Remove
        # only critics whose previously reviewed evidence snapshot is now stale;
        # otherwise that stale peer would prevent an unrelated fresh critic from
        # merging.  Broken ledgers/references are not silently discarded: they
        # remain below and fail ordinary context validation.
        for identifier in list(ordered):
            retained = values[identifier]
            expected, binding_errors = expected_critic_input_digests(
                retained, context_root, f"critic ({identifier})",
            )
            stale = (
                not binding_errors
                and (
                    retained.get("input_digests") != expected
                    or retained.get("evidence_critic_prompt_version")
                    != EVIDENCE_CRITIC_PROMPT_VERSION
                )
            )
            if stale:
                invalidated_ids.append(identifier)
                ordered.remove(identifier)
                values.pop(identifier, None)

    imported = 0
    critic_history_path: Path | None = None
    critic_history_values: list[dict[str, Any]] = []
    critic_history_by_key: dict[str, dict[str, Any]] = {}
    pending_critic_history: list[dict[str, Any]] = []
    if artifact_type == "critic" and context_root is not None:
        critic_history_path = context_root / "critic_review_history.jsonl"
        if not critic_history_path.is_file() or critic_history_path.is_symlink():
            raise ValueError("critic merge requires tool-owned critic_review_history.jsonl")
        critic_history_values, history_errors = ac.load_jsonl(critic_history_path)
        if history_errors:
            raise ValueError("critic review history is invalid: " + "; ".join(history_errors))
        for entry in critic_history_values:
            key_value = str(entry.get("review_key") or "")
            if not key_value or key_value in critic_history_by_key:
                raise ValueError("critic review history contains a missing or duplicate review_key")
            critic_history_by_key[key_value] = entry
    files = sorted(
        path for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    )
    imported_items: list[tuple[Path, dict[str, Any]]] = []
    imported_sources: dict[str, Path] = {}
    for path in files:
        for item_index, item in enumerate(_read_values(path), start=1):
            identifier = str(item.get(key) or "")
            if not identifier:
                raise ValueError(f"{path}: handoff entry lacks {key}")
            if artifact_type != "generic" and identifier in imported_sources:
                raise ValueError(
                    f"duplicate {key} {identifier!r} across typed handoffs "
                    f"{imported_sources[identifier]} and {path}"
                )
            imported_sources[identifier] = path
            imported_items.append((path, item))

    submitted_sweep_ids: set[str] = set()
    if artifact_type == "risk":
        submitted_sweep_ids = {
            str(item.get("sweep_id") or "") for _path, item in imported_items
            if isinstance(item.get("sweep_id"), str) and item.get("sweep_id")
        }
        # One submitted sweep file is authoritative only for that sweep.  Remove
        # its previous observations, preserving every other completed sweep.
        replaced_ids = {
            identifier for identifier in ordered
            if values[identifier].get("sweep_id") in submitted_sweep_ids
        }
        if replaced_ids:
            ordered = [identifier for identifier in ordered if identifier not in replaced_ids]
            for identifier in replaced_ids:
                values.pop(identifier, None)

    for _path, item in imported_items:
        if artifact_type == "critic" and context_root is not None:
            item = materialize_critic_bindings(
                item,
                context_root,
                f"critic ({item.get('finding_id') or item.get('review_id') or '?'})",
            )
            history_key = _critic_history_key(item)
            historical = critic_history_by_key.get(history_key)
            item_sha256 = canonical_digest(item)
            if historical is not None and historical.get("critic_sha256") != item_sha256:
                raise HandoffValidationError({str(item.get("finding_id") or "?"): [
                    f"critic ({item.get('finding_id') or '?'}): current evidence snapshot "
                    "was already reviewed in critic history; new claim/finding/probe "
                    "evidence is required before revision"
                ]})
            if historical is None:
                history_entry = {
                    "recorded_at": ac.now_iso(),
                    "session_id": item.get("session_id"),
                    "finding_id": item.get("finding_id"),
                    "review_key": history_key,
                    "input_digests": item.get("input_digests"),
                    "evidence_critic_prompt_version": item.get(
                        "evidence_critic_prompt_version"
                    ),
                    "critic_sha256": item_sha256,
                }
                pending_critic_history.append(history_entry)
                critic_history_by_key[history_key] = history_entry
        identifier = str(item.get(key) or "")
        prior = values.get(identifier)
        if artifact_type == "critic" and prior is not None:
            same_snapshot = (
                prior.get("input_digests") == item.get("input_digests")
                and prior.get("evidence_critic_prompt_version")
                == item.get("evidence_critic_prompt_version")
            )
            if prior == item:
                # Exact retries are idempotent; they do not create a second review.
                continue
            if same_snapshot:
                raise HandoffValidationError({identifier: [
                    f"critic ({identifier}): current evidence snapshot was already "
                    "reviewed; new claim/finding/probe evidence is required before revision"
                ]})
        if artifact_type == "risk" and prior is not None:
            raise ValueError(
                f"risk observation_id {identifier!r} conflicts with preserved sweep "
                f"{prior.get('sweep_id')!r}"
            )
        if (
            artifact_type == "task" and prior
            and prior.get("status") == "complete" and item.get("status") == "pending"
        ):
            item = {**item, "status": "complete"}
        if identifier not in values:
            ordered.append(identifier)
        values[identifier] = item
        imported += 1

    validation_errors: dict[str, list[str]] = {}
    for identifier in ordered:
        item = values[identifier]
        template_errors: list[str] = []
        template = None
        if artifact_type == "finding":
            try:
                template = _load_template(
                    template_root, item, required=template_root is not None,
                )
            except (OSError, ValueError) as exc:
                template_errors.append(f"finding ({identifier}): {exc}")
        item_errors = [
            *template_errors,
            *validate_item(
                item, artifact_type=artifact_type, identifier=identifier,
                session_id=session_id, code_root=code_root, design_root=design_root,
                template=template,
            ),
        ]
        if context_root is not None:
            item_errors.extend(_context_errors(
                item, artifact_type, context_root, f"{artifact_type} ({identifier})",
            ))
        if item_errors:
            validation_errors[identifier] = item_errors
    candidate_local_types = {"task", "finding", "probe", "critic"}
    blocking_validation_errors = {
        identifier: item_errors
        for identifier, item_errors in validation_errors.items()
        if artifact_type not in candidate_local_types
        or identifier in imported_sources
    }
    if blocking_validation_errors:
        raise HandoffValidationError(blocking_validation_errors)
    retained_invalid_ids = sorted(
        set(validation_errors) - set(blocking_validation_errors)
    )
    if artifact_type == "risk":
        if context_root is None:
            raise ValueError("risk merge requires a state-root context")
        import risk_sweep_plan_validator as validator

        expected_sweep_ids = _expected_risk_sweep_ids(context_root)
        completed_sweep_ids = {
            str(item.get("sweep_id") or "") for item in values.values()
            if isinstance(item.get("sweep_id"), str) and item.get("sweep_id")
        }
        sweep_errors: list[str] = []
        for sweep_id in sorted(completed_sweep_ids):
            sweep_errors.extend(validator.validate_sweep_coverage(
                [
                    item for item in values.values()
                    if item.get("sweep_id") == sweep_id
                ],
                context_root,
                sweep_id,
            ))
        if sweep_errors:
            raise ValueError("; ".join(sweep_errors))
        closed = completed_sweep_ids == expected_sweep_ids
        if closed:
            coverage_errors, _coverage = validator.validate_risk_coverage(
                values, context_root,
            )
            if coverage_errors:
                raise ValueError("; ".join(coverage_errors))

    ac.ensure_dir(output.parent)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(values[identifier], ensure_ascii=False) + "\n" for identifier in ordered),
        encoding="utf-8",
    )
    temporary.replace(output)
    if critic_history_path is not None and pending_critic_history:
        history_temporary = critic_history_path.with_suffix(".jsonl.tmp")
        history_temporary.write_text(
            "".join(
                json.dumps(entry, ensure_ascii=False) + "\n"
                for entry in [*critic_history_values, *pending_critic_history]
            ),
            encoding="utf-8",
        )
        history_temporary.replace(critic_history_path)
    validated_ids = [
        identifier for identifier in ordered if identifier not in validation_errors
    ]
    result = {
        "files": len(files), "imported": imported, "ledger_entries": len(ordered),
        "validated_ids": validated_ids,
    }
    if artifact_type in candidate_local_types:
        result["retained_invalid_ids"] = retained_invalid_ids
    if artifact_type == "critic":
        result["invalidated_ids"] = sorted(invalidated_ids)
        result["critic_history_entries"] = len(
            critic_history_values
        ) + len(pending_critic_history)
    if artifact_type == "task":
        result["task_plan_ledger_sha256"] = task_plan_ledger_sha256(values)
    if artifact_type == "risk":
        expected = sorted(expected_sweep_ids)
        completed = sorted(completed_sweep_ids)
        result.update({
            "expected_sweep_ids": expected,
            "submitted_sweep_ids": sorted(submitted_sweep_ids),
            # Kept as an explicit compatibility alias: validation in this call
            # applies to submitted sweeps, not cumulative completion.
            "validated_sweep_ids": sorted(submitted_sweep_ids),
            "completed_sweep_ids": completed,
            "missing_sweep_ids": sorted(expected_sweep_ids - completed_sweep_ids),
            "closed": closed,
            "global_coverage_validated": closed,
        })
    return result


def _complete_tasks_for_findings(
    state_root: Path, submitted_finding_ids: set[str],
) -> dict[str, Any]:
    """Apply finding-owned lifecycle updates without revalidating the stable plan."""
    task_path = state_root / "investigation_tasks.jsonl"
    finding_path = state_root / "investigation_findings.jsonl"
    round_path = state_root / "investigation_rounds.jsonl"
    tasks, task_errors = ac.load_jsonl(task_path)
    findings, finding_errors = ac.load_jsonl(finding_path)
    rounds, round_errors = ac.load_jsonl(round_path)
    errors = [*task_errors, *finding_errors, *round_errors]
    task_index = {
        str(item.get("task_id") or ""): item for item in tasks if item.get("task_id")
    }
    findings_by_task: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        findings_by_task.setdefault(str(finding.get("task_id") or ""), []).append(finding)
    round_membership: dict[str, list[dict[str, Any]]] = {}
    for round_item in rounds:
        round_task_ids = round_item.get("task_ids")
        if not isinstance(round_task_ids, list):
            errors.append(
                f"round {round_item.get('round_id') or '?'} task_ids must be an array"
            )
            continue
        for task_id in round_task_ids:
            if isinstance(task_id, str) and task_id:
                round_membership.setdefault(task_id, []).append(round_item)
    transitioned: list[str] = []
    linked_findings: list[str] = []
    submitted_task_ids: list[str] = []
    for task_id, linked in findings_by_task.items():
        submitted = [
            finding for finding in linked
            if str(finding.get("finding_id") or "") in submitted_finding_ids
        ]
        if not submitted:
            # Retained findings were validated above and are deliberately not a
            # prerequisite for this candidate-owned lifecycle transition.  A
            # stale retained peer remains visible as retained_invalid_ids and
            # must be repaired before the final gate, but it cannot roll back an
            # unrelated candidate that passed its own template/context checks.
            continue
        task = task_index.get(task_id)
        if task is None:
            errors.append(f"finding references unknown task {task_id!r}")
            continue
        if len(linked) != 1:
            errors.append(f"task {task_id} has {len(linked)} findings; exactly one is required")
            continue
        finding = linked[0]
        finding_id = str(finding.get("finding_id") or "")
        if finding_id in submitted_finding_ids:
            submitted_task_ids.append(task_id)
        if str(task.get("claim_id") or "") != str(finding.get("claim_id") or ""):
            errors.append(f"task/finding claim mismatch for {task_id}")
            continue
        if task.get("status") == "deferred":
            errors.append(f"deferred task {task_id} cannot have a finding")
            continue
        if task.get("status") != "complete":
            task["status"] = "complete"
            task["defer_reason"] = ""
            task.pop("defer_evidence", None)
            transitioned.append(task_id)
        owning_rounds = round_membership.get(task_id, [])
        if len(owning_rounds) != 1:
            errors.append(
                f"task {task_id} belongs to {len(owning_rounds)} rounds; exactly one is required"
            )
            continue
        round_item = owning_rounds[0]
        round_findings = round_item.get("finding_ids")
        if not isinstance(round_findings, list):
            errors.append(
                f"round {round_item.get('round_id') or '?'} finding_ids must be an array"
            )
            continue
        if round_findings.count(finding_id) > 1:
            errors.append(f"round contains duplicate finding_id {finding_id}")
            continue
        if finding_id not in round_findings:
            round_findings.append(finding_id)
        linked_findings.append(finding_id)
    if errors:
        raise ValueError("; ".join(errors))
    temporary = task_path.with_suffix(task_path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in tasks),
        encoding="utf-8",
    )
    temporary.replace(task_path)
    round_temporary = round_path.with_suffix(round_path.suffix + ".tmp")
    round_temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rounds),
        encoding="utf-8",
    )
    round_temporary.replace(round_path)
    return {
        # Populated from the fresh candidate-aware lifecycle trace by the caller.
        "validated_ids": [],
        "transitioned_ids": transitioned,
        "submitted_task_ids": sorted(set(submitted_task_ids)),
        "linked_finding_ids": sorted(linked_findings),
        "ledger_sha256": ac.sha256_file(task_path),
        "rounds_sha256": ac.sha256_file(round_path),
    }


def _task_lifecycle_trace_path(state_root: Path) -> Path | None:
    manifest_path = state_root / "workspace_manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = ac.load_json(manifest_path)
    log_root = manifest.get("paths", {}).get("log_root") if isinstance(manifest, dict) else None
    if not isinstance(log_root, str) or not log_root:
        return None
    return Path(log_root).resolve() / "trace" / "task_lifecycle_validation.json"


def _refresh_task_lifecycle_trace(
    state_root: Path, *, code_root: Path | None, design_root: Path | None,
    required_task_ids: set[str],
) -> dict[str, Any]:
    manifest = ac.load_json(state_root / "workspace_manifest.json")
    paths = manifest.get("paths", {}) if isinstance(manifest, dict) else {}
    resolved_code = code_root or Path(str(paths.get("code_root") or "")).resolve()
    resolved_design = design_root or Path(str(paths.get("design_root") or "")).resolve()
    result_root = Path(str(paths.get("result_root") or "")).resolve()
    log_root = Path(str(paths.get("log_root") or "")).resolve()
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "stage_artifact_validator.py"),
        "--stage", "task-lifecycle",
        "--code-root", str(resolved_code),
        "--design-root", str(resolved_design),
        "--result-root", str(result_root),
        "--log-root", str(log_root),
        "--state-root", str(state_root),
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    trace_path = log_root / "trace" / "task_lifecycle_validation.json"
    trace = ac.load_json(trace_path) if trace_path.is_file() else {}
    if not isinstance(trace, dict) or trace.get("global_passed") is not True:
        detail = result.stdout.strip() or result.stderr.strip() or "unknown lifecycle error"
        raise ValueError(
            "task lifecycle validation has global structural errors after finding merge: "
            + detail
        )
    trace_digests = trace.get("input_digests", {})
    stale_inputs = [
        name for name in (
            "investigation_tasks.jsonl", "investigation_findings.jsonl",
            "investigation_rounds.jsonl",
        )
        if trace_digests.get(name) != ac.sha256_file(state_root / name)
    ] if isinstance(trace_digests, dict) else ["input_digests"]
    if stale_inputs:
        raise ValueError(
            f"task lifecycle validation trace is stale after finding merge: {stale_inputs}"
        )
    missing = sorted(required_task_ids - set(trace.get("valid_task_ids", [])))
    if missing:
        raise ValueError(
            f"submitted finding tasks failed lifecycle validation after merge: {missing}"
        )
    return trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge isolated subagent JSON handoffs into JSONL.")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input-dir")
    inputs.add_argument("--check-file")
    parser.add_argument("--output")
    parser.add_argument("--key")
    parser.add_argument("--artifact-type", choices=sorted(ARTIFACT_TYPES), default="generic")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--code-root", default=None)
    parser.add_argument("--design-root", default=None)
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)
    key = args.key or ARTIFACT_KEYS.get(args.artifact_type)
    report_path = Path(args.report).resolve() if args.report else None
    batch_gate_path = (
        Path(args.output).resolve().parent / "investigator_batch_gate.json"
        if args.input_dir and args.output and args.artifact_type == "finding"
        else None
    )
    batch_expected_ids: list[str] = []
    batch_missing_ids: list[str] = []
    batch_deferred_ids: list[str] = []
    risk_expected_sweep_ids: list[str] = []
    risk_submitted_sweep_ids: list[str] = []
    risk_validated_sweep_ids: list[str] = []
    risk_completed_sweep_ids: list[str] = []
    risk_closed = False
    risk_plan_sha256 = ""
    risk_architecture_sha256 = ""
    task_lifecycle: dict[str, Any] | None = None
    ledger_snapshot: dict[Path, bytes | None] = {}
    try:
        if not key:
            raise ValueError("--key is required for generic artifacts")
        typed_key = ARTIFACT_KEYS.get(args.artifact_type)
        if typed_key and args.key and args.key != typed_key:
            raise ValueError(
                f"--artifact-type {args.artifact_type} requires key {typed_key}; "
                f"got {args.key}"
            )
        if args.input_dir and args.artifact_type != "generic" and not report_path:
            raise ValueError("typed --input-dir merge requires --report for digest-bound provenance")
        code_root = Path(args.code_root).resolve() if args.code_root else None
        design_root = Path(args.design_root).resolve() if args.design_root else None
        if args.check_file:
            check_path = Path(args.check_file).resolve()
            values = _read_values(check_path)
            if args.artifact_type == "risk":
                state_root = _risk_state_root_for_handoff(check_path)
                risk_expected_sweep_ids = sorted(_expected_risk_sweep_ids(state_root))
                plan_path = state_root / "risk_sweep_plan.json"
                architecture_path = state_root / "architecture_map.json"
                risk_plan_sha256 = (
                    ac.sha256_file(plan_path) if plan_path.is_file() else ""
                )
                risk_architecture_sha256 = (
                    ac.sha256_file(architecture_path)
                    if architecture_path.is_file() else ""
                )
                risk_completed_sweep_ids = _risk_ledger_sweep_ids(
                    state_root / "risk_observations.jsonl", state_root,
                )
                sweep_id, identifiers = _validate_risk_handoff_file(
                    check_path,
                    values,
                    state_root=state_root,
                    session_id=args.session_id,
                    code_root=code_root,
                    validate_items=True,
                )
                risk_submitted_sweep_ids = [sweep_id]
                risk_validated_sweep_ids = [sweep_id]
                risk_closed = set(risk_completed_sweep_ids) == set(
                    risk_expected_sweep_ids
                )
                result = {
                    "files": 1,
                    "validated_ids": identifiers,
                    "expected_sweep_ids": risk_expected_sweep_ids,
                    "submitted_sweep_ids": risk_submitted_sweep_ids,
                    "validated_sweep_ids": risk_validated_sweep_ids,
                    "completed_sweep_ids": risk_completed_sweep_ids,
                    "missing_sweep_ids": sorted(
                        set(risk_expected_sweep_ids) - set(risk_completed_sweep_ids)
                    ),
                    "closed": risk_closed,
                    "global_coverage_validated": False,
                    "risk_sweep_plan_sha256": risk_plan_sha256,
                    "architecture_map_sha256": risk_architecture_sha256,
                }
            else:
                if len(values) != 1:
                    raise ValueError("--check-file must contain exactly one object")
                item = values[0]
                typed_state_root: Path | None = None
                if args.artifact_type == "critic":
                    typed_state_root = _critic_state_root_for_handoff(check_path)
                    item = materialize_critic_bindings(
                        item,
                        typed_state_root,
                        f"critic ({item.get('finding_id') or item.get('review_id') or '?'})",
                    )
                elif args.artifact_type == "probe":
                    typed_state_root = _probe_state_root_for_handoff(check_path)
                identifier = str(item.get(key) or "")
                if not identifier:
                    raise ValueError(f"checked handoff lacks {key}")
                template_root = _template_root_for_handoff(check_path) if args.artifact_type == "finding" else None
                template = _load_template(
                    template_root, item, required=template_root is not None,
                ) if args.artifact_type == "finding" else None
                errors = validate_item(
                    item, artifact_type=args.artifact_type, identifier=identifier,
                    session_id=args.session_id, code_root=code_root, design_root=design_root,
                    template=template,
                )
                if typed_state_root is not None:
                    errors.extend(_context_errors(
                        item,
                        args.artifact_type,
                        typed_state_root,
                        f"{args.artifact_type} ({identifier})",
                    ))
                if errors:
                    raise HandoffValidationError({identifier: errors})
                result = {"files": 1, "validated_ids": [identifier]}
                if args.artifact_type == "critic":
                    result.update({
                        "input_digests": item["input_digests"],
                        "evidence_critic_prompt_version": item[
                            "evidence_critic_prompt_version"
                        ],
                    })
        else:
            if not args.output:
                raise ValueError("--output is required with --input-dir")
            input_dir = Path(args.input_dir).resolve()
            output = Path(args.output).resolve()
            template_root = _template_root_for_handoff(input_dir / "placeholder.json") if args.artifact_type == "finding" else None
            if args.artifact_type == "risk":
                risk_expected_sweep_ids = sorted(_expected_risk_sweep_ids(output.parent))
                plan_path = output.parent / "risk_sweep_plan.json"
                architecture_path = output.parent / "architecture_map.json"
                risk_plan_sha256 = (
                    ac.sha256_file(plan_path) if plan_path.is_file() else ""
                )
                risk_architecture_sha256 = (
                    ac.sha256_file(architecture_path)
                    if architecture_path.is_file() else ""
                )
                risk_completed_sweep_ids = _risk_ledger_sweep_ids(
                    output, output.parent,
                )
                risk_closed = set(risk_completed_sweep_ids) == set(
                    risk_expected_sweep_ids
                )
                risk_submitted_sweep_ids = _validate_risk_batch(
                    input_dir, output.parent, risk_expected_sweep_ids,
                )
                risk_validated_sweep_ids = list(risk_submitted_sweep_ids)
            if args.artifact_type == "finding":
                batch_expected_ids, batch_missing_ids, batch_deferred_ids = _finding_batch_expectation(output, input_dir)
                deferred_input_errors = _deferred_finding_input_errors(output.parent, input_dir)
                if deferred_input_errors:
                    raise HandoffValidationError(deferred_input_errors)
                lifecycle_trace_path = _task_lifecycle_trace_path(output.parent)
                lifecycle_files = [
                    output,
                    output.parent / "investigation_tasks.jsonl",
                    output.parent / "investigation_rounds.jsonl",
                ]
                if lifecycle_trace_path is not None:
                    lifecycle_files.append(lifecycle_trace_path)
                ledger_snapshot = _snapshot_files(lifecycle_files)
            result = merge(
                input_dir, output, key,
                artifact_type=args.artifact_type, session_id=args.session_id,
                code_root=code_root, design_root=design_root,
                template_root=template_root,
                context_root=output.parent,
            )
            result["ledger_sha256"] = ac.sha256_file(output)
            if args.artifact_type == "risk":
                risk_submitted_sweep_ids = list(result["submitted_sweep_ids"])
                risk_validated_sweep_ids = list(result["validated_sweep_ids"])
                risk_completed_sweep_ids = list(result["completed_sweep_ids"])
                risk_closed = result["closed"] is True
                result.update({
                    "risk_sweep_plan_sha256": risk_plan_sha256,
                    "architecture_map_sha256": risk_architecture_sha256,
                })
            if args.artifact_type == "finding":
                result["expected_ids"] = batch_expected_ids
                result["batch_validated_ids"] = sorted(
                    set(batch_expected_ids).intersection(result["validated_ids"])
                )
                result["missing_ids"] = []
                result["deferred_ids"] = batch_deferred_ids
            if args.artifact_type == "finding":
                task_lifecycle = _complete_tasks_for_findings(
                    output.parent, set(result.get("batch_validated_ids", [])),
                )
                lifecycle_trace = _refresh_task_lifecycle_trace(
                    output.parent, code_root=code_root, design_root=design_root,
                    required_task_ids=set(task_lifecycle["submitted_task_ids"]),
                )
                task_lifecycle.update({
                    "validated_ids": lifecycle_trace.get("valid_task_ids", []),
                    "task_lifecycle_sha256": lifecycle_trace.get("task_lifecycle_sha256"),
                    "candidate_digests": lifecycle_trace.get("candidate_digests", {}),
                    "valid_task_ids": lifecycle_trace.get("valid_task_ids", []),
                    "invalid_task_ids": lifecycle_trace.get("invalid_task_ids", []),
                    "lifecycle_trace": str(
                        _task_lifecycle_trace_path(output.parent) or ""
                    ),
                })
    except (OSError, ValueError) as exc:
        if ledger_snapshot:
            try:
                _restore_files(ledger_snapshot)
            except OSError as rollback_exc:
                exc = ValueError(f"{exc}; ledger rollback failed: {rollback_exc}")
        errors = exc.errors if isinstance(exc, HandoffValidationError) else [str(exc)]
        invalid_ids = exc.invalid_ids if isinstance(exc, HandoffValidationError) else []
        report = {
            "passed": False, "artifact_type": args.artifact_type,
            "invalid_ids": invalid_ids, "missing_ids": batch_missing_ids,
            "expected_ids": batch_expected_ids, "deferred_ids": batch_deferred_ids,
            "errors": errors,
        }
        if args.artifact_type == "risk":
            report.update({
                "expected_sweep_ids": risk_expected_sweep_ids,
                "submitted_sweep_ids": risk_submitted_sweep_ids,
                "validated_sweep_ids": risk_validated_sweep_ids,
                "completed_sweep_ids": risk_completed_sweep_ids,
                "missing_sweep_ids": sorted(
                    set(risk_expected_sweep_ids) - set(risk_completed_sweep_ids)
                ),
                "closed": risk_closed,
                "global_coverage_validated": False,
                "risk_sweep_plan_sha256": risk_plan_sha256,
                "architecture_map_sha256": risk_architecture_sha256,
            })
        if report_path:
            ac.save_json(report_path, report)
        if batch_gate_path:
            ac.save_json(batch_gate_path, report)
        print(json.dumps({
            "passed": False, "invalid_ids": invalid_ids, "error_count": len(errors),
            "errors": errors, "report": str(report_path) if report_path else "",
        }))
        return 1
    report = {"passed": True, "artifact_type": args.artifact_type, "errors": [], **result}
    if report_path:
        ac.save_json(report_path, report)
    if batch_gate_path:
        ac.save_json(batch_gate_path, report)
    if args.input_dir and args.output:
        output = Path(args.output).resolve()
        state_path = output.parent / "agent_loop_state.json"
        if state_path.is_file():
            state = ac.load_json(state_path)
            phase = {
                "task": "investigation_planning", "finding": "investigation",
                "risk": "code_risk_backtracking", "probe": "dynamic_probe", "critic": "critic_review",
            }.get(args.artifact_type, "handoff")
            state["updated_at"] = ac.now_iso()
            state["status"] = "in_progress"
            state["current_phase"] = phase
            ac.save_json(state_path, state)
            ac.append_jsonl(output.parent / "agent_run_ledger.jsonl", {
                "recorded_at": ac.now_iso(), "session_id": state.get("session_id", ""),
                "event": "handoff_merge", "actor": "handoff_merge_helper",
                "phase": phase, "status": "complete", "artifact_type": args.artifact_type,
                "validated_ids": result.get("validated_ids", []),
                "expected_ids": result.get("expected_ids", []),
                "report": str(report_path) if report_path else "",
                "report_sha256": ac.sha256_file(report_path) if report_path and report_path.is_file() else "",
                "ledger_sha256": result.get("ledger_sha256", ""),
                **({
                    "task_plan_ledger_sha256": result.get(
                        "task_plan_ledger_sha256", ""
                    ),
                } if args.artifact_type == "task" else {}),
            })
            if task_lifecycle is not None and report_path is not None:
                task_report_path = report_path.parent / "task-lifecycle-transition.json"
                task_report = {
                    "passed": True, "artifact_type": "task-lifecycle", "errors": [],
                    **task_lifecycle,
                    "lifecycle_source": "validated finding handoff merge",
                }
                ac.save_json(task_report_path, task_report)
                ac.append_jsonl(output.parent / "agent_run_ledger.jsonl", {
                    "recorded_at": ac.now_iso(), "session_id": state.get("session_id", ""),
                    "event": "task_lifecycle_transition", "actor": "handoff_merge_helper",
                    "phase": "investigation", "status": "complete",
                    "artifact_type": "task-lifecycle", "validated_ids": task_report["validated_ids"],
                    "report": str(task_report_path),
                    "report_sha256": ac.sha256_file(task_report_path),
                    "ledger_sha256": task_report["ledger_sha256"],
                    "rounds_sha256": task_report["rounds_sha256"],
                    "task_lifecycle_sha256": task_report["task_lifecycle_sha256"],
                    "transitioned_ids": task_report["transitioned_ids"],
                })
            if args.artifact_type == "probe":
                ac.append_jsonl(output.parent / "approval_events.jsonl", {
                    "recorded_at": ac.now_iso(), "session_id": state.get("session_id", ""),
                    "actor": "handoff_merge_helper", "action": "focused_dynamic_probe",
                    "scope": str(output.parent / "probes"), "decision": "auto_approved",
                    "rationale": (
                        "Validated probe handoffs are confined to session-owned copies and use "
                        "claim-bound design oracles, explicit non-triviality checks, and a "
                        "secondary oracle when one is available."
                    ),
                })
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
