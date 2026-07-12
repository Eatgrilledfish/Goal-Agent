#!/usr/bin/env python3
"""Validate focused, non-overlapping code-risk sweep slices."""

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
CONTRACT_NAME = "agent_loop_contract.json"
SAFE_SWEEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_IMPLEMENTATION_PLANES_PER_SLICE = 6


def plan_input_digests(state_root: Path) -> tuple[dict[str, str], str]:
    paths = [
        state_root / "workspace_manifest.json",
        state_root / ARCHITECTURE_NAME,
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


def _required_coverage(
    boundaries: dict[str, dict], planes: dict[str, dict], paths: dict[str, dict],
) -> tuple[set[str], set[str], set[str]]:
    # The sweeps are the complete architecture breadth pass, not a high-risk
    # sample. High/medium/low remains useful for later frontier ordering, but
    # every mapped reachable scope must appear in at least one focused slice.
    required_boundaries = set(boundaries)
    required_paths = set(paths)
    required_planes = set(planes)
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
    review_code_root: Path | None = None,
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

    boundaries, planes, parallel_paths = _indexes(architecture)
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
    anchor_owners: dict[str, str] = {}
    assigned_lenses: set[str] = set()
    allowed_keys = {
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
        values_by_kind: dict[str, list[str]] = {}
        for field, kind, known in (
            ("architecture_boundaries", "boundary", set(boundaries)),
            ("implementation_planes", "plane", set(planes)),
            ("parallel_path_ids", "path", set(parallel_paths)),
        ):
            values, value_errors = _strings(
                item.get(field), f"{label}.{field}",
                allow_empty=kind != "plane",
            )
            errors.extend(value_errors)
            values_by_kind[kind] = values
            unknown = set(values) - known
            if unknown:
                errors.append(f"{label}.{field}: unknown IDs {sorted(unknown)}")
            coverage_by_kind[kind].update(values)
        if len(values_by_kind["plane"]) > MAX_IMPLEMENTATION_PLANES_PER_SLICE:
            errors.append(
                f"{label}.implementation_planes must contain at most "
                f"{MAX_IMPLEMENTATION_PLANES_PER_SLICE} values"
            )
        for boundary_id in values_by_kind["boundary"]:
            linked_planes = set(
                boundaries.get(boundary_id, {}).get("plane_ids", [])
            )
            if boundary_id in boundaries and not linked_planes.intersection(
                values_by_kind["plane"]
            ):
                errors.append(
                    f"{label}.architecture_boundaries: {boundary_id} has no linked "
                    "implementation plane in this slice"
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
            item.get("anchor_paths"), f"{label}.anchor_paths", allow_empty=False,
        )
        errors.extend(anchor_errors)
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
                f"boundary:{boundary_id}": [
                    str(PurePosixPath(path))
                    for path in boundaries.get(boundary_id, {}).get("paths", [])
                ]
                for boundary_id in values_by_kind["boundary"]
            },
            **{
                f"plane:{plane_id}": [
                    str(PurePosixPath(path))
                    for path in planes.get(plane_id, {}).get("paths", [])
                ]
                for plane_id in values_by_kind["plane"]
            },
        }
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
            path for paths_for_id in scoped_paths_by_id.values()
            for path in paths_for_id
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
        assigned_lenses.update(lenses)
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
    if assigned_lenses != known_lenses:
        errors.append(
            f"{PLAN_NAME}: review_lenses must cover the complete contract portfolio; "
            f"missing={sorted(known_lenses - assigned_lenses)}, "
            f"extra={sorted(assigned_lenses - known_lenses)}"
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
    }


def load_validated_plan(state_root: Path) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        state = ac.load_json(state_root / "agent_loop_state.json")
        architecture = ac.load_json(state_root / ARCHITECTURE_NAME)
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
    digest = ac.sha256_file(architecture_path) if architecture_path.is_file() else ""
    plan_errors, index = validate_plan(
        plan, session_id=session_id, architecture=architecture,
        architecture_digest=digest, contract=contract,
        review_code_root=review_code_root,
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


def validate_observation_against_plan(
    item: dict[str, Any], state_root: Path, label: str,
) -> list[str]:
    plan, index, errors = load_validated_plan(state_root)
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
    field_map = {
        "architecture_boundaries": "architecture_boundaries",
        "implementation_planes": "implementation_planes",
        "parallel_path_ids": "parallel_path_ids",
    }
    for field, plan_field in field_map.items():
        values = item.get(field)
        if not isinstance(values, list):
            continue
        outside = set(values) - set(sweep.get(plan_field, []))
        if outside:
            errors.append(f"{label}: {field} escapes assigned sweep: {sorted(outside)}")
    observation_lenses = item.get("review_lenses")
    if isinstance(observation_lenses, list):
        outside_lenses = set(observation_lenses) - set(sweep.get("review_lenses", []))
        if outside_lenses:
            errors.append(
                f"{label}: review_lenses escape assigned sweep: {sorted(outside_lenses)}"
            )
    observation_planes = {
        str(value) for value in item.get("implementation_planes", [])
        if isinstance(value, str) and value
    }
    evidence_files = [
        str(evidence.get("file"))
        for evidence in item.get("code_evidence", [])
        if isinstance(evidence, dict)
        and isinstance(evidence.get("file"), str)
        and evidence.get("file")
    ]
    for boundary_id in item.get("architecture_boundaries", []):
        boundary_planes = set(
            index["boundaries"].get(boundary_id, {}).get("plane_ids", [])
        )
        if boundary_id in index["boundaries"] and not observation_planes.intersection(
            boundary_planes
        ):
            errors.append(
                f"{label}: architecture boundary {boundary_id} has no linked "
                "observation plane"
            )
        boundary_paths = index["boundaries"].get(boundary_id, {}).get("paths", [])
        if boundary_id in index["boundaries"] and not any(
            _in_scope(file_value, boundary_paths) for file_value in evidence_files
        ):
            errors.append(
                f"{label}: architecture boundary {boundary_id} lacks local code evidence"
            )
    for plane_id in observation_planes:
        plane_paths = index["planes"].get(plane_id, {}).get("paths", [])
        if plane_id in index["planes"] and not any(
            _in_scope(file_value, plane_paths) for file_value in evidence_files
        ):
            errors.append(
                f"{label}: implementation plane {plane_id} lacks local code evidence"
            )
    for path_id in item.get("parallel_path_ids", []):
        path_planes = set(index["parallel_paths"].get(path_id, {}).get("plane_ids", []))
        if path_id in index["parallel_paths"] and not observation_planes.intersection(path_planes):
            errors.append(
                f"{label}: parallel path {path_id} has no linked observation plane"
            )
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
    observed_sweeps = {str(item.get("sweep_id") or "") for item in risks.values()}
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
    missing_sweeps = expected_sweeps - observed_sweeps
    if missing_sweeps:
        errors.append(
            f"risk observations do not include completed sweeps: {sorted(missing_sweeps)}"
        )
    return errors, {
        "expected_sweeps": sorted(expected_sweeps),
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
    plan, index, plan_errors = load_validated_plan(root)
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
