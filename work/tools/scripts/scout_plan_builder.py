#!/usr/bin/env python3
"""Build a complete bidirectional semantic-scout plan without model routing.

Design sections are assigned in balanced, bounded slices.  A scout may own a
small number of documents, but every document-local range remains contiguous
and every required section has exactly one owner.
Code scopes are partitioned by real file count, so a large imported tree never
masquerades as one reviewable anchor.  The helper does not rank requirements
or infer inconsistencies.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any

import agent_common as ac
import risk_sweep_plan_validator as validator


MAX_DESIGN_SLICE_LINES = 3500
MAX_DOCUMENTS_PER_DESIGN_SCOUT = 2
MAX_CODE_SLICE_FILES = 1200


def _indexes(values: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {
        str(item[key]): item for item in values
        if isinstance(item, dict) and isinstance(item.get(key), str) and item.get(key)
    }


def _path_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return left_path == right_path or left_path.is_relative_to(
        right_path,
    ) or right_path.is_relative_to(left_path)


def _partition_code_scope(
    relative: str, code_root: Path, counts: dict[str, int],
) -> list[tuple[str, int]]:
    """Split one repository scope until every leaf has bounded file cardinality."""
    path = code_root / PurePosixPath(relative)
    if path.is_file():
        return [(relative, 1)]
    if not path.is_dir():
        return []

    def file_count(candidate: Path) -> int:
        key = candidate.relative_to(code_root).as_posix()
        if key not in counts:
            counts[key] = sum(
                1 for item in candidate.rglob("*")
                if item.is_file() and not item.is_symlink()
            )
        return counts[key]

    count = file_count(path)
    if count <= MAX_CODE_SLICE_FILES:
        return [(relative, max(1, count))]
    partitions: list[tuple[str, int]] = []
    for child in sorted(path.iterdir(), key=lambda item: item.name):
        if child.is_symlink() or child.name in ac.DEFAULT_IGNORED_DIRS:
            continue
        child_relative = child.relative_to(code_root).as_posix()
        if child.is_file():
            partitions.append((child_relative, 1))
        elif child.is_dir():
            partitions.extend(_partition_code_scope(
                child_relative, code_root, counts,
            ))
    return partitions or [(relative, max(1, count))]


def _code_components(
    planes: dict[str, dict[str, Any]], boundaries: dict[str, dict[str, Any]],
    code_root: Path,
) -> list[tuple[list[str], list[str]]]:
    """Build disjoint, file-count-bounded code scopes within each repository owner."""
    raw_scopes = sorted({
        str(PurePosixPath(raw_path))
        for item in [*planes.values(), *boundaries.values()]
        for raw_path in item.get("paths", [])
        if isinstance(raw_path, str) and raw_path
        and not PurePosixPath(raw_path).is_absolute()
        and ".." not in PurePosixPath(raw_path).parts
        and str(PurePosixPath(raw_path)) != "."
        and (code_root / PurePosixPath(raw_path)).exists()
    })
    # A parent scope owns all of its descendants.  Removing redundant child
    # entries is what makes the resulting anchors provably non-overlapping.
    root_scopes = [
        scope for scope in raw_scopes
        if not any(
            scope != other and PurePosixPath(scope).is_relative_to(
                PurePosixPath(other),
            )
            for other in raw_scopes
        )
    ]
    scopes_by_top_level: dict[str, list[str]] = {}
    for scope in root_scopes:
        scopes_by_top_level.setdefault(PurePosixPath(scope).parts[0], []).append(scope)
    counts: dict[str, int] = {}
    components: list[tuple[list[str], list[str]]] = []
    for top_level in sorted(scopes_by_top_level):
        atoms = [
            atom for scope in sorted(scopes_by_top_level[top_level])
            for atom in _partition_code_scope(scope, code_root, counts)
        ]
        bins: list[dict[str, Any]] = []
        for anchor, count in sorted(atoms, key=lambda item: (-item[1], item[0])):
            candidates = [
                index for index, bucket in enumerate(bins)
                if bucket["files"] + count <= MAX_CODE_SLICE_FILES
            ]
            if not candidates:
                bins.append({"files": 0, "anchors": []})
                candidates = [len(bins) - 1]
            owner = min(candidates, key=lambda index: (bins[index]["files"], index))
            bins[owner]["files"] += count
            bins[owner]["anchors"].append(anchor)
        for bucket in bins:
            anchors = sorted(bucket["anchors"])
            plane_ids = sorted(
                plane_id for plane_id, item in planes.items()
                if any(
                    _path_overlap(anchor, raw_path)
                    for anchor in anchors
                    for raw_path in item.get("paths", [])
                    if isinstance(raw_path, str) and raw_path
                )
            )
            if anchors:
                components.append((plane_ids, anchors))
    return components


def _section_span(section: dict[str, Any]) -> int:
    source_ref = section.get("source_ref")
    source_ref = source_ref if isinstance(source_ref, dict) else section
    start = source_ref.get("line_start")
    end = source_ref.get("line_end")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return 1
    return end - start + 1


def _design_section_chunks(
    inventory: dict[str, Any],
) -> list[tuple[str, list[str], int]]:
    """Return bounded contiguous chunks before deterministic load balancing."""
    result: list[tuple[str, list[str], int]] = []
    for group in inventory.get("document_groups", []):
        if not isinstance(group, dict) or group.get("scope_relation") not in {
            "required", "in_scope",
        }:
            continue
        key = group.get("document_key")
        if not isinstance(key, str) or not key:
            continue
        current: list[str] = []
        current_lines = 0
        for section in group.get("sections", []):
            if not isinstance(section, dict):
                continue
            section_id = section.get("section_id")
            if not isinstance(section_id, str) or not section_id:
                continue
            span = _section_span(section)
            if current and current_lines + span > MAX_DESIGN_SLICE_LINES:
                result.append((key, current, current_lines))
                current = []
                current_lines = 0
            current.append(section_id)
            current_lines += span
        if current:
            result.append((key, current, current_lines))
    return result


def _balanced_design_slices(
    inventory: dict[str, Any],
) -> list[tuple[list[str], list[str]]]:
    """Pack document chunks into a bounded number of balanced semantic scouts.

    This is scheduling only: it neither ranks documents nor interprets their
    contents.  Large documents are split into separate contiguous chunks; a
    bin never receives two chunks from the same document, which keeps each
    document-local ownership range contiguous by construction.
    """
    chunks = _design_section_chunks(inventory)
    if not chunks:
        return []
    total_lines = sum(item[2] for item in chunks)
    # Use the smallest starting frontier that can satisfy both hard attention
    # bounds.  This scales with the current input instead of imposing a
    # project-specific task count.
    bin_count = min(len(chunks), max(
        1,
        math.ceil(total_lines / MAX_DESIGN_SLICE_LINES),
        math.ceil(len(chunks) / MAX_DOCUMENTS_PER_DESIGN_SCOUT),
    ))
    bins: list[dict[str, Any]] = [
        {"lines": 0, "documents": set(), "chunks": []}
        for _index in range(bin_count)
    ]
    ordered_chunks = sorted(
        enumerate(chunks), key=lambda item: (-item[1][2], item[1][0], item[0]),
    )
    for ordinal, (document_key, section_ids, line_count) in ordered_chunks:
        candidates = [
            index for index, bucket in enumerate(bins)
            if document_key not in bucket["documents"]
            and len(bucket["documents"]) < MAX_DOCUMENTS_PER_DESIGN_SCOUT
            and bucket["lines"] + line_count <= MAX_DESIGN_SLICE_LINES
        ]
        if not candidates:
            bins.append({"lines": 0, "documents": set(), "chunks": []})
            candidates = [len(bins) - 1]
        owner = min(candidates, key=lambda index: (bins[index]["lines"], index))
        bins[owner]["lines"] += line_count
        bins[owner]["documents"].add(document_key)
        bins[owner]["chunks"].append((ordinal, document_key, section_ids))

    result: list[tuple[list[str], list[str]]] = []
    for bucket in bins:
        if not bucket["chunks"]:
            continue
        ordered = sorted(bucket["chunks"], key=lambda item: item[0])
        document_keys = list(dict.fromkeys(item[1] for item in ordered))
        section_ids = [
            section_id for _ordinal, _document_key, values in ordered
            for section_id in values
        ]
        result.append((document_keys, section_ids))
    return result


def build(state_root: Path) -> dict[str, Any]:
    state = ac.load_json(state_root / "agent_loop_state.json")
    architecture = ac.load_json(state_root / "architecture_map.json")
    inventory = ac.load_json(state_root / "design_inventory.json")
    contract = ac.load_json(state_root / "agent_loop_contract.json")
    manifest = ac.load_json(state_root / "workspace_manifest.json")
    code_root = Path(manifest["paths"]["review_code_root"])
    lenses = list(contract.get("coverage_contract", {}).get("portfolio_lenses", []))
    planes = _indexes(architecture.get("implementation_planes", []), "plane_id")
    all_boundaries = _indexes(
        architecture.get("integration_boundaries", []), "boundary_id",
    )
    boundaries = {
        identifier: item for identifier, item in all_boundaries.items()
        if set(item.get("plane_ids", [])).intersection(planes)
    }
    all_parallel = _indexes(
        architecture.get("parallel_behavior_paths", []), "path_id",
    )
    parallel = {
        identifier: item for identifier, item in all_parallel.items()
        if set(item.get("plane_ids", [])).intersection(planes)
    }

    slices: list[dict[str, Any]] = []
    for index, (document_keys, section_ids) in enumerate(
        _balanced_design_slices(inventory), start=1,
    ):
        slices.append({
            "sweep_id": f"SCOUT-DESIGN-{index:02d}",
            "direction": "design_to_code",
            "document_keys": document_keys,
            "section_ids": section_ids,
            "architecture_boundaries": [],
            "implementation_planes": [],
            "parallel_path_ids": [],
            "anchor_paths": [],
            "review_lenses": lenses,
            "scope_rationale": (
                "Own balanced bounded section ranges from at most two design documents "
                "and search the complete code repository for reachable behavior."
            ),
        })

    components = sorted(
        _code_components(planes, boundaries, code_root),
        key=lambda item: (item[1], item[0]),
    )
    boundary_owner: dict[str, int] = {}
    parallel_owner: dict[str, int] = {}
    boundary_load = [0 for _item in components]
    for identifier, item in boundaries.items():
        linked = set(item.get("plane_ids", []))
        local_candidates = [
            index for index, (_plane_ids, anchors) in enumerate(components)
            if any(
                _path_overlap(anchor, raw_path)
                for anchor in anchors for raw_path in item.get("paths", [])
                if isinstance(raw_path, str) and raw_path
            )
        ]
        linked_candidates = [
            index for index, (plane_ids, _roots) in enumerate(components)
            if linked.intersection(plane_ids)
        ]
        candidates = local_candidates or linked_candidates
        if not candidates:
            continue
        owner = min(candidates, key=lambda index: (boundary_load[index], index))
        boundary_owner[identifier] = owner
        boundary_load[owner] += 1
    for identifier, item in parallel.items():
        linked = set(item.get("plane_ids", []))
        candidates = [
            index for index, (plane_ids, _roots) in enumerate(components)
            if linked.intersection(plane_ids)
        ]
        if candidates:
            parallel_owner[identifier] = min(
                candidates, key=lambda index: (boundary_load[index], index),
            )
    for index, (plane_ids, roots) in enumerate(components, start=1):
        slices.append({
            "sweep_id": f"SCOUT-CODE-{index:02d}",
            "direction": "code_to_design",
            "document_keys": [],
            "section_ids": [],
            "architecture_boundaries": sorted(
                key for key, owner in boundary_owner.items() if owner == index - 1
            ),
            "implementation_planes": plane_ids,
            "parallel_path_ids": sorted(
                key for key, owner in parallel_owner.items() if owner == index - 1
            ),
            "anchor_paths": roots,
            "review_lenses": lenses,
            "scope_rationale": (
                "Own non-overlapping file-count-bounded code anchors and retrieve "
                "relevant design requirements from the complete inventory."
            ),
        })

    plan = {
        "session_id": state.get("session_id"),
        "plan_id": "SEMANTIC-SCOUT-PLAN-001",
        "architecture_map_sha256": ac.sha256_file(state_root / "architecture_map.json"),
        "design_inventory_sha256": ac.sha256_file(state_root / "design_inventory.json"),
        "required_coverage": {
            "boundary_ids": sorted(boundaries),
            "plane_ids": sorted(planes),
            "parallel_path_ids": sorted(parallel),
        },
        "slices": slices,
    }
    ac.save_json(state_root / "risk_sweep_plan.json", plan)
    _plan, _index, errors = validator.load_validated_plan(
        state_root, check_code_file_counts=True,
    )
    if errors:
        (state_root / "risk_sweep_plan.json").unlink(missing_ok=True)
        raise ValueError("generated semantic scout plan is invalid: " + "; ".join(errors))
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    args = parser.parse_args(argv)
    try:
        plan = build(Path(args.state_root).resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "passed": True,
        "sweep_ids": [item["sweep_id"] for item in plan["slices"]],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
