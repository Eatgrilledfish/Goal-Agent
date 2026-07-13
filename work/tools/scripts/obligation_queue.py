#!/usr/bin/env python3
"""Materialize a compact, source-bound design obligation queue for one scout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any

import agent_common as ac
import risk_sweep_plan_validator as rpv


REVIEW_MODES = {
    "contract_mechanics",
    "temporal_conditional",
    "routing_capability",
}
NORMATIVE_STRENGTHS = {
    "mandatory", "recommended", "declared_capability", "optional",
}
MAX_SOURCE_LINES = 80


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string array")
    return [item for item in value]


def _section_index(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for group in inventory.get("document_groups", []):
        if not isinstance(group, dict) or group.get("scope_relation") not in {
            "required", "in_scope",
        }:
            continue
        document_key = group.get("document_key")
        for section in group.get("sections", []):
            if not isinstance(section, dict):
                continue
            section_id = section.get("section_id")
            source = section.get("source_ref")
            source = source if isinstance(source, dict) else section
            if isinstance(section_id, str) and section_id:
                result[section_id] = {
                    "section_id": section_id,
                    "document_key": document_key,
                    "path": source.get("path"),
                    "line_start": source.get("line_start"),
                    "line_end": source.get("line_end"),
                }
    return result


def _source_ref(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    path = _nonempty(value.get("path"), f"{label}.path")
    parsed = PurePosixPath(path)
    start = value.get("line_start")
    end = value.get("line_end")
    if parsed.is_absolute() or ".." in parsed.parts or str(parsed) == ".":
        raise ValueError(f"{label}.path must be a safe relative path")
    if (
        not isinstance(start, int) or isinstance(start, bool) or start < 1
        or not isinstance(end, int) or isinstance(end, bool) or end < start
    ):
        raise ValueError(f"{label} has an invalid line range")
    if end - start + 1 > MAX_SOURCE_LINES:
        raise ValueError(f"{label} may contain at most {MAX_SOURCE_LINES} lines")
    return {"path": str(parsed), "line_start": start, "line_end": end}


def _owning_section(
    ref: dict[str, Any], assigned: list[str], sections: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    owners = [
        sections[section_id] for section_id in assigned
        if section_id in sections
        and sections[section_id].get("path") == ref["path"]
        and isinstance(sections[section_id].get("line_start"), int)
        and isinstance(sections[section_id].get("line_end"), int)
        and sections[section_id]["line_start"] <= ref["line_start"]
        and ref["line_end"] <= sections[section_id]["line_end"]
    ]
    if len(owners) != 1:
        raise ValueError("obligation source_ref must belong to exactly one assigned section")
    return owners[0]


def _excerpt(design_root: Path, ref: dict[str, Any]) -> str:
    path = design_root / PurePosixPath(ref["path"])
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"design source is not a regular file: {ref['path']}")
    lines = ac.read_text(path).splitlines()
    if ref["line_end"] > len(lines):
        raise ValueError(f"design source range exceeds file length: {ref['path']}")
    return "\n".join(lines[ref["line_start"] - 1:ref["line_end"]])


def materialize(
    state_root: Path, sweep_id: str, semantic_path: Path, output: Path,
) -> dict[str, Any]:
    _plan, index, errors = rpv.load_validated_plan(state_root)
    if errors:
        raise ValueError("scout plan is invalid: " + "; ".join(errors))
    sweep = index.get("slices", {}).get(sweep_id)
    if not isinstance(sweep, dict) or sweep.get("direction") != "design_to_code":
        raise ValueError("obligation queues require a design_to_code slice")
    semantic = ac.load_json(semantic_path)
    if not isinstance(semantic, dict):
        raise ValueError("semantic obligation output must be an object")
    raw_obligations = semantic.get("obligations")
    if not isinstance(raw_obligations, list):
        raise ValueError("semantic obligations must be an array")
    inventory = ac.load_json(state_root / "design_inventory.json")
    sections = _section_index(inventory)
    assigned = list(sweep.get("section_ids", []))
    missing_sections = [section_id for section_id in assigned if section_id not in sections]
    if missing_sections:
        raise ValueError(f"assigned design sections are missing: {missing_sections}")
    manifest = ac.load_json(state_root / "workspace_manifest.json")
    design_root = Path(manifest["paths"]["review_design_root"])
    obligations: list[dict[str, Any]] = []
    obligation_sections: set[str] = set()
    seen: set[str] = set()
    for index_number, raw in enumerate(raw_obligations, start=1):
        label = f"obligations[{index_number}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label} must be an object")
        ref = _source_ref(raw.get("source_ref"), f"{label}.source_ref")
        owner = _owning_section(ref, assigned, sections)
        strength = _nonempty(raw.get("normative_strength"), f"{label}.normative_strength")
        if strength not in NORMATIVE_STRENGTHS:
            raise ValueError(f"{label}.normative_strength is unsupported")
        mode = _nonempty(raw.get("review_mode"), f"{label}.review_mode")
        if mode not in REVIEW_MODES:
            raise ValueError(f"{label}.review_mode is unsupported")
        semantic_fields = {
            "source_ref": ref,
            "section_ids": [owner["section_id"]],
            "document_key": owner["document_key"],
            "subject": _nonempty(raw.get("subject"), f"{label}.subject"),
            "trigger": _nonempty(raw.get("trigger"), f"{label}.trigger"),
            "obligation": _nonempty(raw.get("obligation"), f"{label}.obligation"),
            "observable_result": _nonempty(
                raw.get("observable_result"), f"{label}.observable_result",
            ),
            "normative_strength": strength,
            "applicability": _nonempty(raw.get("applicability"), f"{label}.applicability"),
            "exceptions": _strings(raw.get("exceptions", []), f"{label}.exceptions"),
            "ambiguities": _strings(raw.get("ambiguities", []), f"{label}.ambiguities"),
            "review_mode": mode,
        }
        obligation_id = "OBL-" + ac.stable_id(
            sweep_id, ref["path"], str(ref["line_start"]), str(ref["line_end"]),
            semantic_fields["subject"], semantic_fields["trigger"],
            semantic_fields["obligation"], length=16,
        ).upper()
        if obligation_id in seen:
            raise ValueError(f"{label} duplicates another atomic obligation")
        seen.add(obligation_id)
        obligation_sections.add(owner["section_id"])
        obligations.append({
            "obligation_id": obligation_id,
            **semantic_fields,
            "source_excerpt": _excerpt(design_root, ref),
        })
    raw_empty = semantic.get("no_obligation_sections")
    if not isinstance(raw_empty, list):
        raise ValueError("no_obligation_sections must be an array")
    empty_reasons: dict[str, str] = {}
    for index_number, raw in enumerate(raw_empty, start=1):
        label = f"no_obligation_sections[{index_number}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label} must be an object")
        section_id = _nonempty(raw.get("section_id"), f"{label}.section_id")
        if section_id not in assigned:
            raise ValueError(f"{label} references an unassigned section")
        if section_id in empty_reasons:
            raise ValueError(f"{label} duplicates section {section_id}")
        empty_reasons[section_id] = _nonempty(raw.get("reason"), f"{label}.reason")
    expected_empty = [
        section_id for section_id in assigned if section_id not in obligation_sections
    ]
    if list(empty_reasons) != expected_empty:
        raise ValueError(
            "no_obligation_sections must exactly account for assigned sections "
            "without extracted obligations, in plan order"
        )
    section_checks = [
        {
            "section_id": section_id,
            "disposition": (
                "obligations_extracted"
                if section_id in obligation_sections
                else "no_implementable_obligation"
            ),
            "obligation_count": sum(
                1 for item in obligations if section_id in item["section_ids"]
            ),
            "no_obligation_reason": empty_reasons.get(section_id, ""),
        }
        for section_id in assigned
    ]
    state = ac.load_json(state_root / "agent_loop_state.json")
    value = {
        "version": 1,
        "session_id": state.get("session_id"),
        "sweep_id": sweep_id,
        "direction": "design_to_code",
        "risk_sweep_plan_sha256": ac.sha256_file(state_root / "risk_sweep_plan.json"),
        "design_inventory_sha256": ac.sha256_file(state_root / "design_inventory.json"),
        "semantic_input_sha256": ac.sha256_file(semantic_path),
        "assigned_section_ids": assigned,
        "section_checks": section_checks,
        "obligations": obligations,
    }
    ac.save_json(output, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--sweep-id", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        value = materialize(
            Path(args.state_root).resolve(), args.sweep_id,
            Path(args.input).resolve(), Path(args.output).resolve(),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "passed": True, "sweep_id": value["sweep_id"],
        "obligation_count": len(value["obligations"]),
        "output": str(Path(args.output).resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
