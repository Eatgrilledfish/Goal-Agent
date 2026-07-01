#!/usr/bin/env python3
"""Stability Runner — runs tests N times consecutively to detect flaky behavior.

Per DESIGN.md §17 — ensures tests pass consistently, not just once.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


FLAKY_PATTERNS = [
    ("Ordering instability", r"expected.*but was.*(?i)(?:order|sort|sequence)"),
    ("Time dependency", r"(?i)(?:timestamp|datetime|now\(\)|LocalDateTime\.now|Instant\.now)"),
    ("State pollution", r"(?i)(?:already exists|duplicate|unique.*violation)"),
    ("Null pointer intermittent", r"NullPointerException"),
    ("Concurrency issue", r"(?i)(?:concurrent|race condition|deadlock|timeout)"),
    ("Static state leak", r"(?i)(?:static.*(?:field|variable|state))"),
    ("HashMap ordering", r"HashMap.*keySet|HashMap.*values|HashMap.*entrySet"),
    ("Transaction isolation", r"(?i)(?:transaction.*isolation|rollback|lazy.*init)"),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run stability verification.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--runs", type=int, default=3, help="Number of consecutive runs (default: 3).")
    parser.add_argument("--timeout", type=int, default=900, help="Timeout per run in seconds.")
    parser.add_argument("--test-filter", default=None, help="Filter tests via -Dtest=...")
    parser.add_argument("--output", default=None, help="Output path for JSON report.")
    return parser


def run_maven_test(root: Path, timeout: int, test_filter: str | None) -> dict[str, Any]:
    """Run a single Maven test suite and return results."""
    settings = runner.find_maven_settings(root)
    args = ["-f", "test-cases/pom.xml"]
    if settings:
        args = ["-s", runner.rel(root, settings)] + args
    if test_filter:
        args.append(f"-Dtest={test_filter}")
    args.append("test")

    start_time = time.time()
    try:
        completed = subprocess.run(
            ["mvn"] + args,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
        elapsed = time.time() - start_time
        return {
            "returncode": completed.returncode,
            "elapsed_seconds": round(elapsed, 1),
            "passed": completed.returncode == 0,
            "stdout_snippet": completed.stdout[-3000:] if len(completed.stdout) > 3000 else completed.stdout,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - start_time
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "returncode": 124,
            "elapsed_seconds": round(elapsed, 1),
            "passed": False,
            "timeout": True,
            "stdout_snippet": output[-3000:] if len(output) > 3000 else output,
        }


def parse_test_results(output: str) -> dict[str, Any]:
    """Parse Maven Surefire test results from output."""
    surefire = re.findall(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)",
        output,
    )
    if surefire:
        last = surefire[-1]
        return {
            "tests_run": int(last[0]),
            "failures": int(last[1]),
            "errors": int(last[2]),
            "skipped": int(last[3]),
        }
    return {"tests_run": 0, "failures": 0, "errors": 0, "skipped": 0, "parse_error": True}


def analyze_flaky_patterns(all_outputs: list[str]) -> list[dict[str, Any]]:
    """Analyze test outputs for flaky test patterns."""
    findings: list[dict[str, Any]] = []
    combined = "\n".join(all_outputs)

    for pattern_name, pattern_regex in FLAKY_PATTERNS:
        matches = re.findall(pattern_regex, combined, re.I)
        if matches:
            findings.append({
                "pattern": pattern_name,
                "regex": pattern_regex,
                "match_count": len(matches),
                "sample_matches": list(set(matches))[:3],
            })

    return findings


def detect_intermittent_failures(run_results: list[dict[str, Any]]) -> bool:
    """Check if tests pass sometimes but not always."""
    results = [r["passed"] for r in run_results]
    return any(results) and not all(results)


def run_stability(root: Path, runs: int, timeout: int, test_filter: str | None) -> dict[str, Any]:
    """Run stability verification."""
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    run_results: list[dict[str, Any]] = []
    all_outputs: list[str] = []

    for i in range(1, runs + 1):
        result = run_maven_test(root, timeout, test_filter)
        result["run_number"] = i
        run_results.append(result)
        all_outputs.append(result.get("stdout_snippet", ""))

        # Parse test counts
        parsed = parse_test_results(result.get("stdout_snippet", ""))
        result["test_summary"] = parsed

        # Save individual run log
        log_path = paths.test_results / f"stability-run-{i:02d}.log"
        runner.write_text(log_path, result.get("stdout_snippet", ""))

    # Analyze
    all_passed = all(r["passed"] for r in run_results)
    has_intermittent = detect_intermittent_failures(run_results)
    flaky_findings = analyze_flaky_patterns(all_outputs)

    # Check for varying test counts (indicates flakiness)
    test_counts = [r.get("test_summary", {}).get("tests_run", 0) for r in run_results]
    counts_vary = len(set(test_counts)) > 1

    # Check for varying failure counts
    failure_counts = [r.get("test_summary", {}).get("failures", 0) for r in run_results]
    failures_vary = len(set(failure_counts)) > 1

    is_stable = all_passed and not has_intermittent and not counts_vary and not failures_vary

    report: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "runs_requested": runs,
        "runs_completed": len(run_results),
        "stable": is_stable,
        "all_passed": all_passed,
        "has_intermittent_failures": has_intermittent,
        "test_counts_vary": counts_vary,
        "failure_counts_vary": failures_vary,
        "flaky_findings": flaky_findings,
        "run_results": [
            {
                "run": r["run_number"],
                "passed": r["passed"],
                "returncode": r.get("returncode"),
                "elapsed_seconds": r.get("elapsed_seconds"),
                "tests_run": r.get("test_summary", {}).get("tests_run"),
                "failures": r.get("test_summary", {}).get("failures"),
                "errors": r.get("test_summary", {}).get("errors"),
                "skipped": r.get("test_summary", {}).get("skipped"),
            }
            for r in run_results
        ],
    }

    runner.write_json(paths.work / "stability_report.json", report)

    # Summary markdown
    lines = [
        "# Stability Verification Report",
        "",
        f"Generated: {runner.now_iso()}",
        f"Stable: {'✅ YES' if is_stable else '❌ NO'}",
        f"All {runs} runs passed: {'✅ YES' if all_passed else '❌ NO'}",
        "",
        "## Run Results",
        "",
        "| Run | Passed | Return | Time (s) | Tests | Failed | Errors | Skipped |",
        "|-----|--------|--------|----------|-------|--------|--------|---------|",
    ]
    for r in report["run_results"]:
        lines.append(
            f"| {r['run']} | {'✅' if r['passed'] else '❌'} | {r.get('returncode', '')} | "
            f"{r.get('elapsed_seconds', '')} | {r.get('tests_run', '')} | {r.get('failures', '')} | "
            f"{r.get('errors', '')} | {r.get('skipped', '')} |"
        )
    lines.append("")

    if has_intermittent:
        lines.append("⚠️  Intermittent failures detected — tests pass sometimes but not always.")
        lines.append("")
    if counts_vary:
        lines.append("⚠️  Test count varies between runs — possible flaky test discovery.")
        lines.append("")
    if flaky_findings:
        lines.append("## Flaky Indicators")
        lines.append("")
        for f in flaky_findings:
            lines.append(f"- **{f['pattern']}**: {f['match_count']} matches (e.g., `{f['sample_matches'][:2]}`)")
        lines.append("")

    runner.write_text(paths.work / "08_stability_report.md", "\n".join(lines).rstrip() + "\n")

    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = run_stability(root, args.runs, args.timeout, args.test_filter)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report["stable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
