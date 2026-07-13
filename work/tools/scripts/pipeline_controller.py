#!/usr/bin/env python3
"""Project and reconcile the one mechanically valid pipeline action.

Semantic checkpoints are evidence in the append-only run ledger.  They are not
pipeline state.  This controller derives state only from current, validated
artifacts and is the sole owner of ``current_phase`` and ``next_actions``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import agent_common as ac
import risk_sweep_plan_validator as rpv
import stage_artifact_validator as sav


NEXT_ACTIONS = {
    "map_architecture",
    "build_inventory",
    "build_scout_plan",
    "finish_scouts",
    "select_candidates",
    "review_claims",
    "plan_investigations",
    "finish_investigations",
    "finish_critics",
    "run_final",
}

PHASE_BY_ACTION = {
    "map_architecture": "architecture_mapping",
    "build_inventory": "design_inventory",
    "build_scout_plan": "semantic_scout_planning",
    "finish_scouts": "semantic_scouting",
    "select_candidates": "candidate_selection",
    "review_claims": "claim_review",
    "plan_investigations": "investigation_planning",
    "finish_investigations": "investigation",
    "finish_critics": "critic_review",
    "run_final": "finalization",
}


def _json_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = ac.load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _jsonl(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Load a JSONL ledger and distinguish a valid empty file from absence."""
    if not path.is_file() or path.is_symlink():
        return [], False
    values, errors = ac.load_jsonl(path)
    return values, not errors


def _identifier_set(
    values: list[dict[str, Any]], field: str,
) -> tuple[set[str], bool]:
    result: set[str] = set()
    for item in values:
        identifier = item.get(field)
        if not isinstance(identifier, str) or not identifier or identifier in result:
            return set(), False
        result.add(identifier)
    return result, True


def _status(
    action: str | None, pending_ids: set[str] | list[str], *, expected: int,
    completed: int, blocked_reason: str = "", errors: list[str] | None = None,
    current_phase: str | None = None,
) -> dict[str, Any]:
    pending = sorted(set(pending_ids))
    return {
        "next_action": action,
        "current_phase": current_phase or (
            PHASE_BY_ACTION[action] if action else "complete"
        ),
        "pending_ids": pending,
        "counts": {
            "expected": expected,
            "completed": completed,
            "pending": len(pending),
        },
        "blocked_reason": blocked_reason,
        "errors": (errors or [])[:20],
        "terminal": action is None,
    }


def _trace_root(root: Path) -> tuple[Path | None, list[str]]:
    manifest = _json_object(root / "workspace_manifest.json")
    paths = manifest.get("paths") if isinstance(manifest.get("paths"), dict) else {}
    log_root = paths.get("log_root")
    if not isinstance(log_root, str) or not log_root:
        return None, ["workspace_manifest.json has no log_root"]
    return Path(log_root).resolve() / "trace", []


def _architecture_ready(root: Path, session_id: str) -> tuple[bool, list[str]]:
    architecture_path = root / "architecture_map.json"
    architecture = _json_object(architecture_path)
    if not architecture:
        return False, ["architecture_map.json is missing or invalid"]
    trace_root, errors = _trace_root(root)
    if trace_root is None:
        return False, errors
    trace = _json_object(trace_root / "architecture_validation.json")
    expected_inputs, expected_combined = sav._input_digests(
        root, sav._stage_inputs(root, "architecture"),
    )
    if not trace:
        errors.append("architecture_validation.json is missing or invalid")
    else:
        if trace.get("stage") != "architecture":
            errors.append("architecture_validation.json has the wrong stage")
        if trace.get("session_id") != session_id:
            errors.append("architecture_validation.json belongs to another session")
        if trace.get("passed") is not True:
            errors.append("architecture_validation.json has not passed")
        if trace.get("input_digests") != expected_inputs:
            errors.append("architecture_validation.json input digests are stale")
        if trace.get("combined_input_sha256") != expected_combined:
            errors.append("architecture_validation.json combined digest is stale")
    return not errors, errors


def _inventory_ready(root: Path, session_id: str) -> tuple[bool, list[str]]:
    inventory_path = root / "design_inventory.json"
    inventory = _json_object(inventory_path)
    if not inventory:
        return False, ["design_inventory.json is missing or invalid"]
    trace_root, errors = _trace_root(root)
    if trace_root is None:
        return False, errors
    trace = _json_object(trace_root / "design_validation.json")
    expected_inventory_digest = ac.sha256_file(inventory_path)
    expected_manifest_digest = ac.sha256_file(root / "workspace_manifest.json")
    if not trace:
        errors.append("design_validation.json is missing or invalid")
    else:
        if trace.get("mode") not in {"inventory", "claims", "all"}:
            errors.append("design_validation.json does not include inventory validation")
        if trace.get("session_id") != session_id:
            errors.append("design_validation.json belongs to another session")
        if trace.get("passed") is not True:
            errors.append("design_validation.json has not passed")
        input_digests = trace.get("input_digests")
        if not isinstance(input_digests, dict) or (
            input_digests.get("design_inventory.json") != expected_inventory_digest
            or input_digests.get("workspace_manifest.json") != expected_manifest_digest
        ):
            errors.append("design_validation.json inventory inputs are stale")
    return not errors, errors


def _scout_plan_ready(root: Path, session_id: str) -> tuple[bool, list[str]]:
    plan = _json_object(root / "risk_sweep_plan.json")
    if not plan:
        return False, ["risk_sweep_plan.json is missing or invalid"]
    trace_root, errors = _trace_root(root)
    if trace_root is None:
        return False, errors
    trace = _json_object(trace_root / "risk_sweep_plan_validation.json")
    expected_inputs, expected_combined = rpv.plan_input_digests(root)
    if not trace:
        errors.append("risk_sweep_plan_validation.json is missing or invalid")
    else:
        if trace.get("session_id") != session_id:
            errors.append("risk_sweep_plan_validation.json belongs to another session")
        if trace.get("passed") is not True:
            errors.append("risk_sweep_plan_validation.json has not passed")
        if trace.get("input_digests") != expected_inputs:
            errors.append("risk_sweep_plan_validation.json input digests are stale")
        if trace.get("combined_input_sha256") != expected_combined:
            errors.append("risk_sweep_plan_validation.json combined digest is stale")
    return not errors, errors


def _scout_progress(
    root: Path, session_id: str,
) -> tuple[set[str], set[str], list[str], str]:
    """Return planned IDs, valid completed IDs, receipt errors, and plan digest."""
    plan_path = root / "risk_sweep_plan.json"
    plan = _json_object(plan_path)
    slices = plan.get("slices") if isinstance(plan.get("slices"), list) else []
    planned: set[str] = set()
    errors: list[str] = []
    for index, item in enumerate(slices, start=1):
        identifier = item.get("sweep_id") if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"risk_sweep_plan.json:slices[{index}] has no sweep_id")
        elif identifier in planned:
            errors.append(f"risk_sweep_plan.json has duplicate sweep_id {identifier!r}")
        else:
            planned.add(identifier)
    if not plan or not planned:
        errors.append("risk_sweep_plan.json is missing, invalid, or has no slices")
        return planned, set(), errors, ""
    if plan.get("session_id") != session_id:
        errors.append("risk_sweep_plan.json session_id does not match current session")
        return planned, set(), errors, ac.sha256_file(plan_path)

    plan_digest = ac.sha256_file(plan_path)
    observations, observations_valid = _jsonl(root / "risk_observations.jsonl")
    observed_by_sweep: dict[str, set[str]] = {identifier: set() for identifier in planned}
    if not observations_valid:
        errors.append("risk_observations.jsonl is missing or invalid")
    else:
        for index, item in enumerate(observations, start=1):
            observation_id = item.get("observation_id")
            sweep_id = item.get("sweep_id")
            if (
                not isinstance(observation_id, str) or not observation_id
                or not isinstance(sweep_id, str) or sweep_id not in planned
            ):
                errors.append(
                    f"risk_observations.jsonl:{index} has invalid observation_id/sweep_id"
                )
                continue
            observed_by_sweep[sweep_id].add(observation_id)

    receipts, receipts_valid = _jsonl(root / "scout_receipts.jsonl")
    if not receipts_valid:
        errors.append("scout_receipts.jsonl is missing or invalid")
        return planned, set(), errors, plan_digest

    completed: set[str] = set()
    seen: set[str] = set()
    for index, item in enumerate(receipts, start=1):
        label = f"scout_receipts.jsonl:{index}"
        identifier = item.get("sweep_id") or item.get("scout_id")
        if not isinstance(identifier, str) or identifier not in planned:
            errors.append(f"{label} has an unknown sweep_id")
            continue
        if identifier in seen:
            errors.append(f"{label} duplicates sweep_id {identifier!r}")
            completed.discard(identifier)
            continue
        seen.add(identifier)
        candidate_ids = item.get("candidate_ids")
        valid_candidate_ids = (
            isinstance(candidate_ids, list)
            and all(isinstance(value, str) and value for value in candidate_ids)
            and len(set(candidate_ids)) == len(candidate_ids)
        )
        receipt_errors: list[str] = []
        if item.get("session_id") != session_id:
            receipt_errors.append("session_id does not match current session")
        if item.get("risk_sweep_plan_sha256") != plan_digest:
            receipt_errors.append("risk_sweep_plan_sha256 is stale")
        if item.get("status") != "complete":
            receipt_errors.append("status is not complete")
        if not valid_candidate_ids:
            receipt_errors.append("candidate_ids are invalid")
        elif item.get("candidate_count") != len(candidate_ids):
            receipt_errors.append("candidate_count does not match candidate_ids")
        elif observations_valid and set(candidate_ids) != observed_by_sweep[identifier]:
            receipt_errors.append("candidate_ids do not match merged observations")
        if not observations_valid:
            receipt_errors.append("merged observations are not currently valid")
        if receipt_errors:
            errors.extend(f"{label}: {value}" for value in receipt_errors)
            continue
        completed.add(identifier)
    return planned, completed, errors, plan_digest


def _candidate_projection(
    root: Path,
) -> tuple[bool, set[str], int, int, str, list[str]]:
    observations, observations_valid = _jsonl(root / "risk_observations.jsonl")
    observation_ids, observations_unique = _identifier_set(observations, "observation_id")
    if not observations_valid or not observations_unique:
        return False, set(), len(observations), 0, "risk_observations_invalid", [
            "risk_observations.jsonl is missing, invalid, or contains duplicate IDs"
        ]

    selection = _json_object(root / "candidate_selection.json")
    raw_candidate_ids = selection.get("candidate_ids")
    if not isinstance(raw_candidate_ids, list) or any(
        not isinstance(value, str) or not value for value in raw_candidate_ids
    ) or len(set(raw_candidate_ids)) != len(raw_candidate_ids):
        return (
            False, observation_ids, len(observation_ids), 0,
            "candidate_selection_missing_or_invalid", [],
        )
    selected = set(raw_candidate_ids)
    if observation_ids and not selected:
        return (
            False, observation_ids, len(observation_ids), 0,
            "candidate_selection_empty_with_observations", [],
        )
    unknown = selected - observation_ids
    if unknown:
        return (
            False, unknown, len(selected), len(selected - unknown),
            "candidate_selection_references_unknown_observations", [],
        )

    claims, claims_valid = _jsonl(root / "design_claims.jsonl")
    lookups, lookups_valid = _jsonl(root / "design_lookup_requests.jsonl")
    if not claims_valid or not lookups_valid:
        return (
            False, selected, len(selected), 0,
            "candidate_projection_artifacts_missing_or_invalid", [],
        )
    claim_candidates = [
        item.get("candidate_id") for item in claims
        if isinstance(item.get("candidate_id"), str)
    ]
    lookup_candidates = [
        item.get("candidate_id") for item in lookups
        if isinstance(item.get("candidate_id"), str)
    ]
    complete = (
        len(claims) == len(selected)
        and len(lookups) == len(selected)
        and len(set(claim_candidates)) == len(claim_candidates)
        and len(set(lookup_candidates)) == len(lookup_candidates)
        and set(claim_candidates) == selected
        and set(lookup_candidates) == selected
    )
    projected = set(claim_candidates).intersection(lookup_candidates).intersection(selected)
    return (
        complete,
        selected - projected,
        len(selected),
        len(projected),
        "" if complete else "candidate_projection_incomplete",
        [],
    )


def _claim_review_progress(
    root: Path,
) -> tuple[bool, set[str], int, int, str]:
    scope = _json_object(root / "claim_review_scope.json")
    review = _json_object(root / "design_claim_review.json")
    scoped = scope.get("claim_ids")
    reviews = review.get("claim_reviews")
    if not isinstance(scoped, list) or any(
        not isinstance(value, str) or not value for value in scoped
    ) or len(set(scoped)) != len(scoped):
        return False, set(), 0, 0, "claim_review_scope_missing_or_invalid"
    scoped_ids = set(scoped)
    if not isinstance(reviews, list):
        return False, scoped_ids, len(scoped_ids), 0, "claim_review_missing_or_invalid"
    accepted = {
        item.get("claim_id") for item in reviews
        if isinstance(item, dict) and item.get("decision") == "accept"
        and isinstance(item.get("claim_id"), str)
    }
    manifest = _json_object(root / "workspace_manifest.json")
    paths = manifest.get("paths") if isinstance(manifest.get("paths"), dict) else {}
    log_root = paths.get("log_root")
    trace = _json_object(
        Path(log_root).resolve() / "trace" / "claim_review_validation.json"
    ) if isinstance(log_root, str) and log_root else {}
    trace_ids = trace.get("accepted_claim_ids")
    trace_valid = (
        trace.get("passed") is True
        and isinstance(trace_ids, list)
        and all(isinstance(value, str) and value for value in trace_ids)
        and set(trace_ids) == scoped_ids
    )
    semantic_pending = scoped_ids.symmetric_difference(accepted)
    complete = not semantic_pending and accepted == scoped_ids and trace_valid
    pending = semantic_pending or (scoped_ids if not trace_valid else set())
    reason = "" if complete else (
        "claims_not_all_accepted" if semantic_pending or accepted != scoped_ids
        else "claim_review_validation_missing_or_stale"
    )
    return complete, pending, len(scoped_ids), len(scoped_ids.intersection(accepted)), reason


def _selected_work(
    root: Path, candidate_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], bool, set[str], str]:
    """Load current tasks; obsolete selected_candidates.jsonl is never consulted."""
    values, valid = _jsonl(root / "investigation_tasks.jsonl")
    if not valid:
        return {}, False, set(candidate_ids), "investigation_tasks_missing_or_invalid"
    selected: dict[str, dict[str, Any]] = {}
    task_candidates: set[str] = set()
    invalid = False
    for item in values:
        task_id = item.get("task_id")
        candidate_id = item.get("candidate_id")
        if (
            not isinstance(task_id, str) or not task_id or task_id in selected
            or not isinstance(candidate_id, str) or not candidate_id
        ):
            invalid = True
            continue
        selected[task_id] = item
        task_candidates.add(candidate_id)
    missing_candidates = candidate_ids - task_candidates
    extra_candidates = task_candidates - candidate_ids
    if invalid or missing_candidates or extra_candidates or len(task_candidates) != len(selected):
        return (
            selected, False, missing_candidates or extra_candidates or candidate_ids,
            "investigation_task_projection_incomplete",
        )
    return selected, True, set(), ""


def _task_validation_complete(
    root: Path, session_id: str, task_ids: set[str],
) -> tuple[bool, list[str]]:
    trace_root, errors = _trace_root(root)
    if trace_root is None:
        return False, errors

    contract = _json_object(root / "agent_loop_contract.json")
    architecture = _json_object(root / "architecture_map.json")
    if not contract:
        errors.append("agent_loop_contract.json is missing or invalid")
    if not architecture:
        errors.append("architecture_map.json is missing or invalid")

    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    ordered_rounds: list[dict[str, Any]] = []
    for filename, key in (
        ("design_claims.jsonl", "claim_id"),
        ("risk_observations.jsonl", "observation_id"),
        ("investigation_tasks.jsonl", "task_id"),
        ("investigation_findings.jsonl", "finding_id"),
    ):
        values, valid = _jsonl(root / filename)
        identifiers, unique = _identifier_set(values, key)
        if not valid or not unique:
            errors.append(f"{filename} is missing, invalid, or contains duplicate IDs")
            indexed[filename] = {}
            continue
        indexed[filename] = {str(item[key]): item for item in values}
        if filename == "investigation_tasks.jsonl" and identifiers != task_ids:
            errors.append("investigation_tasks.jsonl does not match current task IDs")
    ordered_rounds, rounds_valid = _jsonl(root / "investigation_rounds.jsonl")
    _round_ids, rounds_unique = _identifier_set(ordered_rounds, "round_id")
    if not rounds_valid or not rounds_unique:
        errors.append(
            "investigation_rounds.jsonl is missing, invalid, or contains duplicate IDs"
        )

    expected_digests: dict[str, str] = {}
    if not errors:
        try:
            tasks = indexed["investigation_tasks.jsonl"]
            findings = indexed["investigation_findings.jsonl"]
            expected_digests = {
                "task-plan": sav.task_plan_snapshot_sha256(
                    root,
                    contract=contract,
                    architecture=architecture,
                    claims=indexed["design_claims.jsonl"],
                    risks=indexed["risk_observations.jsonl"],
                    tasks=tasks,
                    rounds=ordered_rounds,
                ),
                "task-lifecycle": sav.task_lifecycle_snapshot_sha256(
                    tasks=tasks, findings=findings, rounds=ordered_rounds,
                ),
            }
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            errors.append(f"cannot calculate current task validation snapshots: {exc}")

    for filename, stage, digest_field in (
        ("task_plan_validation.json", "task-plan", "task_plan_sha256"),
        ("task_lifecycle_validation.json", "task-lifecycle", "task_lifecycle_sha256"),
    ):
        trace = _json_object(trace_root / filename)
        label = filename
        if not trace:
            errors.append(f"{label}: missing or invalid")
            continue
        if trace.get("stage") != stage:
            errors.append(f"{label}: missing or wrong stage")
        if trace.get("session_id") != session_id:
            errors.append(f"{label}: session_id does not match current session")
        if trace.get("passed") is not True or trace.get("global_passed") is not True:
            errors.append(f"{label}: passed/global_passed is not true")
        valid_ids = trace.get("valid_task_ids")
        if (
            not isinstance(valid_ids, list)
            or any(not isinstance(value, str) or not value for value in valid_ids)
            or len(set(valid_ids)) != len(valid_ids)
            or set(valid_ids) != task_ids
        ):
            errors.append(f"{label}: valid_task_ids do not match current tasks")
        expected_digest = expected_digests.get(stage)
        if not expected_digest or trace.get(digest_field) != expected_digest:
            errors.append(f"{label}: {digest_field} is stale for current stable inputs")
    return not errors, errors[:8]


def _findings(root: Path) -> tuple[dict[str, dict[str, Any]], set[str], bool]:
    values, valid = _jsonl(root / "investigation_findings.jsonl")
    if not valid:
        return {}, set(), False
    findings: dict[str, dict[str, Any]] = {}
    covered_work: set[str] = set()
    for item in values:
        finding_id = item.get("finding_id")
        work_id = item.get("task_id") or item.get("candidate_id")
        if (
            not isinstance(finding_id, str) or not finding_id or finding_id in findings
            or not isinstance(work_id, str) or not work_id
        ):
            return {}, set(), False
        findings[finding_id] = item
        covered_work.add(work_id)
    return findings, covered_work, True


def _critic_finding_ids(root: Path) -> tuple[set[str], bool]:
    values, valid = _jsonl(root / "critic_reviews.jsonl")
    if not valid:
        return set(), False
    result, unique = _identifier_set(values, "finding_id")
    return result, unique


def derive_status(state_root: Path) -> dict[str, Any]:
    root = state_root.resolve()
    state = _json_object(root / "agent_loop_state.json")
    if state.get("status") == "complete" and state.get("stop_reason") == "final_gate_passed":
        return _status(None, [], expected=0, completed=0)
    if state.get("status") == "blocked" and state.get("stop_reason") == "hard_deadline_reached":
        return _status(
            None, [], expected=0, completed=0,
            blocked_reason="hard_deadline_reached",
            errors=["the six-hour hard deadline has been reached"],
            current_phase=str(state.get("current_phase") or "time_limit"),
        )

    session_id = state.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return _status(
            "map_architecture", [], expected=0, completed=0,
            blocked_reason="current_session_missing",
            errors=["agent_loop_state.json has no current session_id"],
        )

    architecture_ready, bootstrap_errors = _architecture_ready(root, session_id)
    if not architecture_ready:
        return _status(
            "map_architecture", ["ARCHITECTURE-MAP"], expected=1, completed=0,
            blocked_reason="architecture_missing_or_stale", errors=bootstrap_errors,
        )

    inventory_ready, bootstrap_errors = _inventory_ready(root, session_id)
    if not inventory_ready:
        return _status(
            "build_inventory", ["DESIGN-INVENTORY"], expected=1, completed=0,
            blocked_reason="design_inventory_missing_or_stale", errors=bootstrap_errors,
        )

    scout_plan_ready, bootstrap_errors = _scout_plan_ready(root, session_id)
    if not scout_plan_ready:
        return _status(
            "build_scout_plan", ["SEMANTIC-SCOUT-PLAN"], expected=1, completed=0,
            blocked_reason="scout_plan_missing_or_stale", errors=bootstrap_errors,
        )

    planned, completed, scout_errors, _plan_digest = _scout_progress(root, session_id)
    pending_scouts = planned - completed
    if not planned or pending_scouts or scout_errors:
        reason = "scout_plan_missing_or_invalid" if not planned else (
            "scout_receipts_invalid" if scout_errors else "scouts_incomplete"
        )
        return _status(
            "finish_scouts", pending_scouts, expected=len(planned),
            completed=len(completed), blocked_reason=reason, errors=scout_errors,
        )

    projection = _candidate_projection(root)
    if not projection[0]:
        return _status(
            "select_candidates", projection[1], expected=projection[2],
            completed=projection[3], blocked_reason=projection[4], errors=projection[5],
        )

    review = _claim_review_progress(root)
    if not review[0]:
        return _status(
            "review_claims", review[1], expected=review[2], completed=review[3],
            blocked_reason=review[4],
        )

    selection = _json_object(root / "candidate_selection.json")
    candidate_ids = set(selection.get("candidate_ids", []))
    selected, selection_exists, pending_candidates, task_reason = _selected_work(
        root, candidate_ids,
    )
    if not selection_exists:
        return _status(
            "plan_investigations", pending_candidates, expected=len(candidate_ids),
            completed=len(candidate_ids - pending_candidates), blocked_reason=task_reason,
        )

    task_gate_passed, task_gate_errors = _task_validation_complete(
        root, session_id, set(selected),
    )
    if not task_gate_passed:
        return _status(
            "plan_investigations", set(selected), expected=len(selected), completed=0,
            blocked_reason="task_validation_missing_or_failed",
            errors=task_gate_errors,
        )

    findings, investigated_work, findings_valid = _findings(root)
    deferred_work = {
        identifier for identifier, item in selected.items()
        if item.get("status") == "deferred"
    }
    required_work = set(selected) - deferred_work
    pending_work = required_work - investigated_work
    extra_work = investigated_work - set(selected)
    duplicate_work = len(investigated_work) != len(findings)
    if not findings_valid or pending_work or extra_work or duplicate_work:
        invalid_work = pending_work or extra_work or set(selected)
        return _status(
            "finish_investigations", invalid_work,
            expected=len(required_work),
            completed=len(required_work.intersection(investigated_work)),
            blocked_reason=(
                "investigation_findings_missing_or_invalid"
                if not findings_valid or extra_work or duplicate_work
                else "investigations_incomplete"
            ),
        )

    critic_ids, critics_valid = _critic_finding_ids(root)
    pending_findings = set(findings) - critic_ids
    extra_critics = critic_ids - set(findings)
    if not critics_valid or pending_findings or extra_critics:
        return _status(
            "finish_critics", pending_findings or extra_critics or set(findings),
            expected=len(findings), completed=len(set(findings).intersection(critic_ids)),
            blocked_reason=(
                "critic_reviews_missing_or_invalid"
                if not critics_valid or extra_critics else "critics_incomplete"
            ),
        )

    return _status("run_final", [], expected=len(findings), completed=len(findings))


def next_action(state_root: Path) -> str | None:
    return derive_status(state_root)["next_action"]


def _projection_fingerprint(status: dict[str, Any]) -> str:
    value = {
        key: status[key]
        for key in (
            "next_action", "current_phase", "pending_ids", "counts",
            "blocked_reason", "errors", "terminal",
        )
    }
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _atomic_save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def reconcile(state_root: Path, status: dict[str, Any] | None = None) -> dict[str, Any]:
    root = state_root.resolve()
    state_path = root / "agent_loop_state.json"
    state = _json_object(state_path)
    hard_deadline_blocked = (
        state.get("status") == "blocked"
        and state.get("stop_reason") == "hard_deadline_reached"
    )
    projection = (
        derive_status(root)
        if hard_deadline_blocked
        else (status or derive_status(root))
    )
    now = ac.now_iso()
    fingerprint = _projection_fingerprint(projection)
    previous = state.get("pipeline") if isinstance(state.get("pipeline"), dict) else {}
    progressed = previous.get("fingerprint") != fingerprint
    last_progress_at = now if progressed else str(previous.get("last_progress_at") or now)

    state["current_phase"] = projection["current_phase"]
    state["next_actions"] = (
        [projection["next_action"]] if projection["next_action"] else []
    )
    state["status"] = (
        "blocked" if hard_deadline_blocked
        else "complete" if projection["terminal"] else "in_progress"
    )
    state["pipeline"] = {
        **projection,
        "fingerprint": fingerprint,
        "last_progress_at": last_progress_at,
        "checked_at": now,
    }
    state["pipeline_blocked_reason"] = projection["blocked_reason"]
    state["last_progress_at"] = last_progress_at
    state["controller_checked_at"] = now
    if progressed:
        state["updated_at"] = now
    _atomic_save_json(state_path, state)
    return {**projection, "last_progress_at": last_progress_at}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Return and reconcile the single valid Goal-Agent pipeline action."
    )
    parser.add_argument("command", choices=["status"])
    parser.add_argument("--state-root", required=True)
    args = parser.parse_args(argv)
    result = reconcile(Path(args.state_root))
    action = result["next_action"]
    if action is not None and action not in NEXT_ACTIONS:
        raise RuntimeError(f"invalid pipeline action: {action}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
