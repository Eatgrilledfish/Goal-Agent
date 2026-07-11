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
CLAIM_TEXT_FIELDS = (
    "claim_id", "session_id", "document", "path", "section", "quote", "behavior",
    "behavior_family", "normative_strength", "applicability", "priority",
)


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _required(item: dict[str, Any], fields: tuple[str, ...], label: str) -> list[str]:
    return [f"{label}: missing/empty {field}" for field in fields if not _nonempty(item.get(field))]


def _relative_design_path(value: Any) -> str | None:
    """Return a canonical manifest-relative path without resolving the filesystem."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalized = path.as_posix()
    if normalized in {"", "."}:
        return None
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or None


def _catalog_scoped_group_keys(manifest: dict[str, Any]) -> set[str]:
    """Identify materialized catalog sources from recorded provenance, not catalog text."""
    source_manifest = manifest.get("design", {}).get("source_manifest")
    if not isinstance(source_manifest, dict):
        return set()
    keys: set[str] = set()
    sources = source_manifest.get("sources")
    if not isinstance(sources, list):
        return keys
    for source in sources:
        if not isinstance(source, dict) or source.get("source_id") == "catalog":
            continue
        evidence = source.get("catalog_evidence")
        bundle_path = _relative_design_path(source.get("bundle_path"))
        if bundle_path and isinstance(evidence, dict) and _nonempty(evidence.get("path")):
            keys.add(str(Path(bundle_path).with_suffix("")).lower())
    return keys


def validate_claim(claim: dict[str, Any], session_id: str, design_root: Path, label: str) -> list[str]:
    errors = _required(claim, (
        "claim_id", "session_id", "document", "path", "section", "line_start", "line_end",
        "quote", "behavior", "behavior_family", "normative_strength", "applicability",
        "priority", "probe_oracle",
    ), label)
    for field in CLAIM_TEXT_FIELDS:
        value = claim.get(field)
        if _nonempty(value) and not isinstance(value, str):
            errors.append(f"{label}: {field} must be a string")
    for field in ("line_start", "line_end"):
        value = claim.get(field)
        if _nonempty(value) and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{label}: {field} must be an integer")
    start = claim.get("line_start")
    end = claim.get("line_end")
    if (
        isinstance(start, int) and not isinstance(start, bool)
        and isinstance(end, int) and not isinstance(end, bool)
        and (start < 1 or end < start)
    ):
        errors.append(f"{label}: invalid line range {start}-{end}")
    if claim.get("session_id") != session_id:
        errors.append(f"{label}: session_id does not match current session")
    if not isinstance(claim.get("normative_strength"), str) or claim.get("normative_strength") not in NORMATIVE_STRENGTHS:
        errors.append(f"{label}: invalid normative_strength")
    if not isinstance(claim.get("priority"), str) or claim.get("priority") not in PRIORITIES:
        errors.append(f"{label}: invalid priority")
    if not isinstance(claim.get("ambiguities"), list):
        errors.append(f"{label}: ambiguities must be an array")
    elif any(not isinstance(value, str) or not value.strip() for value in claim["ambiguities"]):
        errors.append(f"{label}: ambiguities entries must be non-empty strings")
    oracle = claim.get("probe_oracle")
    if not isinstance(oracle, dict):
        errors.append(f"{label}: probe_oracle must be an object")
    else:
        testability = oracle.get("testability")
        if not isinstance(testability, str) or testability not in TESTABILITY:
            errors.append(f"{label}: invalid probe_oracle.testability")
        if not isinstance(oracle.get("preconditions"), list):
            errors.append(f"{label}: probe_oracle.preconditions must be an array")
        elif any(not isinstance(value, str) or not value.strip() for value in oracle["preconditions"]):
            errors.append(f"{label}: probe_oracle.preconditions entries must be non-empty strings")
        if testability == "not_suitable":
            if not _nonempty(oracle.get("non_testable_reason")):
                errors.append(f"{label}: not_suitable oracle needs non_testable_reason")
            elif not isinstance(oracle.get("non_testable_reason"), str):
                errors.append(f"{label}: probe_oracle.non_testable_reason must be a string")
        else:
            for field in ("stimulus", "expected_observation"):
                if not _nonempty(oracle.get(field)):
                    errors.append(f"{label}: probe_oracle missing/empty {field}")
                elif not isinstance(oracle.get(field), str):
                    errors.append(f"{label}: probe_oracle.{field} must be a string")
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
    member_groups: dict[str, str] = {}
    for document_key, group in manifest_groups.items():
        members = group.get("members")
        if not isinstance(members, list):
            errors.append(f"workspace manifest document group {document_key!r}: members must be an array")
            continue
        for member in members:
            normalized = _relative_design_path(member)
            if normalized is None:
                errors.append(f"workspace manifest document group {document_key!r}: invalid member {member!r}")
            elif normalized in member_groups and member_groups[normalized] != document_key:
                errors.append(f"workspace manifest member belongs to multiple document groups: {normalized!r}")
            else:
                member_groups[normalized] = document_key

    claim_groups: dict[str, str] = {}
    for claim_id, claim in claim_index.items():
        path_value = claim.get("path")
        normalized = _relative_design_path(path_value)
        if normalized is None:
            errors.append(f"design claim {claim_id}: path must be relative to the design root without traversal")
            continue
        document_key = member_groups.get(normalized)
        if document_key is None:
            errors.append(f"design claim {claim_id}: path is not a member of any manifest document group: {normalized!r}")
            continue
        claim_groups[claim_id] = document_key

    catalog_scoped_groups = _catalog_scoped_group_keys(manifest) & set(manifest_groups)
    coverage_groups: dict[str, dict[str, Any]] = {}
    referenced_claims: set[str] = set()
    for index, group in enumerate(groups, start=1):
        label = f"design_coverage.json document_groups[{index}]"
        if not isinstance(group, dict):
            errors.append(f"{label}: must be an object")
            continue
        raw_document_key = group.get("document_key")
        document_key = str(raw_document_key or "")
        if _nonempty(raw_document_key) and not isinstance(raw_document_key, str):
            errors.append(f"{label}: document_key must be a string")
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
        if not isinstance(group.get("disposition"), str) or group.get("disposition") not in DISPOSITIONS:
            errors.append(f"{label}: invalid disposition")
        if not _nonempty(group.get("evidence")):
            errors.append(f"{label}: evidence must explain scope disposition")
        elif not isinstance(group.get("evidence"), str):
            errors.append(f"{label}: evidence must be a string")
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
        if any(not isinstance(value, str) or not value.strip() for value in claim_ids):
            errors.append(f"{label}: claim_ids entries must be non-empty strings")
        if len({value for value in claim_ids if isinstance(value, str)}) != len(claim_ids):
            errors.append(f"{label}: claim_ids must not contain duplicates")
        if any(not isinstance(value, str) or not value.strip() for value in families):
            errors.append(f"{label}: behavior_families entries must be non-empty strings")
        if len({value for value in families if isinstance(value, str)}) != len(families):
            errors.append(f"{label}: behavior_families must not contain duplicates")

        valid_group_claims: list[str] = []
        for raw_claim_id in claim_ids:
            if not isinstance(raw_claim_id, str) or not raw_claim_id.strip():
                continue
            claim_id = raw_claim_id
            if claim_id not in claim_index:
                errors.append(f"{label}: unknown claim_id {claim_id!r}")
            else:
                valid_group_claims.append(claim_id)
                claim_group = claim_groups.get(claim_id)
                if claim_group is not None and claim_group != document_key:
                    errors.append(
                        f"{label}: claim {claim_id!r} cites different document group {claim_group!r}"
                    )
            referenced_claims.add(claim_id)

        represented_families = {
            str(claim_index[claim_id].get("behavior_family"))
            for claim_id in valid_group_claims
            if claim_groups.get(claim_id) == document_key
            and isinstance(claim_index[claim_id].get("behavior_family"), str)
            and claim_index[claim_id].get("behavior_family")
        }
        if group.get("disposition") == "applicable":
            missing_families = sorted(
                family for family in families
                if isinstance(family, str) and family not in represented_families
            )
            if missing_families:
                errors.append(f"{label}: behavior families lack same-group claims {missing_families}")
            undeclared_families = sorted(
                family for family in represented_families if family not in families
            )
            if undeclared_families:
                errors.append(f"{label}: same-group claims use undeclared behavior families {undeclared_families}")
            if document_key in catalog_scoped_groups and not any(
                claim_groups.get(claim_id) == document_key
                and claim_index[claim_id].get("normative_strength") == "declared_capability"
                for claim_id in valid_group_claims
            ):
                errors.append(f"{label}: catalog-scoped applicable group needs a declared_capability claim")

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
        "input_digests": {
            "design_claims.jsonl": ac.sha256_file(root / "design_claims.jsonl"),
            "design_coverage.json": ac.sha256_file(coverage_path) if coverage_path.is_file() else "",
            "workspace_manifest.json": ac.sha256_file(root / "workspace_manifest.json"),
        },
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
