#!/usr/bin/env python3
"""Prepare and reconcile blind semantic review of scout-negative conclusions.

This helper never decides whether design and code agree.  It removes the scout's
disposition *and reasoning* from the first-pass packet, partitions comparison
units into small independent batches, binds fresh model responses to current
artifacts, and makes every disagreement enter the ordinary candidate pipeline.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import risk_sweep_plan_validator as rpv


VERDICTS = {"upheld", "challenged"}
REVIEW_STATUSES = {"not_applicable", "upheld", "challenged"}
EXECUTION_ACCOUNTING_FIELDS = (
    "entry",
    "progress_or_transition",
    "guards_and_bounds",
    "termination_or_exit",
    "remaining_applicable_work",
    "alternate_or_compensating_path",
)
PACKET_VERSION = 2
MAX_ITEMS_PER_REVIEW_BATCH = 4


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be an array of objects")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{label} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must not contain duplicates")
    return list(value)


def _context(
    state_root: Path, sweep_id: str, semantic_candidates: Path,
    semantic_coverage: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], str, str, list[str], dict[str, dict[str, Any]]]:
    _plan, index, errors = rpv.load_validated_plan(state_root)
    if errors:
        raise ValueError("scout plan is invalid: " + "; ".join(errors))
    sweep = index.get("slices", {}).get(sweep_id)
    if not isinstance(sweep, dict):
        raise ValueError(f"unknown sweep_id {sweep_id!r}")
    candidates = _objects(ac.load_json(semantic_candidates), "semantic candidates")
    if len(candidates) > rpv.MAX_OBSERVATIONS_PER_SWEEP:
        raise ValueError(
            "raw scout exceeded the per-sweep observation limit before blind review"
        )
    coverage = ac.load_json(semantic_coverage)
    if not isinstance(coverage, dict):
        raise ValueError("semantic coverage must be an object")
    direction = str(sweep.get("direction") or "")
    item_index: dict[str, dict[str, Any]] = {}
    if direction == "design_to_code":
        queue_path = state_root / "design-obligations" / f"{sweep_id}.json"
        queue = ac.load_json(queue_path)
        if queue.get("risk_sweep_plan_sha256") != ac.sha256_file(
            state_root / "risk_sweep_plan.json"
        ):
            raise ValueError("design obligation queue is stale")
        obligations = _objects(queue.get("obligations"), "design obligations")
        expected = [_nonempty(item.get("obligation_id"), "obligation_id") for item in obligations]
        item_index = {item["obligation_id"]: item for item in obligations}
        checks_key, identifier = "obligation_checks", "obligation_id"
    elif direction == "code_to_design":
        expected = list(sweep.get("anchor_paths", []))
        checks_key, identifier = "anchor_checks", "anchor_path"
    else:
        raise ValueError("unsupported scout direction")
    checks = _objects(coverage.get(checks_key), checks_key)
    owners = [
        _nonempty(item.get(identifier), f"{checks_key}[{number}].{identifier}")
        for number, item in enumerate(checks, start=1)
    ]
    if owners != expected:
        raise ValueError(f"{checks_key} must exactly follow the assigned review order")

    candidate_keys: set[str] = set()
    candidate_owner: dict[str, str] = {}
    owner_field = "obligation_id" if direction == "design_to_code" else "primary_anchor_path"
    for number, candidate in enumerate(candidates, start=1):
        key = _nonempty(candidate.get("candidate_key"), f"candidates[{number}].candidate_key")
        owner = _nonempty(candidate.get(owner_field), f"candidates[{number}].{owner_field}")
        if key in candidate_keys:
            raise ValueError("semantic candidate keys must be unique")
        candidate_keys.add(key)
        candidate_owner[key] = owner
    bound: set[str] = set()
    for number, check in enumerate(checks, start=1):
        label = f"{checks_key}[{number}]"
        disposition = _nonempty(check.get("disposition"), f"{label}.disposition")
        keys = _strings(check.get("candidate_keys"), f"{label}.candidate_keys")
        if disposition == "candidate" and not keys:
            raise ValueError(f"{label} candidate disposition requires candidate keys")
        if disposition == "no_mismatch" and keys:
            raise ValueError(f"{label} no_mismatch cannot bind candidates")
        if disposition not in {"candidate", "no_mismatch"}:
            raise ValueError(f"{label} has an unsupported disposition")
        owner = owners[number - 1]
        for key in keys:
            if key not in candidate_keys or candidate_owner.get(key) != owner or key in bound:
                raise ValueError(f"{label} has an invalid candidate binding")
            bound.add(key)
    if bound != candidate_keys:
        raise ValueError("semantic coverage must bind every candidate exactly once")
    return sweep, candidates, coverage, checks_key, identifier, expected, item_index


def prepare(
    state_root: Path, sweep_id: str, semantic_candidates: Path,
    semantic_coverage: Path, output: Path | None,
) -> dict[str, Any]:
    sweep, candidates, coverage, checks_key, identifier, _expected, item_index = _context(
        state_root, sweep_id, semantic_candidates, semantic_coverage,
    )
    items: list[dict[str, Any]] = []
    for check in coverage[checks_key]:
        if check["disposition"] != "no_mismatch":
            continue
        review_id = check[identifier]
        # Validate that the scout actually supplied its audit trail, but do not
        # disclose that trail to the blind reviewer.  A new provider session is
        # not independent if its first context contains the prior conclusion.
        _nonempty(check.get("code_search_summary"), "code_search_summary")
        _nonempty(check.get("countercheck"), "countercheck")
        item = {"review_item_id": review_id}
        if sweep.get("direction") == "design_to_code":
            item["design_obligation"] = item_index[review_id]
        else:
            item["primary_anchor_path"] = review_id
        items.append(item)
    batches = [
        {
            "batch_id": f"BATCH-{offset // MAX_ITEMS_PER_REVIEW_BATCH + 1:03d}",
            "items": items[offset:offset + MAX_ITEMS_PER_REVIEW_BATCH],
        }
        for offset in range(0, len(items), MAX_ITEMS_PER_REVIEW_BATCH)
    ]
    if not batches:
        batches = [{"batch_id": "BATCH-001", "items": []}]
    state = ac.load_json(state_root / "agent_loop_state.json")
    packet = {
        "version": PACKET_VERSION,
        "session_id": state.get("session_id"),
        "sweep_id": sweep_id,
        "direction": sweep.get("direction"),
        "risk_sweep_plan_sha256": ac.sha256_file(state_root / "risk_sweep_plan.json"),
        "design_inventory_sha256": ac.sha256_file(state_root / "design_inventory.json"),
        "semantic_candidates_sha256": ac.sha256_file(semantic_candidates),
        "semantic_coverage_sha256": ac.sha256_file(semantic_coverage),
        "existing_candidate_count": len(candidates),
        "max_items_per_review_batch": MAX_ITEMS_PER_REVIEW_BATCH,
        "review_item_count": len(items),
        "batches": batches,
    }
    if output is not None:
        ac.save_json(output, packet)
    return packet


def extract_batch(packet_path: Path, batch_id: str, output: Path) -> dict[str, Any]:
    """Materialize one source-only blind packet for exactly one reviewer."""
    packet = ac.load_json(packet_path)
    if not isinstance(packet, dict) or packet.get("version") != PACKET_VERSION:
        raise ValueError("blind negative review packet is invalid")
    batches = _objects(packet.get("batches"), "negative review batches")
    matches = [item for item in batches if item.get("batch_id") == batch_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicated review batch {batch_id!r}")
    batch = matches[0]
    items = _objects(batch.get("items"), f"{batch_id}.items")
    if len(items) > MAX_ITEMS_PER_REVIEW_BATCH:
        raise ValueError(f"{batch_id} has an invalid item count")
    value = {
        "version": PACKET_VERSION,
        "session_id": packet.get("session_id"),
        "sweep_id": packet.get("sweep_id"),
        "direction": packet.get("direction"),
        "batch_id": batch_id,
        "items": items,
    }
    ac.save_json(output, value)
    return value


def _validate_raw_reviews(
    raw: Any, expected_ids: list[str], label: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    reviews = _objects(raw.get("reviews"), f"{label}.reviews")
    actual_ids = [
        _nonempty(item.get("review_item_id"), f"{label}.reviews[{number}].review_item_id")
        for number, item in enumerate(reviews, start=1)
    ]
    if actual_ids != expected_ids:
        raise ValueError(f"{label} must exactly follow its blind batch order")
    for number, item in enumerate(reviews, start=1):
        accounting = item.get("execution_accounting")
        item_label = f"{label}.reviews[{number}].execution_accounting"
        if not isinstance(accounting, dict):
            raise ValueError(f"{item_label} must be an object")
        for field in EXECUTION_ACCOUNTING_FIELDS:
            _nonempty(accounting.get(field), f"{item_label}.{field}")
    return reviews


def assemble(
    packet_path: Path, batch_review_paths: list[Path],
    reviewer_provider_session_ids: list[str], output: Path,
) -> dict[str, Any]:
    """Bind independently-run blind batch reviews into one trusted manifest."""
    packet = ac.load_json(packet_path)
    if not isinstance(packet, dict) or packet.get("version") != PACKET_VERSION:
        raise ValueError("blind negative review packet is invalid")
    batches = _objects(packet.get("batches"), "negative review batches")
    if len(batch_review_paths) != len(batches):
        raise ValueError("one blind review file is required for every review batch")
    if len(reviewer_provider_session_ids) != len(batches):
        raise ValueError("one provider session is required for every review batch")
    sessions = [
        _nonempty(value, f"reviewer_provider_session_ids[{number}]")
        for number, value in enumerate(reviewer_provider_session_ids, start=1)
    ]
    if len(sessions) != len(set(sessions)):
        raise ValueError("blind review batches must use distinct provider sessions")
    assembled_batches: list[dict[str, Any]] = []
    for batch, path, provider_session_id in zip(
        batches, batch_review_paths, sessions, strict=True,
    ):
        batch_id = _nonempty(batch.get("batch_id"), "batch_id")
        expected_ids = [
            _nonempty(item.get("review_item_id"), f"{batch_id}.review_item_id")
            for item in _objects(batch.get("items"), f"{batch_id}.items")
        ]
        reviews = _validate_raw_reviews(ac.load_json(path), expected_ids, batch_id)
        assembled_batches.append({
            "batch_id": batch_id,
            "reviewer_provider_session_id": provider_session_id,
            "raw_review_sha256": ac.sha256_file(path),
            "reviews": reviews,
        })
    value = {
        "version": PACKET_VERSION,
        "packet_sha256": ac.sha256_file(packet_path),
        "batches": assembled_batches,
    }
    ac.save_json(output, value)
    return value


def reconcile(
    state_root: Path, sweep_id: str, semantic_candidates: Path,
    semantic_coverage: Path, packet_path: Path, review_path: Path,
    scout_provider_session_id: str,
    candidates_output: Path, coverage_output: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scout_session = _nonempty(scout_provider_session_id, "scout provider session ID")
    _sweep, candidates, coverage, checks_key, identifier, _expected, _index = _context(
        state_root, sweep_id, semantic_candidates, semantic_coverage,
    )
    packet = ac.load_json(packet_path)
    if not isinstance(packet, dict) or packet.get("sweep_id") != sweep_id:
        raise ValueError("negative review packet is invalid")
    expected_packet = prepare(
        state_root, sweep_id, semantic_candidates, semantic_coverage, None,
    )
    if packet != expected_packet:
        raise ValueError("negative review packet is stale or was modified")
    review = ac.load_json(review_path)
    if not isinstance(review, dict) or review.get("version") != PACKET_VERSION:
        raise ValueError("negative review manifest is invalid")
    if review.get("packet_sha256") != ac.sha256_file(packet_path):
        raise ValueError("negative review manifest belongs to another packet")
    packet_batches = _objects(packet.get("batches"), "negative review batches")
    review_batches = _objects(review.get("batches"), "negative review manifest batches")
    if [item.get("batch_id") for item in review_batches] != [
        item.get("batch_id") for item in packet_batches
    ]:
        raise ValueError("negative review manifest does not follow packet batches")
    reviews: list[dict[str, Any]] = []
    reviewer_sessions: list[str] = []
    for packet_batch, review_batch in zip(packet_batches, review_batches, strict=True):
        batch_id = _nonempty(packet_batch.get("batch_id"), "batch_id")
        expected_batch_ids = [
            _nonempty(item.get("review_item_id"), f"{batch_id}.review_item_id")
            for item in _objects(packet_batch.get("items"), f"{batch_id}.items")
        ]
        reviews.extend(_validate_raw_reviews(
            {"reviews": review_batch.get("reviews")}, expected_batch_ids, batch_id,
        ))
        reviewer_sessions.append(_nonempty(
            review_batch.get("reviewer_provider_session_id"),
            f"{batch_id}.reviewer_provider_session_id",
        ))
    if len(reviewer_sessions) != len(set(reviewer_sessions)):
        raise ValueError("negative review batches reused a provider session")
    if scout_session in reviewer_sessions:
        raise ValueError("negative review must use fresh provider sessions")
    expected_ids = [
        item["review_item_id"] for batch in packet_batches for item in batch["items"]
    ]
    actual_ids = [item["review_item_id"] for item in reviews]

    existing_keys = {
        _nonempty(item.get("candidate_key"), "candidate_key") for item in candidates
    }
    additions: dict[str, dict[str, Any]] = {}
    verdicts: dict[str, str] = {}
    for number, item in enumerate(reviews, start=1):
        label = f"reviews[{number}]"
        review_id = actual_ids[number - 1]
        verdict = _nonempty(item.get("verdict"), f"{label}.verdict")
        if verdict not in VERDICTS:
            raise ValueError(f"{label}.verdict is unsupported")
        _nonempty(item.get("independent_analysis"), f"{label}.independent_analysis")
        _nonempty(item.get("falsification_attempt"), f"{label}.falsification_attempt")
        candidate = item.get("candidate")
        if verdict == "upheld":
            if candidate is not None:
                raise ValueError(f"{label} upheld review cannot contain a candidate")
        else:
            if not isinstance(candidate, dict):
                raise ValueError(f"{label} challenged review requires one candidate")
            owner_field = (
                "obligation_id" if packet["direction"] == "design_to_code"
                else "primary_anchor_path"
            )
            if candidate.get(owner_field) != review_id:
                raise ValueError(f"{label} candidate is bound to another review item")
            key = _nonempty(candidate.get("candidate_key"), f"{label}.candidate.candidate_key")
            if key in existing_keys or key in {value["candidate_key"] for value in additions.values()}:
                raise ValueError(f"{label} candidate_key is not unique")
            additions[review_id] = candidate
        verdicts[review_id] = verdict
    combined = [*candidates, *[additions[key] for key in expected_ids if key in additions]]

    reconciled = deepcopy(coverage)
    for check in reconciled[checks_key]:
        review_id = check[identifier]
        if check["disposition"] == "candidate":
            check["negative_review_status"] = "not_applicable"
            continue
        verdict = verdicts[review_id]
        if verdict == "challenged":
            candidate = additions[review_id]
            check["disposition"] = "candidate"
            check["candidate_keys"] = [candidate["candidate_key"]]
            check["negative_review_status"] = "challenged"
        else:
            check["negative_review_status"] = "upheld"
    reconciled["negative_review"] = {
        "version": PACKET_VERSION,
        "packet_sha256": ac.sha256_file(packet_path),
        "review_sha256": ac.sha256_file(review_path),
        "scout_provider_session_id": scout_session,
        "reviewer_provider_session_ids": reviewer_sessions,
        "review_batch_count": len(review_batches),
        "reviewed_item_count": len(reviews),
        "challenged_item_count": sum(1 for value in verdicts.values() if value == "challenged"),
    }
    ac.save_json(candidates_output, combined)
    ac.save_json(coverage_output, reconciled)
    return combined, reconciled


def validate_attestation(
    state_root: Path, sweep_id: str, coverage: dict[str, Any],
) -> None:
    attestation = coverage.get("negative_review")
    if not isinstance(attestation, dict):
        raise ValueError("canonical coverage requires a fresh negative review attestation")
    packet = state_root / "semantic" / "negative-reviews" / f"{sweep_id}.packet.json"
    review = state_root / "semantic" / "negative-reviews" / f"{sweep_id}.review.json"
    if not packet.is_file() or packet.is_symlink() or not review.is_file() or review.is_symlink():
        raise ValueError("fresh negative review artifacts are missing")
    if attestation.get("packet_sha256") != ac.sha256_file(packet):
        raise ValueError("negative review packet digest is stale")
    if attestation.get("review_sha256") != ac.sha256_file(review):
        raise ValueError("negative review output digest is stale")
    scout = attestation.get("scout_provider_session_id")
    reviewers = attestation.get("reviewer_provider_session_ids")
    if not isinstance(scout, str) or not scout:
        raise ValueError("negative review provider sessions are missing")
    reviewers = _strings(reviewers, "reviewer_provider_session_ids")
    if scout in reviewers:
        raise ValueError("negative review did not use fresh provider sessions")
    packet_value = ac.load_json(packet)
    review_value = ac.load_json(review)
    batches = packet_value.get("batches") if isinstance(packet_value, dict) else None
    review_batches = review_value.get("batches") if isinstance(review_value, dict) else None
    if not isinstance(batches, list) or not isinstance(review_batches, list):
        raise ValueError("negative review artifacts are invalid")
    items = [item for batch in batches for item in batch.get("items", [])]
    reviews = [item for batch in review_batches for item in batch.get("reviews", [])]
    if attestation.get("review_batch_count") != len(batches) or len(review_batches) != len(batches):
        raise ValueError("negative review batch count is incomplete")
    if attestation.get("reviewed_item_count") != len(items) or len(reviews) != len(items):
        raise ValueError("negative review item count is incomplete")
    state = ac.load_json(state_root / "agent_loop_state.json")
    if packet_value.get("session_id") != state.get("session_id"):
        raise ValueError("negative review packet belongs to another session")
    if packet_value.get("sweep_id") != sweep_id:
        raise ValueError("negative review packet belongs to another sweep")
    if packet_value.get("risk_sweep_plan_sha256") != ac.sha256_file(
        state_root / "risk_sweep_plan.json"
    ):
        raise ValueError("negative review packet plan digest is stale")
    if packet_value.get("design_inventory_sha256") != ac.sha256_file(
        state_root / "design_inventory.json"
    ):
        raise ValueError("negative review packet design inventory digest is stale")
    raw_root = state_root / "semantic" / "scouts"
    raw_candidates = raw_root / f"{sweep_id}.candidates.json"
    raw_coverage = raw_root / f"{sweep_id}.coverage.json"
    if not raw_candidates.is_file() or not raw_coverage.is_file():
        raise ValueError("negative review source semantics are missing")
    expected_packet = prepare(
        state_root, sweep_id, raw_candidates, raw_coverage, None,
    )
    if packet_value != expected_packet:
        raise ValueError("negative review packet no longer matches source semantics")
    if review_value.get("packet_sha256") != ac.sha256_file(packet):
        raise ValueError("negative review manifest packet digest is stale")
    manifest_sessions = [
        item.get("reviewer_provider_session_id")
        for item in review_batches if isinstance(item, dict)
    ]
    if manifest_sessions != reviewers:
        raise ValueError("negative review provider session attestation is stale")
    review_ids = [item.get("review_item_id") for item in reviews if isinstance(item, dict)]
    if review_ids != [item.get("review_item_id") for item in items if isinstance(item, dict)]:
        raise ValueError("negative review output does not follow packet order")
    challenged = sum(
        1 for item in reviews
        if isinstance(item, dict) and item.get("verdict") == "challenged"
    )
    if attestation.get("challenged_item_count") != challenged:
        raise ValueError("negative review challenged count is stale")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    batch_parser = subparsers.add_parser("batch")
    assemble_parser = subparsers.add_parser("assemble")
    reconcile_parser = subparsers.add_parser("reconcile")
    for child in (prepare_parser, reconcile_parser):
        child.add_argument("--state-root", required=True)
        child.add_argument("--sweep-id", required=True)
        child.add_argument("--semantic-candidates", required=True)
        child.add_argument("--semantic-coverage", required=True)
    prepare_parser.add_argument("--output", required=True)
    batch_parser.add_argument("--packet", required=True)
    batch_parser.add_argument("--batch-id", required=True)
    batch_parser.add_argument("--output", required=True)
    assemble_parser.add_argument("--packet", required=True)
    assemble_parser.add_argument("--batch-review", action="append", required=True)
    assemble_parser.add_argument(
        "--reviewer-provider-session-id", action="append", required=True,
    )
    assemble_parser.add_argument("--output", required=True)
    reconcile_parser.add_argument("--packet", required=True)
    reconcile_parser.add_argument("--review", required=True)
    reconcile_parser.add_argument("--scout-provider-session-id", required=True)
    reconcile_parser.add_argument("--candidates-output", required=True)
    reconcile_parser.add_argument("--coverage-output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            state_root = Path(args.state_root).resolve()
            value = prepare(
                state_root, args.sweep_id, Path(args.semantic_candidates).resolve(),
                Path(args.semantic_coverage).resolve(), Path(args.output).resolve(),
            )
            result = {
                "passed": True,
                "review_item_count": value["review_item_count"],
                "review_batch_count": len(value["batches"]),
            }
        elif args.command == "batch":
            value = extract_batch(
                Path(args.packet).resolve(), args.batch_id, Path(args.output).resolve(),
            )
            result = {
                "passed": True, "batch_id": value["batch_id"],
                "review_item_count": len(value["items"]),
            }
        elif args.command == "assemble":
            value = assemble(
                Path(args.packet).resolve(),
                [Path(value).resolve() for value in args.batch_review],
                args.reviewer_provider_session_id, Path(args.output).resolve(),
            )
            result = {"passed": True, "review_batch_count": len(value["batches"])}
        else:
            state_root = Path(args.state_root).resolve()
            candidates, coverage = reconcile(
                state_root, args.sweep_id, Path(args.semantic_candidates).resolve(),
                Path(args.semantic_coverage).resolve(), Path(args.packet).resolve(),
                Path(args.review).resolve(), args.scout_provider_session_id,
                Path(args.candidates_output).resolve(), Path(args.coverage_output).resolve(),
            )
            result = {
                "passed": True, "candidate_count": len(candidates),
                "challenged_item_count": coverage["negative_review"]["challenged_item_count"],
            }
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result = {"passed": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
