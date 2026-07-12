#!/usr/bin/env python3
"""Validate spec-critic coverage and artifact bindings without judging semantics."""

from __future__ import annotations

import argparse
import hashlib
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
SPEC_CRITIC_PROMPT_VERSION = "spec-critic-v2"


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonical_digest(value: Any) -> str:
    """Hash a JSON value without depending on source-file whitespace or ordering."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _claim_digest(claim: dict[str, Any]) -> str:
    return _canonical_digest(claim)


def _inventory_group_digest(group: dict[str, Any]) -> str:
    return _canonical_digest({
        key: value for key, value in group.items() if key != "group_sha256"
    })


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
    design_root: Path,
    claims: dict[str, dict[str, Any]],
    errors: list[str],
) -> tuple[str, str, bool]:
    aggregate_errors = errors
    errors = []
    label = f"design_claim_review.json claim_reviews[{index}]"
    item = _review_object(review, label, errors)
    claim_id = item.get("claim_id")
    if not _nonempty_string(claim_id):
        errors.append(f"{label}.claim_id must be a non-empty string")
        claim_id = ""
    if item.get("session_id") != session_id:
        errors.append(f"{label}.session_id does not match current session")

    claim = claims.get(str(claim_id))
    expected_claim_digest = _claim_digest(claim) if claim is not None else ""
    if not _nonempty_string(item.get("claim_sha256")):
        errors.append(f"{label}.claim_sha256 must be a non-empty string")
    elif claim is not None and item.get("claim_sha256") != expected_claim_digest:
        errors.append(f"{label}.claim_sha256 does not match the current claim")
    source_ref = claim.get("source_ref") if isinstance(claim, dict) else None
    expected_source_digest = (
        source_ref.get("source_sha256") if isinstance(source_ref, dict) else None
    )
    if not _nonempty_string(expected_source_digest):
        errors.append(f"{label}: current claim is missing source_ref.source_sha256")
    source_path_value = source_ref.get("path") if isinstance(source_ref, dict) else None
    source_path = Path(source_path_value) if _nonempty_string(source_path_value) else None
    if source_path is None or source_path.is_absolute() or ".." in source_path.parts:
        errors.append(f"{label}: current claim source_ref.path must be design-root relative")
    else:
        current_source_path = design_root / source_path
        if not current_source_path.is_file():
            errors.append(f"{label}: current claim source_ref.path does not exist")
        elif (
            _nonempty_string(expected_source_digest)
            and ac.sha256_file(current_source_path) != expected_source_digest
        ):
            errors.append(
                f"{label}: current claim source_ref.source_sha256 does not match source file"
            )
    if not _nonempty_string(item.get("source_sha256")):
        errors.append(f"{label}.source_sha256 must be a non-empty string")
    elif _nonempty_string(expected_source_digest) and (
        item.get("source_sha256") != expected_source_digest
    ):
        errors.append(f"{label}.source_sha256 does not match the current claim source")
    if item.get("spec_critic_prompt_version") != SPEC_CRITIC_PROMPT_VERSION:
        errors.append(
            f"{label}.spec_critic_prompt_version must be "
            f"{SPEC_CRITIC_PROMPT_VERSION!r}"
        )

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
    valid = not errors
    aggregate_errors.extend(errors)
    return str(claim_id), decision, valid


def _validate_missing_item(
    value: Any,
    label: str,
    scope_claim_ids: set[str],
    group_claim_ids: set[str],
    errors: list[str],
) -> tuple[dict[str, Any], list[str]]:
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
    affected_claim_ids = _string_array(
        item.get("affected_claim_ids"), f"{label}.affected_claim_ids", errors,
    )
    duplicate_affected = sorted({
        claim_id for claim_id in affected_claim_ids
        if affected_claim_ids.count(claim_id) > 1
    })
    if duplicate_affected:
        errors.append(f"{label}.affected_claim_ids contains duplicates: {duplicate_affected}")
    out_of_scope = sorted(set(affected_claim_ids) - scope_claim_ids)
    if out_of_scope:
        errors.append(f"{label}.affected_claim_ids contains out-of-scope claims: {out_of_scope}")
    wrong_group = sorted(set(affected_claim_ids) - group_claim_ids)
    if wrong_group:
        errors.append(f"{label}.affected_claim_ids contains claims from another group: {wrong_group}")
    return item, affected_claim_ids


def _validate_group_dimension(
    value: Any,
    *,
    label: str,
    document_key: str,
    dimension_name: str,
    scope_claim_ids: set[str],
    group_claim_ids: set[str],
    errors: list[str],
) -> tuple[str, list[dict[str, Any]], set[str]]:
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
    expansion_requests: list[dict[str, Any]] = []
    affected_claim_ids: set[str] = set()
    for index, item in enumerate(missing, start=1):
        missing_item, affected = _validate_missing_item(
            item, f"{label}.missing_items[{index}]", scope_claim_ids,
            group_claim_ids, errors,
        )
        affected_claim_ids.update(affected)
        request_payload = {
            "document_key": document_key,
            "dimension": dimension_name,
            "description": missing_item.get("description", ""),
            "path": missing_item.get("path", ""),
            "section": missing_item.get("section", ""),
            "line_start": missing_item.get("line_start"),
            "line_end": missing_item.get("line_end"),
            "quote": missing_item.get("quote", ""),
            "why_independent": missing_item.get("why_independent", ""),
            "affected_claim_ids": sorted(set(affected)),
            "blocking": bool(affected),
        }
        request_payload["expansion_request_id"] = (
            "EXPANSION-" + _canonical_digest(request_payload)[:16].upper()
        )
        expansion_requests.append(request_payload)
    if assessment == "complete" and missing:
        errors.append(f"{label}: complete assessment must not contain missing_items")
    if assessment in {"gaps_found", "ambiguous"} and not missing:
        errors.append(f"{label}: {assessment} assessment requires at least one missing item")
    return assessment, expansion_requests, affected_claim_ids


def _validate_group_review(
    review: Any,
    *,
    index: int,
    session_id: str,
    expected_group_digests: dict[str, str],
    scope_claim_ids: set[str],
    claim_group_index: dict[str, str],
    errors: list[str],
) -> tuple[str, str, list[dict[str, Any]], set[str], bool]:
    aggregate_errors = errors
    errors = []
    label = f"design_claim_review.json group_reviews[{index}]"
    item = _review_object(review, label, errors)
    document_key = item.get("document_key")
    if not _nonempty_string(document_key):
        errors.append(f"{label}.document_key must be a non-empty string")
        document_key = ""
    if item.get("session_id") != session_id:
        errors.append(f"{label}.session_id does not match current session")
    expected_group_digest = expected_group_digests.get(str(document_key))
    if not _nonempty_string(item.get("group_sha256")):
        errors.append(f"{label}.group_sha256 must be a non-empty string")
    elif expected_group_digest is not None and item.get("group_sha256") != expected_group_digest:
        errors.append(f"{label}.group_sha256 does not match the current inventory group")
    group_claim_ids = {
        claim_id for claim_id, group_id in claim_group_index.items()
        if group_id == document_key
    }
    assessments: list[str] = []
    expansion_requests: list[dict[str, Any]] = []
    affected_claim_ids: set[str] = set()
    for name in ("behavior_families", "roles", "branches"):
        assessment, requests, affected = _validate_group_dimension(
            item.get(name), label=f"{label}.{name}", document_key=str(document_key),
            dimension_name=name, scope_claim_ids=scope_claim_ids,
            group_claim_ids=group_claim_ids, errors=errors,
        )
        assessments.append(assessment)
        expansion_requests.extend(requests)
        affected_claim_ids.update(affected)
    decision = _assessment(
        item.get("decision"), allowed=DECISIONS, label=f"{label}.decision", errors=errors,
    )
    repair_actions = _string_array(item.get("repair_actions"), f"{label}.repair_actions", errors)
    # Coverage gaps are expansion signals. They become repair-blocking only when
    # the critic explicitly says that a gap changes a scoped claim's semantics.
    expected = "repair" if affected_claim_ids else "accept"
    if decision and decision != expected:
        errors.append(f"{label}.decision must be {expected!r} for its assessments")
    if decision == "accept" and repair_actions:
        errors.append(f"{label}: accepted group must not contain repair_actions")
    if decision == "repair" and not repair_actions:
        errors.append(f"{label}: repair decision requires repair_actions")
    valid = not errors
    aggregate_errors.extend(errors)
    return (
        str(document_key), decision, expansion_requests, affected_claim_ids, valid,
    )


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
            "input_digests": {}, "scope_digest": "", "accepted_claim_ids": [],
            "repaired_claim_ids": [], "expansion_requests": [],
            "metrics": {}, "errors": path_errors,
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
    inventory_path = root / "design_inventory.json"
    manifest_path = root / "design_agent_manifest.json"
    scope_path = root / "claim_review_scope.json"
    workspace_path = root / "workspace_manifest.json"
    claims, claim_errors = ac.load_jsonl(claims_path)
    errors.extend(claim_errors)
    claim_ids = _indexed_ids(claims, "claim_id", "design_claims.jsonl", errors)
    claim_index = {
        str(item.get("claim_id")): item
        for item in claims if _nonempty_string(item.get("claim_id"))
    }
    claims_digest = _digest(claims_path)

    try:
        scope = ac.load_json(scope_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"claim_review_scope.json: cannot load JSON: {exc}")
        scope = {}
    if not isinstance(scope, dict):
        errors.append("claim_review_scope.json must be an object")
        scope = {}
    if scope.get("session_id") != session_id:
        errors.append("claim_review_scope.json session_id does not match current session")
    if not _nonempty_string(scope.get("round_id")):
        errors.append("claim_review_scope.json round_id must be a non-empty string")
    raw_scope_claim_ids = scope.get("claim_ids")
    if not isinstance(raw_scope_claim_ids, list):
        errors.append("claim_review_scope.json claim_ids must be an array")
        raw_scope_claim_ids = []
    elif not raw_scope_claim_ids:
        errors.append("claim_review_scope.json claim_ids must not be empty")
    scope_claim_id_values = [
        value for value in raw_scope_claim_ids if _nonempty_string(value)
    ]
    if len(scope_claim_id_values) != len(raw_scope_claim_ids):
        errors.append("claim_review_scope.json claim_ids entries must be non-empty strings")
    duplicate_scope_claim_ids = sorted({
        claim_id for claim_id in scope_claim_id_values
        if scope_claim_id_values.count(claim_id) > 1
    })
    if duplicate_scope_claim_ids:
        errors.append(
            f"claim_review_scope.json has duplicate claim_ids: {duplicate_scope_claim_ids}"
        )
    scope_claim_ids = set(scope_claim_id_values)
    if len(scope_claim_ids) > 12:
        errors.append(
            "claim_review_scope.json may contain at most 12 evidence-pair claims per full review"
        )
    unknown_scope_claim_ids = sorted(scope_claim_ids - claim_ids)
    if unknown_scope_claim_ids:
        errors.append(
            f"claim_review_scope.json has unknown claim_ids: {unknown_scope_claim_ids}"
        )
    missing_scope_claim_ids = sorted(claim_ids - scope_claim_ids)
    if missing_scope_claim_ids:
        errors.append(
            "claim_review_scope.json must include every materialized claim in the "
            f"bounded portfolio; missing={missing_scope_claim_ids}"
        )

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
    group_claim_ids: dict[str, set[str]] = {}
    claim_group_index: dict[str, str] = {}
    for index, item in enumerate(groups, start=1):
        if not isinstance(item, dict):
            continue
        document_key = item.get("document_key")
        if not _nonempty_string(document_key):
            continue
        raw_group_claim_ids = item.get("claim_ids")
        if not isinstance(raw_group_claim_ids, list):
            errors.append(
                f"design_coverage.json.document_groups:{index}: claim_ids must be an array"
            )
            continue
        if any(not _nonempty_string(value) for value in raw_group_claim_ids):
            errors.append(
                f"design_coverage.json.document_groups:{index}: "
                "claim_ids entries must be non-empty strings"
            )
        normalized_group_claim_ids = {
            str(value) for value in raw_group_claim_ids if _nonempty_string(value)
        }
        group_claim_ids[str(document_key)] = normalized_group_claim_ids
        for claim_id in normalized_group_claim_ids:
            previous_group = claim_group_index.get(claim_id)
            if previous_group is not None and previous_group != document_key:
                errors.append(
                    "design_coverage.json assigns claim "
                    f"{claim_id!r} to multiple document groups: "
                    f"{previous_group!r}, {document_key!r}"
                )
            else:
                claim_group_index[claim_id] = str(document_key)
    scoped_group_ids = {
        document_key for document_key, member_claim_ids in group_claim_ids.items()
        if member_claim_ids.intersection(scope_claim_ids)
    }
    mapped_scope_claim_ids = {
        claim_id
        for document_key in scoped_group_ids
        for claim_id in group_claim_ids.get(document_key, set())
    }
    unmapped_scope_claim_ids = sorted(scope_claim_ids - mapped_scope_claim_ids)
    if unmapped_scope_claim_ids:
        errors.append(
            "claim_review_scope.json claim_ids are not assigned to a design_coverage "
            f"document group: {unmapped_scope_claim_ids}"
        )

    try:
        inventory = ac.load_json(inventory_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"design_inventory.json: cannot load JSON: {exc}")
        inventory = {}
    if not isinstance(inventory, dict):
        errors.append("design_inventory.json must be an object")
        inventory = {}
    if inventory.get("session_id") != session_id:
        errors.append("design_inventory.json session_id does not match current session")
    inventory_groups = inventory.get("document_groups")
    if not isinstance(inventory_groups, list):
        errors.append("design_inventory.json.document_groups must be an array")
        inventory_groups = []
    inventory_group_ids = _indexed_ids(
        [item for item in inventory_groups if isinstance(item, dict)],
        "document_key", "design_inventory.json.document_groups", errors,
    )
    if any(not isinstance(item, dict) for item in inventory_groups):
        errors.append("design_inventory.json.document_groups entries must be objects")
    expected_group_digests: dict[str, str] = {}
    for index, item in enumerate(inventory_groups, start=1):
        if not isinstance(item, dict):
            continue
        document_key = item.get("document_key")
        if not _nonempty_string(document_key):
            continue
        declared_digest = item.get("group_sha256")
        calculated_digest = _inventory_group_digest(item)
        if not _nonempty_string(declared_digest):
            errors.append(
                f"design_inventory.json.document_groups:{index}: "
                "group_sha256 must be a non-empty string"
            )
        elif declared_digest != calculated_digest:
            errors.append(
                f"design_inventory.json.document_groups:{index}: "
                "group_sha256 does not match the canonical group object"
            )
        expected_group_digests[str(document_key)] = calculated_digest
    if inventory_group_ids != group_ids:
        errors.append(
            "design_inventory.json document groups do not match design_coverage.json"
        )

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
        "design_claims.jsonl": claims_digest,
        "design_coverage.json": _digest(coverage_path),
        "design_inventory.json": _digest(inventory_path),
        "design_agent_manifest.json": _digest(manifest_path),
        "claim_review_scope.json": _digest(scope_path),
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
    if not _nonempty_string(review.get("summary")):
        errors.append("design_claim_review.json summary must be a non-empty string")

    claim_reviews = review.get("claim_reviews")
    if not isinstance(claim_reviews, list):
        errors.append("design_claim_review.json claim_reviews must be an array")
        claim_reviews = []
    reviewed_claim_ids: list[str] = []
    accepted_reviewed_claim_ids: list[str] = []
    repaired_reviewed_claim_ids: list[str] = []
    claim_decisions: list[str] = []
    for index, item in enumerate(claim_reviews, start=1):
        identifier, decision, item_valid = _validate_claim_review(
            item, index=index, session_id=session_id, design_root=design_root,
            claims=claim_index, errors=errors,
        )
        if identifier:
            reviewed_claim_ids.append(identifier)
            if item_valid and decision == "accept":
                accepted_reviewed_claim_ids.append(identifier)
            elif item_valid and decision == "repair":
                repaired_reviewed_claim_ids.append(identifier)
        if decision:
            claim_decisions.append(decision)
    reviewed_claim_set = set(reviewed_claim_ids)
    duplicate_claim_reviews = sorted({
        identifier for identifier in reviewed_claim_ids if reviewed_claim_ids.count(identifier) > 1
    })
    if duplicate_claim_reviews:
        errors.append(f"design_claim_review.json has duplicate claim reviews: {duplicate_claim_reviews}")
    missing_claims = sorted(scope_claim_ids - reviewed_claim_set)
    extra_claims = sorted(reviewed_claim_set - scope_claim_ids)
    if missing_claims:
        errors.append(f"design_claim_review.json missing claim reviews: {missing_claims}")
    if extra_claims:
        errors.append(f"design_claim_review.json has out-of-scope claim reviews: {extra_claims}")

    group_reviews = review.get("group_reviews", [])
    if not isinstance(group_reviews, list):
        errors.append("design_claim_review.json group_reviews must be an array")
        group_reviews = []
    reviewed_group_ids: list[str] = []
    expansion_requests: list[dict[str, Any]] = []
    semantically_affected_claim_ids: set[str] = set()
    for index, item in enumerate(group_reviews, start=1):
        identifier, _decision, requests, affected_claims, item_valid = _validate_group_review(
            item, index=index, session_id=session_id,
            expected_group_digests=expected_group_digests,
            scope_claim_ids=scope_claim_ids, claim_group_index=claim_group_index,
            errors=errors,
        )
        if identifier:
            reviewed_group_ids.append(identifier)
        if item_valid:
            expansion_requests.extend(requests)
            semantically_affected_claim_ids.update(affected_claims)
    reviewed_group_set = set(reviewed_group_ids)
    duplicate_group_reviews = sorted({
        identifier for identifier in reviewed_group_ids if reviewed_group_ids.count(identifier) > 1
    })
    if duplicate_group_reviews:
        errors.append(f"design_claim_review.json has duplicate group reviews: {duplicate_group_reviews}")
    extra_groups = sorted(reviewed_group_set - scoped_group_ids)
    if extra_groups:
        errors.append(f"design_claim_review.json has out-of-scope group reviews: {extra_groups}")

    valid_repaired_claim_ids = set(repaired_reviewed_claim_ids).intersection(scope_claim_ids)
    affected_but_accepted = sorted(
        semantically_affected_claim_ids - valid_repaired_claim_ids
    )
    if affected_but_accepted:
        errors.append(
            "design_claim_review.json group gaps that affect scoped claim semantics "
            "must have matching repair claim reviews: "
            f"{affected_but_accepted}"
        )

    overall_decision = _assessment(
        review.get("decision"), allowed=DECISIONS,
        label="design_claim_review.json decision", errors=errors,
    )
    # Top-level repair describes claim-local repair work. Non-blocking document
    # coverage gaps remain expansion signals and do not poison accepted claims.
    expected_overall = "accept" if (
        len(claim_decisions) == len(claim_reviews)
        and all(value == "accept" for value in claim_decisions)
    ) else "repair"
    if overall_decision and overall_decision != expected_overall:
        errors.append(
            f"design_claim_review.json decision must be {expected_overall!r} for child decisions"
        )

    validation_passed = not errors
    accepted_claim_ids = sorted(
        set(accepted_reviewed_claim_ids).intersection(scope_claim_ids)
    )
    repaired_claim_ids = sorted(valid_repaired_claim_ids)
    report = {
        "validated_at": ac.now_iso(),
        "session_id": session_id,
        "schema_valid": not errors,
        "passed": validation_passed,
        "decision": overall_decision,
        "repair_required": validation_passed and bool(repaired_claim_ids),
        "input_digests": expected_digests,
        "scope_digest": expected_digests["claim_review_scope.json"],
        "spec_critic_prompt_version": SPEC_CRITIC_PROMPT_VERSION,
        "accepted_claim_ids": accepted_claim_ids,
        "repaired_claim_ids": repaired_claim_ids,
        "expansion_requests": expansion_requests,
        "metrics": {
            "claims": len(claim_ids),
            "claim_reviews": len(claim_reviews),
            "document_groups": len(group_ids),
            "group_reviews": len(group_reviews),
            "repairs": len(repaired_claim_ids),
            "expansion_requests": len(expansion_requests),
        },
        "errors": errors,
    }
    ac.save_json(trace_path, report)
    print(json.dumps({
        "passed": validation_passed,
        "decision": overall_decision,
        "accepted_claims": len(accepted_claim_ids),
        "repaired_claims": len(repaired_claim_ids),
        "claims": len(claim_ids),
        "document_groups": len(group_ids),
        "errors": len(errors),
    }))
    return 0 if validation_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate spec-critic review completeness and snapshot bindings.",
    )
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
