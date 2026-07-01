#!/usr/bin/env python3
"""Forbidden Change Guard — deterministic checks that prevent illegal modifications.

Checks git diff against forbidden paths and detects prohibited code patterns.
Per DESIGN.md §16 — does not depend on LLM judgment.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


FORBIDDEN_PATHS = [
    "design-docs/",
    "test-cases/",
    "README.md",
]

FORBIDDEN_CODE_PATTERNS = [
    # Hardcoded test fixture values
    (r'if\s*\(\s*\w+\.equals\s*\(\s*"(?:test|测试|公开|public)', "HARDCODED_TEST_VALUE"),
    (r'if\s*\(\s*\w+\s*==\s*1\s*\)', "HARDCODED_ID_CHECK"),
    # Exception swallowing
    (r'catch\s*\(\s*Exception\s+\w+\s*\)\s*\{[^}]*return\s+(?:success|ok|true)', "EXCEPTION_SWALLOW_WITH_SUCCESS"),
    (r'catch\s*\(\s*Exception\s+\w+\s*\)\s*\{[^}]*log', "EXCEPTION_SWALLOW_WITH_LOG_ONLY"),
    # Uniform 200 response for errors
    (r'return\s+(?:new\s+)?ResponseEntity\s*(?:<[^>]*>)?\s*\.ok\s*\(', "UNIFORM_200_FOR_ERRORS"),
    # Commented-out validation
    (r'//\s*@Valid', "COMMENTED_OUT_VALID"),
    (r'//\s*@NotNull', "COMMENTED_OUT_NOT_NULL"),
    (r'//\s*@NotBlank', "COMMENTED_OUT_NOT_BLANK"),
    (r'//\s*@DecimalMin', "COMMENTED_OUT_DECIMAL_MIN"),
    (r'/\*.*@Valid.*\*/', "COMMENTED_OUT_VALID_BLOCK"),
    # Commented-out logic
    (r'//\s*(?:TODO|FIXME|HACK).*(?:remove|delete|skip|disable|bypass)', "DANGEROUS_TODO"),
    # Hardcoded magic numbers in validation bypass
    (r'if\s*\(\s*\w+Id\s*==\s*0\s*\)\s*return', "MAGIC_ID_BYPASS"),
]

FORBIDDEN_ANNOTATION_REMOVALS = [
    "@Valid",
    "@NotNull",
    "@NotBlank",
    "@NotEmpty",
    "@Size",
    "@Min",
    "@Max",
    "@DecimalMin",
    "@DecimalMax",
    "@Positive",
    "@PositiveOrZero",
    "@Transactional",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check for forbidden changes.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--output", default=None, help="Output path for JSON report.")
    return parser


def get_modified_files(root: Path) -> list[str]:
    """Get list of changed files from git diff."""
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        staged = completed.stdout.strip().split("\n")

        completed = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        unstaged = completed.stdout.strip().split("\n")

        # Also check untracked
        completed = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        untracked = completed.stdout.strip().split("\n")

        all_files = [f for f in staged + unstaged + untracked if f]
        return sorted(set(all_files))
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"ERROR: git commands failed: {exc}"]


def get_diff_content(root: Path) -> str:
    """Get full diff content."""
    try:
        completed = subprocess.run(
            ["git", "diff", "--", "code"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=60,
        )
        return completed.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def check_forbidden_paths(modified_files: list[str]) -> list[dict[str, Any]]:
    """Check if any modified file touches a forbidden path."""
    violations: list[dict[str, Any]] = []
    for file_path in modified_files:
        for forbidden in FORBIDDEN_PATHS:
            if file_path.startswith(forbidden):
                violations.append({
                    "type": "FORBIDDEN_PATH",
                    "severity": "BLOCKER",
                    "file": file_path,
                    "forbidden_area": forbidden,
                    "detail": f"Cannot modify files under '{forbidden}'",
                })
    return violations


def check_forbidden_code_patterns(root: Path, diff: str) -> list[dict[str, Any]]:
    """Check diff for prohibited code patterns."""
    violations: list[dict[str, Any]] = []
    if not diff:
        return violations

    # Only check added lines (starting with +)
    added_lines = [line[1:] for line in diff.split("\n") if line.startswith("+") and not line.startswith("+++")]

    for line in added_lines:
        for pattern, violation_type in FORBIDDEN_CODE_PATTERNS:
            if re.search(pattern, line, re.I):
                violations.append({
                    "type": violation_type,
                    "severity": "BLOCKER",
                    "pattern": pattern,
                    "matched_line": line.strip()[:120],
                    "detail": f"Forbidden code pattern detected: {violation_type}",
                })

    return violations


def check_annotation_removals(root: Path, diff: str) -> list[dict[str, Any]]:
    """Check if any validation annotations were removed."""
    violations: list[dict[str, Any]] = []
    if not diff:
        return violations

    # Check removed lines (starting with -)
    removed_lines = [line[1:] for line in diff.split("\n") if line.startswith("-") and not line.startswith("---")]

    for line in removed_lines:
        for annotation in FORBIDDEN_ANNOTATION_REMOVALS:
            if annotation in line.strip():
                violations.append({
                    "type": "ANNOTATION_REMOVAL",
                    "severity": "BLOCKER",
                    "annotation": annotation,
                    "removed_line": line.strip()[:120],
                    "detail": f"Validation annotation '{annotation}' was removed — this may disable validation",
                })

    return violations


def check_structural_safety(root: Path) -> list[dict[str, Any]]:
    """Check for structural safety issues in modified code files."""
    violations: list[dict[str, Any]] = []
    code_dir = root / "code"
    if not code_dir.exists():
        return violations

    modified_files = get_modified_files(root)
    java_modified = [f for f in modified_files if f.endswith(".java") and f.startswith("code/")]

    for file_path in java_modified:
        full_path = root / file_path
        if not full_path.exists():
            continue
        text = runner.read_text(full_path)

        # Check for services without @Transactional on write methods
        if "Service" in file_path and "Impl" in file_path:
            has_transactional = "@Transactional" in text
            has_write_method = bool(re.search(
                r"\b(?:save|update|delete|remove|create|insert)\s*\(", text
            ))
            if has_write_method and not has_transactional:
                violations.append({
                    "type": "MISSING_TRANSACTIONAL",
                    "severity": "WARNING",
                    "file": file_path,
                    "detail": "Service has write methods but no @Transactional — may cause data inconsistency",
                })

    return violations


def run_guard(root: Path, strict: bool = False) -> dict[str, Any]:
    """Run all forbidden change checks."""
    paths = runner.RunnerPaths(root)

    modified_files = get_modified_files(root)
    diff = get_diff_content(root)

    all_violations: list[dict[str, Any]] = []

    # Check forbidden paths
    all_violations.extend(check_forbidden_paths(modified_files))

    # Check forbidden code patterns
    all_violations.extend(check_forbidden_code_patterns(root, diff))

    # Check annotation removals
    all_violations.extend(check_annotation_removals(root, diff))

    # Check structural safety
    all_violations.extend(check_structural_safety(root))

    # Classify
    blockers = [v for v in all_violations if v["severity"] == "BLOCKER"]
    warnings = [v for v in all_violations if v["severity"] == "WARNING"]

    passed = len(blockers) == 0
    if strict:
        passed = passed and len(warnings) == 0

    report: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "passed": passed,
        "strict_mode": strict,
        "modified_files": modified_files,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "total_violations": len(all_violations),
            "blockers": len(blockers),
            "warnings": len(warnings),
            "modified_file_count": len(modified_files),
        },
    }

    runner.write_json(paths.work / "forbidden_change_report.json", report)

    # Summary markdown
    lines = [
        "# Forbidden Change Guard Report",
        "",
        f"Generated: {runner.now_iso()}",
        f"Passed: {'✅ YES' if passed else '❌ NO'}",
        f"Strict mode: {'ON' if strict else 'OFF'}",
        "",
        f"## Summary",
        f"- Modified files: {len(modified_files)}",
        f"- Blockers: {len(blockers)}",
        f"- Warnings: {len(warnings)}",
        "",
    ]
    if modified_files:
        lines.append("## Modified Files")
        lines.append("")
        for f in modified_files:
            lines.append(f"- `{f}`")
        lines.append("")
    if blockers:
        lines.append("## Blockers (must fix)")
        lines.append("")
        for b in blockers:
            lines.append(f"- [{b['type']}] `{b.get('file', b.get('matched_line', ''))}` — {b['detail']}")
        lines.append("")
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- [{w['type']}] `{w.get('file', '')}` — {w.get('detail', '')}")
        lines.append("")

    runner.write_text(paths.work / "07_forbidden_change_report.md", "\n".join(lines).rstrip() + "\n")

    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = run_guard(root, strict=args.strict)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
