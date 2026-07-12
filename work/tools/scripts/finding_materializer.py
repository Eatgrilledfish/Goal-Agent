#!/usr/bin/env python3
"""Materialize a validated finding from a small investigator-owned decision.

Task, claim, design evidence, and review-lens fields come exclusively from the
current pristine finding template.  The investigator supplies only its semantic
assessment and code locations; snippets are copied from the read-only code root.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import handoff_template as ht


ASSESSMENTS = {
    "contradiction_supported", "uncertain", "design_satisfied",
}
RECOMMENDATIONS = {
    "contradiction_supported": "critic_review",
    "uncertain": "probable",
    "design_satisfied": "reject",
}
PROBE_DISPOSITIONS = {
    "selected", "not_selected", "not_suitable", "environment_limited",
}
SEMANTIC_FIELDS = {
    "task_id", "assessment", "observed_behavior", "code_locations",
    "false_positive_checks", "design_read_result", "code_search_result",
    "reverse_check_result", "supporting_evidence", "disconfirming_evidence",
    "dynamic_probe_selection",
}
REQUIRED_SEMANTIC_FIELDS = {
    "task_id", "assessment", "observed_behavior", "code_locations",
    "false_positive_checks", "design_read_result", "code_search_result",
    "reverse_check_result",
}
LOCATION_FIELDS = {"file", "line_start", "line_end", "symbol"}
CHECK_FIELDS = {"question", "method", "target", "result"}


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, field: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if any(not _nonempty_string(item) for item in value):
        raise ValueError(f"{field} entries must be non-empty strings")
    return list(value)


def _state_root_for_template(template_path: Path) -> Path:
    if (
        template_path.parent.name != "investigators"
        or template_path.parent.parent.name != "handoff-templates"
    ):
        raise ValueError(
            "template must be under <state-root>/handoff-templates/investigators"
        )
    return template_path.parent.parent.parent


def _load_pristine_template(template_path: Path, task_id: str) -> dict[str, Any]:
    state_root = _state_root_for_template(template_path)
    expected_path = state_root / "handoff-templates" / "investigators" / f"{task_id}.json"
    if template_path != expected_path.resolve():
        raise ValueError(f"template path must be the canonical task template: {expected_path}")
    template = ac.load_json(template_path)
    if not isinstance(template, dict):
        raise ValueError("pristine finding template must be an object")
    tasks, task_errors = ac.load_jsonl(state_root / "investigation_tasks.jsonl")
    claims, claim_errors = ac.load_jsonl(state_root / "design_claims.jsonl")
    if task_errors or claim_errors:
        raise ValueError("cannot reconstruct template from invalid task or claim ledger")
    task_matches = [item for item in tasks if item.get("task_id") == task_id]
    if len(task_matches) != 1:
        raise ValueError(f"task must occur exactly once in the task ledger: {task_id}")
    claim_id = str(task_matches[0].get("claim_id") or "")
    claim_matches = [item for item in claims if item.get("claim_id") == claim_id]
    if len(claim_matches) != 1:
        raise ValueError(f"claim must occur exactly once in the claim ledger: {claim_id}")
    reconstructed = ht.finding_template(task_matches[0], claim_matches[0])
    if template != reconstructed:
        raise ValueError("pristine template differs from current task and claim ledgers")
    return template


def _materialize_code_evidence(
    raw_locations: Any, code_root: Path,
) -> list[dict[str, Any]]:
    if not isinstance(raw_locations, list) or not raw_locations:
        raise ValueError("code_locations must be a non-empty array")
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for index, raw in enumerate(raw_locations, start=1):
        label = f"code_locations[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label} must be an object")
        unexpected = sorted(set(raw) - LOCATION_FIELDS)
        if unexpected:
            raise ValueError(f"{label} has unsupported fields {unexpected}")
        file_value = raw.get("file")
        if not _nonempty_string(file_value):
            raise ValueError(f"{label}.file must be a non-empty string")
        source_path = ac.contained_path(code_root, str(file_value))
        if source_path is None or not source_path.is_file():
            raise ValueError(f"{label}.file is outside code root or missing: {file_value}")
        relative_path = source_path.relative_to(code_root).as_posix()
        line_start = raw.get("line_start")
        line_end = raw.get("line_end")
        if not isinstance(line_start, int) or isinstance(line_start, bool):
            raise ValueError(f"{label}.line_start must be an integer")
        if not isinstance(line_end, int) or isinstance(line_end, bool):
            raise ValueError(f"{label}.line_end must be an integer")
        if line_start < 1 or line_end < line_start:
            raise ValueError(f"{label} has invalid line range {line_start}-{line_end}")
        try:
            source_text = source_path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{label}.file is not valid UTF-8: {relative_path}") from exc
        if "\x00" in source_text:
            raise ValueError(f"{label}.file contains binary NUL bytes: {relative_path}")
        lines = source_text.splitlines()
        if line_end > len(lines):
            raise ValueError(
                f"{label} line range {line_start}-{line_end} exceeds "
                f"{len(lines)} lines: {relative_path}"
            )
        location_key = (relative_path, line_start, line_end)
        if location_key in seen:
            raise ValueError(f"{label} duplicates code evidence {location_key}")
        seen.add(location_key)
        symbol = raw.get("symbol", "")
        if not isinstance(symbol, str):
            raise ValueError(f"{label}.symbol must be a string")
        evidence.append({
            "file": relative_path,
            "line_start": line_start,
            "line_end": line_end,
            "symbol": symbol,
            "snippet": "\n".join(lines[line_start - 1:line_end]),
        })
    return evidence


def _false_positive_checks(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError("false_positive_checks must contain at least two checks")
    checks: list[dict[str, str]] = []
    for index, raw in enumerate(value, start=1):
        label = f"false_positive_checks[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label} must be an object")
        unexpected = sorted(set(raw) - CHECK_FIELDS)
        missing = sorted(CHECK_FIELDS - set(raw))
        if unexpected:
            raise ValueError(f"{label} has unsupported fields {unexpected}")
        if missing:
            raise ValueError(f"{label} is missing fields {missing}")
        if any(not _nonempty_string(raw.get(field)) for field in CHECK_FIELDS):
            raise ValueError(f"{label} fields must be non-empty strings")
        checks.append({field: str(raw[field]) for field in CHECK_FIELDS})
    return checks


def _probe_selection(value: Any) -> dict[str, str]:
    if value is None:
        return {
            "disposition": "not_selected",
            "reason": "The investigator did not select a focused dynamic probe.",
        }
    if not isinstance(value, dict) or set(value) != {"disposition", "reason"}:
        raise ValueError(
            "dynamic_probe_selection must contain exactly disposition and reason"
        )
    if value.get("disposition") not in PROBE_DISPOSITIONS:
        raise ValueError("dynamic_probe_selection has invalid disposition")
    if not _nonempty_string(value.get("reason")):
        raise ValueError("dynamic_probe_selection.reason must be a non-empty string")
    return {"disposition": value["disposition"], "reason": value["reason"]}


def materialize_finding(
    semantic: dict[str, Any], template_path: Path, code_root: Path,
) -> dict[str, Any]:
    if not isinstance(semantic, dict):
        raise ValueError("investigator semantic output must be an object")
    unexpected = sorted(set(semantic) - SEMANTIC_FIELDS)
    if unexpected:
        raise ValueError(f"investigator semantic output has unsupported fields {unexpected}")
    missing = sorted(REQUIRED_SEMANTIC_FIELDS - set(semantic))
    if missing:
        raise ValueError(f"investigator semantic output is missing fields {missing}")
    for field in (
        "task_id", "observed_behavior", "design_read_result",
        "code_search_result", "reverse_check_result",
    ):
        if not _nonempty_string(semantic.get(field)):
            raise ValueError(f"{field} must be a non-empty string")
    task_id = str(semantic["task_id"])
    assessment = semantic.get("assessment")
    if assessment not in ASSESSMENTS:
        raise ValueError(f"assessment must be one of {sorted(ASSESSMENTS)}")
    template = _load_pristine_template(template_path.resolve(), task_id)
    code_evidence = _materialize_code_evidence(
        semantic.get("code_locations"), code_root.resolve(),
    )
    checks = _false_positive_checks(semantic.get("false_positive_checks"))
    supporting = semantic.get("supporting_evidence")
    if supporting is None:
        supporting_values = [str(semantic["observed_behavior"])]
    else:
        supporting_values = _string_list(
            supporting, "supporting_evidence", allow_empty=False,
        )
    disconfirming = semantic.get("disconfirming_evidence", [])
    disconfirming_values = _string_list(
        disconfirming, "disconfirming_evidence", allow_empty=True,
    )
    code_files = list(dict.fromkeys(item["file"] for item in code_evidence))
    code_ranges = [
        f"{item['file']}:{item['line_start']}-{item['line_end']}"
        for item in code_evidence
    ]
    reverse_targets = list(dict.fromkeys(check["target"] for check in checks))
    design_targets = [
        f"{item['path']}:{item['line_start']}-{item['line_end']}"
        for item in template.get("design_evidence", [])
        if isinstance(item, dict)
    ]

    finding = copy.deepcopy(template)
    finding.update({
        "observed_behavior": semantic["observed_behavior"],
        "code_evidence": code_evidence,
        "supporting_evidence": supporting_values,
        "disconfirming_evidence": disconfirming_values,
        "false_positive_checks": checks,
        "tool_trace": [
            {
                "seq": 1, "kind": "design_read", "tool": "read",
                "target": "; ".join(design_targets),
                "purpose": "Re-read the supplied design claim.",
                "result": semantic["design_read_result"],
            },
            {
                "seq": 2, "kind": "code_search", "tool": "search",
                "target": "; ".join(code_files),
                "purpose": "Locate the implementation and relevant alternate paths.",
                "result": semantic["code_search_result"],
            },
            {
                "seq": 3, "kind": "code_read", "tool": "read",
                "target": "; ".join(code_ranges),
                "purpose": "Derive actual behavior from reachable code.",
                "result": semantic["observed_behavior"],
            },
            {
                "seq": 4, "kind": "reverse_check", "tool": "search",
                "target": "; ".join(reverse_targets),
                "purpose": "Check for compensating, configured, or parallel behavior.",
                "result": semantic["reverse_check_result"],
            },
        ],
        "dynamic_probe_selection": _probe_selection(
            semantic.get("dynamic_probe_selection"),
        ),
        "assessment": assessment,
        "recommendation": RECOMMENDATIONS[str(assessment)],
    })
    return finding


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def run(args: argparse.Namespace) -> int:
    input_path = Path(args.input).resolve()
    template_path = Path(args.template).resolve()
    code_root = Path(args.code_root).resolve()
    output_path = Path(args.output).resolve()
    trace_path = Path(args.trace).resolve() if args.trace else None
    errors: list[str] = []
    task_id = ""
    if not code_root.is_dir():
        errors.append(f"code root is not a directory: {code_root}")
    if not input_path.is_file():
        errors.append(f"investigator semantic input is missing: {input_path}")
    if not template_path.is_file():
        errors.append(f"pristine finding template is missing: {template_path}")
    for label, path in (("input", input_path), ("output", output_path), ("trace", trace_path)):
        if path is not None and _inside(code_root, path):
            errors.append(f"{label} must be outside the read-only code root")
    template_root = template_path.parent
    for label, path in (("input", input_path), ("output", output_path), ("trace", trace_path)):
        if path is not None and _inside(template_root, path):
            errors.append(f"{label} must be outside the pristine template directory")
    if trace_path is not None and trace_path in {input_path, output_path}:
        errors.append("trace must not overwrite the semantic input or finding output")
    if output_path == input_path:
        errors.append("finding output must not overwrite the semantic input")
    if not errors:
        try:
            semantic = ac.load_json(input_path)
            if isinstance(semantic, dict):
                task_id = str(semantic.get("task_id") or "")
            finding = materialize_finding(semantic, template_path, code_root)
            ac.save_json(output_path, finding)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    report = {
        "materialized_at": ac.now_iso(),
        "passed": not errors,
        "task_id": task_id,
        "semantic_analysis_performed": False,
        "input_path": str(input_path),
        "template_path": str(template_path),
        "output_path": str(output_path),
        "errors": errors,
    }
    if trace_path:
        ac.save_json(trace_path, report)
    print(json.dumps({"passed": not errors, "task_id": task_id, "errors": len(errors)}))
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize a full finding from a minimal investigator decision.",
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--trace", default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
