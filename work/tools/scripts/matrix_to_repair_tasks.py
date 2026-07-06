#!/usr/bin/env python3
"""Matrix to Repair Tasks — convert ERROR/SKIPPED/NOT_RUN to repair/diagnosis tasks.

Takes a test outcome matrix and matrix_diff as input, and produces structured
repair tasks that can be merged into the issue queue.

Task types produced:
  - test_failure:   FAILURE outcome → repair task (needs design evidence)
  - test_error:     ERROR outcome → repair task (often infrastructure/config)
  - test_skipped:   SKIPPED outcome → diagnosis task (was it expected?)
  - test_not_run:   NOT_RUN outcome → diagnosis task (what masked it?)
  - test_regression: REGRESSED change → urgent repair task
  - test_unmasked:  UNMASKED change → repair task for newly visible failure

Output:
  .agent-work/test_matrix/matrix_repair_tasks.json  — structured task list

Usage:
  python3 matrix_to_repair_tasks.py --root . --matrix .agent-work/test_matrix/current_test_matrix.json
  python3 matrix_to_repair_tasks.py --root . --matrix current --diff .agent-work/test_matrix/matrix_diff.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_mode
import shophub_goal_runner as runner


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_tasks_from_matrix(
    root: Path,
    matrix: dict[str, Any],
    diff: dict[str, Any] | None = None,
    prefix: str = "MATRIX",
    public_can_feed_requirements: bool | None = None,
) -> list[dict[str, Any]]:
    """Generate repair/diagnosis tasks from a test outcome matrix.

    Args:
        root: Project root.
        matrix: The test outcome matrix dict.
        diff: Optional matrix diff for enriched context.
        prefix: Task ID prefix.

    Returns:
        List of repair task dicts following the unified schema.
    """
    if public_can_feed_requirements is None:
        public_can_feed_requirements = pipeline_mode.allows_public_derived_requirements()
    if not public_can_feed_requirements:
        return []

    tasks: list[dict[str, Any]] = []
    records = matrix.get("matrix", [])
    run_id = matrix.get("run_id", "unknown")

    # Build diff lookup if available
    diff_map: dict[tuple[str, str], dict[str, Any]] = {}
    if diff:
        for c in diff.get("changes", []):
            key = (c["class_name"], c["method_name"])
            diff_map[key] = c

    counter = 0
    for record in records:
        outcome = record.get("outcome", "")
        class_name = record.get("class_name", "")
        method_name = record.get("method_name", "")
        key = (class_name, method_name)

        task = _make_task(record, outcome, class_name, method_name, run_id,
                          diff_map.get(key), counter, prefix)
        if task:
            counter += 1
            tasks.append(task)

    return tasks


def _make_task(
    record: dict[str, Any],
    outcome: str,
    class_name: str,
    method_name: str,
    run_id: str,
    diff_change: dict[str, Any] | None,
    counter: int,
    prefix: str,
) -> dict[str, Any] | None:
    """Create a single task from a test record. Returns None for PASS records."""

    if outcome == "PASS":
        return None

    # Determine priority, type, and task category
    if outcome == "FAILURE":
        priority = "P0"
        issue_type = "test_failure"
        task_category = "repair"
    elif outcome == "ERROR":
        priority = "P0"
        issue_type = "test_error"
        task_category = "repair"
    elif outcome == "TIMEOUT":
        priority = "P0"
        issue_type = "test_timeout"
        task_category = "repair"
    elif outcome == "SKIPPED":
        priority = "P1"
        issue_type = "test_skipped"
        task_category = "diagnosis"
    elif outcome == "NOT_RUN":
        priority = "P1"
        issue_type = "test_not_run"
        task_category = "diagnosis"
    else:
        return None

    # Enrich with diff context
    change_label = diff_change.get("change", "") if diff_change else ""
    if change_label in ("REGRESSED",):
        priority = "P0"
        task_category = "repair"

    task_id = f"TASK-{prefix}-{issue_type.upper()}-{counter + 1:03d}"

    failure_kind = record.get("failure_kind", "")
    message = record.get("message", "")
    stack_top = record.get("stack_top", "")

    return {
        "task_id": task_id,
        "source": "blackbox_matrix",
        "priority": priority,
        "issue_type": issue_type,
        "status": "open",
        "task_category": task_category,
        "evidence": {
            "design_rule_ids": [],
            "api_ids": [],
            "test_cases": [f"{class_name}#{method_name}"],
            "xml": [record.get("source_xml", "")],
            "run_id": run_id,
        },
        "localization": {
            "endpoint": record.get("related_endpoint", ""),
            "controller": "",
            "service": "",
            "repository": "",
            "module": record.get("related_module", ""),
            "confidence": 0.5,
        },
        "observed_behavior": _format_symptom(outcome, failure_kind, message, stack_top),
        "expected_behavior": f"Test `{class_name}#{method_name}` should pass or be accounted for",
        "repair_strategy": _default_strategy(outcome, failure_kind),
        "regression_risk": "high" if change_label == "REGRESSED" else "medium",
        "matrix_context": {
            "class_name": class_name,
            "method_name": method_name,
            "outcome": outcome,
            "failure_kind": failure_kind,
            "message": message[:300] if message else "",
            "stack_top": stack_top[:300] if stack_top else "",
            "diff_change": change_label,
            "suite": record.get("suite", ""),
        },
    }


def _format_symptom(outcome: str, failure_kind: str, message: str, stack_top: str) -> str:
    """Format observed behavior for the task."""
    parts = [f"Test outcome: {outcome}"]
    if failure_kind:
        parts.append(f"Failure kind: {failure_kind}")
    if message:
        parts.append(f"Message: {message[:200]}")
    if stack_top:
        parts.append(f"Stack: {stack_top[:200]}")
    return "; ".join(parts)


def _default_strategy(outcome: str, failure_kind: str) -> str:
    """Suggest a default repair strategy based on outcome and failure kind."""
    if outcome == "ERROR":
        if "ApplicationContext" in failure_kind:
            return "Fix Spring application context or bean wiring issue"
        if "NullPointer" in failure_kind:
            return "Add null-safety checks in service or controller layer"
        if "SQL" in failure_kind:
            return "Fix database schema, constraints, or repository query"
        if "Serialization" in failure_kind or "Json" in failure_kind:
            return "Fix JSON serialization/deserialization in DTO or controller"
        return "Diagnose and fix infrastructure or configuration issue causing test ERROR"
    if outcome == "FAILURE":
        if "AssertionError" in failure_kind:
            return "Align business logic with design specification"
        return "Fix implementation to match expected behavior"
    if outcome == "NOT_RUN":
        return "Run the test independently to determine actual status; check for masking by prior failures"
    if outcome == "SKIPPED":
        return "Determine if skip is expected; if not, fix the skip condition"
    if outcome == "TIMEOUT":
        return "Investigate performance bottleneck or deadlock causing timeout"
    return "Diagnose and repair"


def matrix_task_to_legacy_issue(task: dict[str, Any]) -> dict[str, Any]:
    """Convert a matrix-generated task into the legacy issues.jsonl schema."""
    ctx = task.get("matrix_context", {})
    evidence = task.get("evidence", {})
    class_name = ctx.get("class_name", "")
    method_name = ctx.get("method_name", "")
    outcome = ctx.get("outcome", "")
    failure_kind = ctx.get("failure_kind", "")
    stack_top = ctx.get("stack_top", "")
    message = ctx.get("message", "")
    issue_type = task.get("issue_type", "test_matrix_issue")

    raw_id = task.get("task_id") or f"{class_name}-{method_name}-{outcome}"
    issue_id = str(raw_id).replace("TASK-", "")

    priority = task.get("priority", "P1")
    if priority == "P0":
        severity = "high"
    elif priority == "P1":
        severity = "medium"
    else:
        severity = "low"

    module = task.get("localization", {}).get("module") or ctx.get("suite", "blackbox-public")

    return {
        "issue_id": issue_id,
        "type": issue_type,
        "severity": severity,
        "module": module or "unknown",
        "confidence": 0.60,
        "estimated_fix_effort": "medium",
        "status": "open",
        "design_basis": (
            "Public black-box test matrix symptom. This is not design evidence by itself; "
            "patch-agent must localize the related endpoint/controller/service and cite "
            "design-docs or API baseline before editing."
        ),
        "code_location": "code/<to_be_localized>#<to_be_localized>",
        "design_behavior": task.get(
            "expected_behavior",
            f"Test `{class_name}#{method_name}` should pass according to design/API contract.",
        ),
        "actual_behavior": task.get(
            "observed_behavior",
            f"Test `{class_name}#{method_name}` outcome={outcome}, failure_kind={failure_kind}, "
            f"message={message}, stack_top={stack_top}",
        ),
        "fix_suggestion": task.get(
            "repair_strategy",
            "Run the test independently, map it to endpoint/controller/service/repository, "
            "then fix implementation against design evidence.",
        ),
        "api_impact": "unknown; must be checked by API guardian before patch acceptance",
        "evidence": evidence,
        "matrix_context": ctx,
        "source": "test_outcome_matrix",
        "is_design_evidence": False,
    }


def generate_legacy_issues_from_matrix(
    root: Path,
    matrix: dict[str, Any],
    diff: dict[str, Any] | None = None,
    prefix: str = "MATRIX",
    public_can_feed_requirements: bool | None = None,
) -> list[dict[str, Any]]:
    tasks = generate_tasks_from_matrix(root, matrix, diff, prefix, public_can_feed_requirements)
    return [matrix_task_to_legacy_issue(task) for task in tasks]


def public_diagnostics_from_matrix(matrix: dict[str, Any], diff: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    diff_lookup: dict[tuple[str, str], str] = {}
    if diff:
        for change in diff.get("changes", []):
            diff_lookup[(change.get("class_name", ""), change.get("method_name", ""))] = change.get("change", "")
    diagnostics: list[dict[str, Any]] = []
    for record in matrix.get("matrix", []):
        if record.get("outcome") in ("PASS", "EXPECTED_SKIPPED"):
            continue
        class_name = record.get("class_name", "")
        method_name = record.get("method_name", "")
        diagnostics.append(
            {
                "test_id": f"{class_name}#{method_name}",
                "outcome": record.get("outcome", ""),
                "message": record.get("message", ""),
                "source_file": record.get("source_file", ""),
                "diff_change": diff_lookup.get((class_name, method_name), ""),
                "status": "diagnostic_only_unmapped",
                "required_next_step": "map to README/design-docs/API contract before creating a repair issue",
            }
        )
    return diagnostics


# ---------------------------------------------------------------------------
# Merge with existing issue queue
# ---------------------------------------------------------------------------

def merge_into_queue(root: Path, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge matrix-generated legacy issues into the existing issue queue.

    Returns the list of newly-added issues.
    """
    paths = runner.RunnerPaths(root)
    existing = runner.read_jsonl(paths.issues)

    existing_ids = {issue.get("issue_id") for issue in existing}
    existing_keys = {
        (
            issue.get("matrix_context", {}).get("class_name"),
            issue.get("matrix_context", {}).get("method_name"),
            issue.get("type"),
        )
        for issue in existing
    }

    new_issues: list[dict[str, Any]] = []
    for issue in issues:
        key = (
            issue.get("matrix_context", {}).get("class_name"),
            issue.get("matrix_context", {}).get("method_name"),
            issue.get("type"),
        )
        if issue.get("issue_id") in existing_ids or key in existing_keys:
            continue
        runner.append_jsonl_record(paths.issues, issue)
        new_issues.append(issue)
        existing_ids.add(issue.get("issue_id"))
        existing_keys.add(key)

    return new_issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert test matrix outcomes into repair/diagnosis tasks."
    )
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--matrix", default="current",
                        help="Matrix path or name (baseline/current/previous, or file path).")
    parser.add_argument("--diff", default=None,
                        help="Matrix diff path or name (for enriched context).")
    parser.add_argument("--output", default=None,
                        help="Output path for task JSON (default: .agent-work/test_matrix/matrix_repair_tasks.json).")
    parser.add_argument("--merge", action="store_true",
                        help="Merge generated tasks into the issue queue.")
    parser.add_argument("--prefix", default="MATRIX",
                        help="Task ID prefix (default: MATRIX).")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"
    matrix_dir.mkdir(parents=True, exist_ok=True)

    # Resolve matrix path
    import matrix_diff as md
    matrix_path = md.resolve_matrix_path(root, args.matrix)
    if not matrix_path.exists():
        print(f"ERROR: Matrix not found: {matrix_path}", file=sys.stderr)
        return 2

    matrix = runner.read_json(matrix_path, {"matrix": [], "run_id": "unknown"})

    # Load diff if provided
    diff = None
    if args.diff:
        diff_path = md.resolve_matrix_path(root, args.diff)
        if diff_path.exists():
            diff = runner.read_json(diff_path, {})

    public_can_feed_requirements = pipeline_mode.allows_public_derived_requirements()
    # Generate legacy issues for downstream issue queue compatibility only in
    # local-public-debug mode. In competition-final, matrix output is diagnostic.
    issues = generate_legacy_issues_from_matrix(
        root,
        matrix,
        diff,
        prefix=args.prefix,
        public_can_feed_requirements=public_can_feed_requirements,
    )

    if not public_can_feed_requirements:
        diagnostics = public_diagnostics_from_matrix(matrix, diff)
        output_path = Path(args.output) if args.output else matrix_dir / "matrix_public_diagnostics.json"
        runner.write_json(
            output_path,
            {
                "generated_at": runner.now_iso(),
                "mode": pipeline_mode.current_mode(),
                "source_matrix": str(matrix_path),
                "diagnostic_count": len(diagnostics),
                "diagnostics": diagnostics,
                "policy": "public matrix symptoms are not repair issues until mapped to README/design-docs/API contract",
            },
        )
        if args.merge:
            print("competition-final: --merge ignored; public matrix is diagnostic-only.")
        print(f"Wrote {len(diagnostics)} public diagnostic record(s): {output_path}")
        return 0

    if not issues:
        print("No repair tasks generated — matrix is all green or has no actionable issues.")
        return 0

    # Persist
    output_path = Path(args.output) if args.output else matrix_dir / "matrix_repair_tasks.json"
    runner.write_json(output_path, {
        "generated_at": runner.now_iso(),
        "source_matrix": str(matrix_path),
        "task_count": len(issues),
        "tasks": issues,
    })

    # Merge into issue queue
    new_count = 0
    if args.merge:
        new_issues = merge_into_queue(root, issues)
        new_count = len(new_issues)

    # Summary
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for t in issues:
        by_type[t["type"]] = by_type.get(t["type"], 0) + 1
        by_priority[t["severity"]] = by_priority.get(t["severity"], 0) + 1

    print(f"Generated {len(issues)} repair issues:")
    print(f"  By type: {dict(by_type)}")
    print(f"  By priority: {dict(by_priority)}")
    if args.merge:
        print(f"  Merged into issue queue: {new_count} new (deduped)")
    print(f"  Output: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
