#!/usr/bin/env python3
"""Validate spec-analyst artifacts without making semantic judgements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac


DISPOSITIONS = {"applicable", "inapplicable", "superseded", "supporting"}
NORMATIVE_STRENGTHS = {
    "mandatory", "recommended", "optional", "declared_capability", "informational",
}
PRIORITIES = {"high", "medium", "low"}
TESTABILITY = {"candidate", "not_suitable", "unknown"}


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _required(item: dict[str, Any], fields: tuple[str, ...], label: str) -> list[str]:
    return [f"{label}: missing/empty {field}" for field in fields if not _nonempty(item.get(field))]


def validate_claim(claim: dict[str, Any], session_id: str, design_root: Path, label: str) -> list[str]:
    errors = _required(claim, (
        "claim_id", "session_id", "document", "path", "section", "line_start", "line_end",
        "quote", "behavior", "behavior_family", "normative_strength", "applicability",
        "priority", "probe_oracle",
    ), label)
    if claim.get("session_id") != session_id:
        errors.append(f"{label}: session_id does not match current session")
    if claim.get("normative_strength") not in NORMATIVE_STRENGTHS:
        errors.append(f"{label}: invalid normative_strength")
    if claim.get("priority") not in PRIORITIES:
        errors.append(f"{label}: invalid priority")
    if not isinstance(claim.get("ambiguities"), list):
        errors.append(f"{label}: ambiguities must be an array")
    oracle = claim.get("probe_oracle")
    if not isinstance(oracle, dict):
        errors.append(f"{label}: probe_oracle must be an object")
    else:
        testability = oracle.get("testability")
        if testability not in TESTABILITY:
            errors.append(f"{label}: invalid probe_oracle.testability")
        if not isinstance(oracle.get("preconditions"), list):
            errors.append(f"{label}: probe_oracle.preconditions must be an array")
        if testability == "not_suitable":
            if not _nonempty(oracle.get("non_testable_reason")):
                errors.append(f"{label}: not_suitable oracle needs non_testable_reason")
        else:
            for field in ("stimulus", "expected_observation"):
                if not _nonempty(oracle.get(field)):
                    errors.append(f"{label}: probe_oracle missing/empty {field}")
    errors.extend(ac.validate_source_evidence(claim, design_root, label, "quote"))
    return errors


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    trace_path = log_root / "trace" / "design_validation.json"
    path_errors = ac.session_path_errors(
        root, code_root=code_root, design_root=design_root,
        result_root=result_root, log_root=log_root,
    )
    if path_errors:
        ac.save_json(trace_path, {"session_id": "", "passed": False, "metrics": {}, "errors": path_errors})
        print(json.dumps({"passed": False, "errors": len(path_errors)}))
        return 2

    state = ac.load_json(root / "agent_loop_state.json")
    manifest = ac.load_json(root / "workspace_manifest.json")
    session_id = str(state.get("session_id") or "")
    errors: list[str] = []

    claims, claim_parse_errors = ac.load_jsonl(root / "design_claims.jsonl")
    errors.extend(claim_parse_errors)
    claim_index: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims, start=1):
        claim_id = str(claim.get("claim_id") or "")
        label = f"design_claims.jsonl:{index} ({claim_id or '?'})"
        errors.extend(validate_claim(claim, session_id, design_root, label))
        if claim_id in claim_index:
            errors.append(f"{label}: duplicate claim_id")
        elif claim_id:
            claim_index[claim_id] = claim

    coverage_path = root / "design_coverage.json"
    coverage = ac.load_json(coverage_path) if coverage_path.is_file() else {}
    if not isinstance(coverage, dict):
        errors.append("design_coverage.json must be an object")
        coverage = {}
    if coverage.get("session_id") != session_id:
        errors.append("design_coverage.json session_id does not match current session")
    groups = coverage.get("document_groups")
    if not isinstance(groups, list):
        errors.append("design_coverage.json document_groups must be an array")
        groups = []

    manifest_groups = {
        str(group.get("document_key")): group
        for group in manifest.get("design", {}).get("document_groups", [])
        if isinstance(group, dict) and group.get("document_key")
    }
    coverage_groups: dict[str, dict[str, Any]] = {}
    referenced_claims: set[str] = set()
    for index, group in enumerate(groups, start=1):
        label = f"design_coverage.json document_groups[{index}]"
        if not isinstance(group, dict):
            errors.append(f"{label}: must be an object")
            continue
        document_key = str(group.get("document_key") or "")
        for field in ("document_key", "members", "disposition", "evidence", "claim_ids", "behavior_families"):
            if field not in group:
                errors.append(f"{label}: missing {field}")
        if document_key in coverage_groups:
            errors.append(f"{label}: duplicate document_key {document_key!r}")
        elif document_key:
            coverage_groups[document_key] = group
        expected_group = manifest_groups.get(document_key)
        if not expected_group:
            errors.append(f"{label}: unknown document_key {document_key!r}")
        elif group.get("members") != expected_group.get("members"):
            errors.append(f"{label}: members do not match workspace manifest")
        if group.get("disposition") not in DISPOSITIONS:
            errors.append(f"{label}: invalid disposition")
        if not _nonempty(group.get("evidence")):
            errors.append(f"{label}: evidence must explain scope disposition")
        claim_ids = group.get("claim_ids")
        if not isinstance(claim_ids, list):
            errors.append(f"{label}: claim_ids must be an array")
            claim_ids = []
        families = group.get("behavior_families")
        if not isinstance(families, list):
            errors.append(f"{label}: behavior_families must be an array")
            families = []
        if group.get("disposition") == "applicable" and (not claim_ids or not families):
            errors.append(f"{label}: applicable group needs claim_ids and behavior_families")
        for claim_id in claim_ids:
            if claim_id not in claim_index:
                errors.append(f"{label}: unknown claim_id {claim_id!r}")
            referenced_claims.add(str(claim_id))

    missing_groups = set(manifest_groups) - set(coverage_groups)
    extra_groups = set(coverage_groups) - set(manifest_groups)
    if missing_groups:
        errors.append(f"design coverage missing document groups: {sorted(missing_groups)}")
    if extra_groups:
        errors.append(f"design coverage has unknown document groups: {sorted(extra_groups)}")
    unreferenced = set(claim_index) - referenced_claims
    if unreferenced:
        errors.append(f"design claims not referenced by coverage: {sorted(unreferenced)}")

    report = {
        "validated_at": ac.now_iso(),
        "session_id": session_id,
        "passed": not errors,
        "metrics": {
            "claims": len(claim_index),
            "manifest_document_groups": len(manifest_groups),
            "coverage_document_groups": len(coverage_groups),
        },
        "errors": errors,
    }
    ac.save_json(trace_path, report)
    print(json.dumps({"passed": not errors, "claims": len(claim_index), "errors": len(errors)}))
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate spec-analyst claim and coverage artifacts.")
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
