#!/usr/bin/env python3
"""Deterministic fresh-context patch review scaffold."""

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review a candidate diff against a repair task.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--task-id", default="", help="Repair task id.")
    parser.add_argument("--candidate-id", default="", help="Candidate id.")
    parser.add_argument("--diff-file", default=None, help="Patch/diff file. Defaults to git diff -- code.")
    parser.add_argument("--task-file", default=None, help="Task JSON file.")
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


def review(root: Path, task_id: str, candidate_id: str, diff_text: str, task: dict[str, Any]) -> dict[str, Any]:
    correctness_risks: list[str] = []
    overfit_risks: list[str] = []
    contract_risks: list[str] = []
    regression_risks: list[str] = []

    if re.search(r"catch\s*\(\s*(?:Exception|Throwable)\s+\w+\s*\)\s*\{\s*(?:return\s+[^;]+;)?\s*\}", diff_text, re.S):
        correctness_risks.append("empty or success-returning broad catch block")
    if re.search(r"ResponseEntity\.ok|HttpStatus\.OK|status\s*\(\s*200", diff_text) and re.search(r"error|Exception|fail", diff_text, re.I):
        contract_risks.append("error path may be normalized to HTTP 200")
    if re.search(r"design-docs/|test-cases/|README\.md", diff_text):
        contract_risks.append("diff touches frozen or diagnostic-only inputs")
    if re.search(r'testRunId|phone|mobile|couponCode|orderNo|商品|测试用户', diff_text, re.I):
        overfit_risks.append("diff contains common public fixture identifiers")
    if len(re.findall(r"^\+\+\+ b/", diff_text, re.M)) > 8:
        regression_risks.append("large patch surface for a single repair task")

    hard_risks = correctness_risks + overfit_risks + contract_risks
    verdict = "APPROVE"
    if hard_risks:
        verdict = "REJECT"
    elif regression_risks:
        verdict = "REQUEST_CHANGES"

    score = max(0.0, 1.0 - 0.25 * len(hard_risks) - 0.10 * len(regression_risks))
    report = {
        "task_id": task_id or task.get("task_id", ""),
        "candidate_id": candidate_id,
        "verdict": verdict,
        "correctness_risks": correctness_risks,
        "overfit_risks": overfit_risks,
        "contract_risks": contract_risks,
        "regression_risks": regression_risks,
        "reviewer_score": round(score, 3),
        "required_followups": hard_risks + regression_risks,
        "generated_at": runner.now_iso(),
    }
    runner.append_jsonl_record(runner.RunnerPaths(root).work / "reviewer_reports.jsonl", report)
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    diff_text = runner.read_text(Path(args.diff_file)) if args.diff_file else git_diff(root)
    task = runner.read_json(Path(args.task_file), {}) if args.task_file else {}
    report = review(root, args.task_id, args.candidate_id, diff_text, task)
    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "APPROVE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
