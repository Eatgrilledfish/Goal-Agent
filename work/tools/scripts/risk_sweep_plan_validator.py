#!/usr/bin/env python3
"""Validate requirement-centric, bidirectional scout slices."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

import agent_common as ac


PLAN_NAME = "risk_sweep_plan.json"
ARCHITECTURE_NAME = "architecture_map.json"
INVENTORY_NAME = "design_inventory.json"
CONTRACT_NAME = "agent_loop_contract.json"
SCOUT_RECEIPTS_NAME = "scout_receipts.jsonl"
SAFE_SWEEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_OBSERVATIONS_PER_SWEEP = 12
MAX_DESIGN_SLICE_LINES = 1200
MAX_DOCUMENTS_PER_DESIGN_SCOUT = 1
MAX_CODE_SLICE_FILES = 1200
MAX_REQUIREMENT_SOURCE_LINES = 80
SCOUT_DIRECTIONS = {"design_to_code", "code_to_design"}


def plan_input_digests(state_root: Path) -> tuple[dict[str, str], str]:
    paths = [
        state_root / "workspace_manifest.json",
        state_root / ARCHITECTURE_NAME,
        state_root / INVENTORY_NAME,
        state_root / PLAN_NAME,
        state_root / CONTRACT_NAME,
    ]
    values = {
        path.name: ac.sha256_file(path) if path.is_file() else ""
        for path in paths
    }
    combined = hashlib.sha256(
        json.dumps(values, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return values, combined


def _strings(value: Any, label: str, *, allow_empty: bool = True) -> tuple[list[str], list[str]]:
    if not isinstance(value, list):
        return [], [f"{label} must be an array"]
    values: list[str] = []
    errors: list[str] = []
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{label}[{index}] must be a non-empty string")
        else:
            values.append(entry)
    if not allow_empty and not values:
        errors.append(f"{label} must contain at least one value")
    if len(set(values)) != len(values):
        errors.append(f"{label} must not contain duplicates")
    return values, errors


def _indexes(architecture: dict[str, Any]) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    boundaries = {
        str(item.get("boundary_id")): item
        for item in architecture.get("integration_boundaries", [])
        if isinstance(item, dict) and item.get("boundary_id")
    }
    planes = {
        str(item.get("plane_id")): item
        for item in architecture.get("implementation_planes", [])
        if isinstance(item, dict) and item.get("plane_id")
    }
    paths = {
        str(item.get("path_id")): item
        for item in architecture.get("parallel_behavior_paths", [])
        if isinstance(item, dict) and item.get("path_id")
    }
    return boundaries, planes, paths


def _design_sections(
    inventory: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]], dict[str, str], dict[str, dict[str, Any]],
    set[str], set[str], dict[str, str], dict[str, list[str]],
]:
    sections: dict[str, dict[str, Any]] = {}
    section_documents: dict[str, str] = {}
    documents: dict[str, dict[str, Any]] = {}
    required_documents: set[str] = set()
    required_sections: set[str] = set()
    document_relations: dict[str, str] = {}
    ordered_sections: dict[str, list[str]] = {}
    for group in inventory.get("document_groups", []):
        if not isinstance(group, dict):
            continue
        document_key = group.get("document_key")
        if not isinstance(document_key, str) or not document_key:
            continue
        documents[document_key] = group
        relation = group.get("scope_relation")
        document_relations[document_key] = str(relation or "")
        if relation in {"required", "in_scope"}:
            required_documents.add(document_key)
        ordered_sections[document_key] = []
        for section in group.get("sections", []):
            if not isinstance(section, dict):
                continue
            section_id = section.get("section_id")
            if not isinstance(section_id, str) or not section_id:
                continue
            sections[section_id] = section
            section_documents[section_id] = document_key
            ordered_sections[document_key].append(section_id)
            if relation in {"required", "in_scope"}:
                required_sections.add(section_id)
    return (
        sections, section_documents, documents, required_documents,
        required_sections, document_relations, ordered_sections,
    )


def _section_range(section: dict[str, Any]) -> tuple[str, int, int]:
    source_ref = section.get("source_ref")
    source_ref = source_ref if isinstance(source_ref, dict) else section
    path = source_ref.get("path", section.get("path", ""))
    start = source_ref.get("line_start", section.get("line_start"))
    end = source_ref.get("line_end", section.get("line_end"))
    return (
        str(path or ""),
        start if isinstance(start, int) and not isinstance(start, bool) else 0,
        end if isinstance(end, int) and not isinstance(end, bool) else 0,
    )


def _required_coverage(
    boundaries: dict[str, dict], planes: dict[str, dict], paths: dict[str, dict],
) -> tuple[set[str], set[str], set[str]]:
    # The sweeps are the complete architecture breadth pass, not a high-risk
    # sample. High/medium/low remains useful for later frontier ordering, but
    # every mapped reachable scope must appear in at least one focused slice.
    required_planes = set(planes)
    required_boundaries = {
        boundary_id for boundary_id, item in boundaries.items()
        if set(item.get("plane_ids", [])).intersection(required_planes)
    }
    required_paths = {
        path_id for path_id, item in paths.items()
        if set(item.get("plane_ids", [])).intersection(required_planes)
    }
    return required_boundaries, required_planes, required_paths


def _components(
    required_boundaries: set[str], required_planes: set[str], required_paths: set[str],
    boundaries: dict[str, dict], planes: dict[str, dict], paths: dict[str, dict],
) -> list[set[str]]:
    nodes = {
        *(f"boundary:{identifier}" for identifier in required_boundaries),
        *(f"plane:{identifier}" for identifier in required_planes),
        *(f"path:{identifier}" for identifier in required_paths),
    }
    adjacency = {node: set() for node in nodes}

    def connect(left: str, right: str) -> None:
        if left in adjacency and right in adjacency:
            adjacency[left].add(right)
            adjacency[right].add(left)

    for boundary_id in required_boundaries:
        for plane_id in boundaries[boundary_id].get("plane_ids", []):
            connect(f"boundary:{boundary_id}", f"plane:{plane_id}")
    for path_id in required_paths:
        for plane_id in paths[path_id].get("plane_ids", []):
            connect(f"path:{path_id}", f"plane:{plane_id}")

    scoped_nodes: list[tuple[str, list[str]]] = [
        *(
            (f"boundary:{boundary_id}", boundaries[boundary_id].get("paths", []))
            for boundary_id in required_boundaries
        ),
        *(
            (f"plane:{plane_id}", planes[plane_id].get("paths", []))
            for plane_id in required_planes
        ),
    ]
    for index, (left_node, left_paths) in enumerate(scoped_nodes):
        for right_node, right_paths in scoped_nodes[index + 1:]:
            if any(
                _in_scope(left, [right]) or _in_scope(right, [left])
                for left in left_paths for right in right_paths
            ):
                connect(left_node, right_node)

    components: list[set[str]] = []
    unseen = set(nodes)
    while unseen:
        seed = min(unseen)
        stack = [seed]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            unseen.discard(node)
            stack.extend(sorted(adjacency[node] - component))
        components.append(component)
    return components


def validate_plan(
    plan: Any,
    *,
    session_id: str,
    architecture: dict[str, Any],
    architecture_digest: str,
    contract: dict[str, Any],
    inventory: dict[str, Any] | None = None,
    inventory_digest: str = "",
    review_code_root: Path | None = None,
    check_code_file_counts: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return [f"{PLAN_NAME} must be an object"], {}
    if plan.get("session_id") != session_id:
        errors.append(f"{PLAN_NAME}: session_id does not match current session")
    if not isinstance(plan.get("plan_id"), str) or not plan.get("plan_id", "").strip():
        errors.append(f"{PLAN_NAME}: plan_id must be a non-empty string")
    if plan.get("architecture_map_sha256") != architecture_digest:
        errors.append(f"{PLAN_NAME}: architecture_map_sha256 is stale")
    inventory = inventory or {}
    if plan.get("design_inventory_sha256") != inventory_digest:
        errors.append(f"{PLAN_NAME}: design_inventory_sha256 is stale")

    boundaries, planes, parallel_paths = _indexes(architecture)
    (
        design_sections, section_documents, design_documents,
        required_document_keys, required_section_ids, document_relations,
        ordered_sections,
    ) = _design_sections(inventory)
    required_boundaries, required_planes, required_paths = _required_coverage(
        boundaries, planes, parallel_paths,
    )
    required_object = plan.get("required_coverage")
    if not isinstance(required_object, dict):
        errors.append(f"{PLAN_NAME}: required_coverage must be an object")
        required_object = {}
    for field, expected in (
        ("boundary_ids", required_boundaries),
        ("plane_ids", required_planes),
        ("parallel_path_ids", required_paths),
    ):
        values, value_errors = _strings(
            required_object.get(field), f"{PLAN_NAME}.required_coverage.{field}",
        )
        errors.extend(value_errors)
        if set(values) != expected:
            errors.append(
                f"{PLAN_NAME}.required_coverage.{field} must equal architecture-derived IDs; "
                f"missing={sorted(expected - set(values))}, extra={sorted(set(values) - expected)}"
            )
    components = _components(
        required_boundaries, required_planes, required_paths,
        boundaries, planes, parallel_paths,
    )
    if not components:
        errors.append(f"{PLAN_NAME}: architecture has no required risk scope")

    raw_slices = plan.get("slices")
    if not isinstance(raw_slices, list) or not raw_slices:
        errors.append(f"{PLAN_NAME}: slices must contain at least one object")
        raw_slices = []
    known_lenses = set(contract.get("coverage_contract", {}).get("portfolio_lenses", []))
    slices: dict[str, dict[str, Any]] = {}
    coverage_by_kind: dict[str, set[str]] = {
        "boundary": set(), "plane": set(), "path": set(),
    }
    covered_document_keys: set[str] = set()
    covered_section_ids: set[str] = set()
    section_owners: dict[str, str] = {}
    anchor_owners: dict[str, str] = {}
    allowed_keys = {
        "direction", "document_keys", "section_ids",
        "sweep_id", "architecture_boundaries", "implementation_planes",
        "parallel_path_ids", "anchor_paths", "review_lenses", "scope_rationale",
    }
    for index, item in enumerate(raw_slices, start=1):
        label = f"{PLAN_NAME} slices[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        unexpected = sorted(set(item) - allowed_keys)
        if unexpected:
            errors.append(f"{label}: unsupported fields {unexpected}")
        sweep_id = item.get("sweep_id")
        if not isinstance(sweep_id, str) or not sweep_id.strip():
            errors.append(f"{label}: sweep_id must be a non-empty string")
            continue
        if SAFE_SWEEP_ID.fullmatch(sweep_id) is None:
            errors.append(
                f"{label}: sweep_id must be a safe single filename component"
            )
            continue
        if sweep_id in slices:
            errors.append(f"{label}: duplicate sweep_id {sweep_id}")
            continue
        slices[sweep_id] = item
        direction = item.get("direction")
        if direction not in SCOUT_DIRECTIONS:
            errors.append(
                f"{label}: direction must be one of {sorted(SCOUT_DIRECTIONS)}"
            )
        document_keys, document_errors = _strings(
            item.get("document_keys"), f"{label}.document_keys",
            allow_empty=direction == "code_to_design",
        )
        errors.extend(document_errors)
        unknown_documents = set(document_keys) - set(design_documents)
        if unknown_documents:
            errors.append(
                f"{label}.document_keys: unknown document groups "
                f"{sorted(unknown_documents)}"
            )
        if direction == "code_to_design" and document_keys:
            errors.append(
                f"{label}.document_keys must be empty for code_to_design; "
                "the scout retrieves relevant sections from the complete inventory"
            )
        if direction == "design_to_code":
            if len(document_keys) > MAX_DOCUMENTS_PER_DESIGN_SCOUT:
                errors.append(
                    f"{label}.document_keys may contain at most "
                    f"{MAX_DOCUMENTS_PER_DESIGN_SCOUT} documents for design_to_code"
                )
        section_ids, section_errors = _strings(
            item.get("section_ids"), f"{label}.section_ids",
            allow_empty=direction == "code_to_design",
        )
        errors.extend(section_errors)
        unknown_sections = set(section_ids) - set(design_sections)
        if unknown_sections:
            errors.append(
                f"{label}.section_ids: unknown design sections "
                f"{sorted(unknown_sections)}"
            )
        if direction == "code_to_design" and section_ids:
            errors.append(f"{label}.section_ids must be empty for code_to_design")
        if direction == "design_to_code":
            escaped = {
                section_documents.get(section_id, "") for section_id in section_ids
                if section_id in section_documents
            } - set(document_keys)
            if escaped:
                errors.append(
                    f"{label}.section_ids escape assigned documents: {sorted(escaped)}"
                )
            for section_id in section_ids:
                prior_owner = section_owners.get(section_id)
                if prior_owner is not None and prior_owner != sweep_id:
                    errors.append(
                        f"{label}.section_ids: {section_id!r} is already owned by "
                        f"{prior_owner}"
                    )
                section_owners[section_id] = sweep_id
            known_assigned = [
                section_id for section_id in section_ids
                if section_documents.get(section_id) in set(document_keys)
            ]
            for assigned_document in document_keys:
                document_assigned = [
                    section_id for section_id in known_assigned
                    if section_documents.get(section_id) == assigned_document
                ]
                if not document_assigned:
                    errors.append(
                        f"{label}.document_keys includes {assigned_document!r} without "
                        "an assigned section"
                    )
                    continue
                document_order = ordered_sections.get(assigned_document, [])
                positions = [
                    document_order.index(section_id) for section_id in document_assigned
                    if section_id in document_order
                ]
                if positions and positions != list(range(positions[0], positions[-1] + 1)):
                    errors.append(
                        f"{label}.section_ids for {assigned_document!r} must be a "
                        "contiguous document-local range"
                    )
            assigned_lines = sum(
                max(1, end - start + 1)
                for section_id in known_assigned
                for _path, start, end in [_section_range(design_sections[section_id])]
            )
            if assigned_lines > MAX_DESIGN_SLICE_LINES:
                errors.append(
                    f"{label}.section_ids span {assigned_lines} lines; maximum is "
                    f"{MAX_DESIGN_SLICE_LINES}"
                )
            covered_section_ids.update(known_assigned)
            covered_document_keys.update(
                section_documents[section_id] for section_id in known_assigned
            )
        values_by_kind: dict[str, list[str]] = {}
        for field, kind, known in (
            ("architecture_boundaries", "boundary", set(boundaries)),
            ("implementation_planes", "plane", set(planes)),
            ("parallel_path_ids", "path", set(parallel_paths)),
        ):
            values, value_errors = _strings(
                item.get(field), f"{label}.{field}",
                allow_empty=True,
            )
            errors.extend(value_errors)
            values_by_kind[kind] = values
            unknown = set(values) - known
            if unknown:
                errors.append(f"{label}.{field}: unknown IDs {sorted(unknown)}")
            if direction == "code_to_design":
                coverage_by_kind[kind].update(values)
        if direction == "code_to_design":
            if not values_by_kind["plane"] and not values_by_kind["boundary"]:
                errors.append(
                    f"{label}: code_to_design requires an implementation plane or "
                    "a local architecture boundary"
                )
            for path_id in values_by_kind["path"]:
                linked_planes = set(
                    parallel_paths.get(path_id, {}).get("plane_ids", [])
                )
                if path_id in parallel_paths and not linked_planes.intersection(
                    values_by_kind["plane"]
                ):
                    errors.append(
                        f"{label}.parallel_path_ids: {path_id} has no linked "
                        "implementation plane in this slice"
                    )
        anchors, anchor_errors = _strings(
            item.get("anchor_paths"), f"{label}.anchor_paths",
            allow_empty=direction == "design_to_code",
        )
        errors.extend(anchor_errors)
        if direction == "design_to_code" and anchors:
            errors.append(
                f"{label}.anchor_paths must be empty for design_to_code; "
                "code search is repository-wide"
            )
        normalized_anchors: set[str] = set()
        for anchor in anchors:
            parsed = PurePosixPath(anchor)
            if parsed.is_absolute() or ".." in parsed.parts:
                errors.append(f"{label}.anchor_paths: path must be relative without traversal: {anchor}")
                continue
            normalized = str(parsed)
            if normalized == ".":
                errors.append(
                    f"{label}.anchor_paths: repository root is not a focused code scope"
                )
                continue
            normalized_anchors.add(normalized)
        scoped_paths_by_id = {
            **{
                f"plane:{plane_id}": [
                    str(PurePosixPath(path))
                    for path in planes.get(plane_id, {}).get("paths", [])
                ]
                for plane_id in values_by_kind["plane"]
            },
        }
        if direction == "code_to_design":
            for boundary_id in values_by_kind["boundary"]:
                linked_planes = set(
                    boundaries.get(boundary_id, {}).get("plane_ids", [])
                )
                boundary_paths = list(
                    boundaries.get(boundary_id, {}).get("paths", [])
                )
                has_linked_plane = bool(
                    linked_planes.intersection(values_by_kind["plane"])
                )
                has_local_boundary_path = any(
                    _in_scope(path, list(normalized_anchors))
                    or _in_scope(anchor, boundary_paths)
                    for path in boundary_paths for anchor in normalized_anchors
                )
                if boundary_id in boundaries and not (
                    has_linked_plane or has_local_boundary_path
                ):
                    errors.append(
                        f"{label}.architecture_boundaries: {boundary_id} has neither "
                        "a linked implementation plane nor a local boundary path in "
                        "this slice"
                    )
            for scoped_id, scoped_paths in scoped_paths_by_id.items():
                if not any(
                    _in_scope(path, list(normalized_anchors))
                    or _in_scope(anchor, scoped_paths)
                    for path in scoped_paths for anchor in normalized_anchors
                ):
                    errors.append(
                        f"{label}.anchor_paths: {scoped_id} has no local primary scope"
                    )
            architecture_paths = [
                *(
                    path for plane_id in values_by_kind["plane"]
                    for path in planes.get(plane_id, {}).get("paths", [])
                ),
                *(
                    path for boundary_id in values_by_kind["boundary"]
                    for path in boundaries.get(boundary_id, {}).get("paths", [])
                ),
            ]
            for anchor in normalized_anchors:
                if not any(
                    _in_scope(anchor, [path]) or _in_scope(path, [anchor])
                    for path in architecture_paths
                ):
                    errors.append(
                        f"{label}.anchor_paths: {anchor} is unrelated to assigned "
                        "architecture boundary or plane paths"
                    )
        if review_code_root is not None:
            for anchor in normalized_anchors:
                scope_error = _scope_path_error(review_code_root, anchor)
                if scope_error:
                    errors.append(f"{label}.anchor_paths: {scope_error}")
            if direction == "code_to_design" and check_code_file_counts:
                file_count = sum(
                    _scope_file_count(review_code_root, anchor)
                    for anchor in _minimal_anchor_scopes(normalized_anchors)
                )
                if file_count > MAX_CODE_SLICE_FILES:
                    errors.append(
                        f"{label}.anchor_paths contain {file_count} files; maximum is "
                        f"{MAX_CODE_SLICE_FILES}"
                    )
        if direction == "code_to_design":
            for anchor in normalized_anchors:
                for prior_anchor, prior_sweep in anchor_owners.items():
                    if prior_sweep != sweep_id and (
                        _in_scope(anchor, [prior_anchor])
                        or _in_scope(prior_anchor, [anchor])
                    ):
                        errors.append(
                            f"{label}.anchor_paths: {anchor} overlaps {prior_anchor} "
                            f"owned by sweep {prior_sweep}"
                        )
                anchor_owners[anchor] = sweep_id
        lenses, lens_errors = _strings(
            item.get("review_lenses"), f"{label}.review_lenses", allow_empty=False,
        )
        errors.extend(lens_errors)
        unknown_lenses = set(lenses) - known_lenses
        if unknown_lenses:
            errors.append(f"{label}.review_lenses: unknown values {sorted(unknown_lenses)}")
        if set(lenses) != known_lenses:
            errors.append(
                f"{label}.review_lenses must equal the complete contract portfolio; "
                f"missing={sorted(known_lenses - set(lenses))}, "
                f"extra={sorted(set(lenses) - known_lenses)}"
            )
        if not isinstance(item.get("scope_rationale"), str) or not item.get(
            "scope_rationale", "",
        ).strip():
            errors.append(f"{label}: scope_rationale must be a non-empty string")

    expected_by_kind = {
        "boundary": required_boundaries,
        "plane": required_planes,
        "path": required_paths,
    }
    for kind, expected in expected_by_kind.items():
        actual = coverage_by_kind[kind]
        if actual != expected:
            errors.append(
                f"{PLAN_NAME}: {kind} coverage must include all required IDs; "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
    missing_documents = required_document_keys - covered_document_keys
    if missing_documents:
        errors.append(
            f"{PLAN_NAME}: design_to_code document coverage is incomplete; "
            f"missing={sorted(missing_documents)}"
        )
    missing_sections = required_section_ids - covered_section_ids
    extra_sections = covered_section_ids - required_section_ids
    if missing_sections or extra_sections:
        errors.append(
            f"{PLAN_NAME}: design_to_code section coverage must be exact; "
            f"missing={sorted(missing_sections)}, extra={sorted(extra_sections)}"
        )
    return errors, {
        "slices": slices,
        "required_boundaries": required_boundaries,
        "required_planes": required_planes,
        "required_paths": required_paths,
        "components": components,
        "boundaries": boundaries,
        "planes": planes,
        "parallel_paths": parallel_paths,
        "design_sections": design_sections,
        "section_documents": section_documents,
        "design_documents": design_documents,
        "required_document_keys": required_document_keys,
        "covered_document_keys": covered_document_keys,
        "required_section_ids": required_section_ids,
        "covered_section_ids": covered_section_ids,
        "document_relations": document_relations,
        "ordered_sections": ordered_sections,
    }


def load_validated_plan(
    state_root: Path, *, check_code_file_counts: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        state = ac.load_json(state_root / "agent_loop_state.json")
        architecture = ac.load_json(state_root / ARCHITECTURE_NAME)
        inventory = ac.load_json(state_root / INVENTORY_NAME)
        contract = ac.load_json(state_root / CONTRACT_NAME)
        plan = ac.load_json(state_root / PLAN_NAME)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {}, [f"cannot load risk sweep plan inputs: {exc}"]
    session_id = str(state.get("session_id") or "") if isinstance(state, dict) else ""
    review_code_root: Path | None = None
    manifest_path = state_root / "workspace_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = ac.load_json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"cannot load workspace manifest for risk scopes: {exc}")
            manifest = {}
        review_value = (
            manifest.get("paths", {}).get("review_code_root")
            if isinstance(manifest, dict) else None
        )
        if not isinstance(review_value, str) or not review_value:
            errors.append("workspace manifest lacks review_code_root for risk scopes")
        else:
            review_code_root = Path(review_value)
    architecture_path = state_root / ARCHITECTURE_NAME
    inventory_path = state_root / INVENTORY_NAME
    digest = ac.sha256_file(architecture_path) if architecture_path.is_file() else ""
    inventory_digest = ac.sha256_file(inventory_path) if inventory_path.is_file() else ""
    plan_errors, index = validate_plan(
        plan, session_id=session_id, architecture=architecture,
        architecture_digest=digest, contract=contract,
        inventory=inventory, inventory_digest=inventory_digest,
        review_code_root=review_code_root,
        check_code_file_counts=check_code_file_counts,
    )
    errors.extend(plan_errors)
    return plan if isinstance(plan, dict) else {}, index, errors


def expected_sweep_ids(state_root: Path) -> set[str]:
    _plan, index, errors = load_validated_plan(state_root)
    if errors:
        raise ValueError("; ".join(errors))
    return set(index.get("slices", {}))


def _in_scope(relative: str, scopes: list[str]) -> bool:
    try:
        path = PurePosixPath(relative)
    except TypeError:
        return False
    if path.is_absolute() or ".." in path.parts:
        return False
    value = str(path)
    for raw_scope in scopes:
        scope = str(PurePosixPath(raw_scope)).rstrip("/")
        if scope == ".":
            return True
        if value == scope or value.startswith(scope + "/"):
            return True
    return False


def _scope_path_error(review_code_root: Path, relative: str) -> str:
    try:
        root = review_code_root.resolve(strict=True)
    except OSError as exc:
        return f"review code root is unavailable: {exc}"
    parsed = PurePosixPath(relative)
    candidate = root
    for part in parsed.parts:
        if part == ".":
            continue
        candidate = candidate / part
        if candidate.is_symlink():
            return f"scope path must not traverse a symlink: {relative}"
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return f"scope path does not exist inside review_code_root: {relative}"
    if not resolved.is_file() and not resolved.is_dir():
        return f"scope path is not a file or directory: {relative}"
    return ""


def _scope_file_count(review_code_root: Path, relative: str) -> int:
    candidate = review_code_root / PurePosixPath(relative)
    if candidate.is_file() and not candidate.is_symlink():
        return 1
    if not candidate.is_dir() or candidate.is_symlink():
        return 0
    return sum(
        1 for item in candidate.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def _minimal_anchor_scopes(anchors: set[str]) -> list[str]:
    """Drop redundant nested anchors before computing one slice's file union."""
    return sorted(
        anchor for anchor in anchors
        if not any(
            anchor != other and PurePosixPath(anchor).is_relative_to(
                PurePosixPath(other),
            )
            for other in anchors
        )
    )


def validate_observation_against_plan(
    item: dict[str, Any], state_root: Path, label: str,
) -> list[str]:
    _plan, index, errors = load_validated_plan(state_root)
    if errors:
        return [f"{label}: {error}" for error in errors]
    plan_path = state_root / PLAN_NAME
    plan_digest = ac.sha256_file(plan_path)
    if item.get("risk_sweep_plan_sha256") != plan_digest:
        errors.append(f"{label}: risk_sweep_plan_sha256 is stale")
    sweep_id = str(item.get("sweep_id") or "")
    sweep = index["slices"].get(sweep_id)
    if sweep is None:
        errors.append(f"{label}: unknown sweep_id {sweep_id!r}")
        return errors
    direction = sweep.get("direction")
    if item.get("direction") not in {None, direction}:
        errors.append(
            f"{label}: direction does not match assigned sweep {direction!r}"
        )
    # Architecture IDs are retrieval metadata, not candidate truth.  A newly
    # discovered implementation path must not be rejected because the initial
    # architecture map was incomplete or later refined.
    observation_lenses = item.get("review_lenses")
    if isinstance(observation_lenses, list):
        outside_lenses = set(observation_lenses) - set(sweep.get("review_lenses", []))
        if outside_lenses:
            errors.append(
                f"{label}: review_lenses escape assigned sweep: {sorted(outside_lenses)}"
            )
    observation_sections = item.get("design_section_ids")
    if isinstance(observation_sections, list):
        unknown_sections = set(observation_sections) - set(index["design_sections"])
        if unknown_sections:
            errors.append(
                f"{label}: design_section_ids contain unknown IDs: "
                f"{sorted(unknown_sections)}"
            )
        section_documents = {
            index["section_documents"].get(section_id, "")
            for section_id in observation_sections
            if section_id in index["section_documents"]
        }
        invalid_relations = {
            document_key: index["document_relations"].get(document_key, "")
            for document_key in section_documents
            if index["document_relations"].get(document_key) not in {
                "required", "in_scope",
            }
        }
        if invalid_relations:
            errors.append(
                f"{label}: candidate source is not required/in_scope: "
                f"{invalid_relations}"
            )
        if direction == "design_to_code":
            outside_sections = set(observation_sections) - set(
                sweep.get("section_ids", [])
            )
            if outside_sections:
                errors.append(
                    f"{label}: design_section_ids escape assigned section range: "
                    f"{sorted(outside_sections)}"
                )
        requirement = item.get("design_requirement")
        source_ref = requirement.get("source_ref") if isinstance(
            requirement, dict,
        ) else None
        if not isinstance(source_ref, dict):
            errors.append(f"{label}: design_requirement.source_ref is missing")
        else:
            source_path = source_ref.get("path")
            line_start = source_ref.get("line_start")
            line_end = source_ref.get("line_end")
            valid_range = (
                isinstance(source_path, str) and bool(source_path)
                and isinstance(line_start, int) and not isinstance(line_start, bool)
                and isinstance(line_end, int) and not isinstance(line_end, bool)
                and line_start >= 1 and line_end >= line_start
            )
            if not valid_range:
                errors.append(f"{label}: design_requirement.source_ref range is invalid")
            else:
                if line_end - line_start + 1 > MAX_REQUIREMENT_SOURCE_LINES:
                    errors.append(
                        f"{label}: design requirement source_ref exceeds "
                        f"{MAX_REQUIREMENT_SOURCE_LINES} lines"
                    )
                containing = any(
                    source_path == section_path
                    and section_start <= line_start <= line_end <= section_end
                    for section_id in observation_sections
                    if section_id in index["design_sections"]
                    for section_path, section_start, section_end in [
                        _section_range(index["design_sections"][section_id])
                    ]
                )
                if not containing:
                    errors.append(
                        f"{label}: design requirement source_ref is outside cited "
                        "design_section_ids"
                    )
    if direction == "code_to_design":
        owned_paths = list(sweep.get("anchor_paths", []))
        for evidence_index, evidence in enumerate(item.get("code_evidence", []), start=1):
            if not isinstance(evidence, dict):
                continue
            file_value = evidence.get("file")
            if not isinstance(file_value, str) or not _in_scope(file_value, owned_paths):
                errors.append(
                    f"{label}: code_evidence[{evidence_index}] is outside assigned primary paths"
                )
    return errors


def validate_sweep_coverage(
    items: list[dict[str, Any]], state_root: Path, sweep_id: str,
) -> list[str]:
    """Validate one isolated sweep without forcing synthetic observations.

    The plan owns scope assignment.  Observations are sparse, high-information
    semantic leads, so a reviewed boundary/plane that yields no concrete lead
    must not be padded into the handoff merely to satisfy an ID checklist.
    Individual observations still have to stay inside the assigned sweep and
    carry local evidence for every ID they claim.
    """
    _plan, index, errors = load_validated_plan(state_root)
    if errors:
        return errors
    sweep = index.get("slices", {}).get(sweep_id)
    if not isinstance(sweep, dict):
        return [f"risk sweep {sweep_id}: unknown sweep_id"]
    foreign = sorted({
        str(item.get("sweep_id") or "") for item in items
        if item.get("sweep_id") != sweep_id
    })
    if foreign:
        errors.append(f"risk sweep {sweep_id}: contains foreign sweeps {foreign}")
    return errors


def _completed_scout_receipts(
    state_root: Path, *, session_id: str, plan_digest: str,
    slices: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Load completion receipts independently of whether a scout found candidates."""
    values, errors = ac.load_jsonl(state_root / SCOUT_RECEIPTS_NAME)
    completed: dict[str, dict[str, Any]] = {}
    for index, receipt in enumerate(values, start=1):
        label = f"{SCOUT_RECEIPTS_NAME}:{index}"
        sweep_id = receipt.get("sweep_id")
        if not isinstance(sweep_id, str) or not sweep_id:
            errors.append(f"{label}: sweep_id must be a non-empty string")
            continue
        sweep = slices.get(sweep_id)
        if sweep is None:
            errors.append(f"{label}: unknown sweep_id {sweep_id!r}")
            continue
        if receipt.get("session_id") != session_id:
            errors.append(f"{label}: session_id does not match current session")
            continue
        if receipt.get("risk_sweep_plan_sha256") != plan_digest:
            errors.append(f"{label}: risk_sweep_plan_sha256 is stale")
            continue
        if receipt.get("direction") != sweep.get("direction"):
            errors.append(f"{label}: direction does not match current plan slice")
            continue
        if receipt.get("status") != "complete":
            continue
        candidate_count = receipt.get("candidate_count")
        if candidate_count is not None and (
            not isinstance(candidate_count, int)
            or isinstance(candidate_count, bool)
            or candidate_count < 0
        ):
            errors.append(f"{label}: candidate_count must be a non-negative integer")
            continue
        expected_sections = list(sweep.get("section_ids", []))
        expected_anchors = list(sweep.get("anchor_paths", []))
        if receipt.get("assigned_section_ids") != expected_sections:
            errors.append(f"{label}: assigned_section_ids do not match current plan slice")
            continue
        if receipt.get("reviewed_section_ids") != expected_sections:
            errors.append(f"{label}: reviewed_section_ids do not close assigned scope")
            continue
        if receipt.get("assigned_anchor_paths") != expected_anchors:
            errors.append(f"{label}: assigned_anchor_paths do not match current plan slice")
            continue
        if receipt.get("reviewed_anchor_paths") != expected_anchors:
            errors.append(f"{label}: reviewed_anchor_paths do not close assigned scope")
            continue
        coverage_digest = receipt.get("coverage_report_sha256")
        if (
            not isinstance(coverage_digest, str) or len(coverage_digest) != 64
            or any(character not in "0123456789abcdef" for character in coverage_digest)
        ):
            errors.append(f"{label}: coverage_report_sha256 must be a SHA-256 digest")
            continue
        review_packet_digest = receipt.get("negative_review_packet_sha256")
        review_digest = receipt.get("negative_review_sha256")
        if any(
            not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in (review_packet_digest, review_digest)
        ):
            errors.append(f"{label}: negative review digests must be SHA-256 values")
            continue
        scout_provider = receipt.get("scout_provider_session_id")
        reviewer_providers = receipt.get("reviewer_provider_session_ids")
        if (
            not isinstance(scout_provider, str) or not scout_provider
            or not isinstance(reviewer_providers, list)
            or any(not isinstance(value, str) or not value for value in reviewer_providers)
            or len(reviewer_providers) != len(set(reviewer_providers))
            or scout_provider in reviewer_providers
        ):
            errors.append(f"{label}: negative review requires distinct provider sessions")
            continue
        completed[sweep_id] = receipt
    return completed, errors


def validate_risk_coverage(
    risks: dict[str, dict[str, Any]], state_root: Path,
) -> tuple[list[str], dict[str, Any]]:
    _plan, index, errors = load_validated_plan(state_root)
    if errors:
        return errors, {}
    for observation_id, item in risks.items():
        errors.extend(validate_observation_against_plan(
            item, state_root, f"risk ({observation_id})",
        ))
    observed_sweeps = {
        str(item.get("sweep_id") or "") for item in risks.values()
        if item.get("sweep_id")
    }
    observed_boundaries = {
        str(value) for item in risks.values()
        for value in item.get("architecture_boundaries", []) if value
    }
    observed_planes = {
        str(value) for item in risks.values()
        for value in item.get("implementation_planes", []) if value
    }
    observed_paths = {
        str(value) for item in risks.values()
        for value in item.get("parallel_path_ids", []) if value
    }
    expected_sweeps = set(index["slices"])
    state = ac.load_json(state_root / "agent_loop_state.json")
    plan_digest = ac.sha256_file(state_root / PLAN_NAME)
    completed_receipts, receipt_errors = _completed_scout_receipts(
        state_root,
        session_id=str(state.get("session_id") or ""),
        plan_digest=plan_digest,
        slices=index["slices"],
    )
    errors.extend(receipt_errors)
    completed_sweeps = set(completed_receipts)
    missing_sweeps = expected_sweeps - completed_sweeps
    if missing_sweeps:
        errors.append(
            f"scout receipts do not include completed sweeps: {sorted(missing_sweeps)}"
        )
    unexpected_observation_sweeps = observed_sweeps - completed_sweeps
    if unexpected_observation_sweeps:
        errors.append(
            "risk observations belong to scouts without completion receipts: "
            f"{sorted(unexpected_observation_sweeps)}"
        )
    for sweep_id, receipt in completed_receipts.items():
        candidate_ids = receipt.get("candidate_ids")
        if not isinstance(candidate_ids, list) or any(
            not isinstance(value, str) or not value for value in candidate_ids
        ) or len(set(candidate_ids)) != len(candidate_ids):
            errors.append(
                f"scout receipt {sweep_id}: candidate_ids must be unique strings"
            )
            continue
        if receipt.get("candidate_count") != len(candidate_ids):
            errors.append(
                f"scout receipt {sweep_id}: candidate_count does not match candidate_ids"
            )
        observed_ids = {
            observation_id for observation_id, item in risks.items()
            if item.get("sweep_id") == sweep_id
        }
        if set(candidate_ids) != observed_ids:
            errors.append(
                f"scout receipt {sweep_id}: merged candidates do not match handoff; "
                f"missing={sorted(set(candidate_ids) - observed_ids)}, "
                f"extra={sorted(observed_ids - set(candidate_ids))}"
            )
    return errors, {
        "expected_sweeps": sorted(expected_sweeps),
        "completed_sweeps": sorted(completed_sweeps),
        "missing_sweeps": sorted(missing_sweeps),
        "closed": not missing_sweeps and not receipt_errors,
        "observed_sweeps": sorted(observed_sweeps),
        "required_boundaries": sorted(index["required_boundaries"]),
        "required_planes": sorted(index["required_planes"]),
        "required_paths": sorted(index["required_paths"]),
        "observed_boundaries": sorted(observed_boundaries),
        "observed_planes": sorted(observed_planes),
        "observed_paths": sorted(observed_paths),
        "unobserved_boundaries": sorted(
            index["required_boundaries"] - observed_boundaries
        ),
        "unobserved_planes": sorted(index["required_planes"] - observed_planes),
        "unobserved_paths": sorted(index["required_paths"] - observed_paths),
    }


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    trace_path = log_root / "trace" / "risk_sweep_plan_validation.json"
    errors = ac.session_path_errors(
        root, code_root=code_root, design_root=design_root,
        result_root=result_root, log_root=log_root,
    )
    plan, index, plan_errors = load_validated_plan(
        root, check_code_file_counts=True,
    )
    errors.extend(plan_errors)
    input_digests, combined = plan_input_digests(root)
    state = ac.load_json(root / "agent_loop_state.json")
    trace = {
        "validated_at": ac.now_iso(),
        "session_id": state.get("session_id", ""),
        "passed": not errors,
        "input_digests": input_digests,
        "combined_input_sha256": combined,
        "plan_id": plan.get("plan_id", "") if isinstance(plan, dict) else "",
        "validated_sweep_ids": sorted(index.get("slices", {})),
        "metrics": {
            "slices": len(index.get("slices", {})),
            "components": len(index.get("components", [])),
            "required_boundaries": len(index.get("required_boundaries", [])),
            "required_planes": len(index.get("required_planes", [])),
            "required_paths": len(index.get("required_paths", [])),
            "required_document_groups": len(index.get("required_document_keys", [])),
            "design_to_code_slices": sum(
                1 for item in index.get("slices", {}).values()
                if item.get("direction") == "design_to_code"
            ),
            "code_to_design_slices": sum(
                1 for item in index.get("slices", {}).values()
                if item.get("direction") == "code_to_design"
            ),
        },
        "errors": errors,
    }
    ac.save_json(trace_path, trace)
    print(json.dumps({
        "passed": not errors,
        "validated_sweep_ids": trace["validated_sweep_ids"],
        "errors": errors,
        "trace": str(trace_path),
    }, ensure_ascii=False))
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate non-overlapping architecture risk sweep slices."
    )
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
