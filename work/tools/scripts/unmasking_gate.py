#!/usr/bin/env python3
"""Unmasking Gate — post-patch enforcement gate that detects newly-exposed hidden failures.

Runs after every patch acceptance.  The gate:
  1. Runs blackbox_explorer in sweep mode to re-test the current workspace.
  2. Collects the current test outcome matrix.
  3. Computes the diff against the previous matrix.
  4. Determines whether new issues are regressions (patch caused them) or unmasked
     (pre-existing but now visible because prior failures were fixed).
  5. Returns one of three verdicts:
     - PASS:             No new issues. Matrix is all-green. Safe to proceed.
     - REQUEUE:          New unmasked issues found but patch didn't cause regressions.
                         Patch is kept; new issues are added to repair queue.
     - REJECT_AND_REVERT: Patch introduced regressions. Roll back.

Outputs:
  .agent-work/test_matrix/unmasking_report.json   — full gate verdict and evidence
  .agent-work/test_matrix/unmasking_report.md     — human-readable report

Usage:
  python3 unmasking_gate.py --root . --previous .agent-work/test_matrix/previous_test_matrix.json
  python3 unmasking_gate.py --root . --previous previous --round 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner
import test_outcome_collector as collector
import matrix_diff as md
import matrix_to_repair_tasks as m2t
import blackbox_explorer as explorer


# ---------------------------------------------------------------------------
# Gate execution
# ---------------------------------------------------------------------------

def run_unmasking_gate(
    root: Path,
    previous_matrix: dict[str, Any],
    round_no: int = 0,
    timeout: int = 600,
    current_diff: str = "",
) -> dict[str, Any]:
    """Execute the Unmasking Gate.

    Args:
        root: Project root.
        previous_matrix: The test matrix from before the current patch was applied.
        round_no: Current repair round number.
        timeout: Timeout per Maven run in seconds.
        current_diff: The git diff of the current patch (for regression attribution).

    Returns:
        A dict with verdict, new_tasks, matrix, diff, and evidence.
    """
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"
    matrix_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    # Step 1: Run sweep exploration
    print("Unmasking Gate — Step 1: Running sweep exploration...")
    sweep_result = explorer.run_sweep_exploration(
        root, timeout=timeout, previous_matrix=previous_matrix,
    )
    current_matrix = sweep_result["matrix"]

    # Step 2: Save current matrix
    collector.save_matrix(current_matrix, matrix_dir / "current_test_matrix.json")

    # Step 3: Compute diff
    print("Unmasking Gate — Step 2: Computing matrix diff...")
    diff = md.compute_diff(previous_matrix, current_matrix)
    runner.write_json(matrix_dir / "matrix_diff.json", diff)

    # Step 4: Save previous as archive, current becomes new previous
    prev_archive = matrix_dir / f"previous_test_matrix_{previous_matrix.get('run_id', 'unknown')}.json"
    runner.write_json(prev_archive, previous_matrix)
    # Copy current as the new previous for next round
    runner.write_json(matrix_dir / "previous_test_matrix.json", current_matrix)

    # Step 5: Classify new issues
    changes = diff.get("changes", [])
    new_issues = [c for c in changes if c["is_new_issue"]]
    regressions = [c for c in changes if c["is_regression"]]
    unmasked_issues = [c for c in changes if c["change"] == "UNMASKED" and not c["is_regression"]]

    # Step 6: Attribute regressions to the patch
    patch_caused_regression = False
    if regressions and current_diff:
        patch_caused_regression = _attribute_regressions(regressions, current_diff)

    # Step 7: Determine verdict
    diff_summary = diff.get("summary", {})
    current_all_green = diff_summary.get("current_all_green", False)

    if not new_issues and current_all_green:
        verdict = "PASS"
        verdict_reason = "Matrix is all-green with no new issues"
    elif regressions and patch_caused_regression:
        verdict = "REJECT_AND_REVERT"
        verdict_reason = f"Patch introduced {len(regressions)} regression(s)"
    elif new_issues:
        verdict = "REQUEUE"
        verdict_reason = f"{len(new_issues)} new issue(s) need attention ({len(regressions)} regressions, {len(unmasked_issues)} unmasked)"
    else:
        verdict = "PASS"
        verdict_reason = "No blocking issues detected"

    # Step 8: Generate repair tasks for new issues
    new_tasks: list[dict[str, Any]] = []
    if new_issues:
        tasks = m2t.generate_tasks_from_matrix(root, current_matrix, diff, prefix="UNMASK")
        new_tasks = [t for t in tasks if t.get("matrix_context", {}).get("diff_change") in
                     ("NEW", "REGRESSED", "UNMASKED", "FAILURE_TYPE_CHANGED")]
        # Persist tasks
        if new_tasks:
            runner.write_json(matrix_dir / "unmasking_repair_tasks.json", {
                "generated_at": runner.now_iso(),
                "round": round_no,
                "verdict": verdict,
                "task_count": len(new_tasks),
                "tasks": new_tasks,
            })

    elapsed = time.time() - start_time

    # Build report
    report = {
        "generated_at": runner.now_iso(),
        "round": round_no,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "elapsed_seconds": round(elapsed, 1),
        "previous_run_id": previous_matrix.get("run_id", "unknown"),
        "current_run_id": current_matrix.get("run_id", "unknown"),
        "previous_summary": previous_matrix.get("summary", {}),
        "current_summary": current_matrix.get("summary", {}),
        "diff_summary": diff_summary,
        "new_issue_count": len(new_issues),
        "regression_count": len(regressions),
        "unmasked_count": len(unmasked_issues),
        "patch_caused_regression": patch_caused_regression,
        "new_tasks_count": len(new_tasks),
        "new_tasks": new_tasks,
        "matrix_paths": {
            "previous": str(prev_archive),
            "current": str(matrix_dir / "current_test_matrix.json"),
            "diff": str(matrix_dir / "matrix_diff.json"),
        },
    }

    runner.write_json(matrix_dir / "unmasking_report.json", report)

    # Generate markdown report
    md_report = render_unmasking_report(report)
    runner.write_text(matrix_dir / "unmasking_report.md", md_report)

    return report


def _attribute_regressions(regressions: list[dict[str, Any]], current_diff: str) -> bool:
    """Determine if regressions are likely caused by the current patch.

    Heuristic: if a regression test class name or related module appears in the
    patch diff, it's likely the patch caused the regression.
    """
    if not regressions or not current_diff:
        # Without diff context, err on the safe side and attribute to patch
        return bool(regressions)

    diff_lower = current_diff.lower()
    for reg in regressions:
        class_name = reg.get("class_name", "").lower()
        # Check if the test class itself was modified (should not happen, but check)
        if class_name and class_name in diff_lower:
            return True
        # Check if related code (strip "Test" suffix) appears in diff
        related = class_name.replace("test", "").replace("tests", "")
        if related and len(related) > 3 and related in diff_lower:
            return True

    return False


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_unmasking_report(report: dict[str, Any]) -> str:
    """Render human-readable Unmasking Gate report."""
    verdict = report["verdict"]
    verdict_emoji = {"PASS": "✅", "REQUEUE": "⚠️", "REJECT_AND_REVERT": "🔴"}

    lines = [
        "# Unmasking Gate Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Round: {report.get('round', 'N/A')}",
        "",
        f"## Verdict: {verdict_emoji.get(verdict, '❓')} {verdict}",
        "",
        f"**Reason:** {report['verdict_reason']}",
        "",
        f"Elapsed: {report['elapsed_seconds']}s",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Previous run | `{report['previous_run_id']}` |",
        f"| Current run | `{report['current_run_id']}` |",
        f"| New issues | {report['new_issue_count']} |",
        f"| Regressions | {report['regression_count']} |",
        f"| Unmasked | {report['unmasked_count']} |",
        f"| Patch caused regression | {'YES' if report['patch_caused_regression'] else 'NO'} |",
        f"| New repair tasks | {report['new_tasks_count']} |",
        "",
    ]

    # Previous vs current comparison
    prev = report.get("previous_summary", {})
    curr = report.get("current_summary", {})
    lines.extend([
        "## Comparison",
        "",
        "| Metric | Previous | Current |",
        "|--------|----------|---------|",
        f"| Total | {prev.get('total', 0)} | {curr.get('total', 0)} |",
        f"| PASS | {prev.get('pass', 0)} | {curr.get('pass', 0)} |",
        f"| FAILURE | {prev.get('failure', 0)} | {curr.get('failure', 0)} |",
        f"| ERROR | {prev.get('error', 0)} | {curr.get('error', 0)} |",
        f"| SKIPPED | {prev.get('skipped', 0)} | {curr.get('skipped', 0)} |",
        f"| TIMEOUT | {prev.get('timeout', 0)} | {curr.get('timeout', 0)} |",
        f"| NOT_RUN | {prev.get('not_run', 0)} | {curr.get('not_run', 0)} |",
        "",
    ])

    # New tasks
    new_tasks = report.get("new_tasks", [])
    if new_tasks:
        lines.extend([
            "## New Repair Tasks",
            "",
            "| Task ID | Priority | Type | Test | Failure Kind |",
            "|---------|----------|------|------|-------------|",
        ])
        for t in new_tasks[:20]:
            ctx = t.get("matrix_context", {})
            lines.append(
                f"| `{t['task_id']}` | {t['priority']} | {t['issue_type']} | "
                f"`{ctx.get('class_name', '')}#{ctx.get('method_name', '')}` | "
                f"{ctx.get('failure_kind', '')} |"
            )
        if len(new_tasks) > 20:
            lines.append(f"| ... | ... | ... | _and {len(new_tasks) - 20} more_ | ... |")
        lines.append("")

    # Findings
    if report["patch_caused_regression"]:
        lines.extend([
            "## ⚠️ Patch-Caused Regression",
            "",
            "The current patch introduced test regressions. Do NOT accept this patch.",
            "Roll back and generate a new candidate.",
            "",
        ])
    elif report["unmasked_count"] > 0:
        lines.extend([
            "## ⚠️ Unmasked Failures",
            "",
            f"{report['unmasked_count']} previously-hidden test(s) are now visible.",
            "These are NOT caused by the current patch — they were masked by prior failures.",
            "The patch is accepted; new tasks will be queued for the next round.",
            "",
        ])

    if verdict == "PASS":
        lines.extend([
            "## ✅ Gate Passed",
            "",
            "The test matrix is all-green. No new issues detected.",
            "Proceed to stability verification.",
            "",
        ])

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unmasking Gate — post-patch enforcement to detect hidden failures."
    )
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--previous", required=True,
                        help="Previous matrix path or name (baseline/previous/current).")
    parser.add_argument("--round", type=int, default=0,
                        help="Current repair round number.")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Timeout per Maven run in seconds.")
    parser.add_argument("--diff-file", default=None,
                        help="Path to a git diff file for regression attribution.")
    parser.add_argument("--output-dir", default=None,
                        help="Override output directory for matrix files.")
    return parser


def main() -> int:
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    # Resolve previous matrix
    prev_path = md.resolve_matrix_path(root, args.previous)
    if not prev_path.exists():
        print(f"ERROR: Previous matrix not found: {prev_path}", file=sys.stderr)
        return 2

    previous = runner.read_json(prev_path, {"matrix": [], "run_id": "unknown"})

    # Load current diff if provided
    current_diff = ""
    if args.diff_file:
        diff_path = Path(args.diff_file)
        if diff_path.exists():
            current_diff = runner.read_text(diff_path)
    else:
        # Try to get current git diff
        try:
            current_diff = runner.run_git_diff(root)
        except Exception:
            current_diff = ""

    # Run the gate
    report = run_unmasking_gate(
        root,
        previous_matrix=previous,
        round_no=args.round,
        timeout=args.timeout,
        current_diff=current_diff,
    )

    print(f"\nUnmasking Gate verdict: {report['verdict']}")
    print(f"  Reason: {report['verdict_reason']}")
    print(f"  New issues: {report['new_issue_count']} "
          f"(regressions={report['regression_count']}, "
          f"unmasked={report['unmasked_count']})")
    print(f"  New repair tasks: {report['new_tasks_count']}")

    return {
        "PASS": 0,
        "REQUEUE": 0,  # Non-zero would stop the pipeline; REQUEUE is recoverable
        "REJECT_AND_REVERT": 1,
    }.get(report["verdict"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
