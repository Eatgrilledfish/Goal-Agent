#!/usr/bin/env python3
"""Report the one mechanically valid next action for the agent pipeline.

This controller deliberately makes no semantic decisions.  It only compares
planned work with validated handoff ledgers so that a model cannot skip ahead
to coverage/finalization while breadth, investigations, or critics are open.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac


NEXT_ACTIONS = {
    "finish_scouts",
    "select_candidates",
    "review_claims",
    "plan_investigations",
    "finish_investigations",
    "finish_critics",
    "run_final",
}


def _json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = ac.load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _jsonl(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Load a JSONL ledger and distinguish a valid empty file from absence."""
    if not path.is_file():
        return [], False
    values, errors = ac.load_jsonl(path)
    return values, not errors


def _planned_scout_ids(root: Path) -> set[str]:
    plan = _json_object(root / "scout_plan.json")
    if not plan:
        plan = _json_object(root / "risk_sweep_plan.json")
    slices = plan.get("slices") if isinstance(plan.get("slices"), list) else []
    result: set[str] = set()
    for item in slices:
        if not isinstance(item, dict):
            continue
        identifier = item.get("scout_id") or item.get("sweep_id")
        if isinstance(identifier, str) and identifier:
            result.add(identifier)
    return result


def _completed_scout_ids(root: Path) -> set[str]:
    receipts, valid = _jsonl(root / "scout_receipts.jsonl")
    if not valid:
        return set()
    result: set[str] = set()
    for item in receipts:
        identifier = item.get("scout_id") or item.get("sweep_id")
        completed = item.get("status") in {"complete", "completed"} or item.get("passed") is True
        if isinstance(identifier, str) and identifier and completed:
            result.add(identifier)
    return result


def _selected_work(root: Path) -> tuple[dict[str, dict[str, Any]], bool]:
    """Return selected investigation identities and whether selection exists."""
    selected_path = root / "selected_candidates.jsonl"
    values, valid = _jsonl(selected_path)
    selection_exists = selected_path.is_file() and valid

    if not selected_path.is_file():
        task_path = root / "investigation_tasks.jsonl"
        values, valid = _jsonl(task_path)
        selection_exists = task_path.is_file() and valid

    selected: dict[str, dict[str, Any]] = {}
    for item in values:
        identifier = item.get("task_id") or item.get("candidate_id")
        if isinstance(identifier, str) and identifier:
            selected[identifier] = item
    return selected, selection_exists


def _candidate_projection_complete(root: Path) -> bool:
    selection = _json_object(root / "candidate_selection.json")
    candidate_ids = selection.get("candidate_ids")
    if not isinstance(candidate_ids, list) or any(
        not isinstance(value, str) or not value for value in candidate_ids
    ):
        return False
    claims, claims_valid = _jsonl(root / "design_claims.jsonl")
    lookups, lookups_valid = _jsonl(root / "design_lookup_requests.jsonl")
    if not claims_valid or not lookups_valid:
        return False
    selected = set(candidate_ids)
    claim_candidates = {
        item.get("candidate_id") for item in claims
        if isinstance(item.get("candidate_id"), str)
    }
    lookup_candidates = {
        item.get("candidate_id") for item in lookups
        if isinstance(item.get("candidate_id"), str)
    }
    return (
        len(claims) == len(selected)
        and len(lookups) == len(selected)
        and claim_candidates == selected
        and lookup_candidates == selected
    )


def _claim_review_complete(root: Path) -> bool:
    scope = _json_object(root / "claim_review_scope.json")
    review = _json_object(root / "design_claim_review.json")
    scoped = scope.get("claim_ids")
    reviews = review.get("claim_reviews")
    if not isinstance(scoped, list) or not isinstance(reviews, list):
        return False
    reviewed = {
        item.get("claim_id") for item in reviews
        if isinstance(item, dict) and item.get("decision") == "accept"
    }
    if set(scoped) != reviewed:
        return False
    manifest = _json_object(root / "workspace_manifest.json")
    log_root = manifest.get("paths", {}).get("log_root")
    trace = _json_object(
        Path(log_root).resolve() / "trace" / "claim_review_validation.json"
    ) if isinstance(log_root, str) and log_root else {}
    return (
        trace.get("passed") is True
        and set(trace.get("accepted_claim_ids", [])) == set(scoped)
    )


def _findings(root: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    values, valid = _jsonl(root / "investigation_findings.jsonl")
    if not valid:
        return {}, set()
    findings: dict[str, dict[str, Any]] = {}
    covered_work: set[str] = set()
    for item in values:
        finding_id = item.get("finding_id")
        work_id = item.get("task_id") or item.get("candidate_id")
        if isinstance(finding_id, str) and finding_id:
            findings[finding_id] = item
            if isinstance(work_id, str) and work_id:
                covered_work.add(work_id)
    return findings, covered_work


def _critic_finding_ids(root: Path) -> set[str]:
    values, valid = _jsonl(root / "critic_reviews.jsonl")
    if not valid:
        return set()
    return {
        str(item["finding_id"])
        for item in values
        if isinstance(item.get("finding_id"), str) and item.get("finding_id")
    }


def next_action(state_root: Path) -> str:
    root = state_root.resolve()
    planned_scouts = _planned_scout_ids(root)
    completed_scouts = _completed_scout_ids(root)
    if not planned_scouts or not planned_scouts.issubset(completed_scouts):
        return "finish_scouts"

    if not _candidate_projection_complete(root):
        return "select_candidates"

    if not _claim_review_complete(root):
        return "review_claims"

    selected, selection_exists = _selected_work(root)
    if not selection_exists:
        return "plan_investigations"

    findings, investigated_work = _findings(root)
    deferred_work = {
        identifier
        for identifier, item in selected.items()
        if item.get("status") == "deferred"
    }
    if set(selected) - deferred_work - investigated_work:
        return "finish_investigations"

    if set(findings) - _critic_finding_ids(root):
        return "finish_critics"

    return "run_final"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Return the single valid next Goal-Agent pipeline action."
    )
    parser.add_argument("command", choices=["status"])
    parser.add_argument("--state-root", required=True)
    args = parser.parse_args(argv)
    action = next_action(Path(args.state_root))
    if action not in NEXT_ACTIONS:  # defensive assertion for callers
        raise RuntimeError(f"invalid pipeline action: {action}")
    print(json.dumps({"next_action": action}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
