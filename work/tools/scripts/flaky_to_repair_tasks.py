#!/usr/bin/env python3
"""Convert stability/flaky evidence into repair task JSONL records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shophub_goal_runner as runner


CLASSIFICATION_HINTS = {
    "ordering": ("ordering_instability", "Add deterministic ordering and avoid HashMap/HashSet iteration leakage."),
    "hashmap": ("ordering_instability", "Return stable ordered collections or explicit ORDER BY results."),
    "time": ("time_dependency", "Route core business time through a Clock/TestClock abstraction."),
    "timestamp": ("time_dependency", "Route core business time through a Clock/TestClock abstraction."),
    "static": ("state_pollution", "Remove static mutable state or reset it per testRun/business tenant."),
    "duplicate": ("state_pollution", "Check testRun scoping and unique-key isolation."),
    "transaction": ("transaction_isolation", "Narrow transaction boundaries and isolate post-commit actions."),
    "rollback": ("transaction_isolation", "Narrow transaction boundaries and isolate post-commit actions."),
    "timeout": ("resource_timeout", "Bound retries, waits, locks, and async processing."),
    "async": ("async_interference", "Make async side effects idempotent and isolated from the main transaction."),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build flaky repair tasks from stability reports.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output JSONL path.")
    return parser


def classify_finding(finding: dict[str, Any]) -> tuple[str, str]:
    text = json.dumps(finding, ensure_ascii=False).lower()
    for needle, result in CLASSIFICATION_HINTS.items():
        if needle in text:
            return result
    return "flaky", "Re-run focused matrix, identify non-deterministic state, time, ordering, or async behavior."


def build_task(finding: dict[str, Any], index: int) -> dict[str, Any]:
    category, hint = classify_finding(finding)
    class_name = finding.get("class_name", "")
    method_name = finding.get("method_name", "")
    test_id = f"{class_name}#{method_name}" if class_name or method_name else finding.get("pattern", "stability")
    return {
        "task_id": f"TASK-FLAKY-{index:03d}",
        "severity": "P1",
        "category": category,
        "summary": f"Stability gate found flaky behavior: {test_id}",
        "source_issue_ids": [finding.get("pattern", "stability")],
        "suspected_files": [],
        "related_tests": [test_id] if "#" in test_id else [],
        "acceptance_criteria": [
            "stability_runner passes all requested runs",
            "current test matrix has no intermittent method-level outcome changes",
            "no forbidden file changes",
        ],
        "negative_constraints": [
            "不得修改 design-docs/**",
            "不得修改 test-cases/**",
            "不得硬编码 public test fixture",
            "不得改变冻结 API 路径、字段名、HTTP 方法",
        ],
        "repair_hint": hint,
        "source": "stability_runner",
        "status": "open",
        "created_at": runner.now_iso(),
    }


def convert(root: Path, output: str | None = None) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    report = runner.read_json(paths.test_matrix / "stability_report.json", {})
    if not report:
        report = runner.read_json(paths.work / "stability_report.json", {})
    findings = report.get("flaky_findings", []) or []

    output_path = Path(output) if output else paths.work / "repair_tasks.jsonl"
    existing = runner.read_jsonl(output_path) if output_path.exists() else []
    existing_keys = {(item.get("category"), tuple(item.get("related_tests", [])), item.get("summary")) for item in existing}

    new_tasks: list[dict[str, Any]] = []
    for idx, finding in enumerate(findings, start=1):
        task = build_task(finding, idx)
        key = (task.get("category"), tuple(task.get("related_tests", [])), task.get("summary"))
        if key in existing_keys:
            continue
        existing_keys.add(key)
        new_tasks.append(task)

    for task in new_tasks:
        runner.append_jsonl_record(output_path, task)

    result = {
        "generated_at": runner.now_iso(),
        "stability_report_found": bool(report),
        "flaky_findings": len(findings),
        "new_tasks": len(new_tasks),
        "output": str(output_path),
    }
    runner.write_json(paths.work / "flaky_to_repair_tasks_report.json", result)
    return result


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    result = convert(root, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
