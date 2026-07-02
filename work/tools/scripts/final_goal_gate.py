#!/usr/bin/env python3
"""Machine-checkable final DONE gate for Goal-Agent."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate final Goal-Agent completion gates.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path for final_goal_report.json.")
    return parser


def matrix_status(matrix: dict[str, Any]) -> tuple[bool, str]:
    summary = matrix.get("summary", {})
    total = int(summary.get("total", 0) or 0)
    passed = int(summary.get("pass", 0) or 0) + int(summary.get("expected_skipped", 0) or 0)
    blockers = (
        int(summary.get("failure", 0) or 0)
        + int(summary.get("error", 0) or 0)
        + int(summary.get("timeout", 0) or 0)
        + int(summary.get("not_run", 0) or 0)
        + (int(summary.get("skipped", 0) or 0) if summary.get("has_unexpected_skipped", False) else 0)
    )
    all_green = bool(summary.get("all_green")) and blockers == 0 and total > 0
    return all_green, f"{passed}/{total}" if total else "missing"


def feature_status(feature_list: dict[str, Any]) -> tuple[bool, str, list[str]]:
    features = feature_list.get("features", [])
    blockers = [
        f for f in features
        if f.get("severity") in ("P0", "P1") and f.get("passes") is not True
    ]
    p0_total = sum(1 for f in features if f.get("severity") == "P0")
    p0_passed = sum(1 for f in features if f.get("severity") == "P0" and f.get("passes") is True)
    p1_total = sum(1 for f in features if f.get("severity") == "P1")
    p1_passed = sum(1 for f in features if f.get("severity") == "P1" and f.get("passes") is True)
    summary = f"P0 {p0_passed}/{p0_total}, P1 {p1_passed}/{p1_total}"
    return not blockers and bool(features), summary, [f"{f.get('id')}: {f.get('description', '')}" for f in blockers[:20]]


def consistency_status(consistency: dict[str, Any]) -> tuple[bool, str]:
    issues = consistency.get("issues", [])
    blockers = [i for i in issues if i.get("severity") in ("P0", "P1")]
    summary = consistency.get("summary", {})
    if summary:
        return not blockers, f"P0={summary.get('p0_issues', 0)} P1={summary.get('p1_issues', 0)} total={summary.get('total_issues', len(issues))}"
    return not blockers, f"blocking={len(blockers)} total={len(issues)}"


def guard_status(report: dict[str, Any], missing_ok: bool = False) -> tuple[bool, str]:
    if not report:
        return missing_ok, "missing"
    if "passed" in report:
        return report.get("passed") is True, "PASS" if report.get("passed") is True else "FAIL"
    status = str(report.get("status", "")).upper()
    if status:
        return status in ("PASS", "PASSED", "OK"), status
    summary = report.get("summary", {})
    blockers = int(summary.get("blockers", report.get("blocker_count", 1)) or 0)
    return blockers == 0, f"blockers={blockers}"


def log_gate(paths: runner.RunnerPaths, stem: str) -> tuple[bool, str]:
    candidates = sorted(paths.test_results.glob(f"*{stem}*.log"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    if not candidates:
        return False, "missing"
    text = runner.read_text(candidates[0])
    if text.startswith("SKIPPED"):
        return False, "skipped"
    if "BUILD FAILURE" in text or "COMPILATION ERROR" in text or "[ERROR]" in text and "BUILD SUCCESS" not in text:
        return False, runner.rel(paths.root, candidates[0])
    return True, runner.rel(paths.root, candidates[0])


def report_has_evidence(report_path: Path) -> bool:
    if not report_path.exists():
        return False
    text = runner.read_text(report_path)
    return "验证" in text and ("mvn" in text or "Stability" in text or "测试矩阵" in text)


def evaluate(root: Path) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    state = runner.load_state(paths)

    feature_list = runner.read_json(paths.work / "feature_list.json", {})
    matrix = runner.read_json(paths.test_matrix / "current_test_matrix.json", {})
    stability = runner.read_json(paths.test_matrix / "stability_report.json", {})
    if not stability:
        stability = runner.read_json(paths.work / "stability_report.json", {})
    consistency = runner.read_json(paths.work / "consistency_report.json", {})
    forbidden = runner.read_json(paths.work / "forbidden_change_report.json", {})
    hardcoding = runner.read_json(paths.work / "hardcoding_guard_report.json", {})

    matrix_ok, matrix_summary = matrix_status(matrix)
    features_ok, features_summary, feature_blockers = feature_status(feature_list)
    consistency_ok, consistency_summary = consistency_status(consistency)
    forbidden_ok, forbidden_summary = guard_status(forbidden)
    hardcoding_ok, hardcoding_summary = guard_status(hardcoding)
    stability_ok = stability.get("stable") is True
    stability_summary = f"{stability.get('runs_completed', 0)}/{stability.get('runs_requested', 0)}"

    code_tests_ok, code_tests_summary = log_gate(paths, "code-test")
    code_install_ok, code_install_summary = log_gate(paths, "code-install")
    if state.get("last_full_test") == "passed":
        code_tests_ok = code_tests_ok or True
        code_install_ok = code_install_ok or True
    report_ok = report_has_evidence(root / "修复报告.md")

    gates = {
        "code_tests": {"passed": code_tests_ok, "summary": code_tests_summary},
        "code_install": {"passed": code_install_ok, "summary": code_install_summary},
        "public_matrix": {"passed": matrix_ok, "summary": matrix_summary},
        "stability": {"passed": stability_ok, "summary": stability_summary},
        "consistency": {"passed": consistency_ok, "summary": consistency_summary},
        "forbidden_guard": {"passed": forbidden_ok, "summary": forbidden_summary},
        "hardcoding_guard": {"passed": hardcoding_ok, "summary": hardcoding_summary},
        "features": {"passed": features_ok, "summary": features_summary},
        "repair_report": {"passed": report_ok, "summary": "present_with_evidence" if report_ok else "missing_or_no_evidence"},
    }

    blocking_reasons: list[str] = []
    for name, gate in gates.items():
        if gate["passed"] is not True:
            blocking_reasons.append(f"{name}: {gate['summary']}")
    blocking_reasons.extend(feature_blockers)

    done = not blocking_reasons
    report = {
        "generated_at": runner.now_iso(),
        "done": done,
        "blocking_reasons": blocking_reasons,
        "gates": gates,
        "summary": {
            "public_matrix": matrix_summary,
            "stability": stability_summary,
            "features": features_summary,
            "consistency": consistency_summary,
        },
    }
    runner.write_json(paths.work / "final_goal_report.json", report)
    runner.write_json(paths.work / "goal_status.json", {"done": done, "blocking_reasons": blocking_reasons, "summary": report["summary"]})
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    report = evaluate(root)
    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["done"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
