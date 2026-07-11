#!/usr/bin/env python3
"""Fail fast on architecture, task-portfolio, and coverage artifact contracts.

The checks in this module are deliberately semantic-neutral.  They validate
JSON shape, session ownership, typed references, and lifecycle/accounting
relationships.  They never infer a requirement or decide whether code agrees
with a design.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import agent_common as ac
import handoff_merge as hm


STAGES = {"architecture", "task", "coverage"}
ARCHITECTURE_ARRAY_FIELDS = (
    "languages", "entrypoints", "subsystems", "implementation_planes",
    "integration_boundaries", "capability_surfaces", "configuration_surfaces",
    "alternate_execution_paths", "test_surfaces", "parallel_behavior_paths",
)
PLANE_KINDS = {
    "owned", "adapter", "imported", "generated", "fast_path", "slow_path", "other",
}
BOUNDARY_RISKS = {"high", "medium", "low"}
TASK_STATUSES = {"pending", "in_progress", "complete", "deferred"}
LENS_DISPOSITIONS = {"investigated", "inapplicable"}
BOUNDARY_DISPOSITIONS = {"investigated", "deferred"}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _require_fields(
    item: dict[str, Any], fields: Iterable[str], label: str, *, nonempty: bool = True,
) -> list[str]:
    errors: list[str] = []
    for field in fields:
        if field not in item:
            errors.append(f"{label}: missing {field}")
        elif nonempty and not _present(item.get(field)):
            errors.append(f"{label}: missing/empty {field}")
    return errors


def _string(
    item: dict[str, Any], field: str, label: str, *, allow_empty: bool = False,
) -> list[str]:
    value = item.get(field)
    if not isinstance(value, str):
        return [f"{label}: {field} must be a string"]
    if not allow_empty and not value.strip():
        return [f"{label}: {field} must be non-empty"]
    return []


def _string_array(
    item: dict[str, Any], field: str, label: str, *, allow_empty: bool = True,
) -> tuple[list[str], list[str]]:
    value = item.get(field)
    if not isinstance(value, list):
        return [], [f"{label}: {field} must be an array"]
    errors: list[str] = []
    values: list[str] = []
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{label}: {field}[{index}] must be a non-empty string")
        else:
            values.append(entry)
    if not allow_empty and not values:
        errors.append(f"{label}: {field} must contain at least one value")
    if len(set(values)) != len(values):
        errors.append(f"{label}: {field} must not contain duplicates")
    return values, errors


def _load_object(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    if not path.is_file():
        errors.append(f"missing artifact: {path}")
        return {}
    try:
        value = ac.load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: invalid JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label}: must be an object")
        return {}
    return value


def _load_index(
    path: Path, key: str, label: str, errors: list[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    values, parse_errors = ac.load_jsonl(path)
    errors.extend(parse_errors)
    indexed: dict[str, dict[str, Any]] = {}
    for line_number, item in enumerate(values, start=1):
        identifier = item.get(key)
        item_label = f"{label}:{line_number}"
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{item_label}: missing/non-string {key}")
            continue
        if identifier in indexed:
            errors.append(f"{item_label}: duplicate {key} {identifier}")
            continue
        indexed[identifier] = item
    return indexed, values


def _validate_session_owner(
    value: dict[str, Any], session_id: str, label: str, errors: list[str],
) -> None:
    if value.get("session_id") != session_id:
        errors.append(f"{label}: session_id does not match current session")


def _unique_ids(
    values: list[Any], key: str, label: str, errors: list[str],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(values, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        identifier = item.get(key)
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{item_label}: missing/non-string {key}")
        elif identifier in indexed:
            errors.append(f"{item_label}: duplicate {key} {identifier}")
        else:
            indexed[identifier] = item
    return indexed


def validate_architecture(
    architecture: dict[str, Any], session_id: str,
) -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    """Validate the architecture-map machine contract."""
    errors: list[str] = []
    label = "architecture_map.json"
    _validate_session_owner(architecture, session_id, label, errors)
    errors.extend(_require_fields(architecture, ("session_id", "repository_summary", "probe_capabilities"), label))
    errors.extend(_string(architecture, "repository_summary", label))

    arrays: dict[str, list[Any]] = {}
    for field in ARCHITECTURE_ARRAY_FIELDS:
        value = architecture.get(field)
        if not isinstance(value, list):
            errors.append(f"{label}: {field} must be an array")
            arrays[field] = []
        else:
            arrays[field] = value
    for field in ("implementation_planes", "integration_boundaries"):
        if not arrays[field]:
            errors.append(f"{label}: {field} must contain at least one mapped item")

    _, language_errors = _string_array(architecture, "languages", label)
    errors.extend(language_errors)

    for index, item in enumerate(arrays["entrypoints"], start=1):
        item_label = f"architecture entrypoints[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        for field in ("path", "purpose", "evidence"):
            errors.extend(_string(item, field, item_label))

    for index, item in enumerate(arrays["subsystems"], start=1):
        item_label = f"architecture subsystems[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        for field in ("subsystem_id", "name", "role"):
            errors.extend(_string(item, field, item_label))
        _, item_errors = _string_array(item, "paths", item_label, allow_empty=False)
        errors.extend(item_errors)

    plane_index = _unique_ids(
        arrays["implementation_planes"], "plane_id", "architecture implementation_planes", errors,
    )
    for plane_id, item in plane_index.items():
        item_label = f"architecture implementation plane {plane_id}"
        if item.get("kind") not in PLANE_KINDS:
            errors.append(f"{item_label}: invalid kind {item.get('kind')!r}")
        _, item_errors = _string_array(item, "paths", item_label, allow_empty=False)
        errors.extend(item_errors)
        errors.extend(_string(item, "reachable_evidence", item_label))

    boundary_index = _unique_ids(
        arrays["integration_boundaries"], "boundary_id", "architecture integration_boundaries", errors,
    )
    for boundary_id, item in boundary_index.items():
        item_label = f"architecture integration boundary {boundary_id}"
        errors.extend(_string(item, "name", item_label))
        _, item_errors = _string_array(item, "paths", item_label, allow_empty=False)
        errors.extend(item_errors)
        plane_ids, item_errors = _string_array(
            item, "plane_ids", item_label, allow_empty=False,
        )
        errors.extend(item_errors)
        unknown_planes = set(plane_ids) - set(plane_index)
        if unknown_planes:
            errors.append(f"{item_label}: unknown plane_ids {sorted(unknown_planes)}")
        if item.get("risk") not in BOUNDARY_RISKS:
            errors.append(f"{item_label}: invalid risk {item.get('risk')!r}")
        errors.extend(_string(item, "why", item_label))

    for field, key, text_fields in (
        ("capability_surfaces", "surface_id", ("declares_or_registers",)),
    ):
        values = _unique_ids(arrays[field], key, f"architecture {field}", errors)
        for identifier, item in values.items():
            item_label = f"architecture {field} {identifier}"
            _, item_errors = _string_array(item, "paths", item_label, allow_empty=False)
            errors.extend(item_errors)
            for text_field in text_fields:
                errors.extend(_string(item, text_field, item_label))

    for field, string_fields, array_field in (
        ("configuration_surfaces", ("path", "controls"), None),
        ("alternate_execution_paths", ("name", "trigger"), "paths"),
    ):
        for index, item in enumerate(arrays[field], start=1):
            item_label = f"architecture {field}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_label}: must be an object")
                continue
            for text_field in string_fields:
                errors.extend(_string(item, text_field, item_label))
            if array_field:
                _, item_errors = _string_array(item, array_field, item_label, allow_empty=False)
                errors.extend(item_errors)

    for index, item in enumerate(arrays["test_surfaces"], start=1):
        item_label = f"architecture test_surfaces[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        for field in ("path", "coverage", "evidence"):
            errors.extend(_string(item, field, item_label))
        errors.extend(_string(item, "available_command", item_label, allow_empty=True))

    probe = architecture.get("probe_capabilities")
    if not isinstance(probe, dict):
        errors.append(f"{label}: probe_capabilities must be an object")
    else:
        if not isinstance(probe.get("isolated_copy_feasible"), bool):
            errors.append(f"{label}: probe_capabilities.isolated_copy_feasible must be boolean")
        for field in ("available_runtime", "constraints"):
            _, item_errors = _string_array(probe, field, f"{label} probe_capabilities")
            errors.extend(item_errors)

    parallel_index = _unique_ids(
        arrays["parallel_behavior_paths"], "path_id", "architecture parallel_behavior_paths", errors,
    )
    for path_id, item in parallel_index.items():
        item_label = f"architecture parallel behavior path {path_id}"
        errors.extend(_string(item, "behavior", item_label))
        errors.extend(_string(item, "evidence", item_label))
        plane_ids, item_errors = _string_array(item, "plane_ids", item_label, allow_empty=False)
        errors.extend(item_errors)
        if len(set(plane_ids)) < 2:
            errors.append(f"{item_label}: plane_ids must identify at least two planes")
        unknown = set(plane_ids) - set(plane_index)
        if unknown:
            errors.append(f"{item_label}: unknown plane_ids {sorted(unknown)}")

    return errors, {
        "planes": plane_index,
        "boundaries": boundary_index,
        "parallel_paths": parallel_index,
    }


def _validate_claim_sessions(
    claims: dict[str, dict[str, Any]], session_id: str, errors: list[str],
) -> None:
    for claim_id, claim in claims.items():
        _validate_session_owner(claim, session_id, f"design claim {claim_id}", errors)


def _validate_risks(
    risks: dict[str, dict[str, Any]], session_id: str, root: Path, code_root: Path,
    errors: list[str],
) -> None:
    for observation_id, item in risks.items():
        errors.extend(hm.validate_item(
            item, artifact_type="risk", identifier=observation_id,
            session_id=session_id, code_root=code_root,
        ))
        errors.extend(hm._context_errors(
            item, "risk", root, f"risk ({observation_id})",
        ))


def _validate_tasks_typed(
    tasks: dict[str, dict[str, Any]], session_id: str, root: Path, errors: list[str],
) -> None:
    for task_id, item in tasks.items():
        errors.extend(hm.validate_item(
            item, artifact_type="task", identifier=task_id, session_id=session_id,
        ))
        errors.extend(hm._context_errors(
            item, "task", root, f"task ({task_id})",
        ))


def _validated_claim_review_scope(
    root: Path,
    session_id: str,
    claims: dict[str, dict[str, Any]],
    tasks: dict[str, dict[str, Any]],
    errors: list[str],
) -> set[str]:
    """Bind the task frontier to the currently accepted design-only claim review."""
    scope_object = _load_object(
        root / "claim_review_scope.json", "claim_review_scope.json", errors,
    )
    _validate_session_owner(scope_object, session_id, "claim_review_scope.json", errors)
    claims_path = root / "design_claims.jsonl"
    claims_digest = ac.sha256_file(claims_path) if claims_path.is_file() else ""
    if scope_object.get("design_claims_sha256") != claims_digest:
        errors.append("claim_review_scope.json: design_claims_sha256 does not match current claims")
    raw_scope = scope_object.get("claim_ids")
    if not isinstance(raw_scope, list) or not raw_scope:
        errors.append("claim_review_scope.json: claim_ids must be a non-empty array")
        raw_scope = []
    scope_ids = [value for value in raw_scope if isinstance(value, str) and value.strip()]
    if len(scope_ids) != len(raw_scope):
        errors.append("claim_review_scope.json: claim_ids entries must be non-empty strings")
    if len(set(scope_ids)) != len(scope_ids):
        errors.append("claim_review_scope.json: claim_ids must not contain duplicates")
    scope = set(scope_ids)
    unknown_scope = scope - set(claims)
    if unknown_scope:
        errors.append(f"claim_review_scope.json: unknown claim_ids {sorted(unknown_scope)}")

    review = _load_object(
        root / "design_claim_review.json", "design_claim_review.json", errors,
    )
    _validate_session_owner(review, session_id, "design_claim_review.json", errors)
    expected_digests = {
        name: ac.sha256_file(root / name) if (root / name).is_file() else ""
        for name in (
            "design_claims.jsonl", "design_coverage.json", "design_agent_manifest.json",
            "claim_review_scope.json",
        )
    }
    if review.get("input_digests") != expected_digests:
        errors.append("design_claim_review.json: input_digests do not match current design artifacts")
    if review.get("decision") != "accept":
        errors.append("design_claim_review.json: current claim review must be accepted before task planning")

    raw_reviews = review.get("claim_reviews")
    if not isinstance(raw_reviews, list):
        errors.append("design_claim_review.json: claim_reviews must be an array")
        raw_reviews = []
    reviewed_scope: set[str] = set()
    for index, item in enumerate(raw_reviews, start=1):
        label = f"design_claim_review.json claim_reviews[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: must be an object")
            continue
        _validate_session_owner(item, session_id, label, errors)
        claim_id = item.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            errors.append(f"{label}: missing/non-string claim_id")
            continue
        if claim_id in reviewed_scope:
            errors.append(f"{label}: duplicate claim_id {claim_id}")
        reviewed_scope.add(claim_id)
        if claim_id not in claims:
            errors.append(f"{label}: unknown claim_id {claim_id}")
        if claim_id not in scope:
            errors.append(f"{label}: claim_id {claim_id} is outside claim_review_scope.json")
        if item.get("decision") != "accept":
            errors.append(f"{label}: claim must have decision='accept' to enter task scope")
    if reviewed_scope != scope:
        errors.append(
            "design_claim_review.json: accepted claim review membership does not exactly match "
            f"claim_review_scope.json; missing={sorted(scope - reviewed_scope)}, "
            f"extra={sorted(reviewed_scope - scope)}"
        )

    manifest = _load_object(root / "workspace_manifest.json", "workspace_manifest.json", errors)
    log_root_value = manifest.get("paths", {}).get("log_root") if isinstance(manifest, dict) else None
    trace_path = (
        Path(log_root_value).resolve() / "trace" / "claim_review_validation.json"
        if isinstance(log_root_value, str) and log_root_value else None
    )
    if trace_path is None:
        errors.append("workspace_manifest.json: paths.log_root is required for claim review validation")
        claim_trace: dict[str, Any] = {}
    else:
        claim_trace = _load_object(trace_path, "claim_review_validation.json", errors)
    if claim_trace.get("passed") is not True or claim_trace.get("decision") != "accept":
        errors.append("claim_review_validation.json: current scoped claim review has not passed")
    if claim_trace.get("session_id") != session_id:
        errors.append("claim_review_validation.json: session_id does not match current session")
    if claim_trace.get("input_digests") != expected_digests:
        errors.append("claim_review_validation.json: input digests are stale")
    scope_path = root / "claim_review_scope.json"
    scope_digest = ac.sha256_file(scope_path) if scope_path.is_file() else ""
    if claim_trace.get("scope_digest") != scope_digest:
        errors.append("claim_review_validation.json: scope digest is stale")
    accepted_claim_ids = claim_trace.get("accepted_claim_ids")
    if not isinstance(accepted_claim_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in accepted_claim_ids
    ):
        errors.append("claim_review_validation.json: accepted_claim_ids must be an array of strings")
        accepted_claim_id_set: set[str] = set()
    else:
        accepted_claim_id_set = set(accepted_claim_ids)
    if accepted_claim_id_set != scope:
        errors.append("claim_review_validation.json: accepted claim IDs do not match current scope")

    tasks_by_claim: dict[str, list[str]] = {}
    for task_id, task in tasks.items():
        claim_id = str(task.get("claim_id") or "")
        tasks_by_claim.setdefault(claim_id, []).append(task_id)
        if claim_id not in scope:
            errors.append(
                f"investigation task {task_id}: claim_id {claim_id!r} is outside accepted claim review scope"
            )
    for claim_id in sorted(scope):
        if not tasks_by_claim.get(claim_id):
            errors.append(f"accepted claim review scope claim {claim_id}: missing investigation task")
    return scope


def _validate_round_frontier(
    tasks: dict[str, dict[str, Any]],
    rounds: list[dict[str, Any]],
    max_tasks_per_round: int,
    errors: list[str],
) -> tuple[list[list[str]], str]:
    """Freeze task membership and require rounds to drain in JSONL order."""
    memberships: dict[str, list[int]] = {task_id: [] for task_id in tasks}
    ordered_task_ids: list[list[str]] = []
    round_ids: list[str] = []
    for round_index, item in enumerate(rounds, start=1):
        round_id = str(item.get("round_id") or f"#{round_index}")
        round_ids.append(round_id)
        label = f"investigation round {round_id}"
        task_ids, item_errors = _string_array(
            item, "task_ids", label, allow_empty=False,
        )
        errors.extend(item_errors)
        if len(task_ids) > max_tasks_per_round:
            errors.append(
                f"{label}: task_ids exceeds max_tasks_per_round={max_tasks_per_round}"
            )
        ordered_task_ids.append(task_ids)
        for task_id in task_ids:
            if task_id not in tasks:
                errors.append(f"{label}: unknown task_id {task_id}")
                continue
            memberships[task_id].append(round_index - 1)

    for task_id, positions in memberships.items():
        if len(positions) != 1:
            errors.append(
                f"investigation task {task_id}: must belong to exactly one investigation round; "
                f"found {len(positions)}"
            )

    earliest_open = ""
    for round_index, task_ids in enumerate(ordered_task_ids):
        statuses = {
            task_id: tasks[task_id].get("status")
            for task_id in task_ids if task_id in tasks
        }
        open_ids = [
            task_id for task_id, status in statuses.items()
            if status in {"pending", "in_progress"}
        ]
        if not open_ids:
            continue
        if not earliest_open:
            earliest_open = round_ids[round_index]
        for later_index in range(round_index + 1, len(ordered_task_ids)):
            later_round_id = round_ids[later_index]
            if ordered_task_ids[later_index]:
                errors.append(
                    f"investigation round {later_round_id}: cannot exist while earlier round "
                    f"{round_ids[round_index]} has open tasks {open_ids}"
                )
    return ordered_task_ids, earliest_open


def _validate_task_finding_lifecycle(
    tasks: dict[str, dict[str, Any]], findings: dict[str, dict[str, Any]],
    session_id: str, errors: list[str],
) -> None:
    findings_by_task: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for finding_id, finding in findings.items():
        _validate_session_owner(finding, session_id, f"finding {finding_id}", errors)
        task_id = finding.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            errors.append(f"finding {finding_id}: missing/non-string task_id")
            continue
        findings_by_task.setdefault(task_id, []).append((finding_id, finding))
        task = tasks.get(task_id)
        if task is None:
            errors.append(f"finding {finding_id}: unknown task_id {task_id}")
            continue
        if task.get("status") != "complete":
            errors.append(
                f"finding {finding_id}: linked task {task_id} must have status complete"
            )
        if finding.get("claim_id") != task.get("claim_id"):
            errors.append(f"finding {finding_id}: task/finding claim_id mismatch")

    for task_id, linked in findings_by_task.items():
        if len(linked) != 1:
            errors.append(f"task {task_id}: expected exactly one finding, found {len(linked)}")
    for task_id, task in tasks.items():
        linked = findings_by_task.get(task_id, [])
        if task.get("status") == "complete" and len(linked) != 1:
            errors.append(f"complete task {task_id}: expected exactly one linked finding")
        if task.get("status") in {"pending", "in_progress", "deferred"} and linked:
            errors.append(
                f"task {task_id}: status {task.get('status')} is inconsistent with a finding"
            )


def _first_portfolio(
    tasks: dict[str, dict[str, Any]], rounds: list[dict[str, Any]], errors: list[str],
) -> dict[str, dict[str, Any]]:
    if not rounds:
        return tasks
    first = rounds[0]
    task_ids, task_errors = _string_array(
        first, "task_ids", "first investigation round", allow_empty=False,
    )
    errors.extend(task_errors)
    selected: dict[str, dict[str, Any]] = {}
    for task_id in task_ids:
        if task_id not in tasks:
            errors.append(f"first investigation round: unknown task_id {task_id}")
        else:
            selected[task_id] = tasks[task_id]
    return selected


def _validate_first_portfolio(
    portfolio: dict[str, dict[str, Any]], contract: dict[str, Any],
    architecture_indexes: dict[str, dict[str, dict[str, Any]]],
    risks: dict[str, dict[str, Any]], errors: list[str],
) -> None:
    coverage_contract = contract.get("coverage_contract")
    if not isinstance(coverage_contract, dict):
        errors.append("agent_loop_contract.json: coverage_contract must be an object")
        return
    lenses = coverage_contract.get("portfolio_lenses")
    if not isinstance(lenses, list) or any(not isinstance(value, str) or not value for value in lenses):
        errors.append("agent_loop_contract.json: portfolio_lenses must be an array of strings")
        lenses = []
    # Lens applicability is semantic.  The coverage stage must account for every
    # contract lens as investigated or evidence-backed inapplicable; planning
    # only validates lenses that tasks actually declare.

    high_boundaries = {
        boundary_id for boundary_id, boundary in architecture_indexes["boundaries"].items()
        if boundary.get("risk") == "high"
    }
    risk_backtracking_tasks = [
        task for task in portfolio.values()
        if task.get("exploration_mode") == "code-to-design risk backtracking"
    ]
    covered_boundaries = {
        boundary
        for task in risk_backtracking_tasks
        for boundary in task.get("architecture_boundaries", [])
        if isinstance(boundary, str)
        and any(
            boundary in risks[risk_id].get("architecture_boundaries", [])
            for risk_id in task.get("risk_observation_ids", [])
            if risk_id in risks
        )
    }
    if high_boundaries and not high_boundaries.intersection(covered_boundaries):
        errors.append(
            "first-round portfolio needs at least one risk-backed code-to-design task "
            "on a high-risk boundary"
        )


def validate_task_stage(
    *, root: Path, code_root: Path, session_id: str, contract: dict[str, Any],
    architecture: dict[str, Any], claims: dict[str, dict[str, Any]],
    risks: dict[str, dict[str, Any]], tasks: dict[str, dict[str, Any]],
    findings: dict[str, dict[str, Any]], rounds: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    errors, architecture_indexes = validate_architecture(architecture, session_id)
    _validate_claim_sessions(claims, session_id, errors)
    _validate_risks(risks, session_id, root, code_root, errors)
    _validate_tasks_typed(tasks, session_id, root, errors)
    claim_scope = _validated_claim_review_scope(
        root, session_id, claims, tasks, errors,
    )
    iteration_policy = contract.get("iteration_policy", {})
    max_tasks_per_round = iteration_policy.get("max_tasks_per_round")
    if (
        not isinstance(max_tasks_per_round, int)
        or isinstance(max_tasks_per_round, bool)
        or max_tasks_per_round < 1
    ):
        errors.append("agent_loop_contract.json: max_tasks_per_round must be a positive integer")
        max_tasks_per_round = 1
    _, earliest_open_round = _validate_round_frontier(
        tasks, rounds, max_tasks_per_round, errors,
    )
    _validate_task_finding_lifecycle(tasks, findings, session_id, errors)
    portfolio = _first_portfolio(tasks, rounds, errors)
    _validate_first_portfolio(portfolio, contract, architecture_indexes, risks, errors)
    return errors, {
        "claims": len(claims), "risks": len(risks), "tasks": len(tasks),
        "findings": len(findings), "first_round_tasks": len(portfolio),
        "claim_review_scope": len(claim_scope),
        "investigation_rounds": len(rounds),
        "earliest_open_round": earliest_open_round,
    }


def _validate_rounds(
    rounds: dict[str, dict[str, Any]], session_id: str,
    known: dict[str, set[str]], tasks: dict[str, dict[str, Any]],
    findings: dict[str, dict[str, Any]], errors: list[str],
) -> set[str]:
    observed_modes: set[str] = set()
    array_fields = {
        "exploration_modes": "modes", "document_groups": "groups",
        "architecture_boundaries": "boundaries", "implementation_planes": "planes",
        "lenses": "lenses", "claim_ids": "claims", "task_ids": "tasks",
        "finding_ids": "findings",
    }
    for round_id, item in rounds.items():
        label = f"investigation round {round_id}"
        _validate_session_owner(item, session_id, label, errors)
        for field in ("strategy", "outcome", "next_strategy"):
            errors.extend(_string(item, field, label))
        values: dict[str, list[str]] = {}
        for field, known_key in array_fields.items():
            entries, item_errors = _string_array(item, field, label)
            errors.extend(item_errors)
            values[field] = entries
            unknown = set(entries) - known[known_key]
            if unknown:
                errors.append(f"{label}: unknown {field} {sorted(unknown)}")
        round_claims = set(values["claim_ids"])
        round_tasks = set(values["task_ids"])
        round_findings = {
            finding_id: findings[finding_id]
            for finding_id in values["finding_ids"] if finding_id in findings
        }
        for mode in values["exploration_modes"]:
            matching_tasks = {
                task_id: tasks[task_id]
                for task_id in round_tasks if task_id in tasks
                and tasks[task_id].get("exploration_mode") == mode
            }
            if not matching_tasks:
                errors.append(
                    f"{label}: exploration mode {mode!r} has no task with that mode"
                )
                continue
            completed_task_ids = {
                task_id for task_id, task in matching_tasks.items()
                if task.get("status") == "complete"
            }
            evidenced_task_ids = {
                str(finding.get("task_id") or "") for finding in round_findings.values()
            }
            if not completed_task_ids.intersection(evidenced_task_ids):
                errors.append(
                    f"{label}: exploration mode {mode!r} lacks a completed task/finding"
                )
                continue
            observed_modes.add(mode)
        for task_id in round_tasks & known["tasks"]:
            # The relationship itself is checked by the caller's task index.
            if not task_id:
                errors.append(f"{label}: empty task_id")
        if not round_claims and round_tasks:
            errors.append(f"{label}: task_ids require claim_ids")
    return observed_modes


def _validate_semantic_coverage(
    semantic: dict[str, Any], session_id: str, expected_lenses: set[str],
    design_groups: dict[str, dict[str, Any]], claims: dict[str, dict[str, Any]],
    tasks: dict[str, dict[str, Any]], findings: dict[str, dict[str, Any]],
    boundaries: set[str], errors: list[str],
) -> dict[str, dict[str, Any]]:
    label = "semantic_coverage.json"
    _validate_session_owner(semantic, session_id, label, errors)
    lenses = semantic.get("lenses")
    if not isinstance(lenses, list):
        errors.append(f"{label}: lenses must be an array")
        lenses = []
    entries: dict[str, dict[str, Any]] = {}
    finding_use: dict[str, int] = {}
    claim_groups: dict[str, set[str]] = {}
    for group_id, group in design_groups.items():
        for claim_id in group.get("claim_ids", []):
            if isinstance(claim_id, str):
                claim_groups.setdefault(claim_id, set()).add(group_id)

    for index, item in enumerate(lenses, start=1):
        item_label = f"semantic coverage lenses[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        errors.extend(_require_fields(item, (
            "lens", "disposition", "evidence", "task_ids", "finding_ids",
            "design_group_refs", "boundary_refs", "counterfactual",
        ), item_label, nonempty=False))
        lens = item.get("lens")
        if not isinstance(lens, str) or not lens:
            errors.append(f"{item_label}: lens must be a non-empty string")
            continue
        if lens in entries:
            errors.append(f"{item_label}: duplicate lens {lens}")
        else:
            entries[lens] = item
        if lens not in expected_lenses:
            errors.append(f"{item_label}: unknown lens {lens}")
        if item.get("disposition") not in LENS_DISPOSITIONS:
            errors.append(f"{item_label}: invalid disposition {item.get('disposition')!r}")
        errors.extend(_string(item, "evidence", item_label))
        errors.extend(_string(item, "counterfactual", item_label, allow_empty=True))
        task_ids, task_errors = _string_array(item, "task_ids", item_label)
        finding_ids, finding_errors = _string_array(item, "finding_ids", item_label)
        group_refs, group_errors = _string_array(
            item, "design_group_refs", item_label, allow_empty=False,
        )
        boundary_refs, boundary_errors = _string_array(
            item, "boundary_refs", item_label, allow_empty=False,
        )
        errors.extend(task_errors + finding_errors + group_errors + boundary_errors)
        unknown_tasks = set(task_ids) - set(tasks)
        unknown_findings = set(finding_ids) - set(findings)
        unknown_groups = set(group_refs) - set(design_groups)
        unknown_boundaries = set(boundary_refs) - boundaries
        if unknown_tasks:
            errors.append(f"{item_label}: unknown task_ids {sorted(unknown_tasks)}")
        if unknown_findings:
            errors.append(f"{item_label}: unknown finding_ids {sorted(unknown_findings)}")
        if unknown_groups:
            errors.append(f"{item_label}: unknown design_group_refs {sorted(unknown_groups)}")
        if unknown_boundaries:
            errors.append(f"{item_label}: unknown boundary_refs {sorted(unknown_boundaries)}")
        for finding_id in finding_ids:
            finding_use[finding_id] = finding_use.get(finding_id, 0) + 1

        if item.get("disposition") == "inapplicable":
            if not isinstance(item.get("counterfactual"), str) or not item["counterfactual"].strip():
                errors.append(f"{item_label}: inapplicable lens requires counterfactual")
            continue
        if not task_ids or not finding_ids:
            errors.append(f"{item_label}: investigated lens requires task_ids and finding_ids")
        referenced_tasks = {task_id: tasks[task_id] for task_id in task_ids if task_id in tasks}
        referenced_findings = {
            finding_id: findings[finding_id] for finding_id in finding_ids if finding_id in findings
        }
        linked_task_ids = {
            str(finding.get("task_id") or "") for finding in referenced_findings.values()
        }
        for task_id, task in referenced_tasks.items():
            if task.get("status") != "complete":
                errors.append(f"{item_label}: task {task_id} is not complete")
            if lens not in task.get("review_lenses", []):
                errors.append(f"{item_label}: task {task_id} does not declare lens")
            if task_id not in linked_task_ids:
                errors.append(f"{item_label}: task {task_id} lacks a listed finding")
        for finding_id, finding in referenced_findings.items():
            task_id = str(finding.get("task_id") or "")
            task = referenced_tasks.get(task_id)
            if task is None:
                errors.append(f"{item_label}: finding {finding_id} is not linked to a listed task")
                continue
            if lens not in finding.get("review_lenses", []):
                errors.append(f"{item_label}: finding {finding_id} does not declare lens")
            if task.get("claim_id") != finding.get("claim_id"):
                errors.append(f"{item_label}: task/finding claim mismatch for {finding_id}")
        covered_boundaries = {
            boundary for task in referenced_tasks.values()
            for boundary in task.get("architecture_boundaries", []) if isinstance(boundary, str)
        }
        if set(boundary_refs) - covered_boundaries:
            errors.append(
                f"{item_label}: boundary_refs lack linked task evidence "
                f"{sorted(set(boundary_refs) - covered_boundaries)}"
            )
        referenced_claims = {
            str(task.get("claim_id") or "") for task in referenced_tasks.values()
        }
        covered_groups = {
            group_id for claim_id in referenced_claims for group_id in claim_groups.get(claim_id, set())
        }
        if set(group_refs) - covered_groups:
            errors.append(
                f"{item_label}: design_group_refs lack linked claim evidence "
                f"{sorted(set(group_refs) - covered_groups)}"
            )

    missing = expected_lenses - set(entries)
    if missing:
        errors.append(f"{label}: missing contract lenses {sorted(missing)}")
    overloaded = sorted(finding_id for finding_id, count in finding_use.items() if count > 3)
    if overloaded:
        errors.append(f"{label}: findings reused for more than three lenses {overloaded}")
    return entries


def _validate_next_tasks(
    values: Any, *, claims: dict[str, dict[str, Any]], risks: dict[str, dict[str, Any]],
    modes: set[str], lenses: set[str], boundaries: set[str], planes: set[str],
    parallel_paths: dict[str, dict[str, Any]], errors: list[str],
) -> list[dict[str, Any]]:
    label = "coverage_audit.json next_round_tasks"
    if not isinstance(values, list):
        errors.append(f"{label}: must be an array")
        return []
    valid: list[dict[str, Any]] = []
    required = (
        "claim_id", "question", "exploration_mode", "review_lenses",
        "architecture_boundaries", "implementation_planes", "parallel_path_ids",
        "risk_observation_ids", "priority_reason",
    )
    for index, item in enumerate(values, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        errors.extend(_require_fields(item, required, item_label, nonempty=False))
        for field in ("claim_id", "question", "exploration_mode", "priority_reason"):
            errors.extend(_string(item, field, item_label))
        claim_id = str(item.get("claim_id") or "")
        if claim_id not in claims:
            errors.append(f"{item_label}: unknown claim_id {claim_id!r}")
        mode = str(item.get("exploration_mode") or "")
        if mode not in modes:
            errors.append(f"{item_label}: unknown exploration_mode {mode!r}")
        review_lenses, lens_errors = _string_array(
            item, "review_lenses", item_label, allow_empty=False,
        )
        boundary_ids, boundary_errors = _string_array(
            item, "architecture_boundaries", item_label, allow_empty=False,
        )
        plane_ids, plane_errors = _string_array(
            item, "implementation_planes", item_label, allow_empty=False,
        )
        path_ids, path_errors = _string_array(item, "parallel_path_ids", item_label)
        risk_ids, risk_errors = _string_array(item, "risk_observation_ids", item_label)
        errors.extend(lens_errors + boundary_errors + plane_errors + path_errors + risk_errors)
        for referenced, known, field in (
            (set(review_lenses), lenses, "review_lenses"),
            (set(boundary_ids), boundaries, "architecture_boundaries"),
            (set(plane_ids), planes, "implementation_planes"),
            (set(path_ids), set(parallel_paths), "parallel_path_ids"),
            (set(risk_ids), set(risks), "risk_observation_ids"),
        ):
            unknown = referenced - known
            if unknown:
                errors.append(f"{item_label}: unknown {field} {sorted(unknown)}")
        if not 1 <= len(review_lenses) <= 3:
            errors.append(f"{item_label}: review_lenses must contain one to three values")
        if mode == "code-to-design risk backtracking" and not risk_ids:
            errors.append(f"{item_label}: code-to-design task requires risk_observation_ids")
        for path_id in set(path_ids) & set(parallel_paths):
            if not set(plane_ids).intersection(parallel_paths[path_id].get("plane_ids", [])):
                errors.append(f"{item_label}: parallel path {path_id} shares no implementation plane")
        for risk_id in set(risk_ids) & set(risks):
            risk = risks[risk_id]
            if not set(boundary_ids).intersection(risk.get("architecture_boundaries", [])):
                errors.append(f"{item_label}: risk {risk_id} shares no architecture boundary")
            if not set(plane_ids).intersection(risk.get("implementation_planes", [])):
                errors.append(f"{item_label}: risk {risk_id} shares no implementation plane")
        valid.append(item)
    return valid


def _validate_coverage_audit(
    coverage: dict[str, Any], *, session_id: str, manifest: dict[str, Any],
    design_groups: dict[str, dict[str, Any]], claims: dict[str, dict[str, Any]],
    risks: dict[str, dict[str, Any]], tasks: dict[str, dict[str, Any]],
    findings: dict[str, dict[str, Any]], rounds: dict[str, dict[str, Any]],
    observed_modes: set[str], modes: set[str], lenses: set[str],
    scoped_claim_ids: set[str],
    architecture_indexes: dict[str, dict[str, dict[str, Any]]], errors: list[str],
) -> list[dict[str, Any]]:
    label = "coverage_audit.json"
    required = (
        "session_id", "design_documents_reviewed", "claims_total", "claims_investigated",
        "rounds_completed", "exploration_modes_completed", "document_groups_total",
        "document_groups_accounted", "code_areas_reviewed", "architecture_boundaries",
        "remaining_scoped_claims", "deferred_claims",
        "false_positive_samples_rechecked", "next_round_tasks", "stop_reason",
    )
    errors.extend(_require_fields(coverage, required, label, nonempty=False))
    _validate_session_owner(coverage, session_id, label, errors)
    known_documents = {
        str(member) for group in manifest.get("design", {}).get("document_groups", [])
        if isinstance(group, dict) for member in group.get("members", []) if isinstance(member, str)
    }
    documents, document_errors = _string_array(
        coverage, "design_documents_reviewed", label,
    )
    code_areas, code_errors = _string_array(coverage, "code_areas_reviewed", label)
    completed_modes, mode_errors = _string_array(
        coverage, "exploration_modes_completed", label,
    )
    samples, sample_errors = _string_array(
        coverage, "false_positive_samples_rechecked", label,
    )
    errors.extend(document_errors + code_errors + mode_errors + sample_errors)
    unknown_documents = set(documents) - known_documents
    if unknown_documents:
        errors.append(f"{label}: unknown design_documents_reviewed {sorted(unknown_documents)}")
    if set(completed_modes) - modes:
        errors.append(f"{label}: unknown exploration_modes_completed {sorted(set(completed_modes) - modes)}")
    if set(completed_modes) != observed_modes:
        errors.append(f"{label}: exploration_modes_completed does not match round evidence")
    if set(samples) - set(findings):
        errors.append(f"{label}: unknown false_positive_samples_rechecked {sorted(set(samples) - set(findings))}")
    errors.extend(_string(coverage, "stop_reason", label))

    investigated_claims = {
        str(finding.get("claim_id") or "") for finding in findings.values()
        if finding.get("claim_id")
    }
    manifest_groups = {
        str(group.get("document_key")) for group in manifest.get("design", {}).get("document_groups", [])
        if isinstance(group, dict) and group.get("document_key")
    }
    exact_counts = {
        "claims_total": len(claims),
        "claims_investigated": len(investigated_claims),
        "rounds_completed": len(rounds),
        "document_groups_total": len(manifest_groups),
        "document_groups_accounted": len(design_groups),
    }
    for field, expected in exact_counts.items():
        value = coverage.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{label}: {field} must be an integer")
        elif value != expected:
            errors.append(f"{label}: {field} must equal {expected}, got {value}")

    boundary_entries = coverage.get("architecture_boundaries")
    if not isinstance(boundary_entries, list):
        errors.append(f"{label}: architecture_boundaries must be an array")
        boundary_entries = []
    audit_boundaries: dict[str, dict[str, Any]] = {}
    known_boundaries = set(architecture_indexes["boundaries"])
    for index, item in enumerate(boundary_entries, start=1):
        item_label = f"{label} architecture_boundaries[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        errors.extend(_require_fields(item, ("boundary_id", "status", "evidence"), item_label))
        for field in ("boundary_id", "status", "evidence"):
            errors.extend(_string(item, field, item_label))
        boundary_id = str(item.get("boundary_id") or "")
        if boundary_id in audit_boundaries:
            errors.append(f"{item_label}: duplicate boundary_id {boundary_id}")
        elif boundary_id:
            audit_boundaries[boundary_id] = item
        if boundary_id not in known_boundaries:
            errors.append(f"{item_label}: unknown boundary_id {boundary_id!r}")
        if item.get("status") not in BOUNDARY_DISPOSITIONS:
            errors.append(f"{item_label}: invalid status {item.get('status')!r}")
    high_boundaries = {
        boundary_id for boundary_id, item in architecture_indexes["boundaries"].items()
        if item.get("risk") == "high"
    }
    if high_boundaries - set(audit_boundaries):
        errors.append(
            f"{label}: missing high-risk architecture boundaries "
            f"{sorted(high_boundaries - set(audit_boundaries))}"
        )

    deferred_values = coverage.get("deferred_claims")
    if not isinstance(deferred_values, list):
        errors.append(f"{label}: deferred_claims must be an array")
        deferred_values = []
    deferred_claims: set[str] = set()
    for index, item in enumerate(deferred_values, start=1):
        item_label = f"{label} deferred_claims[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        errors.extend(_require_fields(item, ("claim_id", "task_id", "reason"), item_label))
        for field in ("claim_id", "task_id", "reason"):
            errors.extend(_string(item, field, item_label))
        claim_id = str(item.get("claim_id") or "")
        task_id = str(item.get("task_id") or "")
        if claim_id in deferred_claims:
            errors.append(f"{item_label}: duplicate claim_id {claim_id}")
        elif claim_id:
            deferred_claims.add(claim_id)
        if claim_id not in claims:
            errors.append(f"{item_label}: unknown claim_id {claim_id!r}")
        elif claim_id not in scoped_claim_ids:
            errors.append(f"{item_label}: deferred claim is outside current accepted scope")
        task = tasks.get(task_id)
        if task is None:
            errors.append(f"{item_label}: unknown task_id {task_id!r}")
        elif task.get("claim_id") != claim_id:
            errors.append(f"{item_label}: task/claim mismatch")
        elif task.get("status") != "deferred":
            errors.append(f"{item_label}: linked task must be deferred")
        else:
            errors.extend(hm.validate_task_defer_evidence(task, item_label))

    remaining_values = coverage.get("remaining_scoped_claims")
    if not isinstance(remaining_values, list):
        errors.append(f"{label}: remaining_scoped_claims must be an array")
        remaining_values = []
    remaining_claims: set[str] = set()
    for index, item in enumerate(remaining_values, start=1):
        item_label = f"{label} remaining_scoped_claims[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        errors.extend(_require_fields(item, ("claim_id", "reason"), item_label))
        for field in ("claim_id", "reason"):
            errors.extend(_string(item, field, item_label))
        claim_id = str(item.get("claim_id") or "")
        if claim_id in remaining_claims:
            errors.append(f"{item_label}: duplicate claim_id {claim_id}")
        elif claim_id:
            remaining_claims.add(claim_id)
        if claim_id not in scoped_claim_ids:
            errors.append(f"{item_label}: claim must reference the current accepted scope")

    findings_by_task: dict[str, list[dict[str, Any]]] = {}
    for finding in findings.values():
        findings_by_task.setdefault(str(finding.get("task_id") or ""), []).append(finding)
    investigated_scoped: set[str] = set()
    for task_id, task in tasks.items():
        claim_id = str(task.get("claim_id") or "")
        if (
            claim_id in scoped_claim_ids
            and task.get("status") == "complete" and findings_by_task.get(task_id)
        ):
            investigated_scoped.add(claim_id)
    expected_remaining = {
        claim_id for claim_id in scoped_claim_ids
        if claim_id not in investigated_scoped and claim_id not in deferred_claims
    }
    if remaining_claims != expected_remaining:
        errors.append(
            f"{label}: remaining_scoped_claims must equal uninvestigated, "
            f"non-deferred scoped claims {sorted(expected_remaining)}"
        )

    next_tasks = _validate_next_tasks(
        coverage.get("next_round_tasks"), claims=claims, risks=risks,
        modes=modes, lenses=lenses, boundaries=known_boundaries,
        planes=set(architecture_indexes["planes"]),
        parallel_paths=architecture_indexes["parallel_paths"], errors=errors,
    )
    next_claims = {str(item.get("claim_id") or "") for item in next_tasks}
    if remaining_claims - next_claims:
        errors.append(
            f"{label}: remaining scoped claims lack next_round_tasks "
            f"{sorted(remaining_claims - next_claims)}"
        )

    missing_modes = modes - observed_modes
    planned_modes = {
        str(item.get("exploration_mode") or "") for item in next_tasks
    }
    if missing_modes - planned_modes:
        errors.append(
            f"{label}: missing exploration modes lack next_round_tasks "
            f"{sorted(missing_modes - planned_modes)}"
        )

    completed_boundaries = {
        boundary
        for task_id, task in tasks.items()
        if task.get("status") == "complete" and findings_by_task.get(task_id)
        for boundary in task.get("architecture_boundaries", [])
        if isinstance(boundary, str)
    }
    planned_boundaries = {
        boundary for item in next_tasks
        for boundary in item.get("architecture_boundaries", [])
        if isinstance(boundary, str)
    }
    missing_high_boundaries = high_boundaries - completed_boundaries
    if missing_high_boundaries - planned_boundaries:
        errors.append(
            f"{label}: high-risk boundaries lack completed or planned evidence "
            f"{sorted(missing_high_boundaries - planned_boundaries)}"
        )

    for path_id, path in architecture_indexes["parallel_paths"].items():
        required_planes = set(path.get("plane_ids", []))
        completed_planes = {
            plane for task_id, task in tasks.items()
            if task.get("status") == "complete" and findings_by_task.get(task_id)
            and path_id in task.get("parallel_path_ids", [])
            for plane in task.get("implementation_planes", [])
            if isinstance(plane, str)
        }
        missing_planes = required_planes - completed_planes
        planned_planes = {
            plane for item in next_tasks if path_id in item.get("parallel_path_ids", [])
            for plane in item.get("implementation_planes", []) if isinstance(plane, str)
        }
        if missing_planes - planned_planes:
            errors.append(
                f"{label}: parallel path {path_id} has unaccounted planes "
                f"{sorted(missing_planes - planned_planes)}"
            )
    return next_tasks


def validate_coverage_stage(
    *, root: Path, code_root: Path, design_root: Path, session_id: str,
    manifest: dict[str, Any], contract: dict[str, Any], architecture: dict[str, Any],
    design_coverage: dict[str, Any], semantic: dict[str, Any], coverage: dict[str, Any],
    claims: dict[str, dict[str, Any]], risks: dict[str, dict[str, Any]],
    tasks: dict[str, dict[str, Any]], findings: dict[str, dict[str, Any]],
    rounds: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    errors, architecture_indexes = validate_architecture(architecture, session_id)
    _validate_claim_sessions(claims, session_id, errors)
    _validate_risks(risks, session_id, root, code_root, errors)
    _validate_tasks_typed(tasks, session_id, root, errors)
    scoped_claim_ids = _validated_claim_review_scope(
        root, session_id, claims, tasks, errors,
    )
    iteration_policy = contract.get("iteration_policy", {})
    max_tasks_per_round = iteration_policy.get("max_tasks_per_round")
    if (
        not isinstance(max_tasks_per_round, int)
        or isinstance(max_tasks_per_round, bool)
        or max_tasks_per_round < 1
    ):
        errors.append("agent_loop_contract.json: max_tasks_per_round must be a positive integer")
        max_tasks_per_round = 1
    _validate_round_frontier(
        tasks, list(rounds.values()), max_tasks_per_round, errors,
    )
    _validate_task_finding_lifecycle(tasks, findings, session_id, errors)
    for finding_id, finding in findings.items():
        errors.extend(hm.validate_item(
            finding, artifact_type="finding", identifier=finding_id,
            session_id=session_id, code_root=code_root, design_root=design_root,
        ))
        task = tasks.get(str(finding.get("task_id") or ""))
        if task and not set(finding.get("review_lenses", [])).issubset(
            set(task.get("review_lenses", []))
        ):
            errors.append(f"finding {finding_id}: review_lenses are absent from linked task")

    _validate_session_owner(design_coverage, session_id, "design_coverage.json", errors)
    raw_groups = design_coverage.get("document_groups")
    if not isinstance(raw_groups, list):
        errors.append("design_coverage.json: document_groups must be an array")
        raw_groups = []
    design_groups = _unique_ids(
        raw_groups, "document_key", "design_coverage document_groups", errors,
    )
    manifest_groups = {
        str(group.get("document_key")): group
        for group in manifest.get("design", {}).get("document_groups", [])
        if isinstance(group, dict) and group.get("document_key")
    }
    for group_id, group in design_groups.items():
        if group_id not in manifest_groups:
            errors.append(f"design coverage group {group_id}: unknown document_key")
        claim_ids, item_errors = _string_array(group, "claim_ids", f"design coverage group {group_id}")
        errors.extend(item_errors)
        unknown_claims = set(claim_ids) - set(claims)
        if unknown_claims:
            errors.append(f"design coverage group {group_id}: unknown claim_ids {sorted(unknown_claims)}")

    coverage_contract = contract.get("coverage_contract")
    if not isinstance(coverage_contract, dict):
        errors.append("agent_loop_contract.json: coverage_contract must be an object")
        coverage_contract = {}
    expected_lenses = {
        value for value in coverage_contract.get("portfolio_lenses", []) if isinstance(value, str)
    }
    expected_modes = {
        value for value in coverage_contract.get("exploration_modes", []) if isinstance(value, str)
    }
    known = {
        "modes": expected_modes, "groups": set(manifest_groups),
        "boundaries": set(architecture_indexes["boundaries"]),
        "planes": set(architecture_indexes["planes"]), "lenses": expected_lenses,
        "claims": set(claims), "tasks": set(tasks), "findings": set(findings),
    }
    observed_modes = _validate_rounds(
        rounds, session_id, known, tasks, findings, errors,
    )
    for round_id, item in rounds.items():
        round_claims = set(item.get("claim_ids", []))
        round_tasks = set(item.get("task_ids", []))
        for task_id in round_tasks & set(tasks):
            if tasks[task_id].get("claim_id") not in round_claims:
                errors.append(f"investigation round {round_id}: task {task_id} claim is absent")
        for finding_id in set(item.get("finding_ids", [])) & set(findings):
            finding = findings[finding_id]
            if finding.get("task_id") not in round_tasks:
                errors.append(f"investigation round {round_id}: finding {finding_id} task is absent")
            if finding.get("claim_id") not in round_claims:
                errors.append(f"investigation round {round_id}: finding {finding_id} claim is absent")

    _validate_semantic_coverage(
        semantic, session_id, expected_lenses, design_groups, claims, tasks, findings,
        set(architecture_indexes["boundaries"]), errors,
    )
    next_tasks = _validate_coverage_audit(
        coverage, session_id=session_id, manifest=manifest, design_groups=design_groups,
        claims=claims, risks=risks, tasks=tasks, findings=findings, rounds=rounds,
        observed_modes=observed_modes, modes=expected_modes, lenses=expected_lenses,
        scoped_claim_ids=scoped_claim_ids,
        architecture_indexes=architecture_indexes, errors=errors,
    )
    findings_by_task = {
        str(finding.get("task_id") or "") for finding in findings.values()
        if finding.get("task_id")
    }
    unfinished_tasks = {
        task_id for task_id, task in tasks.items()
        if task.get("status") in {"pending", "in_progress"}
    }
    completed_boundaries = {
        str(boundary)
        for task_id, task in tasks.items()
        if task.get("status") == "complete" and task_id in findings_by_task
        for boundary in task.get("architecture_boundaries", []) if boundary
    }
    high_boundaries = {
        boundary_id for boundary_id, item in architecture_indexes["boundaries"].items()
        if item.get("risk") == "high"
    }
    audit_boundaries = {
        str(item.get("boundary_id") or ""): item
        for item in coverage.get("architecture_boundaries", [])
        if isinstance(item, dict) and item.get("boundary_id")
    }
    high_boundaries_closed = all(
        boundary_id in completed_boundaries
        and audit_boundaries.get(boundary_id, {}).get("status") == "investigated"
        for boundary_id in high_boundaries
    )
    parallel_paths_closed = True
    for path_id, path in architecture_indexes["parallel_paths"].items():
        completed_planes = {
            str(plane)
            for task_id, task in tasks.items()
            if task.get("status") == "complete" and task_id in findings_by_task
            and path_id in task.get("parallel_path_ids", [])
            for plane in task.get("implementation_planes", []) if plane
        }
        if set(path.get("plane_ids", [])) - completed_planes:
            parallel_paths_closed = False
    remaining_scoped = coverage.get("remaining_scoped_claims")
    closed = (
        isinstance(remaining_scoped, list) and not remaining_scoped
        and not next_tasks
        and not unfinished_tasks
        and observed_modes == expected_modes
        and high_boundaries_closed
        and parallel_paths_closed
    )
    return errors, {
        "claims": len(claims), "risks": len(risks), "tasks": len(tasks),
        "findings": len(findings), "rounds": len(rounds),
        "semantic_lenses": len(semantic.get("lenses", [])) if isinstance(semantic.get("lenses"), list) else 0,
        "next_round_tasks": len(next_tasks),
        "scoped_claims": len(scoped_claim_ids),
        "unfinished_tasks": len(unfinished_tasks),
        "closed": closed,
    }


def _stage_inputs(root: Path, stage: str) -> list[Path]:
    common = [
        root / "workspace_manifest.json", root / "agent_loop_contract.json",
        root / "architecture_map.json",
    ]
    if stage == "architecture":
        return common
    common.extend([
        root / "risk_sweep_plan.json",
        root / "design_agent_manifest.json", root / "design_coverage.json",
        root / "claim_review_scope.json", root / "design_claim_review.json",
        root / "design_claims.jsonl",
        root / "risk_observations.jsonl",
        root / "investigation_tasks.jsonl", root / "investigation_findings.jsonl",
        root / "investigation_rounds.jsonl",
    ])
    if stage == "coverage":
        common.extend([
            root / "semantic_coverage.json", root / "coverage_audit.json",
        ])
    return common


def _input_digests(root: Path, paths: list[Path]) -> tuple[dict[str, str | None], str]:
    values: dict[str, str | None] = {}
    for path in paths:
        try:
            name = str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            name = str(path.resolve())
        values[name] = ac.sha256_file(path) if path.is_file() else None
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return values, hashlib.sha256(encoded).hexdigest()


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    trace_path = log_root / "trace" / f"{args.stage}_validation.json"
    inputs = _stage_inputs(root, args.stage)
    input_digests, combined_digest = _input_digests(root, inputs)
    errors: list[str] = []
    try:
        errors.extend(ac.session_path_errors(
            root, code_root=code_root, design_root=design_root,
            result_root=result_root, log_root=log_root,
        ))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        errors.append(f"prepared session path validation failed: {exc}")

    manifest = _load_object(root / "workspace_manifest.json", "workspace_manifest.json", errors)
    contract = _load_object(root / "agent_loop_contract.json", "agent_loop_contract.json", errors)
    state = _load_object(root / "agent_loop_state.json", "agent_loop_state.json", errors)
    architecture = _load_object(root / "architecture_map.json", "architecture_map.json", errors)
    session_id = str(state.get("session_id") or "")
    if not session_id:
        errors.append("agent_loop_state.json: missing/non-string session_id")
    if manifest.get("session_id") != session_id:
        errors.append("workspace_manifest.json: session_id does not match current session")
    if contract.get("session", {}).get("session_id") != session_id:
        errors.append("agent_loop_contract.json: session_id does not match current session")

    metrics: dict[str, Any] = {}
    if args.stage == "architecture":
        stage_errors, indexes = validate_architecture(architecture, session_id)
        errors.extend(stage_errors)
        metrics = {
            "implementation_planes": len(indexes["planes"]),
            "integration_boundaries": len(indexes["boundaries"]),
            "parallel_behavior_paths": len(indexes["parallel_paths"]),
        }
    else:
        claims, _ = _load_index(
            root / "design_claims.jsonl", "claim_id", "design_claims.jsonl", errors,
        )
        risks, _ = _load_index(
            root / "risk_observations.jsonl", "observation_id", "risk_observations.jsonl", errors,
        )
        tasks, _ = _load_index(
            root / "investigation_tasks.jsonl", "task_id", "investigation_tasks.jsonl", errors,
        )
        findings, _ = _load_index(
            root / "investigation_findings.jsonl", "finding_id", "investigation_findings.jsonl", errors,
        )
        rounds, round_values = _load_index(
            root / "investigation_rounds.jsonl", "round_id", "investigation_rounds.jsonl", errors,
        )
        if args.stage == "task":
            stage_errors, metrics = validate_task_stage(
                root=root, code_root=code_root, session_id=session_id, contract=contract,
                architecture=architecture, claims=claims, risks=risks, tasks=tasks,
                findings=findings, rounds=round_values,
            )
        else:
            design_coverage = _load_object(
                root / "design_coverage.json", "design_coverage.json", errors,
            )
            semantic = _load_object(
                root / "semantic_coverage.json", "semantic_coverage.json", errors,
            )
            coverage = _load_object(
                root / "coverage_audit.json", "coverage_audit.json", errors,
            )
            stage_errors, metrics = validate_coverage_stage(
                root=root, code_root=code_root, design_root=design_root,
                session_id=session_id, manifest=manifest, contract=contract,
                architecture=architecture, design_coverage=design_coverage,
                semantic=semantic, coverage=coverage, claims=claims, risks=risks,
                tasks=tasks, findings=findings, rounds=rounds,
            )
        errors.extend(stage_errors)

    trace = {
        "stage": args.stage,
        "session_id": session_id,
        "validated_at": ac.now_iso(),
        "passed": not errors,
        "closed": (
            not errors and bool(metrics.get("closed"))
            if args.stage == "coverage" else None
        ),
        "input_digests": input_digests,
        "combined_input_sha256": combined_digest,
        "metrics": metrics,
        "errors": errors,
    }
    ac.save_json(trace_path, trace)
    print(json.dumps({
        "stage": args.stage, "passed": not errors, "error_count": len(errors),
        "trace": str(trace_path), "errors": errors,
    }, ensure_ascii=False))
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate one semantic-neutral agent artifact stage and write a digest-bound trace."
    )
    parser.add_argument("--stage", choices=sorted(STAGES), required=True)
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
