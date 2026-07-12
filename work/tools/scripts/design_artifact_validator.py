#!/usr/bin/env python3
"""Validate incremental design inventory and claims without semantic judgement."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import agent_common as ac
import design_source_materializer as materializer


MODES = {"inventory", "claims", "all"}
SCOPE_RELATIONS = {
    "required", "in_scope", "relevant", "informational", "superseded", "ambiguous",
}
NORMATIVE_STRENGTHS = {
    "mandatory", "recommended", "optional", "declared_capability", "informational",
}
PRIORITIES = {"high", "medium", "low"}
TESTABILITY = {"candidate", "not_suitable", "unknown"}
COVERAGE_DISPOSITIONS = {"applicable", "inapplicable", "superseded", "supporting"}


class Issues:
    """Keep compatible string errors while aggregating machine-readable error codes."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.codes: list[str] = []

    def add(self, code: str, message: str) -> None:
        self.codes.append(code)
        self.errors.append(message)

    def extend(self, code: str, messages: list[str]) -> None:
        for message in messages:
            self.add(code, message)

    def grouped(self) -> list[dict[str, Any]]:
        samples: dict[str, list[str]] = defaultdict(list)
        for code, message in zip(self.codes, self.errors):
            if len(samples[code]) < 5:
                samples[code].append(message)
        counts = Counter(self.codes)
        return [
            {"code": code, "count": counts[code], "samples": samples[code]}
            for code in sorted(counts)
        ]

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(self.codes).items()))


def canonical_object_sha256(value: dict[str, Any], *, excluded: set[str] | None = None) -> str:
    payload = {key: item for key, item in value.items() if key not in (excluded or set())}
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _relative_design_path(value: Any) -> str | None:
    """Return a canonical manifest-relative path without resolving the filesystem."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized if normalized not in {"", "."} else None


def _load_object(path: Path, label: str, issues: Issues) -> dict[str, Any]:
    if not path.is_file():
        issues.add("ARTIFACT_MISSING", f"missing artifact: {path}")
        return {}
    try:
        value = ac.load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        issues.add("ARTIFACT_PARSE_ERROR", f"{label}: cannot load JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        issues.add("ARTIFACT_SCHEMA_INVALID", f"{label} must be an object")
        return {}
    return value


def _require_fields(
    item: dict[str, Any], fields: tuple[str, ...], label: str, issues: Issues,
    *, allow_empty: set[str] | None = None,
) -> None:
    allowed = allow_empty or set()
    for field in fields:
        if field not in item or (field not in allowed and not _nonempty(item.get(field))):
            issues.add("ARTIFACT_SCHEMA_INVALID", f"{label}: missing/empty {field}")


def _validate_string_list(
    value: Any, label: str, issues: Issues, *, allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        issues.add("ARTIFACT_SCHEMA_INVALID", f"{label} must be an array")
        return []
    if not allow_empty and not value:
        issues.add("ARTIFACT_SCHEMA_INVALID", f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        issues.add("ARTIFACT_SCHEMA_INVALID", f"{label} entries must be non-empty strings")
    strings = [item for item in value if isinstance(item, str) and item.strip()]
    if len(set(strings)) != len(strings):
        issues.add("ARTIFACT_SCHEMA_INVALID", f"{label} must not contain duplicates")
    return strings


def _source_ref_details(
    item: dict[str, Any], design_root: Path, label: str, issues: Issues,
    *, quote_field: str | None,
) -> tuple[str | None, int | None, int | None]:
    ref = item.get("source_ref")
    if not isinstance(ref, dict):
        issues.add("SOURCE_REF_INVALID", f"{label}: source_ref must be an object")
        return None, None, None
    path_value = _relative_design_path(ref.get("path"))
    if path_value is None:
        issues.add(
            "SOURCE_REF_INVALID",
            f"{label}: source_ref.path must be relative to the design root without traversal",
        )
        return None, None, None
    path = ac.contained_path(design_root, path_value)
    if path is None or not path.is_file():
        issues.add("SOURCE_REF_INVALID", f"{label}: source_ref file does not exist: {path_value!r}")
        return path_value, None, None
    start = ref.get("line_start")
    end = ref.get("line_end")
    if not isinstance(start, int) or isinstance(start, bool):
        issues.add("SOURCE_REF_INVALID", f"{label}: source_ref.line_start must be an integer")
        start = None
    if not isinstance(end, int) or isinstance(end, bool):
        issues.add("SOURCE_REF_INVALID", f"{label}: source_ref.line_end must be an integer")
        end = None
    if start is not None and end is not None and (start < 1 or end < start):
        issues.add("SOURCE_REF_INVALID", f"{label}: invalid source_ref line range {start}-{end}")
        start = end = None
    source_hash = ref.get("source_sha256")
    expected_hash = ac.sha256_file(path)
    if not isinstance(source_hash, str) or source_hash != expected_hash:
        issues.add("SOURCE_HASH_MISMATCH", f"{label}: source_ref.source_sha256 does not match source file")

    if item.get("path") != path_value:
        issues.add("MATERIALIZED_FIELD_MISMATCH", f"{label}: path does not match source_ref.path")
    if item.get("line_start") != start:
        issues.add("MATERIALIZED_FIELD_MISMATCH", f"{label}: line_start does not match source_ref.line_start")
    if item.get("line_end") != end:
        issues.add("MATERIALIZED_FIELD_MISMATCH", f"{label}: line_end does not match source_ref.line_end")

    try:
        source_text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        issues.add("SOURCE_REF_INVALID", f"{label}: source file is not valid UTF-8")
        return path_value, start, end
    if "\x00" in source_text:
        issues.add("SOURCE_REF_INVALID", f"{label}: source file contains binary NUL bytes")
        return path_value, start, end
    lines = source_text.splitlines()
    if start is not None and end is not None:
        if end > len(lines):
            issues.add(
                "SOURCE_REF_INVALID",
                f"{label}: source_ref line range {start}-{end} exceeds {len(lines)} lines",
            )
        else:
            expected_heading = materializer._section_heading(lines, start, path_value)
            heading_field = "heading" if "heading" in item else "section"
            if item.get(heading_field) != expected_heading:
                issues.add(
                    "MATERIALIZED_FIELD_MISMATCH",
                    f"{label}: {heading_field} was not materialized from the selected source range",
                )
            if quote_field is not None:
                expected_quote = "\n".join(lines[start - 1:end])
                if item.get(quote_field) != expected_quote:
                    issues.add(
                        "QUOTE_RANGE_MISMATCH",
                        f"{label}: {quote_field} does not exactly match cited source lines",
                    )
    return path_value, start, end


def _manifest_groups(
    manifest: dict[str, Any], issues: Issues,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    raw_groups = manifest.get("design", {}).get("document_groups", [])
    if not isinstance(raw_groups, list):
        issues.add(
            "WORKSPACE_MANIFEST_INVALID",
            "workspace_manifest.json design.document_groups must be an array",
        )
        return {}, {}
    groups: dict[str, dict[str, Any]] = {}
    member_groups: dict[str, str] = {}
    for index, group in enumerate(raw_groups, start=1):
        label = f"workspace manifest document_groups[{index}]"
        if not isinstance(group, dict):
            issues.add("WORKSPACE_MANIFEST_INVALID", f"{label}: must be an object")
            continue
        document_key = group.get("document_key")
        if not isinstance(document_key, str) or not document_key.strip():
            issues.add("WORKSPACE_MANIFEST_INVALID", f"{label}: invalid document_key")
            continue
        if document_key in groups:
            issues.add("WORKSPACE_MANIFEST_INVALID", f"{label}: duplicate document_key {document_key!r}")
            continue
        groups[document_key] = group
        members = group.get("members")
        if not isinstance(members, list):
            issues.add("WORKSPACE_MANIFEST_INVALID", f"{label}: members must be an array")
            continue
        for member in members:
            normalized = _relative_design_path(member)
            if normalized is None:
                issues.add("WORKSPACE_MANIFEST_INVALID", f"{label}: invalid member {member!r}")
            elif normalized in member_groups:
                issues.add(
                    "WORKSPACE_MANIFEST_INVALID",
                    f"workspace manifest member belongs to multiple groups: {normalized!r}",
                )
            else:
                member_groups[normalized] = document_key
    return groups, member_groups


def validate_inventory(
    inventory: dict[str, Any], session_id: str, design_root: Path,
    manifest_groups: dict[str, dict[str, Any]], issues: Issues,
) -> dict[str, dict[str, Any]]:
    if inventory.get("session_id") != session_id:
        issues.add("SESSION_MISMATCH", "design_inventory.json session_id does not match current session")
    raw_groups = inventory.get("document_groups")
    if not isinstance(raw_groups, list):
        issues.add("ARTIFACT_SCHEMA_INVALID", "design_inventory.json document_groups must be an array")
        if manifest_groups:
            issues.add(
                "INVENTORY_GROUP_COVERAGE",
                f"design inventory missing document groups: {sorted(manifest_groups)}",
            )
        return {}
    inventory_groups: dict[str, dict[str, Any]] = {}
    section_ids: set[str] = set()
    for index, group in enumerate(raw_groups, start=1):
        label = f"design_inventory.json document_groups[{index}]"
        if not isinstance(group, dict):
            issues.add("ARTIFACT_SCHEMA_INVALID", f"{label}: must be an object")
            continue
        _require_fields(
            group,
            ("document_key", "members", "scope_relation", "scope_evidence", "sections", "group_sha256"),
            label,
            issues,
        )
        document_key = group.get("document_key")
        if not isinstance(document_key, str) or not document_key.strip():
            issues.add("ARTIFACT_SCHEMA_INVALID", f"{label}: document_key must be a non-empty string")
            continue
        if document_key in inventory_groups:
            issues.add("INVENTORY_GROUP_DUPLICATE", f"{label}: duplicate document_key {document_key!r}")
        else:
            inventory_groups[document_key] = group
        expected_group = manifest_groups.get(document_key)
        if expected_group is None:
            issues.add("INVENTORY_GROUP_COVERAGE", f"{label}: unknown document_key {document_key!r}")
            expected_members: list[Any] = []
        else:
            expected_members = expected_group.get("members", [])
        if group.get("members") != expected_members:
            issues.add("INVENTORY_MEMBER_MISMATCH", f"{label}: members do not match workspace manifest")
        relation = group.get("scope_relation")
        if not isinstance(relation, str) or relation not in SCOPE_RELATIONS:
            issues.add("ARTIFACT_SCHEMA_INVALID", f"{label}: invalid scope_relation")
        evidence = group.get("scope_evidence")
        if not isinstance(evidence, dict):
            issues.add("ARTIFACT_SCHEMA_INVALID", f"{label}: scope_evidence must be an object")
        else:
            _source_ref_details(evidence, design_root, f"{label}.scope_evidence", issues, quote_field="quote")

        raw_sections = group.get("sections")
        if not isinstance(raw_sections, list):
            issues.add("ARTIFACT_SCHEMA_INVALID", f"{label}: sections must be an array")
            raw_sections = []
        if not raw_sections:
            issues.add("INVENTORY_SECTION_MISSING", f"{label}: sections must not be empty")
        expected_member_set = {
            normalized for member in expected_members
            if (normalized := _relative_design_path(member)) is not None
        }
        for section_index, section in enumerate(raw_sections, start=1):
            section_label = f"{label}.sections[{section_index}]"
            if not isinstance(section, dict):
                issues.add("ARTIFACT_SCHEMA_INVALID", f"{section_label}: must be an object")
                continue
            _require_fields(
                section,
                ("section_id", "source_ref", "path", "heading", "line_start", "line_end", "behavior_families", "ambiguities"),
                section_label,
                issues,
                allow_empty={"behavior_families", "ambiguities"},
            )
            section_id = section.get("section_id")
            if not isinstance(section_id, str) or not section_id.strip():
                issues.add("ARTIFACT_SCHEMA_INVALID", f"{section_label}: section_id must be a non-empty string")
            elif section_id in section_ids:
                issues.add("INVENTORY_SECTION_DUPLICATE", f"{section_label}: duplicate section_id {section_id!r}")
            else:
                section_ids.add(section_id)
            section_path, _, _ = _source_ref_details(
                section, design_root, section_label, issues, quote_field=None,
            )
            if section_path is not None and section_path not in expected_member_set:
                issues.add(
                    "INVENTORY_MEMBER_MISMATCH",
                    f"{section_label}: path is not a member of document group {document_key!r}",
                )
            _validate_string_list(section.get("behavior_families"), f"{section_label}.behavior_families", issues)
            _validate_string_list(section.get("ambiguities"), f"{section_label}.ambiguities", issues)

        expected_digest = canonical_object_sha256(group, excluded={"group_sha256"})
        if group.get("group_sha256") != expected_digest:
            issues.add("GROUP_DIGEST_MISMATCH", f"{label}: group_sha256 does not match canonical group content")

    missing = sorted(set(manifest_groups) - set(inventory_groups))
    extra = sorted(set(inventory_groups) - set(manifest_groups))
    if missing:
        issues.add("INVENTORY_GROUP_COVERAGE", f"design inventory missing document groups: {missing}")
    if extra:
        issues.add("INVENTORY_GROUP_COVERAGE", f"design inventory has unknown document groups: {extra}")
    return inventory_groups


def _validate_probe_oracle(oracle: Any, label: str, issues: Issues) -> None:
    if not isinstance(oracle, dict):
        issues.add("CLAIM_SCHEMA_INVALID", f"{label}: probe_oracle must be an object")
        return
    testability = oracle.get("testability")
    if not isinstance(testability, str) or testability not in TESTABILITY:
        issues.add("CLAIM_SCHEMA_INVALID", f"{label}: invalid probe_oracle.testability")
    _validate_string_list(oracle.get("preconditions"), f"{label}.probe_oracle.preconditions", issues)
    if testability == "not_suitable":
        if not isinstance(oracle.get("non_testable_reason"), str) or not oracle.get("non_testable_reason", "").strip():
            issues.add("CLAIM_SCHEMA_INVALID", f"{label}: not_suitable oracle needs non_testable_reason")
    else:
        for field in ("stimulus", "expected_observation"):
            if not isinstance(oracle.get(field), str) or not oracle.get(field, "").strip():
                issues.add("CLAIM_SCHEMA_INVALID", f"{label}: probe_oracle missing/empty {field}")


def _validate_claim(
    claim: dict[str, Any], session_id: str, design_root: Path, label: str,
    member_groups: dict[str, str], issues: Issues,
) -> None:
    _require_fields(
        claim,
        (
            "claim_id", "session_id", "source_ref", "path", "section", "line_start", "line_end", "quote",
            "subject", "trigger", "obligation", "exceptions", "observable_result",
            "normative_strength", "applicability", "ambiguities",
        ),
        label,
        issues,
        allow_empty={"exceptions", "ambiguities"},
    )
    for field in (
        "claim_id", "session_id", "path", "section", "quote", "subject", "trigger",
        "obligation", "observable_result", "normative_strength", "applicability",
    ):
        value = claim.get(field)
        if _nonempty(value) and not isinstance(value, str):
            issues.add("CLAIM_SCHEMA_INVALID", f"{label}: {field} must be a string")
    if claim.get("session_id") != session_id:
        issues.add("SESSION_MISMATCH", f"{label}: session_id does not match current session")
    strength = claim.get("normative_strength")
    if not isinstance(strength, str) or strength not in NORMATIVE_STRENGTHS:
        issues.add("CLAIM_SCHEMA_INVALID", f"{label}: invalid normative_strength")
    priority = claim.get("priority")
    if "priority" in claim and (not isinstance(priority, str) or priority not in PRIORITIES):
        issues.add("CLAIM_SCHEMA_INVALID", f"{label}: invalid priority")
    _validate_string_list(claim.get("exceptions"), f"{label}.exceptions", issues)
    _validate_string_list(claim.get("ambiguities"), f"{label}.ambiguities", issues)
    if "probe_oracle" in claim:
        _validate_probe_oracle(claim.get("probe_oracle"), label, issues)
    path_value, _, _ = _source_ref_details(claim, design_root, label, issues, quote_field="quote")
    if path_value is not None and path_value not in member_groups:
        issues.add(
            "CLAIM_GROUP_INVALID",
            f"{label}: source path is not a member of any inventory document group: {path_value!r}",
        )
    declared_group = claim.get("document_key")
    if declared_group is not None:
        if not isinstance(declared_group, str) or declared_group != member_groups.get(path_value or ""):
            issues.add("CLAIM_GROUP_INVALID", f"{label}: document_key does not match source path group")


def validate_claims(
    claims_path: Path, session_id: str, design_root: Path,
    member_groups: dict[str, str], issues: Issues,
) -> dict[str, dict[str, Any]]:
    claims, parse_errors = ac.load_jsonl(claims_path)
    issues.extend("CLAIM_PARSE_ERROR", parse_errors)
    claim_index: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims, start=1):
        claim_id = str(claim.get("claim_id") or "")
        label = f"design_claims.jsonl:{index} ({claim_id or '?'})"
        _validate_claim(claim, session_id, design_root, label, member_groups, issues)
        if claim_id in claim_index:
            issues.add("CLAIM_ID_DUPLICATE", f"{label}: duplicate claim_id")
        elif claim_id:
            claim_index[claim_id] = claim
    return claim_index


def validate_coverage(
    coverage: dict[str, Any], session_id: str,
    inventory_groups: dict[str, dict[str, Any]],
    claim_index: dict[str, dict[str, Any]], member_groups: dict[str, str],
    issues: Issues,
) -> dict[str, dict[str, Any]]:
    """Mechanically validate the current incremental coverage index.

    Applicability remains model-authored in the inventory.  Once the model has
    marked a document group required or in_scope, however, at least one atomic
    claim is required so the implementation review cannot silently skip the
    entire design domain.
    """
    if coverage.get("session_id") != session_id:
        issues.add(
            "SESSION_MISMATCH",
            "design_coverage.json session_id does not match current session",
        )
    raw_groups = coverage.get("document_groups")
    if not isinstance(raw_groups, list):
        issues.add(
            "COVERAGE_SCHEMA_INVALID",
            "design_coverage.json document_groups must be an array",
        )
        return {}

    coverage_groups: dict[str, dict[str, Any]] = {}
    referenced_claims: set[str] = set()
    for index, group in enumerate(raw_groups, start=1):
        label = f"design_coverage.json document_groups[{index}]"
        if not isinstance(group, dict):
            issues.add("COVERAGE_SCHEMA_INVALID", f"{label}: must be an object")
            continue
        _require_fields(
            group,
            (
                "document_key", "members", "disposition", "evidence",
                "claim_ids", "behavior_families",
            ),
            label,
            issues,
            allow_empty={"claim_ids", "behavior_families"},
        )
        document_key = group.get("document_key")
        if not isinstance(document_key, str) or not document_key.strip():
            issues.add(
                "COVERAGE_SCHEMA_INVALID",
                f"{label}: document_key must be a non-empty string",
            )
            continue
        if document_key in coverage_groups:
            issues.add(
                "COVERAGE_GROUP_DUPLICATE",
                f"{label}: duplicate document_key {document_key!r}",
            )
        else:
            coverage_groups[document_key] = group

        inventory_group = inventory_groups.get(document_key)
        if inventory_group is None:
            issues.add(
                "COVERAGE_GROUP_COVERAGE",
                f"{label}: unknown inventory document_key {document_key!r}",
            )
        elif group.get("members") != inventory_group.get("members"):
            issues.add(
                "COVERAGE_MEMBER_MISMATCH",
                f"{label}: members do not match design inventory",
            )

        disposition = group.get("disposition")
        if not isinstance(disposition, str) or disposition not in COVERAGE_DISPOSITIONS:
            issues.add("COVERAGE_SCHEMA_INVALID", f"{label}: invalid disposition")
        evidence = group.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            issues.add(
                "COVERAGE_SCHEMA_INVALID",
                f"{label}: evidence must be a non-empty string",
            )

        claim_ids = _validate_string_list(
            group.get("claim_ids"), f"{label}.claim_ids", issues,
        )
        if (
            isinstance(inventory_group, dict)
            and inventory_group.get("scope_relation") in {"required", "in_scope"}
            and not claim_ids
        ):
            issues.add(
                "COVERAGE_CLAIM_MISSING",
                f"{label}: required/in_scope design group must materialize at least one claim",
            )
        families = _validate_string_list(
            group.get("behavior_families"), f"{label}.behavior_families", issues,
        )
        inventory_families = {
            family
            for section in (
                inventory_group.get("sections", [])
                if isinstance(inventory_group, dict) else []
            )
            if isinstance(section, dict)
            for family in section.get("behavior_families", [])
            if isinstance(family, str) and family.strip()
        }
        unknown_families = sorted(set(families) - inventory_families)
        if unknown_families:
            issues.add(
                "COVERAGE_FAMILY_INVALID",
                f"{label}: behavior_families are not declared by the inventory group: "
                f"{unknown_families}",
            )

        for claim_id in claim_ids:
            if claim_id in referenced_claims:
                issues.add(
                    "COVERAGE_CLAIM_DUPLICATE",
                    f"{label}: claim {claim_id!r} is assigned to multiple coverage groups",
                )
            referenced_claims.add(claim_id)
            claim = claim_index.get(claim_id)
            if claim is None:
                issues.add(
                    "COVERAGE_CLAIM_INVALID",
                    f"{label}: unknown claim_id {claim_id!r}",
                )
                continue
            claim_path = _relative_design_path(claim.get("path"))
            claim_group = member_groups.get(claim_path or "")
            if claim_group != document_key:
                issues.add(
                    "COVERAGE_CLAIM_INVALID",
                    f"{label}: claim {claim_id!r} belongs to document group "
                    f"{claim_group!r}, not {document_key!r}",
                )

    missing = sorted(set(inventory_groups) - set(coverage_groups))
    extra = sorted(set(coverage_groups) - set(inventory_groups))
    if missing:
        issues.add(
            "COVERAGE_GROUP_COVERAGE",
            f"design coverage missing inventory document groups: {missing}",
        )
    if extra:
        issues.add(
            "COVERAGE_GROUP_COVERAGE",
            f"design coverage has unknown inventory document groups: {extra}",
        )
    unreferenced = sorted(set(claim_index) - referenced_claims)
    if unreferenced:
        issues.add(
            "COVERAGE_CLAIM_INVALID",
            f"materialized design claims are not assigned to coverage groups: {unreferenced}",
        )
    return coverage_groups


def validate_claim(
    claim: dict[str, Any], session_id: str, design_root: Path, label: str,
) -> list[str]:
    """Compatibility helper for callers that validate one ungrouped claim."""
    issues = Issues()
    _validate_claim(claim, session_id, design_root, label, {}, issues)
    return issues.errors


def _safe_digest(path: Path) -> str:
    return ac.sha256_file(path) if path.is_file() else ""


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    mode = str(getattr(args, "mode", "all") or "all")
    trace_path = log_root / "trace" / "design_validation.json"
    issues = Issues()
    path_errors = ac.session_path_errors(
        root, code_root=code_root, design_root=design_root,
        result_root=result_root, log_root=log_root,
    )
    issues.extend("SESSION_PATH_INVALID", path_errors)
    if path_errors:
        report = {
            "validated_at": ac.now_iso(), "session_id": "", "mode": mode,
            "passed": False, "metrics": {}, "errors": issues.errors,
            "error_groups": issues.grouped(), "error_count_by_code": issues.counts(),
        }
        ac.save_json(trace_path, report)
        print(json.dumps({"passed": False, "mode": mode, "errors": len(issues.errors)}))
        return 2

    state = _load_object(root / "agent_loop_state.json", "agent_loop_state.json", issues)
    manifest = _load_object(root / "workspace_manifest.json", "workspace_manifest.json", issues)
    session_id = str(state.get("session_id") or "")
    manifest_groups, member_groups = _manifest_groups(manifest, issues)
    inventory_path = root / "design_inventory.json"
    inventory = _load_object(inventory_path, "design_inventory.json", issues)
    inventory_groups = validate_inventory(
        inventory, session_id, design_root, manifest_groups, issues,
    )

    claim_index: dict[str, dict[str, Any]] = {}
    coverage_groups: dict[str, dict[str, Any]] = {}
    claims_path = root / "design_claims.jsonl"
    coverage_path = root / "design_coverage.json"
    if mode in {"claims", "all"}:
        claim_index = validate_claims(
            claims_path, session_id, design_root, member_groups, issues,
        )
        coverage = _load_object(coverage_path, "design_coverage.json", issues)
        coverage_groups = validate_coverage(
            coverage, session_id, inventory_groups, claim_index, member_groups, issues,
        )

    report = {
        "validated_at": ac.now_iso(),
        "session_id": session_id,
        "mode": mode,
        "passed": not issues.errors,
        "input_digests": {
            "design_inventory.json": _safe_digest(inventory_path),
            "design_claims.jsonl": _safe_digest(claims_path) if mode in {"claims", "all"} else "",
            "design_coverage.json": _safe_digest(coverage_path) if mode in {"claims", "all"} else "",
            "workspace_manifest.json": _safe_digest(root / "workspace_manifest.json"),
        },
        "metrics": {
            "claims": len(claim_index),
            "manifest_document_groups": len(manifest_groups),
            "inventory_document_groups": len(inventory_groups),
            "coverage_document_groups": len(coverage_groups),
        },
        "errors": issues.errors,
        "error_groups": issues.grouped(),
        "error_count_by_code": issues.counts(),
    }
    ac.save_json(trace_path, report)
    print(json.dumps({
        "passed": not issues.errors,
        "mode": mode,
        "inventory_groups": len(inventory_groups),
        "claims": len(claim_index),
        "errors": len(issues.errors),
        "error_count_by_code": issues.counts(),
    }))
    return 0 if not issues.errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate incremental design inventory and claims.")
    ac.add_common_arguments(parser)
    parser.add_argument("--mode", choices=sorted(MODES), default="all")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
