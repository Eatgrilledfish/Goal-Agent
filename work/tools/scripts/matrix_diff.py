#!/usr/bin/env python3
"""Matrix Diff — compare test outcome matrices across runs.

Identifies status changes per test method:
  NEW        — test method appears for the first time.
  RESOLVED   — FAILURE/ERROR → PASS.
  REGRESSED  — PASS → FAILURE/ERROR.
  UNMASKED   — NOT_RUN/NOT_SEEN → FAILURE/ERROR (hidden failure now visible).
  MASKED     — FAILURE/ERROR → NOT_RUN (previously failing test now hidden).
  UNCHANGED  — same outcome.

Outputs:
  .agent-work/test_matrix/matrix_diff.json
    {
      "generated_at": "...",
      "previous_run_id": "...",
      "current_run_id": "...",
      "summary": { "new": 0, "resolved": 0, "regressed": 0, "unmasked": 0, ... },
      "changes": [ { class_name, method_name, previous, current, change, ... } ]
    }

Usage:
  python3 matrix_diff.py --root . --previous .agent-work/test_matrix/previous_test_matrix.json --current .agent-work/test_matrix/current_test_matrix.json
  python3 matrix_diff.py --root . --previous baseline --current current
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner
import test_outcome_collector as collector


FAILING_OUTCOMES = ("FAILURE", "ERROR", "TIMEOUT")
BLOCKING_OUTCOMES = set(FAILING_OUTCOMES) | {"SKIPPED", "NOT_RUN"}


# ---------------------------------------------------------------------------
# Change classification
# ---------------------------------------------------------------------------

def classify_change(prev_outcome: str, curr_outcome: str) -> str:
    """Classify the change between two outcomes.

    Args:
        prev_outcome: Previous outcome (PASS/FAILURE/ERROR/SKIPPED/TIMEOUT/NOT_RUN/NOT_SEEN).
        curr_outcome: Current outcome.

    Returns:
        One of: NEW, RESOLVED, REGRESSED, UNMASKED, MASKED, UNCHANGED.
    """
    if prev_outcome == "NOT_SEEN":
        return "NEW" if curr_outcome in BLOCKING_OUTCOMES else "NEW_PASSING"

    if prev_outcome in FAILING_OUTCOMES and curr_outcome in ("PASS", "EXPECTED_SKIPPED"):
        return "RESOLVED"

    if prev_outcome == "PASS" and curr_outcome in FAILING_OUTCOMES:
        return "REGRESSED"

    if prev_outcome in ("NOT_RUN", "SKIPPED", "NOT_SEEN", "") and curr_outcome in FAILING_OUTCOMES:
        return "UNMASKED"

    if prev_outcome in FAILING_OUTCOMES and curr_outcome in ("NOT_RUN", ""):
        return "MASKED"

    # Failure type change (e.g., FAILURE → ERROR or vice versa)
    if (prev_outcome in FAILING_OUTCOMES and curr_outcome in FAILING_OUTCOMES
            and prev_outcome != curr_outcome):
        return "FAILURE_TYPE_CHANGED"

    # FAILURE/ERROR → SKIPPED
    if prev_outcome in FAILING_OUTCOMES and curr_outcome == "SKIPPED":
        return "MASKED"

    return "UNCHANGED"


def is_regression(change: str) -> bool:
    """Whether the change represents a regression (things got worse)."""
    return change in ("REGRESSED", "FAILURE_TYPE_CHANGED")


def is_hard_regression(change: str, prev_outcome: str, curr_outcome: str) -> bool:
    """Whether a previously passing test now has a blocking failing outcome."""
    _ = change
    return prev_outcome == "PASS" and curr_outcome in FAILING_OUTCOMES


def is_improvement(change: str) -> bool:
    """Whether the change represents an improvement (things got better)."""
    return change in ("RESOLVED",)


def is_new_issue(change: str) -> bool:
    """Whether the change represents a new issue that needs attention."""
    return change in ("NEW", "REGRESSED", "UNMASKED", "FAILURE_TYPE_CHANGED")


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_diff(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Compute the diff between two test outcome matrices.

    Args:
        previous: The baseline or previous-run matrix.
        current: The current-run matrix.

    Returns:
        A diff report dict.
    """
    # Build lookup maps keyed by (class_name, method_name)
    prev_map: dict[tuple[str, str], dict[str, Any]] = {}
    for r in previous.get("matrix", []):
        key = (r["class_name"], r["method_name"])
        prev_map[key] = r

    curr_map: dict[tuple[str, str], dict[str, Any]] = {}
    for r in current.get("matrix", []):
        key = (r["class_name"], r["method_name"])
        curr_map[key] = r

    changes: list[dict[str, Any]] = []
    all_keys = set(prev_map.keys()) | set(curr_map.keys())

    for key in sorted(all_keys):
        class_name, method_name = key
        prev_rec = prev_map.get(key)
        curr_rec = curr_map.get(key)

        prev_outcome = prev_rec["outcome"] if prev_rec else "NOT_SEEN"
        curr_outcome = curr_rec["outcome"] if curr_rec else "NOT_SEEN"
        change = classify_change(prev_outcome, curr_outcome)

        change_record = {
            "class_name": class_name,
            "method_name": method_name,
            "suite": curr_rec.get("suite", prev_rec.get("suite", "")) if (curr_rec or prev_rec) else "",
            "previous_outcome": prev_outcome,
            "current_outcome": curr_outcome,
            "change": change,
            "is_regression": is_regression(change),
            "hard_regression": is_hard_regression(change, prev_outcome, curr_outcome),
            "is_improvement": is_improvement(change),
            "is_new_issue": is_new_issue(change),
            "previous_failure_kind": prev_rec.get("failure_kind", "") if prev_rec else "",
            "current_failure_kind": curr_rec.get("failure_kind", "") if curr_rec else "",
            "current_message": curr_rec.get("message", "") if curr_rec else "",
            "current_stack_top": curr_rec.get("stack_top", "") if curr_rec else "",
            "previous_message": prev_rec.get("message", "") if prev_rec else "",
            "previous_stack_top": prev_rec.get("stack_top", "") if prev_rec else "",
        }
        changes.append(change_record)

    # Build summary
    summary = _build_diff_summary(previous, current, changes)

    return {
        "generated_at": runner.now_iso(),
        "previous_run_id": previous.get("run_id", "unknown"),
        "current_run_id": current.get("run_id", "unknown"),
        "summary": summary,
        "changes": changes,
    }


def _build_diff_summary(
    previous: dict[str, Any],
    current: dict[str, Any],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build aggregate diff summary."""
    change_counts: dict[str, int] = {}
    for c in changes:
        ch = c["change"]
        change_counts[ch] = change_counts.get(ch, 0) + 1

    prev_summary = previous.get("summary", {})
    curr_summary = current.get("summary", {})

    return {
        "total_changes": len(changes),
        "new": change_counts.get("NEW", 0),
        "resolved": change_counts.get("RESOLVED", 0),
        "regressed": change_counts.get("REGRESSED", 0),
        "unmasked": change_counts.get("UNMASKED", 0),
        "masked": change_counts.get("MASKED", 0),
        "unchanged": change_counts.get("UNCHANGED", 0),
        "failure_type_changed": change_counts.get("FAILURE_TYPE_CHANGED", 0),
        "new_issues": sum(1 for c in changes if c["is_new_issue"]),
        "regressions": sum(1 for c in changes if c["is_regression"]),
        "hard_regressions": sum(1 for c in changes if c.get("hard_regression")),
        "improvements": sum(1 for c in changes if c["is_improvement"]),
        "previous_all_green": prev_summary.get("all_green", False),
        "current_all_green": curr_summary.get("all_green", False),
        "previous_total": prev_summary.get("total", 0),
        "current_total": curr_summary.get("total", 0),
        "net_improvement": _net_improvement(prev_summary, curr_summary),
    }


def _net_improvement(prev: dict[str, Any], curr: dict[str, Any]) -> str:
    """Qualitative assessment of whether things improved."""
    prev_issues = prev.get("failure", 0) + prev.get("error", 0)
    curr_issues = curr.get("failure", 0) + curr.get("error", 0)
    prev_not_run = prev.get("not_run", 0)
    curr_not_run = curr.get("not_run", 0)

    if curr_issues < prev_issues and curr_not_run <= prev_not_run:
        return "IMPROVED"
    elif curr_issues > prev_issues:
        return "DEGRADED"
    elif curr_not_run < prev_not_run and curr_issues == prev_issues:
        return "BETTER_COVERAGE"
    elif curr_issues == prev_issues and curr_not_run == prev_not_run:
        return "STABLE"
    return "MIXED"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def render_diff_report(diff: dict[str, Any]) -> str:
    """Render a human-readable diff report in Markdown."""
    summary = diff["summary"]
    lines = [
        "# Test Matrix Diff Report",
        "",
        f"Generated: {diff['generated_at']}",
        f"Previous run: `{diff['previous_run_id']}`",
        f"Current run:  `{diff['current_run_id']}`",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total changes | {summary['total_changes']} |",
        f"| ⭐ NEW | {summary['new']} |",
        f"| ✅ RESOLVED | {summary['resolved']} |",
        f"| 🔴 REGRESSED | {summary['regressed']} |",
        f"| 🚫 HARD REGRESSIONS | {summary.get('hard_regressions', 0)} |",
        f"| ⚠️ UNMASKED | {summary['unmasked']} |",
        f"| 🙈 MASKED | {summary['masked']} |",
        f"| ➡️ UNCHANGED | {summary['unchanged']} |",
        f"| Previous all-green | {'✅' if summary['previous_all_green'] else '❌'} |",
        f"| Current all-green | {'✅' if summary['current_all_green'] else '❌'} |",
        f"| Net assessment | **{summary['net_improvement']}** |",
        "",
    ]

    # New issues section
    new_issues = [c for c in diff.get("changes", []) if c["is_new_issue"]]
    if new_issues:
        lines.extend([
            "## 🔴 New Issues (requires attention)",
            "",
            "| Class | Method | Change | Previous | Current | Failure Kind |",
            "|-------|--------|--------|----------|---------|--------------|",
        ])
        for c in new_issues:
            lines.append(
                f"| `{c['class_name']}` | `{c['method_name']}` | "
                f"{c['change']} | {c['previous_outcome']} | {c['current_outcome']} | "
                f"{c.get('current_failure_kind', '')} |"
            )
        lines.append("")

    # Resolved section
    resolved = [c for c in diff.get("changes", []) if c["is_improvement"]]
    if resolved:
        lines.extend([
            "## ✅ Resolved Issues",
            "",
            "| Class | Method | Previous | Current |",
            "|-------|--------|----------|---------|",
        ])
        for c in resolved:
            lines.append(
                f"| `{c['class_name']}` | `{c['method_name']}` | "
                f"{c['previous_outcome']} | {c['current_outcome']} |"
            )
        lines.append("")

    # Unmasked detail
    unmasked = [c for c in diff.get("changes", []) if c["change"] == "UNMASKED"]
    if unmasked:
        lines.extend([
            "## ⚠️ Unmasked Failures (were hidden, now visible)",
            "",
        ])
        for c in unmasked:
            lines.extend([
                f"### `{c['class_name']}#{c['method_name']}`",
                "",
                f"- Previous outcome: `{c['previous_outcome']}`",
                f"- Current outcome: `{c['current_outcome']}`",
                f"- Failure kind: `{c.get('current_failure_kind', '')}`",
                f"- Message: {c.get('current_message', 'N/A')[:200]}",
                "",
                "```",
                c.get('current_stack_top', 'N/A'),
                "```",
                "",
            ])

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Resolver helpers
# ---------------------------------------------------------------------------

def resolve_matrix_path(root: Path, name: str) -> Path:
    """Resolve a matrix path from shorthand names like 'baseline' or 'current'."""
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"

    if name == "baseline":
        return matrix_dir / "baseline_test_matrix.json"
    if name == "current":
        return matrix_dir / "current_test_matrix.json"
    if name == "previous":
        return matrix_dir / "previous_test_matrix.json"

    # Treat as a direct path
    p = Path(name)
    if p.is_absolute():
        return p
    return root / p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare test outcome matrices to identify NEW/RESOLVED/REGRESSED/UNMASKED/MASKED changes."
    )
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--previous", required=True,
                        help="Previous matrix path, or 'baseline', 'previous', 'current'.")
    parser.add_argument("--current", required=True,
                        help="Current matrix path, or 'baseline', 'previous', 'current'.")
    parser.add_argument("--output", default=None,
                        help="Output path for diff JSON (default: .agent-work/test_matrix/matrix_diff.json).")
    parser.add_argument("--no-report", action="store_true",
                        help="Skip Markdown report generation.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"
    matrix_dir.mkdir(parents=True, exist_ok=True)

    # Resolve paths
    prev_path = resolve_matrix_path(root, args.previous)
    curr_path = resolve_matrix_path(root, args.current)

    if not prev_path.exists():
        print(f"ERROR: Previous matrix not found: {prev_path}", file=sys.stderr)
        return 2

    if not curr_path.exists():
        print(f"ERROR: Current matrix not found: {curr_path}", file=sys.stderr)
        return 2

    # Load and diff
    previous = runner.read_json(prev_path, {"matrix": [], "run_id": "unknown"})
    current = runner.read_json(curr_path, {"matrix": [], "run_id": "unknown"})

    diff = compute_diff(previous, current)

    # Persist
    output_path = Path(args.output) if args.output else matrix_dir / "matrix_diff.json"
    runner.write_json(output_path, diff)

    # Generate report
    if not args.no_report:
        report = render_diff_report(diff)
        report_path = matrix_dir / "matrix_diff.md"
        runner.write_text(report_path, report)
        print(f"Diff report: {report_path}")

    # Summary to stdout
    s = diff["summary"]
    print(f"Matrix Diff: {s['total_changes']} changes")
    print(f"  NEW={s['new']} RESOLVED={s['resolved']} REGRESSED={s['regressed']} "
          f"UNMASKED={s['unmasked']} MASKED={s['masked']}")
    print(f"  New issues: {s['new_issues']} | Net: {s['net_improvement']}")
    print(f"  Output: {output_path}")

    # Return non-zero only for hard regressions; unmasked issues are re-queued.
    return 1 if s.get("hard_regressions", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
