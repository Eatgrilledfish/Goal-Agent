#!/usr/bin/env python3
"""Bind a spec critic's small semantic review to the current claim snapshot.

The critic supplies one ordered semantic assessment per scoped claim.  Claim
identity, session, source and claim digests, stated normative strength, prompt
version, input digests, overall decision, and summary are reconstructed from
the current state.  Consequently the critic cannot forge or accidentally
stale any of those fields.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import claim_review_validator as validator


TOP_LEVEL_FIELDS = {"reviews"}
REVIEW_FIELDS = {
    "quote_entailment", "normative_strength", "atomicity", "applicability",
    "decision", "repair_rationale",
}
ENTAILMENT_FIELDS = {"assessment", "rationale"}
STRENGTH_FIELDS = {"assessment", "recommended_strength", "rationale"}
ATOMICITY_FIELDS = {"assessment", "obligations", "rationale"}
APPLICABILITY_FIELDS = {"assessment", "rationale"}
BOUND_INPUTS = (
    "design_claims.jsonl",
    "design_coverage.json",
    "design_inventory.json",
    "design_agent_manifest.json",
    "claim_review_scope.json",
)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing {label}: {path}")
    value = ac.load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _exact_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    missing = sorted(expected - set(value))
    unsupported = sorted(set(value) - expected)
    if missing:
        raise ValueError(f"{label} is missing fields {missing}")
    if unsupported:
        raise ValueError(f"{label} has unsupported fields {unsupported}")
    return value


def _rationale_assessment(
    value: Any, *, fields: set[str], allowed: set[str], label: str,
) -> dict[str, Any]:
    item = _exact_fields(value, fields, label)
    if item.get("assessment") not in allowed:
        raise ValueError(f"{label}.assessment must be one of {sorted(allowed)}")
    if not _nonempty_string(item.get("rationale")):
        raise ValueError(f"{label}.rationale must be a non-empty string")
    return item


def _claims(state_root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    values, errors = ac.load_jsonl(state_root / "design_claims.jsonl")
    if errors:
        raise ValueError(f"design_claims.jsonl is invalid: {'; '.join(errors)}")
    indexed: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(values, start=1):
        claim_id = claim.get("claim_id")
        if not _nonempty_string(claim_id):
            raise ValueError(f"design_claims.jsonl:{index} lacks a non-empty claim_id")
        if claim_id in indexed:
            raise ValueError(f"design_claims.jsonl:{index} duplicates claim_id {claim_id!r}")
        indexed[str(claim_id)] = claim
    return values, indexed


def _scope_claim_ids(scope: dict[str, Any], claims: dict[str, dict[str, Any]]) -> list[str]:
    raw = scope.get("claim_ids")
    if not isinstance(raw, list) or not raw:
        raise ValueError("claim_review_scope.json claim_ids must be a non-empty array")
    if any(not _nonempty_string(value) for value in raw):
        raise ValueError(
            "claim_review_scope.json claim_ids entries must be non-empty strings"
        )
    claim_ids = [str(value) for value in raw]
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("claim_review_scope.json claim_ids must not contain duplicates")
    if len(claim_ids) > 12:
        raise ValueError("claim_review_scope.json may contain at most 12 claims")
    unknown = sorted(set(claim_ids) - set(claims))
    if unknown:
        raise ValueError(f"claim_review_scope.json has unknown claim_ids: {unknown}")
    missing = sorted(set(claims) - set(claim_ids))
    if missing:
        raise ValueError(
            "claim_review_scope.json must include every materialized claim; "
            f"missing={missing}"
        )
    return claim_ids


def _semantic_review(value: Any, *, index: int) -> dict[str, Any]:
    label = f"reviews[{index}]"
    item = _exact_fields(value, REVIEW_FIELDS, label)
    entailment = _rationale_assessment(
        item["quote_entailment"], fields=ENTAILMENT_FIELDS,
        allowed=validator.ENTAILMENT, label=f"{label}.quote_entailment",
    )
    strength = _rationale_assessment(
        item["normative_strength"], fields=STRENGTH_FIELDS,
        allowed=validator.STRENGTH_ASSESSMENTS,
        label=f"{label}.normative_strength",
    )
    recommended = strength.get("recommended_strength")
    if recommended not in validator.RECOMMENDED_STRENGTHS:
        raise ValueError(
            f"{label}.normative_strength.recommended_strength must be one of "
            f"{sorted(validator.RECOMMENDED_STRENGTHS)}"
        )
    atomicity = _rationale_assessment(
        item["atomicity"], fields=ATOMICITY_FIELDS,
        allowed=validator.ATOMICITY, label=f"{label}.atomicity",
    )
    obligations = atomicity.get("obligations")
    if not isinstance(obligations, list) or any(
        not _nonempty_string(obligation) for obligation in obligations
    ):
        raise ValueError(f"{label}.atomicity.obligations must be an array of strings")
    applicability = _rationale_assessment(
        item["applicability"], fields=APPLICABILITY_FIELDS,
        allowed=validator.APPLICABILITY, label=f"{label}.applicability",
    )
    decision = item.get("decision")
    if decision not in validator.DECISIONS:
        raise ValueError(f"{label}.decision must be one of {sorted(validator.DECISIONS)}")
    repair_rationale = item.get("repair_rationale")
    if not isinstance(repair_rationale, str):
        raise ValueError(f"{label}.repair_rationale must be a string")
    if decision == "repair" and not repair_rationale.strip():
        raise ValueError(f"{label}.repair_rationale must explain the requested repair")
    if decision == "accept" and repair_rationale.strip():
        raise ValueError(f"{label}.repair_rationale must be empty for an accepted claim")
    return {
        "quote_entailment": dict(entailment),
        "normative_strength": dict(strength),
        "atomicity": {
            **atomicity,
            "obligations": list(obligations),
        },
        "applicability": dict(applicability),
        "decision": str(decision),
        "repair_rationale": repair_rationale.strip(),
    }


def materialize_claim_review(
    semantic: dict[str, Any], state_root: Path,
) -> dict[str, Any]:
    state_root = state_root.resolve()
    semantic = _exact_fields(semantic, TOP_LEVEL_FIELDS, "spec critic semantic output")
    scope = _object(state_root / "claim_review_scope.json", "claim_review_scope.json")
    inventory = _object(state_root / "design_inventory.json", "design_inventory.json")
    manifest = _object(
        state_root / "design_agent_manifest.json", "design_agent_manifest.json",
    )
    state = _object(state_root / "agent_loop_state.json", "agent_loop_state.json")
    _object(state_root / "design_coverage.json", "design_coverage.json")
    _claim_values, claim_index = _claims(state_root)

    session_id = scope.get("session_id")
    if not _nonempty_string(session_id):
        raise ValueError("claim_review_scope.json lacks a non-empty session_id")
    for label, value in (
        ("agent_loop_state.json", state),
        ("design_inventory.json", inventory),
        ("design_agent_manifest.json", manifest),
    ):
        if value.get("session_id") != session_id:
            raise ValueError(f"{label} session_id does not match claim review scope")
    stale_claims = sorted(
        claim_id for claim_id, claim in claim_index.items()
        if claim.get("session_id") != session_id
    )
    if stale_claims:
        raise ValueError(f"claims have a stale session_id: {stale_claims}")

    claim_ids = _scope_claim_ids(scope, claim_index)
    raw_reviews = semantic.get("reviews")
    if not isinstance(raw_reviews, list):
        raise ValueError("spec critic semantic output reviews must be an array")
    if len(raw_reviews) != len(claim_ids):
        raise ValueError(
            "spec critic semantic output must contain exactly one ordered review "
            f"per scoped claim: expected={len(claim_ids)} actual={len(raw_reviews)}"
        )

    design_root_value = manifest.get("review_design_root")
    if not _nonempty_string(design_root_value):
        raise ValueError("design_agent_manifest.json lacks review_design_root")
    design_root = Path(str(design_root_value)).resolve()
    if not design_root.is_dir():
        raise ValueError(f"review_design_root is not a directory: {design_root}")

    claim_reviews: list[dict[str, Any]] = []
    for index, (claim_id, raw_review) in enumerate(
        zip(claim_ids, raw_reviews), start=1,
    ):
        semantic_review = _semantic_review(raw_review, index=index)
        claim = claim_index[claim_id]
        source_ref = claim.get("source_ref")
        if not isinstance(source_ref, dict) or not _nonempty_string(
            source_ref.get("source_sha256")
        ):
            raise ValueError(f"claim {claim_id} lacks source_ref.source_sha256")
        stated_strength = claim.get("normative_strength")
        if stated_strength not in validator.NORMATIVE_STRENGTHS:
            raise ValueError(f"claim {claim_id} has invalid normative_strength")
        review = {
            "session_id": session_id,
            "claim_id": claim_id,
            "claim_sha256": validator._claim_digest(claim),
            "source_sha256": source_ref["source_sha256"],
            "spec_critic_prompt_version": validator.SPEC_CRITIC_PROMPT_VERSION,
            "quote_entailment": semantic_review["quote_entailment"],
            "normative_strength": {
                **semantic_review["normative_strength"],
                "stated_strength": stated_strength,
            },
            "atomicity": semantic_review["atomicity"],
            "applicability": semantic_review["applicability"],
            "decision": semantic_review["decision"],
            "repair_actions": (
                [semantic_review["repair_rationale"]]
                if semantic_review["decision"] == "repair" else []
            ),
        }
        validation_errors: list[str] = []
        validator._validate_claim_review(
            review, index=index, session_id=str(session_id), design_root=design_root,
            claims=claim_index, errors=validation_errors,
        )
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        claim_reviews.append(review)

    repair_count = sum(review["decision"] == "repair" for review in claim_reviews)
    accepted_count = len(claim_reviews) - repair_count
    input_digests = {
        name: ac.sha256_file(state_root / name)
        for name in BOUND_INPUTS
    }
    return {
        "session_id": session_id,
        "claim_reviews": claim_reviews,
        "group_reviews": [],
        "decision": "repair" if repair_count else "accept",
        "summary": (
            f"Spec critic reviewed {len(claim_reviews)} scoped claims: "
            f"{accepted_count} accepted and {repair_count} marked for repair."
        ),
        "input_digests": input_digests,
    }


def run(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root).resolve()
    input_path = Path(args.input).resolve()
    output_path = (
        Path(args.output).resolve()
        if args.output else state_root / "design_claim_review.json"
    )
    trace_path = Path(args.trace).resolve() if args.trace else None
    errors: list[str] = []
    review_count = 0
    if not state_root.is_dir():
        errors.append(f"state root is not a directory: {state_root}")
    if not input_path.is_file():
        errors.append(f"spec critic semantic input is missing: {input_path}")
    expected_output = state_root / "design_claim_review.json"
    if output_path != expected_output:
        errors.append(f"output must be the canonical review artifact: {expected_output}")
    if input_path == output_path:
        errors.append("review output must not overwrite the semantic input")
    if trace_path is not None and trace_path in {input_path, output_path}:
        errors.append("trace must not overwrite the semantic input or review output")
    if not errors:
        try:
            semantic = ac.load_json(input_path)
            review = materialize_claim_review(semantic, state_root)
            review_count = len(review["claim_reviews"])
            ac.save_json(output_path, review)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    report = {
        "materialized_at": ac.now_iso(),
        "passed": not errors,
        "semantic_analysis_performed": False,
        "review_count": review_count,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "errors": errors,
    }
    if trace_path is not None:
        ac.save_json(trace_path, report)
    print(json.dumps({
        "passed": not errors, "reviews": review_count, "errors": len(errors),
    }))
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bind minimal spec-critic assessments to current design claims.",
    )
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--trace", default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
