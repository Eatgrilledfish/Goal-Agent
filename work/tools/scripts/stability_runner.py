#!/usr/bin/env python3
"""Stability Runner — runs tests N times consecutively to detect flaky behavior.

Ensures verification commands pass consistently, not just once.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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
    parser.add_argument("--mode", choices=["public-only", "full-gate"], default="full-gate",
                       help="Verification mode (default: full-gate).")
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


def run_maven_command(root: Path, args: list[str], timeout: int) -> dict[str, Any]:
    """Run a single Maven command and return results."""
    settings = runner.find_maven_settings(root)
    cmd = ["mvn"]
    if settings:
        cmd.extend(["-s", runner.rel(root, settings)])
    cmd.extend(args)

    start_time = time.time()
    try:
        completed = subprocess.run(
            cmd, cwd=root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=timeout,
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


def run_script(root: Path, script_name: str, extra_args: list[str] | None = None,
               timeout: int = 120) -> dict[str, Any]:
    """Run a Python script and return result."""
    script_path = root / "work" / "tools" / "scripts" / script_name
    cmd = [sys.executable, str(script_path), "--root", str(root)]
    if extra_args:
        cmd.extend(extra_args)

    start_time = time.time()
    try:
        completed = subprocess.run(
            cmd, cwd=root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=timeout,
        )
        elapsed = time.time() - start_time
        return {
            "returncode": completed.returncode,
            "elapsed_seconds": round(elapsed, 1),
            "passed": completed.returncode == 0,
        }
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        elapsed = time.time() - start_time
        return {
            "returncode": 1,
            "elapsed_seconds": round(elapsed, 1),
            "passed": False,
        }


def run_generated_tests(root: Path, timeout: int) -> dict[str, Any]:
    """Copy and execute compilable generated tests against the current code.

    Reads .agent-work/generated_tests_manifest.json, copies compilable tests
    into code/src/test/java/generated/, and runs them via Maven.
    """
    manifest_path = root / ".agent-work" / "generated_tests_manifest.json"
    if not manifest_path.exists():
        return {
            "generated_tests": "NONE",
            "generated_test_summary": {"reason": "No generated_tests_manifest.json"},
        }

    manifest = runner.read_json(manifest_path, {})
    test_classes = manifest.get("test_classes", [])
    compilable = [tc for tc in test_classes if tc.get("compilable") in (True, None)]

    if not compilable:
        return {
            "generated_tests": "NONE",
            "generated_test_summary": {"reason": "No compilable generated tests"},
        }

    # Copy generated test files into code module
    dest_dir = root / "code" / "src" / "test" / "java" / "generated"
    dest_dir.mkdir(parents=True, exist_ok=True)
    class_names: list[str] = []

    for tc in compilable:
        cls_name = tc.get("test_class", "")
        source_file = tc.get("file", "")
        if not cls_name or not source_file:
            continue
        source_path = Path(source_file)
        if not source_path.exists():
            continue
        try:
            shutil.copy2(source_path, dest_dir / f"{cls_name}.java")
            class_names.append(cls_name)
        except (OSError, Exception):
            continue

    if not class_names:
        return {
            "generated_tests": "NONE",
            "generated_test_summary": {"reason": "Could not copy generated test files"},
        }

    # Run generated tests
    test_filter = ",".join(class_names)
    try:
        settings = runner.find_maven_settings(root)
        cmd = ["mvn"]
        if settings:
            cmd.extend(["-s", runner.rel(root, settings)])
        cmd.extend(["-f", "code/pom.xml", f"-Dtest={test_filter}", "test"])

        start_time = time.time()
        completed = subprocess.run(
            cmd, cwd=root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=max(timeout, 900),
        )
        elapsed = time.time() - start_time

        output = completed.stdout if completed.stdout else ""
        surefire_match = re.findall(
            r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)",
            output,
        )
        if surefire_match:
            last = surefire_match[-1]
            total = int(last[0])
            failures = int(last[1])
            errors = int(last[2])
            skipped = int(last[3])
            passed_count = total - failures - errors
            pass_rate = passed_count / total if total > 0 else 0.0
            return {
                "generated_tests": "PASS" if failures == 0 and errors == 0 else "FAIL",
                "generated_test_summary": {
                    "tests_run": total,
                    "failures": failures,
                    "errors": errors,
                    "skipped": skipped,
                    "pass_rate": pass_rate,
                },
            }
        else:
            passed = completed.returncode == 0
            return {
                "generated_tests": "PASS" if passed else "FAIL",
                "generated_test_summary": {
                    "returncode": completed.returncode,
                    "elapsed_seconds": round(elapsed, 1),
                },
            }
    except subprocess.TimeoutExpired:
        return {
            "generated_tests": "TIMEOUT",
            "generated_test_summary": {"reason": "Generated test execution timed out"},
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "generated_tests": "UNUSABLE",
            "generated_test_summary": {"reason": f"Maven execution error: {exc}"},
        }


def run_full_gate_iteration(root: Path, timeout: int, test_filter: str | None) -> dict[str, Any]:
    """Run one iteration of the full gate."""
    gate: dict[str, Any] = {}

    # 1. Code module tests
    code_test_args = ["-f", "code/pom.xml", "test"]
    if test_filter:
        code_test_args.append(f"-Dtest={test_filter}")
    code_test_result = run_maven_command(root, code_test_args, timeout)
    gate["code_tests"] = "PASS" if code_test_result["passed"] else "FAIL"

    # 2. Code install
    install_result = run_maven_command(root, ["-f", "code/pom.xml", "install", "-DskipTests"], timeout)
    gate["code_install"] = "PASS" if install_result["passed"] else "FAIL"

    # 3. Public black-box tests
    public_test_args = ["-f", "test-cases/pom.xml"]
    if test_filter:
        public_test_args.append(f"-Dtest={test_filter}")
    public_test_args.append("test")
    public_result = run_maven_command(root, public_test_args, timeout * 2)
    gate["public_tests"] = "PASS" if public_result["passed"] else "FAIL"

    # 4. Generated tests (real execution)
    gen_result = run_generated_tests(root, timeout)
    gate["generated_tests"] = gen_result.get("generated_tests", "NONE")

    # 5. Contract checker
    contract_result = run_script(root, "contract_checker.py", timeout=timeout)
    gate["contract_checker"] = "PASS" if contract_result["passed"] else "FAIL"

    # 6. Forbidden change guard
    guard_result = run_script(root, "forbidden_change_guard.py", ["--strict"], timeout=60)
    gate["forbidden_guard"] = "PASS" if guard_result["passed"] else "FAIL"

    return gate


def run_stability(root: Path, runs: int, timeout: int, test_filter: str | None,
                  mode: str = "full-gate") -> dict[str, Any]:
    """Run stability verification."""
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    gate_results: list[dict[str, Any]] = []
    all_outputs: list[str] = []

    if mode == "public-only":
        # Simple mode: only run public tests
        for i in range(1, runs + 1):
            result = run_maven_test(root, timeout, test_filter)
            result["run_number"] = i
            parsed = parse_test_results(result.get("stdout_snippet", ""))
            result["test_summary"] = parsed

            log_path = paths.test_results / f"stability-run-{i:02d}.log"
            runner.write_text(log_path, result.get("stdout_snippet", ""))

            all_outputs.append(result.get("stdout_snippet", ""))
            gate_results.append({
                "run": i,
                "public_tests": "PASS" if result["passed"] else "FAIL",
            })
    else:
        # Full-gate mode
        for i in range(1, runs + 1):
            gate = run_full_gate_iteration(root, timeout, test_filter)
            gate["run"] = i
            gate_results.append(gate)

    # Analyze
    all_passed = all("FAIL" not in [v for k, v in g.items() if k != "run"] for g in gate_results)
    has_intermittent = detect_intermittent_gate_failures(gate_results)
    flaky_findings = analyze_flaky_patterns(all_outputs)

    is_stable = all_passed and not has_intermittent and len(gate_results) == runs

    report: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "runs_requested": runs,
        "runs_completed": len(gate_results),
        "stable": is_stable,
        "all_passed": all_passed,
        "has_intermittent_failures": has_intermittent,
        "mode": mode,
        "flaky_findings": flaky_findings,
        "gate_results": gate_results,
    }

    runner.write_json(paths.work / "stability_report.json", report)

    # Summary markdown
    lines = [
        "# Stability Gate Report",
        "",
        f"Generated: {runner.now_iso()}",
        f"Mode: **{mode}**",
        f"Stable: {'✅ YES' if is_stable else '❌ NO'}",
        f"All {runs} runs passed: {'✅ YES' if all_passed else '❌ NO'}",
        "",
        "## Gate Results",
        "",
    ]

    if mode == "full-gate":
        lines.append("| Run | Code Tests | Code Install | Public Tests | Generated | Contract | Guard |")
        lines.append("|-----|------------|--------------|-------------|-----------|----------|-------|")
        for g in gate_results:
            lines.append(
                f"| {g.get('run', '')} | "
                f"{'✅' if g.get('code_tests') == 'PASS' else '❌'} | "
                f"{'✅' if g.get('code_install') == 'PASS' else '❌'} | "
                f"{'✅' if g.get('public_tests') == 'PASS' else '❌'} | "
                f"{g.get('generated_tests', 'N/A')} | "
                f"{'✅' if g.get('contract_checker') == 'PASS' else '❌'} | "
                f"{'✅' if g.get('forbidden_guard') == 'PASS' else '❌'} |"
            )
    else:
        lines.append("| Run | Public Tests |")
        lines.append("|-----|-------------|")
        for g in gate_results:
            lines.append(
                f"| {g.get('run', '')} | "
                f"{'✅' if g.get('public_tests') == 'PASS' else '❌'} |"
            )

    lines.append("")

    if has_intermittent:
        lines.append("⚠️  Intermittent failures detected — gates pass sometimes but not always.")
        lines.append("")
    if flaky_findings:
        lines.append("## Flaky Indicators")
        lines.append("")
        for f in flaky_findings:
            lines.append(f"- **{f['pattern']}**: {f['match_count']} matches (e.g., `{f['sample_matches'][:2]}`)")
        lines.append("")

    runner.write_text(paths.work / "08_stability_report.md", "\n".join(lines).rstrip() + "\n")

    return report


def detect_intermittent_gate_failures(gate_results: list[dict[str, Any]]) -> bool:
    """Check if any gate component passes sometimes but not always."""
    if not gate_results:
        return False
    # Check each gate component across runs
    components = [k for k in gate_results[0] if k != "run"]
    for comp in components:
        results = [g.get(comp) for g in gate_results]
        if "PASS" in results and "FAIL" in results:
            return True
    return False


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = run_stability(root, args.runs, args.timeout, args.test_filter, mode=args.mode)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report["stable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
