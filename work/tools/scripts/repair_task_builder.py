#!/usr/bin/env python3
"""Repair Task Builder — deterministic generation of repair_tasks.json from all analysis outputs.

Produces ``repair_tasks.json`` and ``repair_tasks.md`` with deduplicated, prioritized
repair tasks ready for patch generation and sandbox validation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


TASK_TYPES = [
    "validation",
    "api_schema",
    "response_schema",
    "error_handling",
    "business_rule",
    "repository_query",
    "pagination",
    "sorting",
    "state_transition",
    "null_handling",
    "flaky",
    "regression",
]


P0_ISSUE_TYPES = {
    "missing_endpoint",
    "type_mismatch",
    "missing_required_field",
    "missing_field",
    "missing_request_dto",
    "missing_validation_for_required_field",
    "missing_error_handler",
    "wrong_status_code",
    "wrong_error_code",
    "exception_swallow",
    "exception_returns_200",
}

P1_ISSUE_TYPES = {
    "missing_response_field",
    "pagination_metadata_missing",
    "missing_sorting",
    "null_vs_empty_list",
    "repository_query_filter_missing",
    "missing_error_code_in_handler",
    "missing_error_code",
}

P2_ISSUE_TYPES = {
    "documentation_mismatch",
    "log_message",
    "naming_style",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build repair tasks from analysis outputs.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path for JSON (default: .agent-work/repair_tasks.json).")
    return parser


def issue_to_task_type(issue_type: str) -> str:
    """Map consistency issue type to repair task category."""
    mapping: dict[str, str] = {
        "missing_endpoint": "api_schema",
        "type_mismatch": "api_schema",
        "missing_field": "api_schema",
        "missing_request_dto": "api_schema",
        "missing_response_field": "response_schema",
        "pagination_metadata_missing": "pagination",
        "missing_error_handler": "error_handling",
        "missing_error_code": "error_handling",
        "missing_error_code_in_handler": "error_handling",
        "wrong_status_code": "error_handling",
        "wrong_error_code": "error_handling",
        "exception_swallow": "error_handling",
        "exception_returns_200": "error_handling",
        "missing_not_null_on_fk": "validation",
        "missing_not_blank": "validation",
        "missing_decimal_min_or_positive": "validation",
        "missing_positive_or_zero": "validation",
        "missing_min_or_positive_or_zero": "validation",
        "missing_email_validation": "validation",
        "MISSING_NOT_BLANK": "validation",
        "MISSING_DECIMAL_MIN_OR_POSITIVE": "validation",
        "MISSING_MIN_OR_POSITIVE_OR_ZERO": "validation",
        "MISSING_NOT_NULL_ON_FK": "validation",
        "MISSING_EMAIL_VALIDATION": "validation",
        "MISSING_FIELD": "validation",
        "repository_query_filter_missing": "repository_query",
        "missing_sorting": "sorting",
        "null_vs_empty_list": "null_handling",
    }
    return mapping.get(issue_type, "business_rule")


def task_priority(issue_type: str) -> str:
    """Determine repair task priority from issue type."""
    if issue_type in P0_ISSUE_TYPES:
        return "P0"
    if issue_type in P1_ISSUE_TYPES:
        return "P1"
    if issue_type in P2_ISSUE_TYPES:
        return "P2"
    return "P2"


def tasks_from_consistency_report(consistency: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert consistency_report issues into repair tasks."""
    tasks: list[dict[str, Any]] = []
    for issue in consistency.get("issues", []):
        task_type = issue_to_task_type(issue.get("type", ""))
        priority = task_priority(issue.get("type", ""))

        method = issue.get("method", "")
        path = issue.get("path", "")

        task: dict[str, Any] = {
            "type": task_type,
            "priority": priority,
            "source": "consistency_report",
            "source_issue": issue.get("type", ""),
            "related_api": f"{method} {path}" if method else "",
            "requirement_id": issue.get("endpoint_id", ""),
            "symptom": issue.get("detail", ""),
            "suspected_files": issue.get("suspected_files", []),
            "expected_fix": "",
            "verification_tests": [],
            "risk": "low",
        }
        tasks.append(task)
    return tasks


def tasks_from_dto_validation(root: Path, report: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert DTO validation gaps into repair tasks."""
    tasks: list[dict[str, Any]] = []
    for gap in report.get("gap_details", []):
        for gap_type in gap.get("gaps", []):
            task_type = issue_to_task_type(gap_type)
            priority = task_priority(gap_type)

            field = gap.get("field", "")
            endpoint = gap.get("endpoint", "")
            dto = gap.get("dto", "")
            expected = gap.get("expected", "")

            # Try to get real file path: gap.file > find by DTO name > placeholder
            file_path = gap.get("file", "")
            if not file_path and dto:
                file_path = find_dto_file(root, dto)
            if not file_path and dto:
                file_path = f"code/.../{dto}.java"  # last-resort placeholder

            suspected = [file_path] if file_path else []

            task: dict[str, Any] = {
                "type": task_type,
                "priority": priority,
                "source": "dto_validation_report",
                "source_issue": gap_type,
                "related_api": endpoint,
                "related_field": field,
                "related_dto": dto,
                "symptom": f"{field} field lacks validation — expected {expected}, gap: {gap_type}",
                "suspected_files": suspected,
                "expected_fix": (
                    f"Add validation annotation to {field} in {dto} "
                    f"({desired_annotation(gap_type, field)})"
                ),
                "verification_tests": [
                    f"generated:{field}_null_should_return_400",
                ],
                "risk": "low",
            }
            tasks.append(task)
    return tasks


def find_dto_file(root: Path, dto_name: str) -> str:
    """Find the real file path for a DTO class.

    Returns the relative path from root, or empty string if not found.
    """
    if not dto_name:
        return ""
    code_dir = root / "code"
    if not code_dir.exists():
        return ""
    matches = list(code_dir.rglob(f"{dto_name}.java"))
    if matches:
        return runner.rel(root, matches[0])
    return ""


def desired_annotation(gap_type: str, field_name: str) -> str:
    """Suggest the right validation annotation."""
    suggestions = {
        "MISSING_NOT_BLANK": "@NotBlank",
        "MISSING_DECIMAL_MIN_OR_POSITIVE": '@DecimalMin("0.01") or @Positive',
        "MISSING_MIN_OR_POSITIVE_OR_ZERO": "@Min(0) or @PositiveOrZero",
        "MISSING_NOT_NULL_ON_FK": "@NotNull",
        "MISSING_EMAIL_VALIDATION": "@Email",
        "MISSING_FIELD": "add missing field to DTO",
    }
    return suggestions.get(gap_type, "appropriate validation annotation")


def tasks_from_exception_coverage(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert exception coverage gaps into repair tasks."""
    tasks: list[dict[str, Any]] = []
    for gap in coverage.get("gaps", []):
        ex_type = gap.get("exception_type", "")
        task: dict[str, Any] = {
            "type": "error_handling",
            "priority": "P0",
            "source": "exception_coverage",
            "source_issue": f"missing_handler_{ex_type}",
            "related_api": "",
            "symptom": gap.get("impact", f"Missing handler for {ex_type}"),
            "suspected_files": [gap.get("handler_file", "GlobalExceptionHandler.java")],
            "expected_fix": (
                f"Add @ExceptionHandler for {ex_type} returning "
                f"{gap.get('expected_status', '400')} with code='{gap.get('expected_code', '')}'"
            ),
            "verification_tests": [],
            "risk": "low",
        }
        tasks.append(task)
    return tasks


def tasks_from_test_symptoms(symptoms_path: Path) -> list[dict[str, Any]]:
    """Convert test symptoms into repair tasks."""
    symptoms = runner.read_jsonl(symptoms_path) if symptoms_path.exists() else []
    tasks: list[dict[str, Any]] = []
    for sym in symptoms:
        failure_type = sym.get("failure_type", "test_failure")
        if failure_type == "compilation_error":
            task_type = "regression"
        elif failure_type == "timeout":
            task_type = "flaky"
        else:
            task_type = "business_rule"

        task: dict[str, Any] = {
            "type": task_type,
            "priority": "P1",
            "source": "test_symptoms",
            "source_issue": failure_type,
            "related_api": sym.get("test_name", ""),
            "symptom": sym.get("symptom", ""),
            "suspected_files": [],
            "expected_fix": f"Investigate and fix: {sym.get('symptom', '')}",
            "verification_tests": [sym.get("test_name", "")],
            "risk": "medium",
        }
        tasks.append(task)
    return tasks


def tasks_from_issue_queue(issues_path: Path) -> list[dict[str, Any]]:
    """Convert legacy issues.jsonl records into v2 repair task seeds."""
    issues = runner.read_jsonl(issues_path) if issues_path.exists() else []
    tasks: list[dict[str, Any]] = []
    for issue in issues:
        if issue.get("status", "open") not in ("open", "reopen", "queued"):
            continue
        severity = {"high": "P0", "medium": "P1", "low": "P2"}.get(str(issue.get("severity", "")).lower(), "P1")
        task_type = issue_to_task_type(issue.get("type", "business_rule"))
        tasks.append(
            {
                "type": task_type,
                "priority": severity,
                "source": "issues_jsonl",
                "source_issue": issue.get("issue_id", ""),
                "related_api": issue.get("related_api", ""),
                "symptom": issue.get("actual_behavior") or issue.get("design_behavior") or issue.get("fix_suggestion", ""),
                "suspected_files": issue.get("suspected_files", []),
                "expected_fix": issue.get("fix_suggestion", ""),
                "verification_tests": issue.get("related_tests", []),
                "risk": "medium" if severity in ("P0", "P1") else "low",
            }
        )
    return tasks


def tasks_from_public_case_rules(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert public-case semantic rules into repair task seeds."""
    tasks: list[dict[str, Any]] = []
    for rule in report.get("rules", []):
        tasks.append(
            {
                "type": rule.get("category", "business_rule"),
                "priority": rule.get("severity", "P1"),
                "source": "public_case_rules",
                "source_issue": rule.get("id", ""),
                "related_api": "",
                "symptom": rule.get("description", ""),
                "suspected_files": [],
                "expected_fix": rule.get("description", ""),
                "verification_tests": [],
                "risk": "medium",
            }
        )
    return tasks


def tasks_from_checker_reports(paths: runner.RunnerPaths) -> list[dict[str, Any]]:
    """Convert deterministic checker reports into repair task seeds."""
    tasks: list[dict[str, Any]] = []
    report_dir = paths.work / "checker_reports"
    if not report_dir.exists():
        return tasks
    for report_path in sorted(report_dir.glob("*.json")):
        report = runner.read_json(report_path, {})
        for issue in report.get("issues", []):
            tasks.append(
                {
                    "type": issue.get("type", report.get("checker", "business_rule")),
                    "priority": issue.get("severity", "P1"),
                    "source": f"checker:{report.get('checker', report_path.stem)}",
                    "source_issue": issue.get("issue_id", ""),
                    "related_api": "",
                    "symptom": issue.get("summary") or issue.get("detail", ""),
                    "suspected_files": issue.get("suspected_files", []),
                    "expected_fix": issue.get("repair_hint", ""),
                    "verification_tests": issue.get("related_tests", []),
                    "risk": "medium",
                }
            )
    return tasks


def tasks_from_feature_list(feature_list: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert failing P0/P1 features into repair task seeds."""
    tasks: list[dict[str, Any]] = []
    for feature in feature_list.get("features", []):
        if feature.get("passes") is True or feature.get("severity") not in ("P0", "P1"):
            continue
        tasks.append(
            {
                "type": feature.get("category", "business_rule"),
                "priority": feature.get("severity", "P1"),
                "source": "feature_list",
                "source_issue": feature.get("id", ""),
                "related_api": "",
                "symptom": feature.get("description", ""),
                "suspected_files": feature.get("related_files", []),
                "expected_fix": "Make this feature pass with design-backed behavior and update verification evidence.",
                "verification_tests": feature.get("related_tests", []),
                "risk": "medium",
            }
        )
    return tasks


def dedup_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate repair tasks by (related_api, field, type)."""
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    task_id = 0
    for task in tasks:
        related_api = task.get("related_api", "")
        related_field = task.get("related_field", "")
        task_type = task.get("type", "")

        dedup_key = (related_api, related_field, task_type)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        task_id += 1
        task["id"] = f"TASK-{task_id:03d}"
        task.setdefault("task_id", task["id"])
        task.setdefault("severity", task.get("priority", "P2"))
        task.setdefault("category", task.get("type", "business_rule"))
        task.setdefault("summary", task.get("symptom", ""))
        task.setdefault("source_issue_ids", [task.get("source_issue", "")] if task.get("source_issue") else [])
        task.setdefault("related_tests", task.get("verification_tests", []))
        task.setdefault(
            "acceptance_criteria",
            [
                "code/pom.xml compile pass",
                "code tests pass",
                "public test matrix has no hard regression",
                "contract checker has no new P0/P1 issue",
                "forbidden and hardcoding guards pass",
            ],
        )
        task.setdefault(
            "negative_constraints",
            [
                "不得修改 design-docs/**",
                "不得修改 test-cases/**",
                "不得硬编码 public test fixture",
                "不得改变冻结 API 路径、字段名、HTTP 方法",
            ],
        )
        task.setdefault("repair_hint", task.get("expected_fix", ""))
        task.setdefault("status", "open")
        deduped.append(task)
    return deduped


def sort_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort tasks by priority (P0 > P1 > P2)."""
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(tasks, key=lambda t: (priority_order.get(t.get("priority", "P2"), 2), t.get("id", "")))


def build_repair_tasks(root: Path) -> dict[str, Any]:
    """Build the complete repair task list from all analysis outputs."""
    paths = runner.RunnerPaths(root)

    # Load all inputs
    api_contract = runner.read_json(paths.work / "api_contract.json", {})
    business_rules = runner.read_json(paths.work / "business_rules.json", {})
    dto_report = runner.read_json(paths.work / "dto_validation_report.json", {})
    exception_coverage = runner.read_json(paths.work / "exception_coverage.json", {})
    consistency = runner.read_json(paths.work / "consistency_report.json", {})
    public_case_rules = runner.read_json(paths.work / "public_case_rules.json", {})
    feature_list = runner.read_json(paths.work / "feature_list.json", {})

    warnings: list[str] = []

    if not api_contract:
        warnings.append("api_contract.json missing or empty — repair tasks may be incomplete")
    if not consistency:
        warnings.append("consistency_report.json missing — run contract_checker.py first")
    if not dto_report:
        warnings.append("dto_validation_report.json missing — run dto_analyzer.py first")
    if not exception_coverage:
        warnings.append("exception_coverage.json missing — run exception_analyzer.py first")

    all_tasks: list[dict[str, Any]] = []

    # Gather from each source
    all_tasks.extend(tasks_from_consistency_report(consistency))
    all_tasks.extend(tasks_from_dto_validation(root, dto_report))
    all_tasks.extend(tasks_from_exception_coverage(exception_coverage))
    all_tasks.extend(tasks_from_test_symptoms(paths.work / "test_symptoms.jsonl"))
    all_tasks.extend(tasks_from_issue_queue(paths.issues))
    all_tasks.extend(tasks_from_public_case_rules(public_case_rules))
    all_tasks.extend(tasks_from_checker_reports(paths))
    all_tasks.extend(tasks_from_feature_list(feature_list))

    # Deduplicate and sort
    tasks = dedup_tasks(all_tasks)
    tasks = sort_tasks(tasks)

    # Build output
    report: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "p0_count": sum(1 for t in tasks if t["priority"] == "P0"),
            "p1_count": sum(1 for t in tasks if t["priority"] == "P1"),
            "p2_count": sum(1 for t in tasks if t["priority"] == "P2"),
            "by_type": {},
        },
        "warnings": warnings,
    }

    for task in tasks:
        ttype = task["type"]
        report["summary"]["by_type"][ttype] = report["summary"]["by_type"].get(ttype, 0) + 1

    # Persist JSON
    runner.write_json(paths.work / "repair_tasks.json", report)
    runner.append_jsonl(paths.work / "repair_tasks.jsonl", tasks)

    # Write markdown summary
    lines = [
        "# Repair Tasks",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        "## Summary",
        "",
        f"- Total tasks: {report['summary']['total']}",
        f"- P0 (critical): {report['summary']['p0_count']}",
        f"- P1 (important): {report['summary']['p1_count']}",
        f"- P2 (minor): {report['summary']['p2_count']}",
        "",
    ]

    if report["summary"]["by_type"]:
        lines.append("## By Type")
        lines.append("")
        for ttype, count in sorted(report["summary"]["by_type"].items()):
            lines.append(f"- **{ttype}**: {count}")
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- ⚠️  {w}")
        lines.append("")

    if tasks:
        lines.append("## Tasks")
        lines.append("")
        for task in tasks:
            lines.append(
                f"### [{task['priority']}] {task['id']} — {task['type']}"
            )
            lines.append(f"- **API**: {task.get('related_api', 'N/A')}")
            lines.append(f"- **Symptom**: {task.get('symptom', '')}")
            lines.append(f"- **Expected Fix**: {task.get('expected_fix', '')}")
            lines.append(f"- **Suspected Files**: {', '.join(task.get('suspected_files', [])) or 'N/A'}")
            lines.append(f"- **Risk**: {task.get('risk', 'low')}")
            lines.append("")

    runner.write_text(paths.work / "repair_tasks.md", "\n".join(lines).rstrip() + "\n")

    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = build_repair_tasks(root)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if not report.get("warnings") else 1


if __name__ == "__main__":
    raise SystemExit(main())
