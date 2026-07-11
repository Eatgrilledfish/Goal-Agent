#!/usr/bin/env python3
"""Validate spec-critic coverage and artifact bindings without judging semantics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac


DECISIONS = {"accept", "repair"}
ENTAILMENT = {"entailed", "not_entailed", "ambiguous"}
STRENGTH_ASSESSMENTS = {"correct", "incorrect", "ambiguous"}
NORMATIVE_STRENGTHS = {
    "mandatory", "recommended", "optional", "declared_capability", "informational",
}
RECOMMENDED_STRENGTHS = NORMATIVE_STRENGTHS | {"undetermined"}
ATOMICITY = {"atomic", "bundled", "ambiguous"}
APPLICABILITY = {"supported", "unsupported", "ambiguous"}
GROUP_ASSESSMENTS = {"complete", "gaps_found", "ambiguous"}
INPUT_NAMES = (
    "design_claims.jsonl", "design_coverage.json", "design_agent_manifest.json",
)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_array(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    if any(not _nonempty_string(item) for item in value):
        errors.append(f"{label} entries must be non-empty strings")
    return [item for item in value if _nonempty_string(item)]


def _assessment(
    value: Any,
    *,
    allowed: set[str],
    label: str,
    errors: list[str],
) -> str:
    if not isinstance(value, str) or value not in allowed:
        errors.append(f"{label} must be one of {sorted(allowed)}")
        return ""
    return value


def _review_object(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    return value


def _validate_rationale(value: dict[str, Any], label: str, errors: list[str]) -> None:
    if not _nonempty_string(value.get("rationale")):
        errors.append(f"{label}.rationale must be a non-empty string")


def _validate_claim_review(
    review: Any,
    *,
    index: int,
    session_id: str,
    claims: dict[str, dict[str, Any]],
    errors: list[str],
) -> tuple[str, str]:
    label = f"design_claim_review.json claim_reviews[{index}]"
    item = _review_object(review, label, errors)
    claim_id = item.get("claim_id")
    if not _nonempty_string(claim_id):
        errors.append(f"{label}.claim_id must be a non-empty string")
        claim_id = ""
    if item.get("session_id") != session_id:
        errors.append(f"{label}.session_id does not match current session")

    entailment = _review_object(item.get("quote_entailment"), f"{label}.quote_entailment", errors)
    entailment_assessment = _assessment(
        entailment.get("assessment"), allowed=ENTAILMENT,
        label=f"{label}.quote_entailment.assessment", errors=errors,
    )
    _validate_rationale(entailment, f"{label}.quote_entailment", errors)

    strength = _review_object(item.get("normative_strength"), f"{label}.normative_strength", errors)
    strength_assessment = _assessment(
        strength.get("assessment"), allowed=STRENGTH_ASSESSMENTS,
        label=f"{label}.normative_strength.assessment", errors=errors,
    )
    stated = strength.get("stated_strength")
    if not isinstance(stated, str) or stated not in NORMATIVE_STRENGTHS:
        errors.append(
            f"{label}.normative_strength.stated_strength must be one of "
            f"{sorted(NORMATIVE_STRENGTHS)}"
        )
    claim = claims.get(str(claim_id))
    if claim is not None and stated != claim.get("normative_strength"):
        errors.append(f"{label}.normative_strength.stated_strength does not match the claim")
    recommended = strength.get("recommended_strength")
    if not isinstance(recommended, str) or recommended not in RECOMMENDED_STRENGTHS:
        errors.append(
            f"{label}.normative_strength.recommended_strength must be one of "
            f"{sorted(RECOMMENDED_STRENGTHS)}"
        )
    if strength_assessment == "correct" and recommended != stated:
        errors.append(f"{label}: correct normative strength must recommend the stated strength")
    if strength_assessment == "ambiguous" and recommended != "undetermined":
        errors.append(f"{label}: ambiguous normative strength must recommend undetermined")
    _validate_rationale(strength, f"{label}.normative_strength", errors)

    atomicity = _review_object(item.get("atomicity"), f"{label}.atomicity", errors)
    atomicity_assessment = _assessment(
        atomicity.get("assessment"), allowed=ATOMICITY,
        label=f"{label}.atomicity.assessment", errors=errors,
    )
    obligations = _string_array(
        atomicity.get("obligations"), f"{label}.atomicity.obligations", errors,
    )
    if atomicity_assessment == "atomic" and len(obligations) != 1:
        errors.append(f"{label}: atomic assessment requires exactly one obligation")
    if atomicity_assessment == "bundled" and len(obligations) < 2:
        errors.append(f"{label}: bundled assessment requires at least two obligations")
    if atomicity_assessment == "ambiguous" and not obligations:
        errors.append(f"{label}: ambiguous atomicity requires at least one suspected obligation")
    _validate_rationale(atomicity, f"{label}.atomicity", errors)

    applicability = _review_object(item.get("applicability"), f"{label}.applicability", errors)
    applicability_assessment = _assessment(
        applicability.get("assessment"), allowed=APPLICABILITY,
        label=f"{label}.applicability.assessment", errors=errors,
    )
    _validate_rationale(applicability, f"{label}.applicability", errors)

    decision = _assessment(
        item.get("decision"), allowed=DECISIONS, label=f"{label}.decision", errors=errors,
    )
    repair_actions = _string_array(item.get("repair_actions"), f"{label}.repair_actions", errors)
    expected = "accept" if (
        entailment_assessment == "entailed"
        and strength_assessment == "correct"
        and atomicity_assessment == "atomic"
        and applicability_assessment == "supported"
    ) else "repair"
    if decision and decision != expected:
        errors.append(f"{label}.decision must be {expected!r} for its assessments")
    if decision == "accept" and repair_actions:
        errors.append(f"{label}: accepted claim must not contain repair_actions")
    if decision == "repair" and not repair_actions:
        errors.append(f"{label}: repair decision requires repair_actions")
    return str(claim_id), decision


def _validate_missing_item(value: Any, label: str, errors: list[str]) -> None:
    item = _review_object(value, label, errors)
    for field in ("description", "path", "section", "quote", "why_independent"):
        if not _nonempty_string(item.get(field)):
            errors.append(f"{label}.{field} must be a non-empty string")
    for field in ("line_start", "line_end"):
        number = item.get(field)
        if not isinstance(number, int) or isinstance(number, bool):
            errors.append(f"{label}.{field} must be an integer")
    start = item.get("line_start")
    end = item.get("line_end")
    if (
        isinstance(start, int) and not isinstance(start, bool)
        and isinstance(end, int) and not isinstance(end, bool)
        and (start < 1 or end < start)
    ):
        errors.append(f"{label}: invalid line range {start}-{end}")


def _validate_group_dimension(
    value: Any,
    *,
    label: str,
    errors: list[str],
) -> str:
    dimension = _review_object(value, label, errors)
    assessment = _assessment(
        dimension.get("assessment"), allowed=GROUP_ASSESSMENTS,
        label=f"{label}.assessment", errors=errors,
    )
    _validate_rationale(dimension, label, errors)
    missing = dimension.get("missing_items")
    if not isinstance(missing, list):
        errors.append(f"{label}.missing_items must be an array")
        missing = []
    for index, item in enumerate(missing, start=1):
        _validate_missing_item(item, f"{label}.missing_items[{index}]", errors)
    if assessment == "complete" and missing:
        errors.append(f"{label}: complete assessment must not contain missing_items")
    if assessment in {"gaps_found", "ambiguous"} and not missing:
        errors.append(f"{label}: {assessment} assessment requires at least one missing item")
    return assessment


def _validate_group_review(
    review: Any,
    *,
    index: int,
    session_id: str,
    errors: list[str],
) -> tuple[str, str]:
    label = f"design_claim_review.json group_reviews[{index}]"
    item = _review_object(review, label, errors)
    document_key = item.get("document_key")
    if not _nonempty_string(document_key):
        errors.append(f"{label}.document_key must be a non-empty string")
        document_key = ""
    if item.get("session_id") != session_id:
        errors.append(f"{label}.session_id does not match current session")
    assessments = [
        _validate_group_dimension(
            item.get(name), label=f"{label}.{name}", errors=errors,
        )
        for name in ("behavior_families", "roles", "branches")
    ]
    decision = _assessment(
        item.get("decision"), allowed=DECISIONS, label=f"{label}.decision", errors=errors,
    )
    repair_actions = _string_array(item.get("repair_actions"), f"{label}.repair_actions", errors)
    expected = "accept" if all(value == "complete" for value in assessments) else "repair"
    if decision and decision != expected:
        errors.append(f"{label}.decision must be {expected!r} for its assessments")
    if decision == "accept" and repair_actions:
        errors.append(f"{label}: accepted group must not contain repair_actions")
    if decision == "repair" and not repair_actions:
        errors.append(f"{label}: repair decision requires repair_actions")
    return str(document_key), decision


def _indexed_ids(values: list[dict[str, Any]], key: str, label: str, errors: list[str]) -> set[str]:
    identifiers: set[str] = set()
    for index, item in enumerate(values, start=1):
        identifier = item.get(key)
        if not _nonempty_string(identifier):
            errors.append(f"{label}:{index}: missing/empty {key}")
            continue
        if identifier in identifiers:
            errors.append(f"{label}:{index}: duplicate {key} {identifier!r}")
        identifiers.add(identifier)
    return identifiers


def _digest(path: Path) -> str:
    return ac.sha256_file(path) if path.is_file() else ""


def _expected_design_agent_manifest(workspace: dict[str, Any]) -> dict[str, Any]:
    design = workspace.get("design", {}) if isinstance(workspace.get("design"), dict) else {}
    paths = workspace.get("paths", {}) if isinstance(workspace.get("paths"), dict) else {}
    return {
        "session_id": workspace.get("session_id", ""),
        "prepared_at": workspace.get("prepared_at", ""),
        "review_design_root": paths.get("review_design_root", ""),
        "design": {
            key: design.get(key)
            for key in (
                "document_count", "document_group_count", "documents",
                "document_groups", "source_manifest",
            )
        },
        "preflight_problems": list(workspace.get("preflight_problems", [])),
    }


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    trace_path = log_root / "trace" / "claim_review_validation.json"
    path_errors = ac.session_path_errors(
        root, code_root=code_root, design_root=design_root,
        result_root=result_root, log_root=log_root,
    )
    if path_errors:
        ac.save_json(trace_path, {
            "validated_at": ac.now_iso(), "session_id": "", "passed": False,
            "input_digests": {}, "metrics": {}, "errors": path_errors,
        })
        print(json.dumps({"passed": False, "errors": len(path_errors)}))
        return 2

    errors: list[str] = []
    state = ac.load_json(root / "agent_loop_state.json")
    session_id = str(state.get("session_id") or "") if isinstance(state, dict) else ""
    if not session_id:
        errors.append("agent_loop_state.json missing current session_id")

    claims_path = root / "design_claims.jsonl"
    coverage_path = root / "design_coverage.json"
    manifest_path = root / "design_agent_manifest.json"
    workspace_path = root / "workspace_manifest.json"
    claims, claim_errors = ac.load_jsonl(claims_path)
    errors.extend(claim_errors)
    claim_ids = _indexed_ids(claims, "claim_id", "design_claims.jsonl", errors)
    claim_index = {
        str(item.get("claim_id")): item
        for item in claims if _nonempty_string(item.get("claim_id"))
    }

    try:
        coverage = ac.load_json(coverage_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"design_coverage.json: cannot load JSON: {exc}")
        coverage = {}
    if not isinstance(coverage, dict):
        errors.append("design_coverage.json must be an object")
        coverage = {}
    groups = coverage.get("document_groups")
    if not isinstance(groups, list):
        errors.append("design_coverage.json.document_groups must be an array")
        groups = []
    group_ids = _indexed_ids(
        [item for item in groups if isinstance(item, dict)],
        "document_key", "design_coverage.json.document_groups", errors,
    )
    if any(not isinstance(item, dict) for item in groups):
        errors.append("design_coverage.json.document_groups entries must be objects")

    if not manifest_path.is_file():
        errors.append("missing artifact: design_agent_manifest.json")
        design_agent_manifest: dict[str, Any] = {}
    else:
        try:
            loaded_design_manifest = ac.load_json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"design_agent_manifest.json: cannot load JSON: {exc}")
            loaded_design_manifest = {}
        if not isinstance(loaded_design_manifest, dict):
            errors.append("design_agent_manifest.json must be an object")
            design_agent_manifest = {}
        else:
            design_agent_manifest = loaded_design_manifest
    try:
        workspace = ac.load_json(workspace_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"workspace_manifest.json: cannot load JSON: {exc}")
        workspace = {}
    if not isinstance(workspace, dict):
        errors.append("workspace_manifest.json must be an object")
        workspace = {}
    expected_design_manifest = _expected_design_agent_manifest(workspace)
    if design_agent_manifest != expected_design_manifest:
        errors.append(
            "design_agent_manifest.json does not match the current design-only workspace projection"
        )
    if design_agent_manifest.get("session_id") != session_id:
        errors.append("design_agent_manifest.json session_id does not match current session")
    projected_groups = {
        str(item.get("document_key"))
        for item in design_agent_manifest.get("design", {}).get("document_groups", [])
        if isinstance(item, dict) and item.get("document_key")
    }
    if projected_groups != group_ids:
        errors.append(
            "design_agent_manifest.json document groups do not match design_coverage.json"
        )

    expected_digests = {
        "design_claims.jsonl": _digest(claims_path),
        "design_coverage.json": _digest(coverage_path),
        "design_agent_manifest.json": _digest(manifest_path),
    }
    review_path = root / "design_claim_review.json"
    try:
        review = ac.load_json(review_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"design_claim_review.json: cannot load JSON: {exc}")
        review = {}
    if not isinstance(review, dict):
        errors.append("design_claim_review.json must be an object")
        review = {}
    if review.get("session_id") != session_id:
        errors.append("design_claim_review.json session_id does not match current session")
    if review.get("input_digests") != expected_digests:
        errors.append("design_claim_review.json input_digests do not match current inputs")
    if not _nonempty_string(review.get("summary")):
        errors.append("design_claim_review.json summary must be a non-empty string")

    claim_reviews = review.get("claim_reviews")
    if not isinstance(claim_reviews, list):
        errors.append("design_claim_review.json claim_reviews must be an array")
        claim_reviews = []
    reviewed_claim_ids: list[str] = []
    child_decisions: list[str] = []
    for index, item in enumerate(claim_reviews, start=1):
        identifier, decision = _validate_claim_review(
            item, index=index, session_id=session_id, claims=claim_index, errors=errors,
        )
        if identifier:
            reviewed_claim_ids.append(identifier)
        if decision:
            child_decisions.append(decision)
    reviewed_claim_set = set(reviewed_claim_ids)
    duplicate_claim_reviews = sorted({
        identifier for identifier in reviewed_claim_ids if reviewed_claim_ids.count(identifier) > 1
    })
    if duplicate_claim_reviews:
        errors.append(f"design_claim_review.json has duplicate claim reviews: {duplicate_claim_reviews}")
    missing_claims = sorted(claim_ids - reviewed_claim_set)
    extra_claims = sorted(reviewed_claim_set - claim_ids)
    if missing_claims:
        errors.append(f"design_claim_review.json missing claim reviews: {missing_claims}")
    if extra_claims:
        errors.append(f"design_claim_review.json has unknown claim reviews: {extra_claims}")

    group_reviews = review.get("group_reviews")
    if not isinstance(group_reviews, list):
        errors.append("design_claim_review.json group_reviews must be an array")
        group_reviews = []
    reviewed_group_ids: list[str] = []
    for index, item in enumerate(group_reviews, start=1):
        identifier, decision = _validate_group_review(
            item, index=index, session_id=session_id, errors=errors,
        )
        if identifier:
            reviewed_group_ids.append(identifier)
        if decision:
            child_decisions.append(decision)
    reviewed_group_set = set(reviewed_group_ids)
    duplicate_group_reviews = sorted({
        identifier for identifier in reviewed_group_ids if reviewed_group_ids.count(identifier) > 1
    })
    if duplicate_group_reviews:
        errors.append(f"design_claim_review.json has duplicate group reviews: {duplicate_group_reviews}")
    missing_groups = sorted(group_ids - reviewed_group_set)
    extra_groups = sorted(reviewed_group_set - group_ids)
    if missing_groups:
        errors.append(f"design_claim_review.json missing group reviews: {missing_groups}")
    if extra_groups:
        errors.append(f"design_claim_review.json has unknown group reviews: {extra_groups}")

    overall_decision = _assessment(
        review.get("decision"), allowed=DECISIONS,
        label="design_claim_review.json decision", errors=errors,
    )
    expected_overall = "accept" if (
        len(child_decisions) == len(claim_reviews) + len(group_reviews)
        and all(value == "accept" for value in child_decisions)
    ) else "repair"
    if overall_decision and overall_decision != expected_overall:
        errors.append(
            f"design_claim_review.json decision must be {expected_overall!r} for child decisions"
        )

    accepted = not errors and overall_decision == "accept"
    report = {
        "validated_at": ac.now_iso(),
        "session_id": session_id,
        "schema_valid": not errors,
        "passed": accepted,
        "decision": overall_decision,
        "repair_required": not errors and overall_decision == "repair",
        "input_digests": expected_digests,
        "metrics": {
            "claims": len(claim_ids),
            "claim_reviews": len(claim_reviews),
            "document_groups": len(group_ids),
            "group_reviews": len(group_reviews),
            "repairs": sum(value == "repair" for value in child_decisions),
        },
        "errors": errors,
    }
    ac.save_json(trace_path, report)
    print(json.dumps({
        "passed": accepted,
        "decision": overall_decision,
        "claims": len(claim_ids),
        "document_groups": len(group_ids),
        "errors": len(errors),
    }))
    return 0 if accepted else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate spec-critic review completeness and snapshot bindings.",
    )
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
