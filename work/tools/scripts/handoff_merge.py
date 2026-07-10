#!/usr/bin/env python3
"""Validate and merge isolated subagent JSON handoffs into one JSONL ledger.

This helper validates syntax and artifact shape only. It performs no semantic
ranking, filtering, or design/code judgement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac


ARTIFACT_TYPES = {"generic", "task", "finding", "critic", "probe"}
TRACE_KINDS = {
    "design_read", "code_search", "code_navigation", "code_read", "reverse_check",
    "test", "config_read", "history_read", "build_read", "analysis",
}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _require(item: dict[str, Any], fields: tuple[str, ...], label: str) -> list[str]:
    return [f"{label}: missing/empty {field}" for field in fields if not _present(item.get(field))]


def _validate_trace(item: dict[str, Any], label: str) -> list[str]:
    trace = item.get("tool_trace")
    if not isinstance(trace, list) or len(trace) < 4:
        return [f"{label}: tool_trace must contain at least four real steps"]
    errors: list[str] = []
    kinds: set[str] = set()
    for index, step in enumerate(trace, start=1):
        step_label = f"{label}: tool_trace[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{step_label} must be an object")
            continue
        errors.extend(_require(step, ("kind", "tool", "target", "purpose", "result"), step_label))
        if step.get("seq") != index:
            errors.append(f"{step_label}: seq must equal {index}")
        kind = str(step.get("kind") or "")
        if kind not in TRACE_KINDS:
            errors.append(f"{step_label}: unsupported kind {kind!r}")
        kinds.add(kind)
    for required, description in (
        ({"design_read"}, "design_read"),
        ({"code_search", "code_navigation"}, "code_search or code_navigation"),
        ({"code_read"}, "code_read"),
        ({"reverse_check"}, "reverse_check"),
    ):
        if not kinds.intersection(required):
            errors.append(f"{label}: tool_trace lacks {description}")
    return errors


def validate_artifact(item: dict[str, Any], artifact_type: str, label: str) -> list[str]:
    """Check a handoff's machine contract without judging its semantics."""
    if artifact_type == "generic":
        return []
    errors: list[str] = []
    if artifact_type == "task":
        errors.extend(_require(item, (
            "task_id", "session_id", "claim_id", "question", "starting_points",
            "supporting_evidence_needed", "disconfirming_evidence_needed", "review_lenses",
            "exploration_mode", "architecture_boundaries", "implementation_planes", "status",
        ), label))
        if item.get("status") not in {"pending", "in_progress", "complete", "deferred"}:
            errors.append(f"{label}: invalid status")
        lenses = item.get("review_lenses")
        if not isinstance(lenses, list) or not 1 <= len(lenses) <= 3:
            errors.append(f"{label}: review_lenses must contain one to three focused lenses")
        return errors

    if artifact_type == "finding":
        errors.extend(_require(item, (
            "finding_id", "session_id", "task_id", "claim_id", "hypothesis",
            "expected_behavior", "observed_behavior", "design_evidence", "code_evidence",
            "supporting_evidence", "false_positive_checks", "tool_trace",
            "dynamic_probe_selection", "assessment", "review_lenses", "recommendation",
        ), label))
        if item.get("assessment") not in {"contradiction_supported", "uncertain", "design_satisfied"}:
            errors.append(f"{label}: invalid assessment")
        if item.get("recommendation") not in {"critic_review", "probable", "reject"}:
            errors.append(f"{label}: invalid recommendation")
        checks = item.get("false_positive_checks")
        if not isinstance(checks, list) or len(checks) < 2:
            errors.append(f"{label}: false_positive_checks must contain at least two checks")
        else:
            for index, check in enumerate(checks, start=1):
                if not isinstance(check, dict):
                    errors.append(f"{label}: false_positive_checks[{index}] must be an object")
                else:
                    errors.extend(_require(check, ("question", "method", "target", "result"), f"{label}: false_positive_checks[{index}]"))
        selection = item.get("dynamic_probe_selection")
        if not isinstance(selection, dict):
            errors.append(f"{label}: dynamic_probe_selection must be an object")
        elif selection.get("disposition") not in {
            "selected", "not_selected", "not_suitable", "environment_limited",
        } or not _present(selection.get("reason")):
            errors.append(f"{label}: invalid dynamic_probe_selection")
        lenses = item.get("review_lenses")
        if not isinstance(lenses, list) or not 1 <= len(lenses) <= 3:
            errors.append(f"{label}: review_lenses must contain one to three focused lenses")
        errors.extend(_validate_trace(item, label))
        return errors

    if artifact_type == "critic":
        errors.extend(_require(item, (
            "review_id", "session_id", "finding_id", "claim_id", "decision", "challenges",
            "checks_performed", "dynamic_probe_review", "review_context", "resolution",
        ), label))
        if item.get("decision") not in {
            "confirm_contradiction", "probable_contradiction", "reject_issue", "needs_more_evidence",
        }:
            errors.append(f"{label}: invalid decision")
        if item.get("review_context") != "fresh_subagent":
            errors.append(f"{label}: review_context must be fresh_subagent")
        checks = item.get("checks_performed")
        if not isinstance(checks, list) or len(checks) < 2:
            errors.append(f"{label}: checks_performed must contain at least two independent checks")
        probe_review = item.get("dynamic_probe_review")
        if not isinstance(probe_review, dict):
            errors.append(f"{label}: dynamic_probe_review must be an object")
        else:
            errors.extend(_require(probe_review, (
                "status", "oracle_validity", "environment_validity", "reachability", "effect_on_decision",
            ), f"{label}: dynamic_probe_review"))
            if probe_review.get("status") not in {
                "not_run", "supports_contradiction", "disconfirms_contradiction", "inconclusive",
            }:
                errors.append(f"{label}: invalid dynamic_probe_review.status")
            if probe_review.get("status") != "not_run" and not _present(probe_review.get("probe_id")):
                errors.append(f"{label}: executed dynamic probe review needs probe_id")
        return errors

    if artifact_type == "probe":
        errors.extend(_require(item, (
            "probe_id", "session_id", "finding_id", "claim_id", "oracle", "selection_reason",
            "isolation", "baseline", "execution", "interpretation", "tool_trace",
        ), label))
        if item.get("interpretation") not in {
            "supports_contradiction", "disconfirms_contradiction", "inconclusive",
        }:
            errors.append(f"{label}: invalid interpretation")
        return errors
    return [f"{label}: unknown artifact type {artifact_type!r}"]


def _read_values(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        values, errors = ac.load_jsonl(path)
        if errors:
            raise ValueError("; ".join(errors))
        return values
    value = ac.load_json(path)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise ValueError("handoff must be one JSON object, an object array, or JSONL objects")


def merge(
    input_dir: Path,
    output: Path,
    key: str,
    artifact_type: str = "generic",
    session_id: str | None = None,
    code_root: Path | None = None,
    design_root: Path | None = None,
) -> dict[str, int]:
    if not input_dir.is_dir():
        raise ValueError(f"handoff directory is missing: {input_dir}")
    existing: list[dict[str, Any]] = []
    if output.exists():
        existing, errors = ac.load_jsonl(output)
        if errors:
            raise ValueError(f"existing ledger is invalid: {'; '.join(errors)}")
    ordered: list[str] = []
    values: dict[str, dict[str, Any]] = {}
    for item in existing:
        identifier = str(item.get(key) or "")
        if not identifier:
            raise ValueError(f"existing ledger entry lacks {key}")
        if identifier not in values:
            ordered.append(identifier)
        values[identifier] = item

    imported = 0
    files = sorted(
        path for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    )
    imported_items: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        for item_index, item in enumerate(_read_values(path), start=1):
            identifier = str(item.get(key) or "")
            if not identifier:
                raise ValueError(f"{path}: handoff entry lacks {key}")
            imported_items.append((path, item))

    for _path, item in imported_items:
        identifier = str(item.get(key) or "")
        if identifier not in values:
            ordered.append(identifier)
        values[identifier] = item
        imported += 1

    validation_errors: list[str] = []
    for identifier in ordered:
        item = values[identifier]
        label = f"merged {artifact_type} ({identifier})"
        validation_errors.extend(validate_artifact(item, artifact_type, label))
        if session_id and item.get("session_id") != session_id:
            validation_errors.append(f"{label}: session_id does not match current session")
        if artifact_type == "finding" and code_root and design_root:
            for index, evidence in enumerate(item.get("design_evidence", []), start=1):
                validation_errors.extend(ac.validate_source_evidence(
                    evidence, design_root, f"{label}: design_evidence[{index}]", "quote"
                ))
            for index, evidence in enumerate(item.get("code_evidence", []), start=1):
                validation_errors.extend(ac.validate_source_evidence(
                    evidence, code_root, f"{label}: code_evidence[{index}]", "snippet"
                ))
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    ac.ensure_dir(output.parent)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(values[identifier], ensure_ascii=False) + "\n" for identifier in ordered),
        encoding="utf-8",
    )
    temporary.replace(output)
    return {"files": len(files), "imported": imported, "ledger_entries": len(ordered)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge isolated subagent JSON handoffs into JSONL.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--artifact-type", choices=sorted(ARTIFACT_TYPES), default="generic")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--code-root", default=None)
    parser.add_argument("--design-root", default=None)
    args = parser.parse_args(argv)
    try:
        result = merge(
            Path(args.input_dir).resolve(), Path(args.output).resolve(), args.key,
            artifact_type=args.artifact_type, session_id=args.session_id,
            code_root=Path(args.code_root).resolve() if args.code_root else None,
            design_root=Path(args.design_root).resolve() if args.design_root else None,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1
    print(json.dumps({"passed": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
