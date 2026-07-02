#!/usr/bin/env python3
"""Detect likely public-test fixture hardcoding in patch diffs."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shophub_goal_runner as runner


ALLOW_NUMBERS = {"0", "1", "2", "10", "100", "200", "201", "204", "400", "401", "403", "404", "409", "422", "500"}
ALLOW_STRINGS = {"OK", "SUCCESS", "FAIL", "ERROR", "true", "false", "null"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check patch diff for hardcoded public-test fixtures.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--diff-file", default=None, help="Diff file. Defaults to git diff -- code.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def git_diff(root: Path) -> str:
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


def literals_from_java(text: str) -> set[str]:
    strings = {m.group(1) for m in re.finditer(r'"([^"\n]{2,})"', text)}
    numbers = {m.group(0) for m in re.finditer(r"\b\d{2,}\b", text)}
    return strings | numbers


def load_literals(root: Path, patterns: list[str]) -> set[str]:
    values: set[str] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                values.update(literals_from_java(runner.read_text(path)))
    return values


def allowed_literals(root: Path) -> set[str]:
    values = set(ALLOW_STRINGS) | set(ALLOW_NUMBERS)
    doc_paths = [root / "README.md"]
    if (root / "design-docs").exists():
        doc_paths.extend(sorted((root / "design-docs").rglob("*.md")))
    for path in doc_paths:
        if path.exists():
            text = runner.read_text(path)
            values.update(literals_from_java(text))
            values.update(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", text))
            values.update(re.findall(r"/api/v1/[A-Za-z0-9_./{}-]+", text))
    return values


def added_literals(diff: str) -> set[str]:
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    return literals_from_java(added)


def check(root: Path, diff_text: str) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    test_literals = load_literals(root, ["test-cases/**/*.java"])
    allowed = allowed_literals(root)
    added = added_literals(diff_text)
    suspicious = sorted(
        value for value in added
        if value in test_literals and value not in allowed and not value.startswith("/api/")
    )
    risks = [
        {
            "literal": value,
            "reason": "literal appears in public test sources but not in README/design-docs/API allowlist",
        }
        for value in suspicious
    ]
    report = {
        "generated_at": runner.now_iso(),
        "passed": not risks,
        "risk_count": len(risks),
        "risks": risks,
        "summary": {"blockers": len(risks), "added_literals": len(added), "public_test_literals": len(test_literals)},
    }
    runner.write_json(paths.work / "hardcoding_guard_report.json", report)
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    diff_text = runner.read_text(Path(args.diff_file)) if args.diff_file else git_diff(root)
    report = check(root, diff_text)
    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
