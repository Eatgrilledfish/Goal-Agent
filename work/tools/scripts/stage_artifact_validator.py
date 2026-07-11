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


STAGES = {"architecture", "task-plan", "task-lifecycle", "coverage"}
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
TASK_LIFECYCLE_FIELDS = {"status", "defer_reason", "defer_evidence"}
ROUND_LIFECYCLE_FIELDS = {"finding_ids", "outcome", "next_strategy"}
LENS_DISPOSITIONS = {"investigated", "inapplicable", "gap_recorded"}
BOUNDARY_DISPOSITIONS = {"investigated", "gap_recorded"}
GAP_KINDS = {
    "inventory", "claim_review_expansion", "lens", "architecture_boundary",
    "parallel_path", "exploration_mode", "frontier_claim", "critic_request", "other",
}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _supplement_request_sha256(request: dict[str, Any]) -> str:
    return _canonical_sha256({
        "source_gap_ids": request.get("source_gap_ids"),
        "prior_task_ids": request.get("prior_task_ids"),
        "prior_tasks": request.get("prior_tasks"),
        "prior_rounds": request.get("prior_rounds"),
        "task_specs": request.get("task_specs"),
    })


def coverage_provenance_sha256(root: Path) -> str:
    """Digest the append-only merge history that coverage semantics consume.

    Later phase checkpoints may be appended after coverage validation, so the
    entire run ledger cannot be an exact stage input.  This stable projection
    changes only when finding/probe/critic provenance or the one supplement
    request changes, which are precisely the ordering facts validated here.
    """
    events, parse_errors = ac.load_jsonl(root / "agent_run_ledger.jsonl")
    projected = [
        event for event in events
        if (
            event.get("event") == "handoff_merge"
            and event.get("artifact_type") in {"finding", "probe", "critic"}
        ) or event.get("event") == "coverage_supplement_request"
    ]
    return _canonical_sha256({"events": projected, "parse_errors": parse_errors})


def claim_review_provenance_sha256(root: Path) -> str:
    """Digest only claim-review trace facts consumed by coverage validation."""
    manifest = ac.load_json(root / "workspace_manifest.json")
    log_root_value = manifest.get("paths", {}).get("log_root") \
        if isinstance(manifest, dict) else None
    trace_path = (
        Path(str(log_root_value)).resolve() / "trace" / "claim_review_validation.json"
        if isinstance(log_root_value, str) and log_root_value else
        root.parent / "trace" / "claim_review_validation.json"
    )
    trace = ac.load_json(trace_path) if trace_path.is_file() else {}
    return _canonical_sha256({
        "passed": trace.get("passed") if isinstance(trace, dict) else None,
        "session_id": trace.get("session_id") if isinstance(trace, dict) else None,
        "accepted_claim_ids": (
            trace.get("accepted_claim_ids") if isinstance(trace, dict) else None
        ),
        "expansion_requests": (
            trace.get("expansion_requests") if isinstance(trace, dict) else None
        ),
    })


def claim_obligation_sha256(claim: dict[str, Any]) -> str:
    """Return the task binding for one claim's atomic obligation.

    Including the claim ID prevents two claims with identical prose from being
    interchangeable.  The validator deliberately does not interpret the prose.
    """
    claim_id = claim.get("claim_id")
    obligation = claim.get("obligation")
    if not isinstance(claim_id, str) or not claim_id.strip():
        return ""
    if not isinstance(obligation, str) or not obligation.strip():
        return ""
    return _canonical_sha256({"claim_id": claim_id, "obligation": obligation})


def _task_plan_projection(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in task.items()
        if key not in TASK_LIFECYCLE_FIELDS
    }


def _round_plan_projection(round_item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in round_item.items()
        if key not in ROUND_LIFECYCLE_FIELDS
    }


def _task_round_binding(
    task_id: str, rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    bindings: list[dict[str, Any]] = []
    for round_index, round_item in enumerate(rounds):
        task_ids = round_item.get("task_ids")
        if not isinstance(task_ids, list):
            continue
        for position, member in enumerate(task_ids):
            if member == task_id:
                bindings.append({
                    "round_id": round_item.get("round_id"),
                    "round_index": round_index,
                    "position": position,
                })
    return {"bindings": bindings}


def task_plan_digest(
    task: dict[str, Any], claim: dict[str, Any] | None,
    rounds: list[dict[str, Any]],
) -> str:
    """Digest only immutable candidate-plan identity, never lifecycle state."""
    return _canonical_sha256({
        "task": _task_plan_projection(task),
        "claim_sha256": _canonical_sha256(claim) if isinstance(claim, dict) else "",
        "round_binding": _task_round_binding(str(task.get("task_id") or ""), rounds),
    })


def task_lifecycle_digest(
    task: dict[str, Any], findings: list[dict[str, Any]],
) -> str:
    """Digest one candidate's mutable status and finding identity only."""
    identities = sorted(
        ({
            "finding_id": finding.get("finding_id"),
            "session_id": finding.get("session_id"),
            "task_id": finding.get("task_id"),
            "claim_id": finding.get("claim_id"),
        } for finding in findings),
        key=lambda item: str(item.get("finding_id") or ""),
    )
    return _canonical_sha256({
        "task_id": task.get("task_id"),
        "session_id": task.get("session_id"),
        "claim_id": task.get("claim_id"),
        "status": task.get("status"),
        "defer_reason": task.get("defer_reason"),
        "defer_evidence": task.get("defer_evidence"),
        "findings": identities,
    })


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
    errors: list[str],
) -> set[str]:
    """Bind the task frontier to the currently accepted design-only claim review."""
    scope_object = _load_object(
        root / "claim_review_scope.json", "claim_review_scope.json", errors,
    )
    _validate_session_owner(scope_object, session_id, "claim_review_scope.json", errors)
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
    raw_reviews = review.get("claim_reviews")
    if not isinstance(raw_reviews, list):
        errors.append("design_claim_review.json: claim_reviews must be an array")
        raw_reviews = []
    reviewed_scope: set[str] = set()
    currently_accepted: set[str] = set()
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
        claim = claims.get(claim_id)
        if claim is not None and item.get("claim_sha256") != _canonical_sha256(claim):
            errors.append(f"{label}: claim_sha256 does not match current claim")
        source_ref = claim.get("source_ref") if isinstance(
            claim, dict
        ) and isinstance(claim.get("source_ref"), dict) else {}
        if claim is not None and item.get("source_sha256") != source_ref.get("source_sha256"):
            errors.append(f"{label}: source_sha256 does not match current claim source")
        if item.get("spec_critic_prompt_version") != "spec-critic-v2":
            errors.append(f"{label}: spec_critic_prompt_version is not current")
        if item.get("decision") == "accept" and claim is not None:
            currently_accepted.add(claim_id)
    if reviewed_scope != scope:
        errors.append(
            "design_claim_review.json: claim review membership does not exactly match "
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
    if claim_trace.get("passed") is not True:
        errors.append("claim_review_validation.json: current scoped claim review has not passed")
    if claim_trace.get("session_id") != session_id:
        errors.append("claim_review_validation.json: session_id does not match current session")
    accepted_claim_ids = claim_trace.get("accepted_claim_ids")
    if not isinstance(accepted_claim_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in accepted_claim_ids
    ):
        errors.append("claim_review_validation.json: accepted_claim_ids must be an array of strings")
        accepted_claim_id_set: set[str] = set()
    else:
        accepted_claim_id_set = set(accepted_claim_ids)
    if accepted_claim_id_set != currently_accepted:
        errors.append(
            "claim_review_validation.json: accepted claim IDs do not match current claim decisions"
        )
    if accepted_claim_id_set - scope:
        errors.append("claim_review_validation.json: accepted claim IDs are outside current scope")

    return accepted_claim_id_set


def _candidate_error(
    errors_by_task: dict[str, list[str]], task_id: str, error: str,
) -> None:
    errors_by_task.setdefault(task_id, []).append(error)


def _validate_round_plan(
    tasks: dict[str, dict[str, Any]], rounds: list[dict[str, Any]],
    max_tasks_per_round: int, global_errors: list[str],
    errors_by_task: dict[str, list[str]],
) -> list[list[str]]:
    """Validate immutable membership/order without reading task lifecycle state."""
    memberships: dict[str, list[int]] = {task_id: [] for task_id in tasks}
    ordered_task_ids: list[list[str]] = []
    for round_index, item in enumerate(rounds, start=1):
        round_id = str(item.get("round_id") or f"#{round_index}")
        label = f"investigation round {round_id}"
        task_ids, item_errors = _string_array(
            item, "task_ids", label, allow_empty=False,
        )
        global_errors.extend(item_errors)
        if len(task_ids) > max_tasks_per_round:
            global_errors.append(
                f"{label}: task_ids exceeds max_tasks_per_round={max_tasks_per_round}"
            )
        ordered_task_ids.append(task_ids)
        raw_claim_ids = item.get("claim_ids")
        round_claims = {
            value for value in raw_claim_ids
            if isinstance(value, str) and value
        } if isinstance(raw_claim_ids, list) else set()
        for task_id in task_ids:
            if task_id not in tasks:
                global_errors.append(f"{label}: unknown task_id {task_id}")
                continue
            memberships[task_id].append(round_index - 1)
            claim_id = str(tasks[task_id].get("claim_id") or "")
            if claim_id not in round_claims:
                _candidate_error(
                    errors_by_task, task_id,
                    f"investigation task {task_id}: claim_id {claim_id!r} is absent from {round_id}",
                )

    for task_id, positions in memberships.items():
        if len(positions) != 1:
            _candidate_error(
                errors_by_task, task_id,
                f"investigation task {task_id}: must belong to exactly one investigation round; "
                f"found {len(positions)}",
            )
    return ordered_task_ids


def _validate_round_lifecycle(
    tasks: dict[str, dict[str, Any]], findings: dict[str, dict[str, Any]],
    rounds: list[dict[str, Any]], global_errors: list[str],
    errors_by_task: dict[str, list[str]],
) -> str:
    """Require ordered rounds to drain while isolating later candidates."""
    ordered_task_ids: list[list[str]] = []
    round_ids: list[str] = []
    finding_memberships: dict[str, list[str]] = {finding_id: [] for finding_id in findings}
    for round_index, item in enumerate(rounds, start=1):
        round_id = str(item.get("round_id") or f"#{round_index}")
        round_ids.append(round_id)
        task_ids = item.get("task_ids")
        ordered = (
            [value for value in task_ids if isinstance(value, str) and value]
            if isinstance(task_ids, list) else []
        )
        ordered_task_ids.append(ordered)
        finding_ids = item.get("finding_ids")
        listed_findings = (
            [value for value in finding_ids if isinstance(value, str) and value]
            if isinstance(finding_ids, list) else []
        )
        for finding_id in listed_findings:
            finding = findings.get(finding_id)
            if finding is None:
                global_errors.append(
                    f"investigation round {round_id}: unknown finding_id {finding_id}"
                )
                continue
            finding_memberships[finding_id].append(round_id)
            task_id = str(finding.get("task_id") or "")
            if task_id in tasks and task_id not in ordered:
                _candidate_error(
                    errors_by_task, task_id,
                    f"finding {finding_id}: linked task {task_id} is absent from {round_id}",
                )
    for finding_id, finding in findings.items():
        task_id = str(finding.get("task_id") or "")
        memberships = finding_memberships.get(finding_id, [])
        if task_id in tasks and len(memberships) != 1:
            _candidate_error(
                errors_by_task, task_id,
                f"finding {finding_id}: must belong to exactly one investigation round; "
                f"found {len(memberships)}",
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
            for later_task_id in ordered_task_ids[later_index]:
                if later_task_id not in tasks:
                    continue
                _candidate_error(
                    errors_by_task, later_task_id,
                    f"investigation round {later_round_id}: cannot exist while earlier round "
                    f"{round_ids[round_index]} has open tasks {open_ids}",
                )
    return earliest_open


def _validate_task_finding_lifecycle(
    tasks: dict[str, dict[str, Any]], findings: dict[str, dict[str, Any]],
    session_id: str, global_errors: list[str],
    errors_by_task: dict[str, list[str]],
) -> dict[str, list[dict[str, Any]]]:
    findings_by_task: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for finding_id, finding in findings.items():
        task_id = finding.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            global_errors.append(f"finding {finding_id}: missing/non-string task_id")
            continue
        findings_by_task.setdefault(task_id, []).append((finding_id, finding))
        task = tasks.get(task_id)
        if task is None:
            global_errors.append(f"finding {finding_id}: unknown task_id {task_id}")
            continue
        local_errors = errors_by_task.setdefault(task_id, [])
        _validate_session_owner(finding, session_id, f"finding {finding_id}", local_errors)
        if task.get("status") != "complete":
            local_errors.append(
                f"finding {finding_id}: linked task {task_id} must have status complete"
            )
        if finding.get("claim_id") != task.get("claim_id"):
            local_errors.append(f"finding {finding_id}: task/finding claim_id mismatch")

    for task_id, linked in findings_by_task.items():
        if task_id in tasks and len(linked) != 1:
            _candidate_error(
                errors_by_task, task_id,
                f"task {task_id}: expected exactly one finding, found {len(linked)}",
            )
    for task_id, task in tasks.items():
        local_errors = errors_by_task.setdefault(task_id, [])
        _validate_session_owner(task, session_id, f"investigation task {task_id}", local_errors)
        status = task.get("status")
        if status not in TASK_STATUSES:
            local_errors.append(f"investigation task {task_id}: invalid status {status!r}")
        local_errors.extend(hm.validate_task_defer_evidence(
            task, f"investigation task {task_id}",
        ))
        linked = findings_by_task.get(task_id, [])
        if status == "complete" and len(linked) != 1:
            local_errors.append(f"complete task {task_id}: expected exactly one linked finding")
        if status in {"pending", "in_progress", "deferred"} and linked:
            local_errors.append(
                f"task {task_id}: status {status} is inconsistent with a finding"
            )
    return {
        task_id: [finding for _, finding in linked]
        for task_id, linked in findings_by_task.items()
    }


def _task_plan_contract_errors(
    task: dict[str, Any], *, task_id: str, session_id: str, root: Path,
    claims: dict[str, dict[str, Any]], accepted_claim_ids: set[str],
) -> list[str]:
    """Validate one atomic candidate without scoring its semantic quality."""
    label = f"investigation task {task_id}"
    errors: list[str] = []
    typed_task = dict(task)
    typed_task["status"] = "pending"
    typed_task.pop("defer_evidence", None)
    typed_task["defer_reason"] = ""
    errors.extend(hm.validate_item(
        typed_task, artifact_type="task", identifier=task_id,
        session_id=session_id,
    ))
    errors.extend(hm._context_errors(typed_task, "task", root, f"task ({task_id})"))
    for field in ("claim_branch", "hypothesis", "obligation_sha256"):
        errors.extend(_string(task, field, label))
    claim_id = str(task.get("claim_id") or "")
    claim = claims.get(claim_id)
    if claim_id not in accepted_claim_ids:
        errors.append(
            f"{label}: claim_id {claim_id!r} is outside accepted claim review scope"
        )
    expected_obligation_digest = claim_obligation_sha256(claim) if claim else ""
    if claim is not None and not expected_obligation_digest:
        errors.append(f"{label}: linked claim lacks one non-empty obligation")
    supplied_digest = task.get("obligation_sha256")
    if isinstance(supplied_digest, str) and (
        len(supplied_digest) != 64
        or any(character not in "0123456789abcdef" for character in supplied_digest)
    ):
        errors.append(f"{label}: obligation_sha256 must be a lowercase SHA-256 digest")
    if expected_obligation_digest and supplied_digest != expected_obligation_digest:
        errors.append(
            f"{label}: obligation_sha256 does not match the linked claim obligation"
        )
    return errors


def task_plan_snapshot_sha256(
    root: Path, *, contract: dict[str, Any], architecture: dict[str, Any],
    claims: dict[str, dict[str, Any]], risks: dict[str, dict[str, Any]],
    tasks: dict[str, dict[str, Any]], rounds: list[dict[str, Any]],
) -> str:
    """Digest stable planning inputs while ignoring lifecycle-only mutations."""
    selected_claim_ids = {
        str(task.get("claim_id") or "") for task in tasks.values()
        if task.get("claim_id")
    }
    selected_risk_ids = {
        risk_id for task in tasks.values()
        for risk_id in task.get("risk_observation_ids", [])
        if isinstance(risk_id, str) and risk_id
    }
    scope_path = root / "claim_review_scope.json"
    review_path = root / "design_claim_review.json"
    trace_path: Path | None = None
    manifest_path = root / "workspace_manifest.json"
    if manifest_path.is_file():
        manifest = ac.load_json(manifest_path)
        log_root = manifest.get("paths", {}).get("log_root") if isinstance(manifest, dict) else None
        if isinstance(log_root, str) and log_root:
            trace_path = Path(log_root).resolve() / "trace" / "claim_review_validation.json"
    scope = ac.load_json(scope_path) if scope_path.is_file() else {}
    review = ac.load_json(review_path) if review_path.is_file() else {}
    claim_trace = ac.load_json(trace_path) if trace_path and trace_path.is_file() else {}
    history_path = root / "coverage_supplement_history.json"
    supplement_history = ac.load_json(history_path) if history_path.is_file() else {}
    review_entries = {
        str(item.get("claim_id") or ""): item
        for item in review.get("claim_reviews", [])
        if isinstance(item, dict) and item.get("claim_id") in selected_claim_ids
    } if isinstance(review, dict) else {}
    scope_members = {
        value for value in scope.get("claim_ids", [])
        if isinstance(value, str) and value in selected_claim_ids
    } if isinstance(scope, dict) else set()
    accepted_members = {
        value for value in claim_trace.get("accepted_claim_ids", [])
        if isinstance(value, str) and value in selected_claim_ids
    } if isinstance(claim_trace, dict) else set()
    return _canonical_sha256({
        "contract": contract,
        "architecture": architecture,
        "claims": {key: claims.get(key) for key in sorted(selected_claim_ids)},
        "claim_review_bindings": {
            "scope_members": sorted(scope_members),
            "accepted_members": sorted(accepted_members),
            "reviews": {key: review_entries[key] for key in sorted(review_entries)},
        },
        "risks": {key: risks.get(key) for key in sorted(selected_risk_ids)},
        "tasks": {
            key: _task_plan_projection(tasks[key]) for key in sorted(tasks)
        },
        "rounds": [_round_plan_projection(item) for item in rounds],
        "coverage_supplement_history": supplement_history,
    })


def task_lifecycle_snapshot_sha256(
    *, tasks: dict[str, dict[str, Any]], findings: dict[str, dict[str, Any]],
    rounds: list[dict[str, Any]],
) -> str:
    """Digest mutable task/finding accounting, independent of the task plan."""
    finding_identities = {
        key: {
            "finding_id": item.get("finding_id"),
            "session_id": item.get("session_id"),
            "task_id": item.get("task_id"),
            "claim_id": item.get("claim_id"),
        }
        for key, item in sorted(findings.items())
    }
    return _canonical_sha256({
        "tasks": {
            key: {
                "task_id": item.get("task_id"),
                "session_id": item.get("session_id"),
                "claim_id": item.get("claim_id"),
                "status": item.get("status"),
                "defer_reason": item.get("defer_reason"),
                "defer_evidence": item.get("defer_evidence"),
            }
            for key, item in sorted(tasks.items())
        },
        "findings": finding_identities,
        "rounds": [
            {
                "round_id": item.get("round_id"),
                "task_ids": item.get("task_ids"),
                "finding_ids": item.get("finding_ids"),
            }
            for item in rounds
        ],
    })


def validate_task_plan_stage(
    *, root: Path, code_root: Path, session_id: str, contract: dict[str, Any],
    architecture: dict[str, Any], claims: dict[str, dict[str, Any]],
    risks: dict[str, dict[str, Any]], tasks: dict[str, dict[str, Any]],
    rounds: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[str]], dict[str, Any]]:
    global_errors, _ = validate_architecture(architecture, session_id)
    _validate_claim_sessions(claims, session_id, global_errors)
    _validate_risks(risks, session_id, root, code_root, global_errors)
    claim_scope = _validated_claim_review_scope(
        root, session_id, claims, global_errors,
    )
    errors_by_task = {task_id: [] for task_id in tasks}
    for task_id, task in tasks.items():
        errors_by_task[task_id].extend(_task_plan_contract_errors(
            task, task_id=task_id, session_id=session_id, root=root,
            claims=claims, accepted_claim_ids=claim_scope,
        ))
    iteration_policy = contract.get("iteration_policy", {})
    max_tasks_per_round = iteration_policy.get("max_tasks_per_round")
    if (
        not isinstance(max_tasks_per_round, int)
        or isinstance(max_tasks_per_round, bool)
        or max_tasks_per_round < 1
    ):
        global_errors.append("agent_loop_contract.json: max_tasks_per_round must be a positive integer")
        max_tasks_per_round = 1
    ordered_rounds = _validate_round_plan(
        tasks, rounds, max_tasks_per_round, global_errors, errors_by_task,
    )
    valid_task_ids = sorted(
        task_id for task_id, task_errors in errors_by_task.items() if not task_errors
    )
    invalid_task_ids = sorted(set(tasks) - set(valid_task_ids))
    candidate_digests = {
        task_id: task_plan_digest(
            task, claims.get(str(task.get("claim_id") or "")), rounds,
        )
        for task_id, task in sorted(tasks.items())
    }
    return global_errors, errors_by_task, {
        "claims": len(claims), "risks": len(risks), "tasks": len(tasks),
        "claim_review_scope": len(claim_scope),
        "investigation_rounds": len(ordered_rounds),
        "global_passed": not global_errors,
        "valid_task_ids": valid_task_ids,
        "invalid_task_ids": invalid_task_ids,
        "candidate_digests": candidate_digests,
        "task_plan_sha256": task_plan_snapshot_sha256(
            root, contract=contract, architecture=architecture, claims=claims,
            risks=risks, tasks=tasks, rounds=rounds,
        ),
    }


def validate_task_lifecycle_stage(
    *, session_id: str, tasks: dict[str, dict[str, Any]],
    findings: dict[str, dict[str, Any]], rounds: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[str]], dict[str, Any]]:
    global_errors: list[str] = []
    errors_by_task = {task_id: [] for task_id in tasks}
    findings_by_task = _validate_task_finding_lifecycle(
        tasks, findings, session_id, global_errors, errors_by_task,
    )
    earliest_open_round = _validate_round_lifecycle(
        tasks, findings, rounds, global_errors, errors_by_task,
    )
    candidate_digests = {
        task_id: task_lifecycle_digest(task, findings_by_task.get(task_id, []))
        for task_id, task in sorted(tasks.items())
    }
    valid_task_ids = sorted(
        task_id for task_id, task_errors in errors_by_task.items() if not task_errors
    )
    invalid_task_ids = sorted(set(tasks) - set(valid_task_ids))
    return global_errors, errors_by_task, {
        "tasks": len(tasks),
        "findings": len(findings),
        "investigation_rounds": len(rounds),
        "earliest_open_round": earliest_open_round,
        "global_passed": not global_errors,
        "valid_task_ids": valid_task_ids,
        "invalid_task_ids": invalid_task_ids,
        "candidate_digests": candidate_digests,
        "task_lifecycle_sha256": task_lifecycle_snapshot_sha256(
            tasks=tasks, findings=findings, rounds=rounds,
        ),
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

        if item.get("disposition") in {"inapplicable", "gap_recorded"}:
            if not isinstance(item.get("counterfactual"), str) or not item["counterfactual"].strip():
                errors.append(
                    f"{item_label}: {item.get('disposition')} lens requires counterfactual"
                )
            if item.get("disposition") == "gap_recorded" and (task_ids or finding_ids):
                errors.append(
                    f"{item_label}: gap_recorded lens must not claim task/finding coverage"
                )
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
    parallel_paths: dict[str, dict[str, Any]], gap_ids: set[str], errors: list[str],
) -> list[dict[str, Any]]:
    label = "coverage_audit.json next_round_tasks"
    if not isinstance(values, list):
        errors.append(f"{label}: must be an array")
        return []
    valid: list[dict[str, Any]] = []
    seen_specs: set[str] = set()
    required = (
        "claim_id", "claim_branch", "hypothesis", "obligation_sha256",
        "exploration_mode", "review_lenses",
        "architecture_boundaries", "implementation_planes", "parallel_path_ids",
        "risk_observation_ids", "source_gap_ids", "priority_reason",
    )
    for index, item in enumerate(values, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        errors.extend(_require_fields(item, required, item_label, nonempty=False))
        for field in (
            "claim_id", "claim_branch", "hypothesis", "obligation_sha256",
            "exploration_mode", "priority_reason",
        ):
            errors.extend(_string(item, field, item_label))
        claim_id = str(item.get("claim_id") or "")
        if claim_id not in claims:
            errors.append(f"{item_label}: unknown claim_id {claim_id!r}")
        elif item.get("obligation_sha256") != claim_obligation_sha256(claims[claim_id]):
            errors.append(f"{item_label}: obligation_sha256 does not match claim obligation")
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
        source_gap_ids, gap_errors = _string_array(
            item, "source_gap_ids", item_label, allow_empty=False,
        )
        errors.extend(
            lens_errors + boundary_errors + plane_errors + path_errors
            + risk_errors + gap_errors
        )
        unknown_gap_ids = set(source_gap_ids) - gap_ids
        if unknown_gap_ids:
            errors.append(
                f"{item_label}: source_gap_ids reference unknown current gaps "
                f"{sorted(unknown_gap_ids)}"
            )
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
        spec_digest = _canonical_sha256(hm.coverage_task_projection(item))
        if spec_digest in seen_specs:
            errors.append(f"{item_label}: duplicates another supplement task specification")
        else:
            seen_specs.add(spec_digest)
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
        "false_positive_samples_rechecked", "next_round_tasks", "supplement_rounds",
        "remaining_gaps", "stop_reason",
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

    supplement_rounds = coverage.get("supplement_rounds")
    if (
        not isinstance(supplement_rounds, int)
        or isinstance(supplement_rounds, bool)
        or supplement_rounds not in {0, 1}
    ):
        errors.append(f"{label}: supplement_rounds must be 0 or 1")

    gap_values = coverage.get("remaining_gaps")
    if not isinstance(gap_values, list):
        errors.append(f"{label}: remaining_gaps must be an array")
        gap_values = []
    gaps: dict[str, dict[str, Any]] = {}
    gap_refs: dict[str, set[str]] = {}
    for index, item in enumerate(gap_values, start=1):
        item_label = f"{label} remaining_gaps[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label}: must be an object")
            continue
        errors.extend(_require_fields(
            item, ("gap_id", "kind", "ref_id", "reason", "evidence"), item_label,
        ))
        for field in ("gap_id", "kind", "ref_id", "reason", "evidence"):
            errors.extend(_string(item, field, item_label))
        gap_id = str(item.get("gap_id") or "")
        kind = str(item.get("kind") or "")
        ref_id = str(item.get("ref_id") or "")
        if gap_id in gaps:
            errors.append(f"{item_label}: duplicate gap_id {gap_id}")
        elif gap_id:
            gaps[gap_id] = item
        if kind not in GAP_KINDS:
            errors.append(f"{item_label}: invalid kind {kind!r}")
        if kind and ref_id:
            gap_refs.setdefault(kind, set()).add(ref_id)

    investigated_claims = {
        str(finding.get("claim_id") or "") for finding in findings.values()
        if finding.get("claim_id")
    }
    manifest_groups = {
        str(group.get("document_key")) for group in manifest.get("design", {}).get("document_groups", [])
        if isinstance(group, dict) and group.get("document_key")
    }
    if set(design_groups) != set(manifest_groups):
        errors.append(
            "design_coverage.json document groups must match workspace manifest: "
            f"missing={sorted(set(manifest_groups) - set(design_groups))}, "
            f"extra={sorted(set(design_groups) - set(manifest_groups))}"
        )
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
    for kind, known_values in (
        ("lens", lenses),
        ("architecture_boundary", known_boundaries),
        ("parallel_path", set(architecture_indexes["parallel_paths"])),
        ("exploration_mode", modes),
    ):
        unknown = gap_refs.get(kind, set()) - known_values
        if unknown:
            errors.append(f"{label}: {kind} gaps reference unknown IDs {sorted(unknown)}")
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
        parallel_paths=architecture_indexes["parallel_paths"],
        gap_ids=set(gaps), errors=errors,
    )
    next_claims = {str(item.get("claim_id") or "") for item in next_tasks}
    accounted_remaining_claims = next_claims | gap_refs.get("frontier_claim", set())
    if remaining_claims - accounted_remaining_claims:
        errors.append(
            f"{label}: remaining scoped claims lack next task or recorded gap "
            f"{sorted(remaining_claims - accounted_remaining_claims)}"
        )

    missing_modes = modes - observed_modes
    planned_modes = {
        str(item.get("exploration_mode") or "") for item in next_tasks
    }
    accounted_modes = planned_modes | gap_refs.get("exploration_mode", set())
    if missing_modes - accounted_modes:
        errors.append(
            f"{label}: missing exploration modes lack next task or recorded gap "
            f"{sorted(missing_modes - accounted_modes)}"
        )

    completed_boundaries = {
        boundary
        for task_id, task in tasks.items()
        if task.get("status") == "complete" and findings_by_task.get(task_id)
        for boundary in task.get("architecture_boundaries", [])
        if isinstance(boundary, str)
    }
    for boundary_id in high_boundaries:
        audit = audit_boundaries.get(boundary_id, {})
        status = audit.get("status")
        if status == "investigated" and boundary_id not in completed_boundaries:
            errors.append(
                f"{label}: boundary {boundary_id} claims investigated without task/finding evidence"
            )
        if (
            status == "gap_recorded"
            and boundary_id not in gap_refs.get("architecture_boundary", set())
        ):
            errors.append(
                f"{label}: boundary {boundary_id} gap_recorded status lacks remaining_gaps entry"
            )
    planned_boundaries = {
        boundary for item in next_tasks
        for boundary in item.get("architecture_boundaries", [])
        if isinstance(boundary, str)
    }
    missing_high_boundaries = high_boundaries - completed_boundaries
    accounted_boundaries = planned_boundaries | gap_refs.get("architecture_boundary", set())
    if missing_high_boundaries - accounted_boundaries:
        errors.append(
            f"{label}: high-risk boundaries lack completed, planned, or gap evidence "
            f"{sorted(missing_high_boundaries - accounted_boundaries)}"
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
        if missing_planes - planned_planes and path_id not in gap_refs.get("parallel_path", set()):
            errors.append(
                f"{label}: parallel path {path_id} has unaccounted planes "
                f"{sorted(missing_planes - planned_planes)}"
            )
    return next_tasks


def validate_coverage_stage(
    *, root: Path, code_root: Path, design_root: Path, session_id: str,
    manifest: dict[str, Any], contract: dict[str, Any], architecture: dict[str, Any],
    design_inventory: dict[str, Any], design_coverage: dict[str, Any],
    semantic: dict[str, Any], coverage: dict[str, Any],
    supplement_history: dict[str, Any],
    claims: dict[str, dict[str, Any]], risks: dict[str, dict[str, Any]],
    tasks: dict[str, dict[str, Any]], findings: dict[str, dict[str, Any]],
    probes: dict[str, dict[str, Any]], critiques: dict[str, dict[str, Any]],
    rounds: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    errors, architecture_indexes = validate_architecture(architecture, session_id)
    _validate_claim_sessions(claims, session_id, errors)
    _validate_risks(risks, session_id, root, code_root, errors)
    _validate_tasks_typed(tasks, session_id, root, errors)
    scoped_claim_ids = _validated_claim_review_scope(
        root, session_id, claims, errors,
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
    lifecycle_errors_by_task = {task_id: [] for task_id in tasks}
    round_values = list(rounds.values())
    _validate_round_plan(
        tasks, round_values, max_tasks_per_round, errors, lifecycle_errors_by_task,
    )
    _validate_round_lifecycle(
        tasks, findings, round_values, errors, lifecycle_errors_by_task,
    )
    _validate_task_finding_lifecycle(
        tasks, findings, session_id, errors, lifecycle_errors_by_task,
    )
    errors.extend(
        error for task_errors in lifecycle_errors_by_task.values() for error in task_errors
    )
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

    missing_critiques = set(findings) - set(critiques)
    extra_critiques = set(critiques) - set(findings)
    if missing_critiques:
        errors.append(
            "coverage requires an early critic for every finding: "
            f"{sorted(missing_critiques)}"
        )
    if extra_critiques:
        errors.append(f"critic ledger contains unknown findings: {sorted(extra_critiques)}")
    for probe_id, probe in probes.items():
        errors.extend(hm.validate_item(
            probe, artifact_type="probe", identifier=probe_id,
            session_id=session_id,
        ))
        errors.extend(hm._context_errors(
            probe, "probe", root, f"probe ({probe_id})",
        ))
    for finding_id, critique in critiques.items():
        errors.extend(hm.validate_item(
            critique, artifact_type="critic", identifier=finding_id,
            session_id=session_id,
        ))
        errors.extend(hm._context_errors(
            critique, "critic", root, f"critic ({finding_id})",
        ))
    errors.extend(hm.validate_probe_chain(findings, probes, critiques))
    errors.extend(hm.validate_critic_review_history(root, critiques))
    run_events, run_event_errors = ac.load_jsonl(root / "agent_run_ledger.jsonl")
    errors.extend(
        f"agent_run_ledger.jsonl: {error}" for error in run_event_errors
    )
    for finding_id, finding in findings.items():
        finding_positions = [
            index for index, event in enumerate(run_events)
            if event.get("event") == "handoff_merge"
            and event.get("artifact_type") == "finding"
            and event.get("status") == "complete"
            and finding_id in event.get("validated_ids", [])
        ]
        critic_positions = [
            index for index, event in enumerate(run_events)
            if event.get("event") == "handoff_merge"
            and event.get("artifact_type") == "critic"
            and event.get("status") == "complete"
            and finding_id in event.get("validated_ids", [])
        ]
        if not finding_positions:
            errors.append(f"finding {finding_id}: lacks a recorded finding merge event")
            continue
        if not critic_positions:
            errors.append(f"finding {finding_id}: lacks an early critic merge event")
            continue
        latest_finding = max(finding_positions)
        latest_critic = max(critic_positions)
        if latest_critic <= latest_finding:
            errors.append(
                f"finding {finding_id}: current critic was not merged after the current finding evidence"
            )
        selection = finding.get("dynamic_probe_selection")
        if isinstance(selection, dict) and selection.get("disposition") == "selected":
            linked_probe_ids = {
                probe_id for probe_id, probe in probes.items()
                if probe.get("finding_id") == finding_id
            }
            probe_positions = [
                index for index, event in enumerate(run_events)
                if event.get("event") == "handoff_merge"
                and event.get("artifact_type") == "probe"
                and event.get("status") == "complete"
                and linked_probe_ids.intersection(event.get("validated_ids", []))
            ]
            if not probe_positions:
                errors.append(f"finding {finding_id}: selected probe lacks a merge event")
            elif not latest_finding < max(probe_positions) < latest_critic:
                errors.append(
                    f"finding {finding_id}: selected probe must be merged between finding and critic"
                )

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

    semantic_entries = _validate_semantic_coverage(
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
    if supplement_history.get("session_id") != session_id:
        errors.append(
            "coverage_supplement_history.json: session_id does not match current session"
        )
    history_requests = supplement_history.get("requests")
    if not isinstance(history_requests, list):
        errors.append("coverage_supplement_history.json: requests must be an array")
        history_requests = []
    if len(history_requests) > 1:
        errors.append(
            "coverage_supplement_history.json: at most one supplement request is allowed"
        )
    for index, request in enumerate(history_requests, start=1):
        label = f"coverage_supplement_history.json requests[{index}]"
        if not isinstance(request, dict):
            errors.append(f"{label}: must be an object")
            continue
        errors.extend(_require_fields(
            request,
            (
                "request_sha256", "source_gap_ids", "prior_task_ids",
                "prior_tasks", "prior_rounds", "task_specs", "recorded_at",
            ),
            label,
        ))
        request_digest = request.get("request_sha256")
        if not (
            isinstance(request_digest, str)
            and len(request_digest) == 64
            and all(character in "0123456789abcdef" for character in request_digest)
        ):
            errors.append(f"{label}: request_sha256 must be a lowercase SHA-256 digest")
        source_gap_ids, source_errors = _string_array(
            request, "source_gap_ids", label, allow_empty=False,
        )
        errors.extend(source_errors)
        prior_task_ids, prior_errors = _string_array(
            request, "prior_task_ids", label,
        )
        errors.extend(prior_errors)
        unknown_prior_tasks = set(prior_task_ids) - set(tasks)
        if unknown_prior_tasks:
            errors.append(f"{label}: prior_task_ids reference missing tasks {sorted(unknown_prior_tasks)}")
        prior_tasks = request.get("prior_tasks")
        expected_prior_tasks = [
            {"task_id": task_id, "plan": _task_plan_projection(tasks[task_id])}
            for task_id in sorted(set(prior_task_ids).intersection(tasks))
        ]
        if prior_tasks != expected_prior_tasks:
            errors.append(f"{label}: prior_tasks do not match the frozen pre-supplement plan")
        prior_rounds = request.get("prior_rounds")
        if not isinstance(prior_rounds, list):
            errors.append(f"{label}: prior_rounds must be an array")
            prior_rounds = []
        current_round_plans = [_round_plan_projection(item) for item in rounds.values()]
        if current_round_plans[:len(prior_rounds)] != prior_rounds:
            errors.append(f"{label}: prior_rounds do not match the frozen pre-supplement rounds")
        task_specs = request.get("task_specs")
        if not isinstance(task_specs, list) or not task_specs:
            errors.append(f"{label}: task_specs must be a non-empty array")
            task_specs = []
        normalized_specs: list[dict[str, Any]] = []
        for spec_index, spec in enumerate(task_specs, start=1):
            if not isinstance(spec, dict):
                errors.append(f"{label}: task_specs[{spec_index}] must be an object")
                continue
            normalized = hm.coverage_task_projection(spec)
            if spec != normalized:
                errors.append(f"{label}: task_specs[{spec_index}] is not canonical")
            normalized_specs.append(normalized)
        if len({_canonical_sha256(spec) for spec in normalized_specs}) != len(normalized_specs):
            errors.append(f"{label}: task_specs contains duplicate specifications")
        if request_digest != _supplement_request_sha256(request):
            errors.append(f"{label}: request_sha256 does not match the recorded request snapshot")
        recorded_source_gap_ids = sorted({
            gap_id for spec in normalized_specs
            for gap_id in spec.get("source_gap_ids", [])
            if isinstance(gap_id, str) and gap_id
        })
        if source_gap_ids != recorded_source_gap_ids:
            errors.append(f"{label}: source_gap_ids do not match task_specs")
        # While the request is pending it must still be anchored to the gaps in
        # the current audit.  Once the single supplement has completed, those
        # gaps may legitimately disappear from remaining_gaps; the immutable
        # history remains the evidence that the round was requested.
        if next_tasks and set(source_gap_ids) - {
            str(item.get("gap_id") or "")
            for item in coverage.get("remaining_gaps", [])
            if isinstance(item, dict)
        }:
            errors.append(f"{label}: source_gap_ids are absent from current remaining_gaps")

    supplement_rounds = coverage.get("supplement_rounds")
    new_supplement_request: dict[str, Any] | None = None
    request_task_specs = [hm.coverage_task_projection(item) for item in next_tasks]
    if next_tasks:
        request_gap_ids = sorted({
            gap_id for item in next_tasks
            for gap_id in item.get("source_gap_ids", [])
            if isinstance(gap_id, str) and gap_id
        })
        request_payload = {
            "source_gap_ids": request_gap_ids,
            "prior_task_ids": sorted(tasks),
            "prior_tasks": [
                {"task_id": task_id, "plan": _task_plan_projection(tasks[task_id])}
                for task_id in sorted(tasks)
            ],
            "prior_rounds": [_round_plan_projection(item) for item in rounds.values()],
            "task_specs": request_task_specs,
        }
        request_sha256 = _supplement_request_sha256(request_payload)
        if supplement_rounds != 0:
            errors.append(
                "coverage_audit.json: next_round_tasks are allowed only before the single supplement"
            )
        if history_requests:
            retained = history_requests[0] if isinstance(history_requests[0], dict) else {}
            if retained.get("request_sha256") != request_sha256:
                errors.append(
                    "coverage_audit.json: a different or second supplement request is forbidden"
                )
        else:
            new_supplement_request = {
                "request_sha256": request_sha256,
                **request_payload,
                "recorded_at": ac.now_iso(),
            }
    elif history_requests and supplement_rounds != 1:
        errors.append(
            "coverage_audit.json: completed recorded supplement requires supplement_rounds=1"
        )
    elif not history_requests and supplement_rounds != 0:
        errors.append(
            "coverage_audit.json: supplement_rounds=1 requires a recorded supplement request"
        )

    supplement_events = [
        event for event in run_events
        if event.get("event") == "coverage_supplement_request"
    ]
    wrong_session_events = [
        event for event in supplement_events
        if event.get("session_id") != session_id
    ]
    if wrong_session_events:
        errors.append(
            "agent_run_ledger.jsonl: coverage supplement request belongs to another session"
        )
    current_supplement_events = [
        event for event in supplement_events
        if event.get("session_id") == session_id
    ]
    if len(current_supplement_events) > 1:
        errors.append(
            "agent_run_ledger.jsonl: at most one coverage supplement request event is allowed"
        )
    if history_requests:
        request = history_requests[0] if isinstance(history_requests[0], dict) else {}
        expected_history_sha256 = _canonical_sha256(supplement_history)
        matching_events = [
            event for event in current_supplement_events
            if event.get("status") == "complete"
            and event.get("request_sha256") == request.get("request_sha256")
            and event.get("history_sha256") == expected_history_sha256
        ]
        if len(matching_events) != 1:
            errors.append(
                "coverage_supplement_history.json lacks one matching tool-owned ledger event"
            )
    elif current_supplement_events:
        errors.append(
            "coverage_supplement_history.json was cleared after a supplement request was recorded"
        )
    if history_requests and not next_tasks and supplement_rounds == 1:
        request = history_requests[0] if isinstance(history_requests[0], dict) else {}
        prior_task_ids = {
            value for value in request.get("prior_task_ids", [])
            if isinstance(value, str) and value
        }
        requested_specs = [
            spec for spec in request.get("task_specs", []) if isinstance(spec, dict)
        ]
        new_task_ids = sorted(set(tasks) - prior_task_ids)
        actual_specs = [hm.coverage_task_projection(tasks[task_id]) for task_id in new_task_ids]
        if sorted(_canonical_sha256(spec) for spec in actual_specs) != sorted(
            _canonical_sha256(spec) for spec in requested_specs
        ):
            errors.append(
                "coverage_audit.json: completed supplement tasks do not exactly match the recorded request"
            )
        request_sha256 = request.get("request_sha256")
        for task_id in new_task_ids:
            if tasks[task_id].get("coverage_request_sha256") != request_sha256:
                errors.append(
                    f"investigation task {task_id}: missing recorded coverage request binding"
                )
    recorded_gap_refs: dict[str, set[str]] = {}
    for item in coverage.get("remaining_gaps", []):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        ref_id = item.get("ref_id")
        if isinstance(kind, str) and isinstance(ref_id, str) and kind and ref_id:
            recorded_gap_refs.setdefault(kind, set()).add(ref_id)

    unaccounted_materialized_claims = (
        set(claims) - scoped_claim_ids - recorded_gap_refs.get("frontier_claim", set())
    )
    if unaccounted_materialized_claims:
        errors.append(
            "materialized claims outside the current accepted scope require "
            "frontier_claim remaining gaps: "
            f"{sorted(unaccounted_materialized_claims)}"
        )

    inventory_groups = {
        str(item.get("document_key") or ""): item
        for item in design_inventory.get("document_groups", [])
        if isinstance(item, dict) and item.get("document_key")
    } if isinstance(design_inventory, dict) else {}
    for group_id, coverage_group in design_groups.items():
        if coverage_group.get("disposition") != "applicable":
            continue
        inventory_group = inventory_groups.get(group_id, {})
        for section in inventory_group.get("sections", []):
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("section_id") or "")
            section_path = str(section.get("path") or "")
            section_start = section.get("line_start")
            section_end = section.get("line_end")
            if not (
                section_id and section_path
                and isinstance(section_start, int) and not isinstance(section_start, bool)
                and isinstance(section_end, int) and not isinstance(section_end, bool)
            ):
                continue
            materialized = any(
                claim.get("document_key") == group_id
                and claim.get("path") == section_path
                and isinstance(claim.get("line_start"), int)
                and isinstance(claim.get("line_end"), int)
                and claim["line_start"] <= section_end
                and claim["line_end"] >= section_start
                for claim in claims.values()
            )
            if not materialized and section_id not in recorded_gap_refs.get(
                "inventory", set()
            ):
                errors.append(
                    "applicable design inventory section has neither a materialized "
                    f"claim nor an inventory gap: {section_id}"
                )
    log_root_value = manifest.get("paths", {}).get("log_root")
    claim_review_trace = {}
    if isinstance(log_root_value, str) and log_root_value:
        claim_review_trace_path = Path(log_root_value).resolve() / "trace" / "claim_review_validation.json"
        if claim_review_trace_path.is_file():
            loaded_trace = ac.load_json(claim_review_trace_path)
            if isinstance(loaded_trace, dict):
                claim_review_trace = loaded_trace
    expansion_request_ids = {
        str(item.get("expansion_request_id"))
        for item in claim_review_trace.get("expansion_requests", [])
        if isinstance(item, dict) and item.get("expansion_request_id")
    }
    unrecorded_expansions = expansion_request_ids - recorded_gap_refs.get(
        "claim_review_expansion", set()
    )
    if unrecorded_expansions:
        errors.append(
            "coverage_audit.json: current claim-review expansion requests are not "
            f"recorded as remaining gaps: {sorted(unrecorded_expansions)}"
        )
    critic_requests = {
        str(critique.get("review_id"))
        for critique in critiques.values()
        if critique.get("decision") == "needs_more_evidence"
        and critique.get("review_id")
    }
    unknown_critic_requests = recorded_gap_refs.get("critic_request", set()) - critic_requests
    if unknown_critic_requests:
        errors.append(
            "coverage_audit.json: critic_request gaps reference no current "
            f"needs_more_evidence review: {sorted(unknown_critic_requests)}"
        )
    unrecorded_critic_requests = critic_requests - recorded_gap_refs.get(
        "critic_request", set()
    )
    if unrecorded_critic_requests:
        errors.append(
            "coverage_audit.json: current needs_more_evidence reviews lack critic_request gaps: "
            f"{sorted(unrecorded_critic_requests)}"
        )
    for lens, entry in semantic_entries.items():
        if (
            entry.get("disposition") == "gap_recorded"
            and lens not in recorded_gap_refs.get("lens", set())
        ):
            errors.append(
                f"semantic coverage lens {lens}: gap_recorded lacks remaining_gaps entry"
            )
    unfinished_tasks = {
        task_id for task_id, task in tasks.items()
        if task.get("status") in {"pending", "in_progress"}
    }
    remaining_scoped = coverage.get("remaining_scoped_claims")
    supplement_rounds = coverage.get("supplement_rounds")
    closed = (
        isinstance(remaining_scoped, list) and not remaining_scoped
        and not next_tasks
        and not unfinished_tasks
        and supplement_rounds in {0, 1}
    )
    return errors, {
        "claims": len(claims), "risks": len(risks), "tasks": len(tasks),
        "findings": len(findings), "probes": len(probes),
        "critics": len(critiques), "rounds": len(rounds),
        "semantic_lenses": len(semantic.get("lenses", [])) if isinstance(semantic.get("lenses"), list) else 0,
        "next_round_tasks": len(next_tasks),
        "scoped_claims": len(scoped_claim_ids),
        "unfinished_tasks": len(unfinished_tasks),
        "supplement_rounds": supplement_rounds,
        "remaining_gaps": len(coverage.get("remaining_gaps", []))
        if isinstance(coverage.get("remaining_gaps"), list) else 0,
        "closed": closed,
        "_new_supplement_request": new_supplement_request,
    }


def _stage_inputs(root: Path, stage: str) -> list[Path]:
    plan = [
        root / "workspace_manifest.json", root / "agent_loop_contract.json",
        root / "architecture_map.json",
    ]
    if stage == "architecture":
        return plan
    plan.extend([
        root / "risk_sweep_plan.json",
        root / "coverage_supplement_history.json",
        root / "design_agent_manifest.json", root / "design_coverage.json",
        root / "claim_review_scope.json", root / "design_claim_review.json",
        root / "design_claims.jsonl",
        root / "risk_observations.jsonl",
        root / "investigation_tasks.jsonl", root / "investigation_rounds.jsonl",
    ])
    lifecycle = [
        root / "workspace_manifest.json", root / "investigation_tasks.jsonl",
        root / "investigation_findings.jsonl", root / "investigation_rounds.jsonl",
    ]
    if stage == "task-plan":
        return plan
    if stage == "task-lifecycle":
        return lifecycle
    if stage == "coverage":
        plan.extend([
            root / "design_inventory.json",
            root / "investigation_findings.jsonl",
            root / "dynamic_probes.jsonl", root / "critic_reviews.jsonl",
            root / "critic_review_history.jsonl",
            root / "semantic_coverage.json", root / "coverage_audit.json",
        ])
    return plan


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


def _candidate_trace(
    *, stage: str, session_id: str, global_errors: list[str],
    errors_by_task: dict[str, list[str]], metrics: dict[str, Any],
    input_digests: dict[str, str | None], combined_digest: str,
) -> dict[str, Any]:
    candidate_errors = [
        error for task_errors in errors_by_task.values() for error in task_errors
    ]
    all_errors = [*global_errors, *candidate_errors]
    return {
        "stage": stage,
        "session_id": session_id,
        "validated_at": ac.now_iso(),
        "passed": not all_errors,
        "global_passed": not global_errors,
        "valid_task_ids": metrics.get("valid_task_ids", []),
        "invalid_task_ids": metrics.get("invalid_task_ids", []),
        "candidate_digests": metrics.get("candidate_digests", {}),
        "task_plan_sha256": metrics.get("task_plan_sha256"),
        "task_lifecycle_sha256": metrics.get("task_lifecycle_sha256"),
        "input_digests": input_digests,
        "combined_input_sha256": combined_digest,
        "metrics": metrics,
        "errors_by_task": {
            task_id: task_errors for task_id, task_errors in sorted(errors_by_task.items())
            if task_errors
        },
        "errors": all_errors,
    }


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    trace_root = log_root / "trace"
    base_errors: list[str] = []
    try:
        base_errors.extend(ac.session_path_errors(
            root, code_root=code_root, design_root=design_root,
            result_root=result_root, log_root=log_root,
        ))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        base_errors.append(f"prepared session path validation failed: {exc}")

    manifest = _load_object(
        root / "workspace_manifest.json", "workspace_manifest.json", base_errors,
    )
    state = _load_object(root / "agent_loop_state.json", "agent_loop_state.json", base_errors)
    session_id = str(state.get("session_id") or "")
    if not session_id:
        base_errors.append("agent_loop_state.json: missing/non-string session_id")
    if manifest.get("session_id") != session_id:
        base_errors.append("workspace_manifest.json: session_id does not match current session")

    if args.stage in {"task-plan", "task-lifecycle"}:
        plan_trace: dict[str, Any] | None = None
        lifecycle_trace: dict[str, Any] | None = None
        if args.stage == "task-plan":
            plan_errors = list(base_errors)
            contract = _load_object(
                root / "agent_loop_contract.json", "agent_loop_contract.json", plan_errors,
            )
            architecture = _load_object(
                root / "architecture_map.json", "architecture_map.json", plan_errors,
            )
            if contract.get("session", {}).get("session_id") != session_id:
                plan_errors.append("agent_loop_contract.json: session_id does not match current session")
            claims, _ = _load_index(
                root / "design_claims.jsonl", "claim_id", "design_claims.jsonl", plan_errors,
            )
            risks, _ = _load_index(
                root / "risk_observations.jsonl", "observation_id", "risk_observations.jsonl",
                plan_errors,
            )
            tasks, _ = _load_index(
                root / "investigation_tasks.jsonl", "task_id", "investigation_tasks.jsonl",
                plan_errors,
            )
            _, round_values = _load_index(
                root / "investigation_rounds.jsonl", "round_id", "investigation_rounds.jsonl",
                plan_errors,
            )
            stage_global, errors_by_task, metrics = validate_task_plan_stage(
                root=root, code_root=code_root, session_id=session_id, contract=contract,
                architecture=architecture, claims=claims, risks=risks, tasks=tasks,
                rounds=round_values,
            )
            plan_errors.extend(stage_global)
            plan_inputs, plan_combined = _input_digests(
                root, _stage_inputs(root, "task-plan"),
            )
            plan_trace = _candidate_trace(
                stage="task-plan", session_id=session_id, global_errors=plan_errors,
                errors_by_task=errors_by_task, metrics=metrics,
                input_digests=plan_inputs, combined_digest=plan_combined,
            )
            ac.save_json(trace_root / "task_plan_validation.json", plan_trace)

        if args.stage == "task-lifecycle":
            lifecycle_errors = list(base_errors)
            tasks, _ = _load_index(
                root / "investigation_tasks.jsonl", "task_id", "investigation_tasks.jsonl",
                lifecycle_errors,
            )
            findings, _ = _load_index(
                root / "investigation_findings.jsonl", "finding_id", "investigation_findings.jsonl",
                lifecycle_errors,
            )
            _, round_values = _load_index(
                root / "investigation_rounds.jsonl", "round_id", "investigation_rounds.jsonl",
                lifecycle_errors,
            )
            stage_global, errors_by_task, metrics = validate_task_lifecycle_stage(
                session_id=session_id, tasks=tasks, findings=findings, rounds=round_values,
            )
            lifecycle_errors.extend(stage_global)
            lifecycle_inputs, lifecycle_combined = _input_digests(
                root, _stage_inputs(root, "task-lifecycle"),
            )
            lifecycle_trace = _candidate_trace(
                stage="task-lifecycle", session_id=session_id,
                global_errors=lifecycle_errors, errors_by_task=errors_by_task,
                metrics=metrics, input_digests=lifecycle_inputs,
                combined_digest=lifecycle_combined,
            )
            ac.save_json(trace_root / "task_lifecycle_validation.json", lifecycle_trace)

        selected_trace = plan_trace if args.stage == "task-plan" else lifecycle_trace
        trace_path = (
            trace_root / "task_plan_validation.json"
            if args.stage == "task-plan" else trace_root / "task_lifecycle_validation.json"
        )
        assert selected_trace is not None
        print(json.dumps({
            "stage": args.stage, "passed": selected_trace["passed"],
            "error_count": len(selected_trace["errors"]),
            "trace": str(trace_path), "errors": selected_trace["errors"],
        }, ensure_ascii=False))
        return 0 if selected_trace["passed"] else 1

    errors = list(base_errors)
    inputs = _stage_inputs(root, args.stage)
    input_digests, combined_digest = _input_digests(root, inputs)
    metrics: dict[str, Any] = {}
    if args.stage == "architecture":
        architecture = _load_object(
            root / "architecture_map.json", "architecture_map.json", errors,
        )
        stage_errors, indexes = validate_architecture(architecture, session_id)
        errors.extend(stage_errors)
        metrics = {
            "implementation_planes": len(indexes["planes"]),
            "integration_boundaries": len(indexes["boundaries"]),
            "parallel_behavior_paths": len(indexes["parallel_paths"]),
        }
    else:
        contract = _load_object(
            root / "agent_loop_contract.json", "agent_loop_contract.json", errors,
        )
        architecture = _load_object(
            root / "architecture_map.json", "architecture_map.json", errors,
        )
        if contract.get("session", {}).get("session_id") != session_id:
            errors.append("agent_loop_contract.json: session_id does not match current session")
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
        probes, _ = _load_index(
            root / "dynamic_probes.jsonl", "probe_id", "dynamic_probes.jsonl", errors,
        )
        critiques, _ = _load_index(
            root / "critic_reviews.jsonl", "finding_id", "critic_reviews.jsonl", errors,
        )
        rounds, round_values = _load_index(
            root / "investigation_rounds.jsonl", "round_id", "investigation_rounds.jsonl", errors,
        )
        design_coverage = _load_object(
            root / "design_coverage.json", "design_coverage.json", errors,
        )
        design_inventory = _load_object(
            root / "design_inventory.json", "design_inventory.json", errors,
        )
        semantic = _load_object(
            root / "semantic_coverage.json", "semantic_coverage.json", errors,
        )
        coverage = _load_object(
            root / "coverage_audit.json", "coverage_audit.json", errors,
        )
        supplement_history = _load_object(
            root / "coverage_supplement_history.json",
            "coverage_supplement_history.json",
            errors,
        )
        stage_errors, metrics = validate_coverage_stage(
            root=root, code_root=code_root, design_root=design_root,
            session_id=session_id, manifest=manifest, contract=contract,
            architecture=architecture, design_inventory=design_inventory,
            design_coverage=design_coverage,
            semantic=semantic, coverage=coverage,
            supplement_history=supplement_history,
            claims=claims, risks=risks,
            tasks=tasks, findings=findings, probes=probes, critiques=critiques,
            rounds=rounds,
        )
        errors.extend(stage_errors)

        new_supplement_request = metrics.pop("_new_supplement_request", None)
        if not errors and isinstance(new_supplement_request, dict):
            supplement_history["requests"] = [new_supplement_request]
            ac.save_json(
                root / "coverage_supplement_history.json", supplement_history,
            )
            ac.append_jsonl(root / "agent_run_ledger.jsonl", {
                "recorded_at": ac.now_iso(), "session_id": session_id,
                "event": "coverage_supplement_request",
                "actor": "stage_artifact_validator",
                "phase": "coverage_audit", "status": "complete",
                "request_sha256": new_supplement_request["request_sha256"],
                "history_sha256": _canonical_sha256(supplement_history),
            })
            input_digests, combined_digest = _input_digests(root, inputs)

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
        "coverage_provenance_sha256": (
            coverage_provenance_sha256(root)
            if args.stage == "coverage" else None
        ),
        "claim_review_provenance_sha256": (
            claim_review_provenance_sha256(root)
            if args.stage == "coverage" else None
        ),
        "metrics": metrics,
        "errors": errors,
    }
    trace_path = trace_root / f"{args.stage}_validation.json"
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
