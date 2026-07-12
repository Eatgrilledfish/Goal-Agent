#!/usr/bin/env python3
"""Build a complete bidirectional semantic-scout plan without model routing.

Design groups are balanced across a small number of exclusive requirement
scouts.  Code planes are grouped by overlapping top-level ownership so primary
code anchors never overlap.  The helper does not rank requirements or infer
inconsistencies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any

import agent_common as ac
import risk_sweep_plan_validator as validator


MAX_DESIGN_SCOUTS = 4
TARGET_PLANES_PER_CODE_SCOUT = 6


def _indexes(values: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {
        str(item[key]): item for item in values
        if isinstance(item, dict) and isinstance(item.get(key), str) and item.get(key)
    }


def _top_levels(paths: list[Any], code_root: Path) -> set[str]:
    result: set[str] = set()
    for raw in paths:
        if not isinstance(raw, str) or not raw:
            continue
        parsed = PurePosixPath(raw)
        if parsed.is_absolute() or ".." in parsed.parts:
            continue
        parts = [part for part in parsed.parts if part not in {"", "."}]
        if parts:
            result.add(parts[0])
    if result:
        return result
    return {
        path.name for path in code_root.iterdir()
        if path.name not in ac.DEFAULT_IGNORED_DIRS and not path.is_symlink()
    }


def _plane_components(
    planes: dict[str, dict[str, Any]], code_root: Path,
) -> list[tuple[list[str], list[str]]]:
    roots = {
        plane_id: _top_levels(item.get("paths", []), code_root)
        for plane_id, item in planes.items()
    }
    remaining = set(planes)
    components: list[tuple[list[str], list[str]]] = []
    while remaining:
        members = {min(remaining)}
        owned_roots = set(roots[min(remaining)])
        changed = True
        while changed:
            changed = False
            for plane_id in sorted(remaining - members):
                if owned_roots.intersection(roots[plane_id]):
                    members.add(plane_id)
                    owned_roots.update(roots[plane_id])
                    changed = True
        remaining -= members
        components.append((sorted(members), sorted(owned_roots)))
    return components


def _pack_code_components(
    components: list[tuple[list[str], list[str]]],
) -> list[tuple[list[str], list[str]]]:
    packed: list[tuple[list[str], list[str]]] = []
    for members, roots in sorted(components, key=lambda item: (-len(item[0]), item[0])):
        placed = False
        for index, (current_members, current_roots) in enumerate(packed):
            if len(current_members) + len(members) <= TARGET_PLANES_PER_CODE_SCOUT:
                packed[index] = (
                    sorted([*current_members, *members]),
                    sorted([*current_roots, *roots]),
                )
                placed = True
                break
        if not placed:
            packed.append((list(members), list(roots)))
    return packed


def _balanced_design_groups(inventory: dict[str, Any]) -> list[list[str]]:
    weighted: list[tuple[int, str]] = []
    for group in inventory.get("document_groups", []):
        if not isinstance(group, dict) or group.get("scope_relation") not in {
            "required", "in_scope",
        }:
            continue
        key = group.get("document_key")
        if not isinstance(key, str) or not key:
            continue
        weight = sum(
            max(1, int(section.get("line_end", 0)) - int(section.get("line_start", 0)) + 1)
            for section in group.get("sections", []) if isinstance(section, dict)
        )
        weighted.append((weight, key))
    if not weighted:
        return []
    buckets: list[tuple[int, list[str]]] = [
        (0, []) for _ in range(min(MAX_DESIGN_SCOUTS, len(weighted)))
    ]
    for weight, key in sorted(weighted, key=lambda item: (-item[0], item[1])):
        index = min(range(len(buckets)), key=lambda value: (buckets[value][0], value))
        total, keys = buckets[index]
        buckets[index] = (total + weight, [*keys, key])
    return [sorted(keys) for _weight, keys in buckets]


def build(state_root: Path) -> dict[str, Any]:
    state = ac.load_json(state_root / "agent_loop_state.json")
    architecture = ac.load_json(state_root / "architecture_map.json")
    inventory = ac.load_json(state_root / "design_inventory.json")
    contract = ac.load_json(state_root / "agent_loop_contract.json")
    manifest = ac.load_json(state_root / "workspace_manifest.json")
    code_root = Path(manifest["paths"]["review_code_root"])
    lenses = list(contract.get("coverage_contract", {}).get("portfolio_lenses", []))
    planes = _indexes(architecture.get("implementation_planes", []), "plane_id")
    boundaries = _indexes(architecture.get("integration_boundaries", []), "boundary_id")
    parallel = _indexes(architecture.get("parallel_behavior_paths", []), "path_id")

    slices: list[dict[str, Any]] = []
    for index, document_keys in enumerate(_balanced_design_groups(inventory), start=1):
        slices.append({
            "sweep_id": f"SCOUT-DESIGN-{index:02d}",
            "direction": "design_to_code",
            "document_keys": document_keys,
            "architecture_boundaries": [],
            "implementation_planes": [],
            "parallel_path_ids": [],
            "anchor_paths": [],
            "review_lenses": lenses,
            "scope_rationale": (
                "Own these design groups exclusively and search the complete code repository "
                "for their reachable behavior."
            ),
        })

    packed = _pack_code_components(_plane_components(planes, code_root))
    boundary_owner: dict[str, int] = {}
    parallel_owner: dict[str, int] = {}
    boundary_load = [0 for _item in packed]
    for identifier, item in boundaries.items():
        linked = set(item.get("plane_ids", []))
        boundary_roots = _top_levels(item.get("paths", []), code_root)
        candidates = [
            index for index, (plane_ids, roots) in enumerate(packed)
            if linked.intersection(plane_ids) and boundary_roots.intersection(roots)
        ]
        if not candidates:
            # An architecture boundary whose paths cannot be owned alongside any
            # linked plane cannot be split without overlapping anchors.  Merge the
            # code-origin pass; design-origin scouts remain independently parallel.
            merged_planes = sorted({value for values, _roots in packed for value in values})
            merged_roots = sorted({value for _values, roots in packed for value in roots})
            packed = [(merged_planes, merged_roots)]
            boundary_owner = {}
            parallel_owner = {}
            boundary_load = [0]
            break
        owner = min(candidates, key=lambda index: (boundary_load[index], index))
        boundary_owner[identifier] = owner
        boundary_load[owner] += 1
    if len(boundary_owner) != len(boundaries):
        boundary_owner = {
            identifier: 0 for identifier in boundaries
        }
    for identifier, item in parallel.items():
        linked = set(item.get("plane_ids", []))
        candidates = [
            index for index, (plane_ids, _roots) in enumerate(packed)
            if linked.intersection(plane_ids)
        ]
        if candidates:
            parallel_owner[identifier] = min(
                candidates, key=lambda index: (boundary_load[index], index),
            )
    for index, (plane_ids, roots) in enumerate(packed, start=1):
        slices.append({
            "sweep_id": f"SCOUT-CODE-{index:02d}",
            "direction": "code_to_design",
            "document_keys": [],
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
                "Own non-overlapping top-level code anchors and retrieve relevant design "
                "requirements from the complete inventory."
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
    _plan, _index, errors = validator.load_validated_plan(state_root)
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
