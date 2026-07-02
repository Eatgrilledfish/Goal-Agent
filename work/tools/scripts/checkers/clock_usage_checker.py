#!/usr/bin/env python3
"""Static checker for direct system clock usage in core business code."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shophub_goal_runner as runner


TIME_PATTERNS = [
    r"LocalDateTime\.now\s*\(",
    r"Instant\.now\s*\(",
    r"LocalDate\.now\s*\(",
    r"new\s+Date\s*\(",
    r"System\.currentTimeMillis\s*\(",
]
ALLOWED_HINTS = ("ClockProvider", "TestClock", "SystemClock", "TimeProvider", "ClockService")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check direct clock usage.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def check(root: Path) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    report_dir = paths.work / "checker_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    issues: list[dict[str, Any]] = []
    code_dir = root / "code"
    if code_dir.exists():
        for path in sorted(code_dir.rglob("*.java")):
            rel_path = runner.rel(root, path)
            normalized = path.as_posix()
            if "/src/test/" in normalized or any(hint in path.name for hint in ALLOWED_HINTS):
                continue
            text = runner.strip_java_comments(runner.read_text(path))
            if any(hint in text for hint in ALLOWED_HINTS):
                continue
            matched = [pattern for pattern in TIME_PATTERNS if re.search(pattern, text)]
            if matched:
                issues.append({
                    "issue_id": f"ISSUE-CLOCK-{runner.stable_hash(rel_path).upper()}",
                    "severity": "P1",
                    "type": "clock",
                    "summary": "Core business code directly reads system time",
                    "suspected_files": [rel_path],
                    "evidence": ", ".join(matched),
                    "repair_hint": "Inject or call the project clock abstraction so tests can control business time.",
                })
    report = {
        "generated_at": runner.now_iso(),
        "checker": "clock",
        "issues": issues,
        "summary": {"total": len(issues), "p1": sum(1 for i in issues if i["severity"] == "P1")},
    }
    runner.write_json(report_dir / "clock_usage_checker.json", report)
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    report = check(root)
    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
