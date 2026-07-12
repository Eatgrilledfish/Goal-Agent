#!/usr/bin/env python3
"""Project validated scout candidates into claims and investigation tasks.

The model ranks candidate IDs only.  This helper preserves the scout's design
and code provenance so later agents cannot replace the question, starting
points, or origin while copying data between artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import design_source_materializer as materializer
import stage_artifact_validator as sav


MAX_CANDIDATES = 12
MISMATCH_SIGNALS = {
    "direct_conflict", "capability_absence", "cross_plane_mismatch", "uncertain",
}


def _load_jsonl_index(path: Path, key: str) -> dict[str, dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ValueError("; ".join(errors))
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(values, start=1):
        identifier = item.get(key)
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{path.name}:{index}: missing {key}")
        if identifier in result:
            raise ValueError(f"{path.name}:{index}: duplicate {key} {identifier}")
        result[identifier] = item
    return result


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    ac.ensure_dir(path.parent)
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _stable(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def _current_session(root: Path) -> str:
    state = ac.load_json(root / "agent_loop_state.json")
    session_id = state.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("agent_loop_state.json lacks session_id")
    return session_id


def _require_complete_scouts(root: Path) -> None:
    plan = ac.load_json(root / "risk_sweep_plan.json")
    expected = {
        str(item.get("sweep_id") or "") for item in plan.get("slices", [])
        if isinstance(item, dict) and item.get("sweep_id")
    }
    receipts = _load_jsonl_index(root / "scout_receipts.jsonl", "sweep_id")
    plan_digest = ac.sha256_file(root / "risk_sweep_plan.json")
    complete = {
        sweep_id for sweep_id, item in receipts.items()
        if item.get("risk_sweep_plan_sha256") == plan_digest
        and item.get("status") == "complete"
    }
    missing = sorted(expected - complete)
    extra = sorted(complete - expected)
    if not expected or missing or extra:
        raise ValueError(
            "all semantic scouts must complete before candidate selection; "
            f"missing={missing}, extra={extra}"
        )
    observations = _load_jsonl_index(
        root / "risk_observations.jsonl", "observation_id",
    )
    for sweep_id in sorted(expected):
        receipt = receipts[sweep_id]
        candidate_ids = receipt.get("candidate_ids")
        if not isinstance(candidate_ids, list) or any(
            not isinstance(value, str) or not value for value in candidate_ids
        ) or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError(f"{sweep_id}: receipt candidate_ids are invalid")
        if receipt.get("candidate_count") != len(candidate_ids):
            raise ValueError(f"{sweep_id}: receipt candidate_count is inconsistent")
        merged = {
            observation_id for observation_id, item in observations.items()
            if item.get("sweep_id") == sweep_id
        }
        if set(candidate_ids) != merged:
            raise ValueError(
                f"{sweep_id}: receipt candidates are not fully merged; "
                f"missing={sorted(set(candidate_ids) - merged)}, "
                f"extra={sorted(merged - set(candidate_ids))}"
            )


def _inventory_indexes(
    inventory: dict[str, Any],
) -> tuple[dict[str, tuple[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    sections: dict[str, tuple[str, dict[str, Any]]] = {}
    groups: dict[str, dict[str, Any]] = {}
    for group in inventory.get("document_groups", []):
        if not isinstance(group, dict):
            continue
        key = group.get("document_key")
        if not isinstance(key, str) or not key:
            continue
        groups[key] = group
        for section in group.get("sections", []):
            if isinstance(section, dict) and isinstance(section.get("section_id"), str):
                sections[section["section_id"]] = (key, section)
    return sections, groups


def _candidate_claim(
    observation: dict[str, Any], *, session_id: str,
    sections: dict[str, tuple[str, dict[str, Any]]], design_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    observation_id = str(observation["observation_id"])
    if observation.get("mismatch_signal") not in MISMATCH_SIGNALS:
        raise ValueError(
            f"{observation_id}: selected observation lacks a candidate mismatch_signal"
        )
    requirement = observation.get("design_requirement")
    if not isinstance(requirement, dict):
        raise ValueError(f"{observation_id}: design_requirement must be an object")
    required = (
        "source_ref", "subject", "trigger", "obligation", "observable_result",
        "normative_strength", "applicability", "exceptions", "ambiguities",
    )
    missing = [field for field in required if field not in requirement]
    if missing:
        raise ValueError(f"{observation_id}: design_requirement missing {missing}")
    observable = requirement.get("observable_result")
    prefix = "The reachable implementation does not produce the required observable result:"
    if not isinstance(observable, str) or not observable.strip():
        raise ValueError(f"{observation_id}: observable_result must be non-empty")
    if observable.strip().startswith(prefix):
        raise ValueError(
            f"{observation_id}: observable_result must contain expected behavior only"
        )
    section_ids = observation.get("design_section_ids")
    if not isinstance(section_ids, list) or not section_ids:
        raise ValueError(f"{observation_id}: design_section_ids must not be empty")
    known = [sections[value] for value in section_ids if value in sections]
    if len(known) != len(section_ids):
        raise ValueError(f"{observation_id}: design_section_ids contain unknown IDs")
    document_keys = {key for key, _section in known}
    if len(document_keys) != 1:
        raise ValueError(f"{observation_id}: one atomic candidate must use one document group")
    raw_ref = requirement.get("source_ref")
    if not isinstance(raw_ref, dict):
        raise ValueError(f"{observation_id}: source_ref must be an object")
    in_selected_section = any(
        raw_ref.get("path") == section.get("path")
        and isinstance(raw_ref.get("line_start"), int)
        and isinstance(raw_ref.get("line_end"), int)
        and section.get("line_start", 0) <= raw_ref["line_start"]
        and raw_ref["line_end"] <= section.get("line_end", -1)
        for _key, section in known
    )
    if not in_selected_section:
        raise ValueError(
            f"{observation_id}: design requirement source_ref escapes selected sections"
        )
    claim_id = _stable("CLAIM", observation_id)
    request_id = _stable("LOOKUP", observation_id)
    raw_claim = {
        "claim_id": claim_id,
        "candidate_id": observation_id,
        "request_id": request_id,
        "session_id": session_id,
        "document_key": next(iter(document_keys)),
        **requirement,
    }
    claim = materializer.materialize_claims([raw_claim], design_root)[0]
    lookup = {
        "request_id": request_id,
        "candidate_id": observation_id,
        "session_id": session_id,
        "origin": "semantic_scout",
        "origin_id": observation_id,
        "sweep_id": observation.get("sweep_id"),
        "direction": observation.get("direction"),
        "document_keys": sorted(document_keys),
        "section_ids": list(section_ids),
        "question": observation.get("behavior_question"),
        "required_branch": f"{requirement.get('subject')} | {requirement.get('trigger')}",
        "mismatch_signal": observation.get("mismatch_signal"),
        "code_evidence": observation.get("code_evidence", []),
        "architecture_boundaries": observation.get("architecture_boundaries", []),
        "implementation_planes": observation.get("implementation_planes", []),
        "parallel_path_ids": observation.get("parallel_path_ids", []),
        "review_lenses": observation.get("review_lenses", []),
    }
    return claim, lookup


def select(root: Path, design_root: Path, selection_path: Path) -> dict[str, Any]:
    _require_complete_scouts(root)
    selection = ac.load_json(selection_path)
    candidate_ids = selection.get("candidate_ids")
    if (
        not isinstance(candidate_ids, list) or not candidate_ids
        or len(candidate_ids) > MAX_CANDIDATES
        or len(set(candidate_ids)) != len(candidate_ids)
        or any(not isinstance(value, str) or not value for value in candidate_ids)
    ):
        raise ValueError(
            f"candidate_ids must contain 1..{MAX_CANDIDATES} unique IDs"
        )
    observations = _load_jsonl_index(root / "risk_observations.jsonl", "observation_id")
    unknown = sorted(set(candidate_ids) - set(observations))
    if unknown:
        raise ValueError(f"candidate selection contains unknown IDs: {unknown}")
    session_id = _current_session(root)
    inventory = ac.load_json(root / "design_inventory.json")
    sections, groups = _inventory_indexes(inventory)
    claims: list[dict[str, Any]] = []
    lookups: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        claim, lookup = _candidate_claim(
            observations[candidate_id], session_id=session_id,
            sections=sections, design_root=design_root,
        )
        claims.append(claim)
        lookups.append(lookup)
    claim_ids_by_group: dict[str, list[str]] = {key: [] for key in groups}
    for claim in claims:
        claim_ids_by_group[str(claim["document_key"])].append(str(claim["claim_id"]))
    coverage = {
        "session_id": session_id,
        "document_groups": [
            {
                "document_key": key,
                "members": group.get("members", []),
                "disposition": (
                    "applicable" if group.get("scope_relation") in {"required", "in_scope"}
                    else "supporting"
                ),
                "evidence": (
                    "Supplied design remains searchable; candidate selection does not "
                    "make unselected groups inapplicable."
                ),
                "claim_ids": claim_ids_by_group[key],
                "behavior_families": [],
            }
            for key, group in groups.items()
        ],
    }
    _write_jsonl(root / "design_lookup_requests.jsonl", lookups)
    _write_jsonl(root / "design_claims.jsonl", claims)
    ac.save_json(root / "design_coverage.json", coverage)
    ac.save_json(root / "claim_review_scope.json", {
        "session_id": session_id,
        "round_id": "CLAIM-REVIEW-001",
        "claim_ids": [claim["claim_id"] for claim in claims],
    })
    return {"candidate_ids": candidate_ids, "claim_ids": [c["claim_id"] for c in claims]}


def plan(root: Path) -> dict[str, Any]:
    session_id = _current_session(root)
    claims = _load_jsonl_index(root / "design_claims.jsonl", "claim_id")
    observations = _load_jsonl_index(root / "risk_observations.jsonl", "observation_id")
    review = ac.load_json(root / "design_claim_review.json")
    reviews = {
        str(item.get("claim_id")): item for item in review.get("claim_reviews", [])
        if isinstance(item, dict) and item.get("claim_id")
    }
    scope = ac.load_json(root / "claim_review_scope.json").get("claim_ids", [])
    if set(reviews) != set(scope):
        raise ValueError("claim review membership does not match selected candidates")
    rejected = sorted(
        claim_id for claim_id in scope
        if reviews.get(claim_id, {}).get("decision") != "accept"
    )
    if rejected:
        raise ValueError(f"selected claims require repair before task projection: {rejected}")
    manifest = ac.load_json(root / "workspace_manifest.json")
    log_root = manifest.get("paths", {}).get("log_root")
    trace_path = (
        Path(log_root).resolve() / "trace" / "claim_review_validation.json"
        if isinstance(log_root, str) and log_root else None
    )
    trace = ac.load_json(trace_path) if trace_path and trace_path.is_file() else {}
    if (
        trace.get("passed") is not True
        or trace.get("session_id") != session_id
        or set(trace.get("accepted_claim_ids", [])) != set(scope)
    ):
        raise ValueError(
            "a current passed claim-check is required before task projection"
        )
    tasks: list[dict[str, Any]] = []
    for claim_id in scope:
        claim = claims[claim_id]
        candidate_id = str(claim.get("candidate_id") or "")
        observation = observations.get(candidate_id)
        if observation is None:
            raise ValueError(f"{claim_id}: candidate observation is missing")
        code_evidence = observation.get("code_evidence")
        if not isinstance(code_evidence, list) or not code_evidence:
            raise ValueError(f"{candidate_id}: candidate lacks code evidence")
        starting_points = [
            {
                "file": item.get("file"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
            }
            for item in code_evidence if isinstance(item, dict)
        ]
        task_id = _stable("TASK", candidate_id)
        tasks.append({
            "task_id": task_id,
            "candidate_id": candidate_id,
            "request_id": claim.get("request_id"),
            "session_id": session_id,
            "claim_id": claim_id,
            "claim_branch": ac.canonical_claim_branch(claim),
            "hypothesis": ac.canonical_claim_hypothesis(claim),
            "obligation_sha256": sav.claim_obligation_sha256(claim),
            "starting_points": starting_points,
            "supporting_evidence_needed": [
                "Reachable control-flow or structured capability-absence evidence",
            ],
            "disconfirming_evidence_needed": [
                "Alternate implementation, configuration, registration, or compensating path",
            ],
            "review_lenses": observation.get("review_lenses", [])[:3],
            "exploration_mode": (
                "design-to-code obligation tracing"
                if observation.get("direction") == "design_to_code"
                else "code-to-design risk backtracking"
            ),
            "architecture_boundaries": observation.get("architecture_boundaries", []),
            "implementation_planes": observation.get("implementation_planes", []),
            "parallel_path_ids": observation.get("parallel_path_ids", []),
            "risk_observation_ids": [candidate_id],
            "status": "pending",
            "defer_reason": "",
        })
    rounds: list[dict[str, Any]] = []
    for index in range(0, len(tasks), 4):
        items = tasks[index:index + 4]
        rounds.append({
            "session_id": session_id,
            "round_id": f"ROUND-{index // 4 + 1:02d}",
            "status": "pending",
            "task_ids": [item["task_id"] for item in items],
            "claim_ids": [item["claim_id"] for item in items],
            "finding_ids": [],
        })
    _write_jsonl(root / "investigation_tasks.jsonl", tasks)
    _write_jsonl(root / "investigation_rounds.jsonl", rounds)
    return {"task_ids": [item["task_id"] for item in tasks], "rounds": len(rounds)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["select", "plan"])
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--design-root")
    parser.add_argument("--selection")
    args = parser.parse_args(argv)
    root = Path(args.state_root).resolve()
    try:
        if args.command == "select":
            if not args.design_root or not args.selection:
                parser.error("select requires --design-root and --selection")
            result = select(
                root, Path(args.design_root).resolve(), Path(args.selection).resolve(),
            )
        else:
            result = plan(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"passed": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
