#!/usr/bin/env python3
"""Bind model-authored scout semantics to the current plan and source queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any

import agent_common as ac
import risk_sweep_plan_validator as rpv


MISMATCH_SIGNALS = {
    "direct_conflict", "capability_absence", "cross_plane_mismatch", "uncertain",
}
DISPOSITIONS = {"candidate", "no_mismatch"}
MODEL_OWNED_ENVELOPE = {
    "observation_id", "session_id", "sweep_id", "risk_sweep_plan_sha256", "direction",
    "architecture_boundaries", "implementation_planes", "parallel_path_ids",
}


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _unique_strings(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{label} must be an array of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must not contain duplicates")
    return list(value)


def _raw_candidates(path: Path) -> list[dict[str, Any]]:
    value = ac.load_json(path)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("raw scout candidates must be a JSON array of objects")
    if len(value) > rpv.MAX_OBSERVATIONS_PER_SWEEP:
        raise ValueError(
            f"raw scout candidates may contain at most {rpv.MAX_OBSERVATIONS_PER_SWEEP} items"
        )
    return value


def _candidate_base(raw: dict[str, Any], label: str) -> dict[str, Any]:
    forbidden = sorted(MODEL_OWNED_ENVELOPE.intersection(raw))
    if forbidden:
        raise ValueError(f"{label} contains tool-owned envelope fields: {forbidden}")
    candidate_key = _nonempty(raw.get("candidate_key"), f"{label}.candidate_key")
    signal = _nonempty(raw.get("mismatch_signal"), f"{label}.mismatch_signal")
    if signal not in MISMATCH_SIGNALS:
        raise ValueError(f"{label}.mismatch_signal is unsupported")
    return {
        "candidate_key": candidate_key,
        "behavior_question": _nonempty(
            raw.get("behavior_question"), f"{label}.behavior_question",
        ),
        "mismatch_signal": signal,
        "observed_code_behavior": _nonempty(
            raw.get("observed_code_behavior"), f"{label}.observed_code_behavior",
        ),
        "code_evidence": raw.get("code_evidence"),
        "false_positive_checks": raw.get("false_positive_checks"),
        "tool_trace": raw.get("tool_trace"),
    }


def _require_candidate_payload(value: dict[str, Any], label: str) -> None:
    if not isinstance(value.get("code_evidence"), list) or not value["code_evidence"]:
        raise ValueError(f"{label}.code_evidence must not be empty")
    if not isinstance(value.get("false_positive_checks"), list) or not value[
        "false_positive_checks"
    ]:
        raise ValueError(f"{label}.false_positive_checks must not be empty")
    if not isinstance(value.get("tool_trace"), list) or not value["tool_trace"]:
        raise ValueError(f"{label}.tool_trace must not be empty")


def _requirement_from_obligation(obligation: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_ref": obligation["source_ref"],
        "subject": obligation["subject"],
        "trigger": obligation["trigger"],
        "obligation": obligation["obligation"],
        "observable_result": obligation["observable_result"],
        "normative_strength": obligation["normative_strength"],
        "applicability": obligation["applicability"],
        "exceptions": obligation["exceptions"],
        "ambiguities": obligation["ambiguities"],
    }


def _path_in_anchor(path: str, anchor: str) -> bool:
    parsed = PurePosixPath(path)
    root = PurePosixPath(anchor)
    return parsed == root or parsed.is_relative_to(root)


def _checks(
    raw_coverage: dict[str, Any], *, key: str, identifier: str,
    expected: list[str], candidate_keys: set[str], candidate_owner: dict[str, str],
    canonical_ids: dict[str, str],
) -> list[dict[str, Any]]:
    values = raw_coverage.get(key)
    if not isinstance(values, list):
        raise ValueError(f"raw coverage {key} must be an array")
    result: list[dict[str, Any]] = []
    seen: list[str] = []
    bound: set[str] = set()
    for index, item in enumerate(values, start=1):
        label = f"{key}[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object")
        owner = _nonempty(item.get(identifier), f"{label}.{identifier}")
        disposition = _nonempty(item.get("disposition"), f"{label}.disposition")
        if disposition not in DISPOSITIONS:
            raise ValueError(f"{label}.disposition is unsupported")
        keys = _unique_strings(item.get("candidate_keys"), f"{label}.candidate_keys")
        if disposition == "candidate" and not keys:
            raise ValueError(f"{label} candidate disposition requires candidate_keys")
        if disposition == "no_mismatch" and keys:
            raise ValueError(f"{label} no_mismatch disposition cannot bind candidates")
        unknown = set(keys) - candidate_keys
        if unknown:
            raise ValueError(f"{label} references unknown candidates: {sorted(unknown)}")
        for candidate_key in keys:
            if candidate_owner.get(candidate_key) != owner:
                raise ValueError(f"{label} binds a candidate owned by another review item")
            if candidate_key in bound:
                raise ValueError(f"candidate {candidate_key} is bound more than once")
            bound.add(candidate_key)
        seen.append(owner)
        result.append({
            identifier: owner,
            "disposition": disposition,
            "candidate_ids": [canonical_ids[candidate_key] for candidate_key in keys],
            "code_search_summary": _nonempty(
                item.get("code_search_summary"), f"{label}.code_search_summary",
            ),
            "countercheck": _nonempty(item.get("countercheck"), f"{label}.countercheck"),
        })
    if seen != expected:
        raise ValueError(f"{key} must exactly follow the assigned review order")
    if bound != candidate_keys:
        raise ValueError(f"{key} does not bind every candidate exactly once")
    return result


def materialize(
    state_root: Path, sweep_id: str, semantic_candidates: Path,
    semantic_coverage: Path, handoff: Path, coverage_output: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _plan, index, errors = rpv.load_validated_plan(state_root)
    if errors:
        raise ValueError("scout plan is invalid: " + "; ".join(errors))
    sweep = index.get("slices", {}).get(sweep_id)
    if not isinstance(sweep, dict):
        raise ValueError(f"unknown sweep_id {sweep_id!r}")
    direction = sweep.get("direction")
    raw = _raw_candidates(semantic_candidates)
    raw_coverage = ac.load_json(semantic_coverage)
    if not isinstance(raw_coverage, dict):
        raise ValueError("raw scout coverage must be an object")
    state = ac.load_json(state_root / "agent_loop_state.json")
    envelope = {
        "session_id": state.get("session_id"),
        "sweep_id": sweep_id,
        "risk_sweep_plan_sha256": ac.sha256_file(state_root / "risk_sweep_plan.json"),
        "direction": direction,
        "architecture_boundaries": list(sweep.get("architecture_boundaries", [])),
        "implementation_planes": list(sweep.get("implementation_planes", [])),
        "parallel_path_ids": list(sweep.get("parallel_path_ids", [])),
    }
    candidates: list[dict[str, Any]] = []
    candidate_owner: dict[str, str] = {}
    canonical_ids: dict[str, str] = {}
    obligation_digest = ""
    if direction == "design_to_code":
        queue_path = state_root / "design-obligations" / f"{sweep_id}.json"
        queue = ac.load_json(queue_path)
        if queue.get("risk_sweep_plan_sha256") != envelope["risk_sweep_plan_sha256"]:
            raise ValueError("design obligation queue is stale for the current plan")
        obligations = queue.get("obligations")
        if not isinstance(obligations, list):
            raise ValueError("design obligation queue is invalid")
        obligation_index = {item["obligation_id"]: item for item in obligations}
        expected = [item["obligation_id"] for item in obligations]
        obligation_digest = ac.sha256_file(queue_path)
        for number, item in enumerate(raw, start=1):
            label = f"candidates[{number}]"
            base = _candidate_base(item, label)
            _require_candidate_payload(base, label)
            candidate_key = base.pop("candidate_key")
            if candidate_key in canonical_ids:
                raise ValueError(f"{label}.candidate_key duplicates another candidate")
            observation_id = "CANDIDATE-" + ac.stable_id(
                sweep_id, candidate_key, length=16,
            ).upper()
            canonical_ids[candidate_key] = observation_id
            obligation_id = _nonempty(item.get("obligation_id"), f"{label}.obligation_id")
            obligation = obligation_index.get(obligation_id)
            if obligation is None:
                raise ValueError(f"{label} references an unknown obligation")
            candidate_owner[candidate_key] = obligation_id
            candidates.append({
                "observation_id": observation_id, **base, **envelope,
                "origin_obligation_id": obligation_id,
                "design_requirement": _requirement_from_obligation(obligation),
                "design_section_ids": list(obligation["section_ids"]),
                "review_lenses": [obligation["review_mode"]],
            })
        checks = _checks(
            raw_coverage, key="obligation_checks", identifier="obligation_id",
            expected=expected,
            candidate_keys=set(canonical_ids), canonical_ids=canonical_ids,
            candidate_owner=candidate_owner,
        )
        coverage = {
            "sweep_id": sweep_id,
            "reviewed_section_ids": list(sweep.get("section_ids", [])),
            "reviewed_anchor_paths": [],
            "obligation_queue_sha256": obligation_digest,
            "obligation_checks": checks,
        }
    else:
        anchors = list(sweep.get("anchor_paths", []))
        for number, item in enumerate(raw, start=1):
            label = f"candidates[{number}]"
            base = _candidate_base(item, label)
            _require_candidate_payload(base, label)
            candidate_key = base.pop("candidate_key")
            if candidate_key in canonical_ids:
                raise ValueError(f"{label}.candidate_key duplicates another candidate")
            observation_id = "CANDIDATE-" + ac.stable_id(
                sweep_id, candidate_key, length=16,
            ).upper()
            canonical_ids[candidate_key] = observation_id
            anchor = _nonempty(item.get("primary_anchor_path"), f"{label}.primary_anchor_path")
            if anchor not in anchors:
                raise ValueError(f"{label} primary anchor is outside the assigned slice")
            evidence = base.get("code_evidence")
            if not any(
                isinstance(entry, dict) and isinstance(entry.get("file"), str)
                and _path_in_anchor(entry["file"], anchor)
                for entry in evidence
            ):
                raise ValueError(f"{label} has no code evidence inside its primary anchor")
            requirement = item.get("design_requirement")
            section_ids = item.get("design_section_ids")
            lenses = item.get("review_lenses")
            if not isinstance(requirement, dict):
                raise ValueError(f"{label}.design_requirement must be an object")
            _unique_strings(section_ids, f"{label}.design_section_ids", allow_empty=False)
            _unique_strings(lenses, f"{label}.review_lenses", allow_empty=False)
            candidate_owner[candidate_key] = anchor
            candidates.append({
                "observation_id": observation_id, **base, **envelope,
                "origin_anchor_path": anchor,
                "design_requirement": requirement,
                "design_section_ids": section_ids,
                "review_lenses": lenses,
            })
        checks = _checks(
            raw_coverage, key="anchor_checks", identifier="anchor_path",
            expected=anchors,
            candidate_keys=set(canonical_ids), canonical_ids=canonical_ids,
            candidate_owner=candidate_owner,
        )
        coverage = {
            "sweep_id": sweep_id,
            "reviewed_section_ids": [],
            "reviewed_anchor_paths": anchors,
            "anchor_checks": checks,
        }
    ids = [item["observation_id"] for item in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate observation IDs must be unique")
    ac.save_json(handoff, candidates)
    ac.save_json(coverage_output, coverage)
    return candidates, coverage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--sweep-id", required=True)
    parser.add_argument("--semantic-candidates", required=True)
    parser.add_argument("--semantic-coverage", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--coverage-output", required=True)
    args = parser.parse_args()
    try:
        candidates, coverage = materialize(
            Path(args.state_root).resolve(), args.sweep_id,
            Path(args.semantic_candidates).resolve(),
            Path(args.semantic_coverage).resolve(),
            Path(args.handoff).resolve(), Path(args.coverage_output).resolve(),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "passed": True, "candidate_count": len(candidates),
        "coverage_count": len(
            coverage.get("obligation_checks", coverage.get("anchor_checks", []))
        ),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
