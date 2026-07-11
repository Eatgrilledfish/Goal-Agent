#!/usr/bin/env python3
"""Validate and merge isolated subagent JSON handoffs into one JSONL ledger.

This helper validates syntax and artifact shape only. It performs no semantic
ranking, filtering, or design/code judgement.
"""

from __future__ import annotations

import argparse
import json
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
    "finding_id", "session_id", "task_id", "claim_id", "hypothesis", "expected_behavior",
    "design_evidence", "review_lenses",
)
CRITIC_ALLOWED_KEYS = {
    "review_id", "session_id", "finding_id", "claim_id", "decision", "challenges",
    "checks_performed", "dynamic_probe_review", "review_context", "resolution",
    "remaining_risks",
}
DYNAMIC_PROBE_REVIEW_ALLOWED_KEYS = {
    "status", "probe_id", "oracle_validity", "environment_validity", "reachability",
    "effect_on_decision",
}


class HandoffValidationError(ValueError):
    def __init__(self, errors_by_id: dict[str, list[str]]):
        self.errors_by_id = errors_by_id
        self.invalid_ids = sorted(errors_by_id)
        self.errors = [error for identifier in self.invalid_ids for error in errors_by_id[identifier]]
        super().__init__("; ".join(self.errors))


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


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
        return [f"{label}: code-only tool_trace must contain at least three real steps"]
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
        if kind == "design_read":
            errors.append(f"{step_label}: risk explorer must not read design")
        kinds.add(kind)
    for required, description in (
        ({"code_search", "code_navigation"}, "code_search or code_navigation"),
        ({"code_read"}, "code_read"),
        ({"reverse_check"}, "reverse_check"),
    ):
        if not kinds.intersection(required):
            errors.append(f"{label}: tool_trace lacks {description}")
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
            "task_id", "session_id", "claim_id", "question", "starting_points",
            "supporting_evidence_needed", "disconfirming_evidence_needed", "review_lenses",
            "exploration_mode", "architecture_boundaries", "implementation_planes", "status",
        ), label))
        if item.get("status") not in {"pending", "in_progress", "complete", "deferred"}:
            errors.append(f"{label}: invalid status")
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
            "code_evidence", "false_positive_checks", "design_lookup_questions", "tool_trace",
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
        if not isinstance(checks, list) or len(checks) < 2:
            errors.append(f"{label}: false_positive_checks must contain at least two checks")
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
        forbidden = set(item).intersection({
            "claim_id", "design_evidence", "assessment", "recommendation", "status", "confidence",
        })
        if forbidden:
            errors.append(f"{label}: code-only observation contains verdict/design fields {sorted(forbidden)}")
        errors.extend(_validate_risk_trace(item, label))
        return errors

    if artifact_type == "finding":
        errors.extend(_require(item, (
            "finding_id", "session_id", "task_id", "claim_id", "hypothesis",
            "expected_behavior", "observed_behavior", "design_evidence", "code_evidence",
            "supporting_evidence", "false_positive_checks", "tool_trace",
            "dynamic_probe_selection", "assessment", "review_lenses", "recommendation",
        ), label))
        if item.get("assessment") not in {"contradiction_supported", "uncertain", "design_satisfied"}:
            errors.append(f"{label}: invalid assessment")
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
        ), label))
        if item.get("decision") not in {
            "confirm_contradiction", "probable_contradiction", "reject_issue", "needs_more_evidence",
        }:
            errors.append(f"{label}: invalid decision")
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
        errors.extend(_require(item, (
            "probe_id", "session_id", "finding_id", "claim_id", "oracle", "selection_reason",
            "isolation", "baseline", "execution", "interpretation", "tool_trace",
        ), label))
        if item.get("interpretation") not in {
            "supports_contradiction", "disconfirms_contradiction", "inconclusive",
        }:
            errors.append(f"{label}: invalid interpretation")
        return errors
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
    parent = path.resolve().parent
    if parent.name != "investigators" or parent.parent.name != "handoffs":
        return None
    return parent.parent.parent / "handoff-templates" / "investigators"


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
    if artifact_type not in {"task", "risk", "critic"}:
        return []
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
        if claim is None:
            errors.append(f"{label}: unknown claim_id {claim_id!r}")
        elif claim.get("session_id") != item.get("session_id"):
            errors.append(f"{label}: claim belongs to a different session")

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
    return errors


def _finding_batch_expectation(output: Path, input_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """Use already-created pristine templates as the immutable batch membership."""
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
    prior, prior_errors = ac.load_jsonl(output)
    if prior_errors and output.exists():
        raise ValueError(f"existing ledger is invalid: {'; '.join(prior_errors)}")
    prior_ids = sorted(
        str(item.get("finding_id")) for item in prior if item.get("finding_id")
    )
    template_ids: set[str] = set()
    for path in sorted(template_root.glob("*.json")):
        value = ac.load_json(path)
        if not isinstance(value, dict) or not value.get("finding_id"):
            raise ValueError(f"invalid pristine template: {path}")
        template_ids.add(str(value["finding_id"]))
    deferred_ids = sorted(
        finding_id for finding_id in template_ids
        if finding_id.removeprefix("FINDING-") in deferred_task_ids
    )
    expected = sorted(template_ids - set(prior_ids) - set(deferred_ids))
    if len(expected) > 2:
        raise ValueError(
            f"investigator batch has more than two unresolved template IDs: {expected}"
        )
    present = _handoff_identifiers(input_dir, "finding_id")
    missing = sorted(set(expected) - present)
    return expected, missing, deferred_ids


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


def _risk_state_root_for_handoff(path: Path) -> Path:
    parent = path.resolve().parent
    if parent.name != "risks" or parent.parent.name != "handoffs":
        raise ValueError(
            "risk --check-file must be located under <state-root>/handoffs/risks"
        )
    return parent.parent.parent


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
    """Require every plan-owned sweep file before mutating the shared ledger."""
    if not input_dir.is_dir():
        raise ValueError(f"handoff directory is missing: {input_dir}")
    if expected_sweep_ids is None:
        expected_sweep_ids = sorted(_expected_risk_sweep_ids(state_root))
    if len(expected_sweep_ids) < 2:
        raise ValueError(
            "risk sweep plan must declare at least two sweep IDs; "
            f"found {expected_sweep_ids}"
        )

    expected_paths = {
        input_dir / f"{sweep_id}.json" for sweep_id in expected_sweep_ids
    }
    actual_paths = {
        path for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    }
    missing = sorted(path.name for path in expected_paths - actual_paths)
    unexpected = sorted(
        str(path.relative_to(input_dir)) for path in actual_paths - expected_paths
    )
    if missing or unexpected or len(actual_paths) != len(expected_paths):
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unexpected:
            details.append(f"unexpected {unexpected}")
        raise ValueError(
            "risk merge requires exactly all planned sweep JSON files"
            + (f": {'; '.join(details)}" if details else "")
        )

    for sweep_id in expected_sweep_ids:
        path = input_dir / f"{sweep_id}.json"
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
    return expected_sweep_ids


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
    # A repaired architecture/plan invalidates every prior risk observation.  The
    # The current sweep files are therefore the authoritative batch, not an
    # incremental overlay on a stale ledger.
    if output.exists() and artifact_type != "risk":
        existing, errors = ac.load_jsonl(output)
        if errors:
            raise ValueError(f"existing ledger is invalid: {'; '.join(errors)}")
    ordered: list[str] = []
    values: dict[str, dict[str, Any]] = {}
    for item in existing:
        identifier = str(item.get(key) or "")
        if not identifier:
            raise ValueError(f"existing ledger entry lacks {key}")
        if identifier not in values:
            ordered.append(identifier)
        values[identifier] = item

    imported = 0
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

    for _path, item in imported_items:
        identifier = str(item.get(key) or "")
        prior = values.get(identifier)
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
        template = _load_template(
            template_root, item, required=template_root is not None,
        ) if artifact_type == "finding" else None
        item_errors = validate_item(
            item, artifact_type=artifact_type, identifier=identifier,
            session_id=session_id, code_root=code_root, design_root=design_root,
            template=template,
        )
        if context_root is not None:
            item_errors.extend(_context_errors(
                item, artifact_type, context_root, f"{artifact_type} ({identifier})",
            ))
        if item_errors:
            validation_errors[identifier] = item_errors
    if validation_errors:
        raise HandoffValidationError(validation_errors)
    if artifact_type == "risk":
        if context_root is None:
            raise ValueError("risk merge requires a state-root context")
        import risk_sweep_plan_validator as validator

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
    return {
        "files": len(files), "imported": imported, "ledger_entries": len(ordered),
        "validated_ids": ordered,
    }


def _complete_tasks_for_findings(state_root: Path, session_id: str | None) -> dict[str, Any]:
    """Apply the deterministic pending -> complete transition after a finding merge."""
    task_path = state_root / "investigation_tasks.jsonl"
    finding_path = state_root / "investigation_findings.jsonl"
    tasks, task_errors = ac.load_jsonl(task_path)
    findings, finding_errors = ac.load_jsonl(finding_path)
    errors = [*task_errors, *finding_errors]
    task_index = {
        str(item.get("task_id") or ""): item for item in tasks if item.get("task_id")
    }
    findings_by_task: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        findings_by_task.setdefault(str(finding.get("task_id") or ""), []).append(finding)
    transitioned: list[str] = []
    for task_id, linked in findings_by_task.items():
        task = task_index.get(task_id)
        if task is None:
            errors.append(f"finding references unknown task {task_id!r}")
            continue
        if len(linked) != 1:
            errors.append(f"task {task_id} has {len(linked)} findings; exactly one is required")
            continue
        finding = linked[0]
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
    for task in tasks:
        identifier = str(task.get("task_id") or "?")
        errors.extend(validate_item(
            task, artifact_type="task", identifier=identifier, session_id=session_id,
        ))
        errors.extend(_context_errors(task, "task", state_root, f"task ({identifier})"))
    if errors:
        raise ValueError("; ".join(errors))
    temporary = task_path.with_suffix(task_path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in tasks),
        encoding="utf-8",
    )
    temporary.replace(task_path)
    return {
        "validated_ids": [str(item["task_id"]) for item in tasks],
        "transitioned_ids": transitioned,
        "ledger_sha256": ac.sha256_file(task_path),
    }


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
    risk_validated_sweep_ids: list[str] = []
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
                sweep_id, identifiers = _validate_risk_handoff_file(
                    check_path,
                    values,
                    state_root=state_root,
                    session_id=args.session_id,
                    code_root=code_root,
                    validate_items=True,
                )
                risk_validated_sweep_ids = [sweep_id]
                result = {
                    "files": 1,
                    "validated_ids": identifiers,
                    "expected_sweep_ids": risk_expected_sweep_ids,
                    "validated_sweep_ids": risk_validated_sweep_ids,
                    "missing_sweep_ids": sorted(
                        set(risk_expected_sweep_ids) - set(risk_validated_sweep_ids)
                    ),
                    "risk_sweep_plan_sha256": risk_plan_sha256,
                    "architecture_map_sha256": risk_architecture_sha256,
                }
            else:
                if len(values) != 1:
                    raise ValueError("--check-file must contain exactly one object")
                item = values[0]
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
                if errors:
                    raise HandoffValidationError({identifier: errors})
                result = {"files": 1, "validated_ids": [identifier]}
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
                _validate_risk_batch(
                    input_dir, output.parent, risk_expected_sweep_ids,
                )
            if args.artifact_type == "finding":
                batch_expected_ids, batch_missing_ids, batch_deferred_ids = _finding_batch_expectation(output, input_dir)
                if batch_missing_ids:
                    raise HandoffValidationError({
                        identifier: [f"finding batch is missing expected handoff {identifier}"]
                        for identifier in batch_missing_ids
                    })
                deferred_input_errors = _deferred_finding_input_errors(output.parent, input_dir)
                if deferred_input_errors:
                    raise HandoffValidationError(deferred_input_errors)
                ledger_snapshot = _snapshot_files([
                    output, output.parent / "investigation_tasks.jsonl",
                ])
            result = merge(
                input_dir, output, key,
                artifact_type=args.artifact_type, session_id=args.session_id,
                code_root=code_root, design_root=design_root,
                template_root=template_root,
                context_root=output.parent,
            )
            result["ledger_sha256"] = ac.sha256_file(output)
            if args.artifact_type == "risk":
                risk_validated_sweep_ids = risk_expected_sweep_ids
                result.update({
                    "expected_sweep_ids": risk_expected_sweep_ids,
                    "validated_sweep_ids": risk_validated_sweep_ids,
                    "missing_sweep_ids": [],
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
                task_lifecycle = _complete_tasks_for_findings(output.parent, args.session_id)
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
                "validated_sweep_ids": risk_validated_sweep_ids,
                "missing_sweep_ids": sorted(
                    set(risk_expected_sweep_ids) - set(risk_validated_sweep_ids)
                ),
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
            })
            if task_lifecycle is not None and report_path is not None:
                task_report_path = report_path.parent / "task-handoff-merge.json"
                task_report = {
                    "passed": True, "artifact_type": "task", "errors": [],
                    **task_lifecycle,
                    "lifecycle_source": "validated finding handoff merge",
                }
                ac.save_json(task_report_path, task_report)
                ac.append_jsonl(output.parent / "agent_run_ledger.jsonl", {
                    "recorded_at": ac.now_iso(), "session_id": state.get("session_id", ""),
                    "event": "handoff_merge", "actor": "handoff_merge_helper",
                    "phase": "investigation_planning", "status": "complete",
                    "artifact_type": "task", "validated_ids": task_report["validated_ids"],
                    "report": str(task_report_path),
                    "report_sha256": ac.sha256_file(task_report_path),
                    "ledger_sha256": task_report["ledger_sha256"],
                    "transitioned_ids": task_report["transitioned_ids"],
                })
            if args.artifact_type == "probe":
                ac.append_jsonl(output.parent / "approval_events.jsonl", {
                    "recorded_at": ac.now_iso(), "session_id": state.get("session_id", ""),
                    "actor": "handoff_merge_helper", "action": "focused_dynamic_probe",
                    "scope": str(output.parent / "probes"), "decision": "auto_approved",
                    "rationale": (
                        "Validated probe handoffs are confined to session-owned copies and use "
                        "design-derived oracles."
                    ),
                })
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
