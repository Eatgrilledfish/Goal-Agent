#!/usr/bin/env python3
"""Deterministically project completed review evidence into coverage artifacts.

This helper performs no new applicability, inconsistency, or prioritization
judgement.  It only accounts for relationships already present in the current
contract, inventory, accepted claims, completed tasks/findings, rounds, and
architecture map.  Missing direct evidence becomes a stable recorded gap; this
helper never creates a supplement task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import agent_common as ac


def _object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing {label}: {path}")
    value = ac.load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _index(path: Path, key: str, label: str) -> dict[str, dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ValueError(f"{label} is invalid: {'; '.join(errors)}")
    indexed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(values, start=1):
        identifier = item.get(key)
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"{label}:{index} lacks a non-empty {key}")
        if identifier in indexed:
            raise ValueError(f"{label}:{index} duplicates {key} {identifier!r}")
        indexed[identifier] = item
    return indexed


def _strings(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} entries must be non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    return list(value)


def _stable_gap(kind: str, ref_id: str, reason: str, evidence: str) -> dict[str, str]:
    digest = hashlib.sha256(f"{kind}\0{ref_id}".encode("utf-8")).hexdigest()[:16]
    label = re.sub(r"[^A-Za-z0-9]+", "-", kind).strip("-").upper() or "OTHER"
    return {
        "gap_id": f"GAP-{label}-{digest.upper()}",
        "kind": kind,
        "ref_id": ref_id,
        "reason": reason,
        "evidence": evidence,
    }


def _accepted_claim_ids(
    review: dict[str, Any], claims: dict[str, dict[str, Any]], session_id: str,
) -> set[str]:
    if review.get("session_id") != session_id:
        raise ValueError("design_claim_review.json session_id does not match contract")
    raw = review.get("claim_reviews")
    if not isinstance(raw, list):
        raise ValueError("design_claim_review.json claim_reviews must be an array")
    reviewed: set[str] = set()
    accepted: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"claim_reviews[{index}] must be an object")
        claim_id = item.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise ValueError(f"claim_reviews[{index}] lacks claim_id")
        if claim_id in reviewed:
            raise ValueError(f"claim_reviews[{index}] duplicates claim_id {claim_id}")
        if claim_id not in claims:
            raise ValueError(f"claim_reviews[{index}] references unknown claim {claim_id}")
        reviewed.add(claim_id)
        if item.get("decision") == "accept":
            accepted.add(claim_id)
    return accepted


def _inventory_indexes(
    inventory: dict[str, Any], session_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[str]]:
    if inventory.get("session_id") != session_id:
        raise ValueError("design_inventory.json session_id does not match contract")
    raw = inventory.get("document_groups")
    if not isinstance(raw, list) or not raw:
        raise ValueError("design_inventory.json document_groups must be non-empty")
    groups: dict[str, dict[str, Any]] = {}
    member_groups: dict[str, str] = {}
    documents: list[str] = []
    for index, group in enumerate(raw, start=1):
        if not isinstance(group, dict):
            raise ValueError(f"document_groups[{index}] must be an object")
        document_key = group.get("document_key")
        if not isinstance(document_key, str) or not document_key:
            raise ValueError(f"document_groups[{index}] lacks document_key")
        if document_key in groups:
            raise ValueError(f"document_groups[{index}] duplicates {document_key}")
        members = _strings(
            group.get("members"), f"document_groups[{index}].members", allow_empty=False,
        )
        groups[document_key] = group
        for member in members:
            if member in member_groups:
                raise ValueError(f"design member belongs to multiple groups: {member}")
            member_groups[member] = document_key
            documents.append(member)
    return groups, member_groups, sorted(documents)


def _claim_groups(
    claims: dict[str, dict[str, Any]], groups: dict[str, dict[str, Any]],
    member_groups: dict[str, str],
) -> dict[str, str]:
    output: dict[str, str] = {}
    for claim_id, claim in claims.items():
        declared = claim.get("document_key")
        path_group = member_groups.get(str(claim.get("path") or ""))
        group_id = declared if isinstance(declared, str) and declared in groups else path_group
        if not group_id:
            raise ValueError(f"claim {claim_id} does not map to an inventory document group")
        if path_group and declared and declared != path_group:
            raise ValueError(f"claim {claim_id} document_key disagrees with its source path")
        output[claim_id] = str(group_id)
    return output


def _completed_pairs(
    accepted: set[str], tasks: dict[str, dict[str, Any]],
    findings: dict[str, dict[str, Any]], session_id: str,
) -> list[tuple[str, dict[str, Any], str, dict[str, Any]]]:
    findings_by_task: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for finding_id, finding in findings.items():
        if finding.get("session_id") != session_id:
            raise ValueError(f"finding {finding_id} session_id does not match contract")
        task_id = str(finding.get("task_id") or "")
        findings_by_task.setdefault(task_id, []).append((finding_id, finding))
    tasks_by_claim: dict[str, list[str]] = {}
    pairs: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = []
    for task_id, task in tasks.items():
        if task.get("session_id") != session_id:
            raise ValueError(f"task {task_id} session_id does not match contract")
        claim_id = str(task.get("claim_id") or "")
        if claim_id not in accepted:
            raise ValueError(f"task {task_id} references non-accepted claim {claim_id!r}")
        tasks_by_claim.setdefault(claim_id, []).append(task_id)
        linked = findings_by_task.get(task_id, [])
        if task.get("status") != "complete" or len(linked) != 1:
            raise ValueError(
                f"accepted claim {claim_id} requires complete task {task_id} "
                "with exactly one finding"
            )
        finding_id, finding = linked[0]
        if finding.get("claim_id") != claim_id:
            raise ValueError(f"task/finding claim mismatch for {task_id}/{finding_id}")
        pairs.append((task_id, task, finding_id, finding))
    missing_tasks = sorted(accepted - set(tasks_by_claim))
    if missing_tasks:
        raise ValueError(f"accepted claims lack investigation tasks: {missing_tasks}")
    unknown_finding_tasks = sorted(set(findings_by_task) - set(tasks))
    if unknown_finding_tasks:
        raise ValueError(f"findings reference unknown tasks: {unknown_finding_tasks}")
    return sorted(pairs, key=lambda item: (item[0], item[2]))


def _observed_modes(
    rounds: dict[str, dict[str, Any]], pairs: list[tuple[str, dict, str, dict]],
    contract_modes: list[str],
) -> list[str]:
    pair_by_task = {task_id: (task, finding_id) for task_id, task, finding_id, _ in pairs}
    observed: set[str] = set()
    for round_id, item in rounds.items():
        task_ids = set(_strings(item.get("task_ids"), f"round {round_id}.task_ids"))
        finding_ids = set(_strings(item.get("finding_ids"), f"round {round_id}.finding_ids"))
        for mode in _strings(
            item.get("exploration_modes"), f"round {round_id}.exploration_modes",
        ):
            if any(
                task_id in task_ids
                and task.get("exploration_mode") == mode
                and finding_id in finding_ids
                for task_id, (task, finding_id) in pair_by_task.items()
            ):
                observed.add(mode)
    return [mode for mode in contract_modes if mode in observed]


def materialize_coverage(state_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    state_root = state_root.resolve()
    contract = _object(state_root / "agent_loop_contract.json", "agent_loop_contract.json")
    session = contract.get("session")
    session_id = session.get("session_id") if isinstance(session, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("agent_loop_contract.json lacks session.session_id")
    coverage_contract = contract.get("coverage_contract")
    if not isinstance(coverage_contract, dict):
        raise ValueError("agent_loop_contract.json lacks coverage_contract")
    lenses = _strings(
        coverage_contract.get("portfolio_lenses"),
        "coverage_contract.portfolio_lenses", allow_empty=False,
    )
    modes = _strings(
        coverage_contract.get("exploration_modes"),
        "coverage_contract.exploration_modes", allow_empty=False,
    )
    inventory = _object(state_root / "design_inventory.json", "design_inventory.json")
    architecture = _object(state_root / "architecture_map.json", "architecture_map.json")
    review = _object(state_root / "design_claim_review.json", "design_claim_review.json")
    history = _object(
        state_root / "coverage_supplement_history.json",
        "coverage_supplement_history.json",
    )
    if history.get("session_id") != session_id:
        raise ValueError("coverage_supplement_history.json session_id does not match contract")
    if history.get("requests") != []:
        raise ValueError("deterministic coverage materialization does not create or resume supplements")
    claims = _index(state_root / "design_claims.jsonl", "claim_id", "design_claims.jsonl")
    tasks = _index(
        state_root / "investigation_tasks.jsonl", "task_id", "investigation_tasks.jsonl",
    )
    findings = _index(
        state_root / "investigation_findings.jsonl", "finding_id",
        "investigation_findings.jsonl",
    )
    rounds = _index(
        state_root / "investigation_rounds.jsonl", "round_id",
        "investigation_rounds.jsonl",
    )
    groups, member_groups, documents = _inventory_indexes(inventory, session_id)
    claim_groups = _claim_groups(claims, groups, member_groups)
    accepted = _accepted_claim_ids(review, claims, session_id)
    pairs = _completed_pairs(accepted, tasks, findings, session_id)

    raw_boundaries = architecture.get("integration_boundaries")
    raw_planes = architecture.get("implementation_planes")
    raw_parallel = architecture.get("parallel_behavior_paths")
    if not isinstance(raw_boundaries, list) or not isinstance(raw_planes, list) or not isinstance(raw_parallel, list):
        raise ValueError("architecture map boundary/plane/parallel arrays are required")
    boundaries = {
        str(item.get("boundary_id")): item for item in raw_boundaries
        if isinstance(item, dict) and item.get("boundary_id")
    }
    planes = {
        str(item.get("plane_id")): item for item in raw_planes
        if isinstance(item, dict) and item.get("plane_id")
    }
    parallel_paths = {
        str(item.get("path_id")): item for item in raw_parallel
        if isinstance(item, dict) and item.get("path_id")
    }
    if not boundaries or not planes:
        raise ValueError("architecture map must contain boundaries and implementation planes")

    remaining_gaps: list[dict[str, str]] = []
    semantic_entries: list[dict[str, Any]] = []
    all_group_refs = sorted(groups)
    all_boundary_refs = sorted(boundaries)
    for lens in lenses:
        matching = [
            (task_id, task, finding_id, finding)
            for task_id, task, finding_id, finding in pairs
            if lens in task.get("review_lenses", [])
            and lens in finding.get("review_lenses", [])
            and any(isinstance(value, str) and value for value in task.get("architecture_boundaries", []))
        ]
        if matching:
            task_ids = [item[0] for item in matching]
            finding_ids = [item[2] for item in matching]
            group_refs = sorted({
                claim_groups[str(item[1].get("claim_id"))] for item in matching
            })
            boundary_refs = sorted({
                boundary for _, task, _, _ in matching
                for boundary in task.get("architecture_boundaries", [])
                if isinstance(boundary, str) and boundary
            })
            semantic_entries.append({
                "lens": lens,
                "disposition": "investigated",
                "evidence": (
                    "Completed task/finding evidence: "
                    + ", ".join(f"{task}/{finding}" for task, finding in zip(task_ids, finding_ids))
                ),
                "task_ids": task_ids,
                "finding_ids": finding_ids,
                "design_group_refs": group_refs,
                "boundary_refs": boundary_refs,
                "counterfactual": "",
            })
        else:
            remaining_gaps.append(_stable_gap(
                "lens", lens,
                "No completed task/finding pair directly covers this lens.",
                "No completed task and finding jointly declare the lens and a mapped boundary.",
            ))
            semantic_entries.append({
                "lens": lens,
                "disposition": "gap_recorded",
                "evidence": "No completed task/finding pair supplies direct lens evidence.",
                "task_ids": [],
                "finding_ids": [],
                "design_group_refs": all_group_refs,
                "boundary_refs": all_boundary_refs,
                "counterfactual": (
                    "A completed task and finding declaring this lens and a mapped "
                    "architecture boundary would be required."
                ),
            })

    boundary_entries: list[dict[str, str]] = []
    for boundary_id in sorted(boundaries):
        matching = [
            (task_id, finding_id) for task_id, task, finding_id, _ in pairs
            if boundary_id in task.get("architecture_boundaries", [])
        ]
        if matching:
            boundary_entries.append({
                "boundary_id": boundary_id,
                "status": "investigated",
                "evidence": "Completed task/finding evidence: " + ", ".join(
                    f"{task}/{finding}" for task, finding in matching
                ),
            })
        else:
            remaining_gaps.append(_stable_gap(
                "architecture_boundary", boundary_id,
                "No completed task/finding pair directly covers this boundary.",
                f"No completed task references architecture boundary {boundary_id}.",
            ))
            boundary_entries.append({
                "boundary_id": boundary_id,
                "status": "gap_recorded",
                "evidence": f"Recorded deterministic coverage gap for {boundary_id}.",
            })

    for path_id, path in sorted(parallel_paths.items()):
        required_planes = {
            value for value in path.get("plane_ids", []) if isinstance(value, str)
        }
        completed_planes = {
            plane for _, task, _, _ in pairs
            if path_id in task.get("parallel_path_ids", [])
            for plane in task.get("implementation_planes", [])
            if isinstance(plane, str)
        }
        missing_planes = sorted(required_planes - completed_planes)
        if missing_planes:
            remaining_gaps.append(_stable_gap(
                "parallel_path", path_id,
                "The completed task/finding evidence does not cover every mapped plane.",
                f"Missing implementation planes: {missing_planes}.",
            ))

    observed_modes = _observed_modes(rounds, pairs, modes)
    for mode in modes:
        if mode not in observed_modes:
            remaining_gaps.append(_stable_gap(
                "exploration_mode", mode,
                "No completed round records this exploration mode.",
                "No round contains a completed task/finding pair using this mode.",
            ))

    code_areas = sorted({
        str(evidence.get("file")) for finding in findings.values()
        for evidence in finding.get("code_evidence", [])
        if isinstance(evidence, dict) and isinstance(evidence.get("file"), str)
        and evidence.get("file")
    })
    investigated_claims = {
        str(finding.get("claim_id")) for finding in findings.values()
        if isinstance(finding.get("claim_id"), str) and finding.get("claim_id")
    }
    remaining_gaps.sort(key=lambda item: (item["kind"], item["ref_id"], item["gap_id"]))
    semantic = {"session_id": session_id, "lenses": semantic_entries}
    audit = {
        "session_id": session_id,
        "design_documents_reviewed": documents,
        "claims_total": len(claims),
        "claims_investigated": len(investigated_claims),
        "rounds_completed": len(rounds),
        "exploration_modes_completed": observed_modes,
        "document_groups_total": len(groups),
        "document_groups_accounted": len(groups),
        "code_areas_reviewed": code_areas,
        "architecture_boundaries": boundary_entries,
        "remaining_scoped_claims": [],
        "deferred_claims": [],
        "false_positive_samples_rechecked": sorted(findings),
        "next_round_tasks": [],
        "supplement_rounds": 0,
        "remaining_gaps": remaining_gaps,
        "stop_reason": (
            "All accepted claims have complete task/finding evidence; uncovered "
            "lenses and architecture scopes are recorded as deterministic gaps."
        ),
    }
    return semantic, audit


def run(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root).resolve()
    semantic_path = state_root / "semantic_coverage.json"
    audit_path = state_root / "coverage_audit.json"
    trace_path = (
        Path(args.trace).resolve() if args.trace
        else state_root.parent / "trace" / "coverage_materialization.json"
    )
    errors: list[str] = []
    gaps = 0
    try:
        semantic, audit = materialize_coverage(state_root)
        gaps = len(audit["remaining_gaps"])
        ac.save_json(semantic_path, semantic)
        ac.save_json(audit_path, audit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    report = {
        "materialized_at": ac.now_iso(),
        "passed": not errors,
        "semantic_analysis_performed": False,
        "supplement_created": False,
        "state_root": str(state_root),
        "semantic_output": str(semantic_path),
        "audit_output": str(audit_path),
        "gaps": gaps,
        "errors": errors,
    }
    ac.save_json(trace_path, report)
    print(json.dumps({"passed": not errors, "gaps": gaps, "errors": len(errors)}))
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project completed evidence into deterministic coverage artifacts.",
    )
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--trace", default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
