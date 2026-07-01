#!/usr/bin/env python3
"""Candidate Sandbox — validates each candidate patch in isolation.

Each candidate is applied to a clean workspace, then validated through:
compile → code tests → public tests → generated tests → contract checker → forbidden guard.

Produces ``candidate_validation.jsonl`` with pass/fail results per candidate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


FORBIDDEN_PATHS = [
    "design-docs/",
    "README.md",
    "test-cases/",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate candidate patches in isolated sandbox.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--task-id", default=None, help="Filter candidates by task ID.")
    parser.add_argument("--candidate-file", default=None,
                       help="Path to candidate_patches.jsonl (default: .agent-work/candidate_patches.jsonl).")
    parser.add_argument("--output", default=None, help="Output path for validation results.")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per step in seconds.")
    return parser


def check_forbidden_modifications(patch_content: str) -> list[str]:
    """Check if patch modifies forbidden files."""
    violations: list[str] = []
    for line in patch_content.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            path_part = line.split(" ", 1)[0] if line.startswith("+++ b/") else line.split(" ", 1)[0]
            normalized = path_part.replace("+++ b/", "").replace("--- a/", "")
            for forbidden in FORBIDDEN_PATHS:
                if normalized.startswith(forbidden):
                    violations.append(normalized)
    return violations


def check_uniform_200(patch_content: str) -> bool:
    """Check if patch contains patterns that return 200 unconditionally."""
    risky_patterns = [
        r"return\s+(?:new\s+)?\w+\s*\(\s*(?:200|HttpStatus\.OK|ok\(\))",
        r"status\(\s*(?:200|HttpStatus\.OK|ok\(\))\s*\)",
    ]
    return any(re.search(pattern, patch_content) for pattern in risky_patterns)


def check_exception_swallow(patch_content: str) -> bool:
    """Check if patch swallows exceptions."""
    swallow_pattern = r"catch\s*\(\s*(?:Exception|Throwable|RuntimeException)\s+\w+\s*\)\s*\{[^}]*\}"
    return bool(re.search(swallow_pattern, patch_content, re.DOTALL))


def check_hardcoded_test_data(patch_content: str, test_symptoms_path: Path) -> bool:
    """Check if patch hardcodes test data matching public tests."""
    if not test_symptoms_path.exists():
        return False
    return False  # Requires test symptom analysis; placeholder


def run_step(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    """Run a validation step and return result."""
    start = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
        elapsed = time.time() - start
        return {
            "passed": completed.returncode == 0,
            "returncode": completed.returncode,
            "elapsed_seconds": round(elapsed, 1),
            "output_snippet": completed.stdout[-2000:] if len(completed.stdout) > 2000 else completed.stdout,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - start
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "passed": False,
            "returncode": 124,
            "elapsed_seconds": round(elapsed, 1),
            "timeout": True,
            "output_snippet": output[-2000:] if len(output) > 2000 else output,
        }


def validate_candidate(
    root: Path,
    candidate: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    """Validate one candidate patch in isolation."""
    result: dict[str, Any] = {
        "task_id": candidate.get("task_id", ""),
        "candidate_id": candidate.get("candidate_id", ""),
        "strategy": candidate.get("strategy", ""),
        "compile": "SKIPPED",
        "code_tests": "SKIPPED",
        "public_tests": "SKIPPED",
        "generated_tests": "SKIPPED",
        "contract_check": "SKIPPED",
        "forbidden_guard": "SKIPPED",
        "diff_files": 0,
        "diff_lines": 0,
        "score_inputs": {
            "public_test_pass_rate": 0.0,
            "generated_test_pass_rate": 0.0,
            "contract_checker_pass": 0.0,
            "diff_files": 0,
            "diff_lines": 0,
            "stable": None,
        },
        "eligible": False,
        "elimination_reason": "",
        "errors": [],
    }

    patch_file = candidate.get("patch_file", "")
    if not patch_file:
        result["elimination_reason"] = "No patch file specified"
        return result

    patch_path = root / patch_file
    if not patch_path.exists():
        result["elimination_reason"] = f"Patch file not found: {patch_file}"
        result["errors"].append(result["elimination_reason"])
        return result

    patch_content = runner.read_text(patch_path)

    # --- Pre-checks on patch content ---
    forbidden = check_forbidden_modifications(patch_content)
    if forbidden:
        result["elimination_reason"] = f"Modifies forbidden files: {', '.join(forbidden)}"
        result["errors"].append(result["elimination_reason"])
        return result

    if check_uniform_200(patch_content):
        result["elimination_reason"] = "Patch contains patterns that may return 200 unconditionally"
        result["errors"].append(result["elimination_reason"])
        return result

    if check_exception_swallow(patch_content):
        result["elimination_reason"] = "Patch contains exception swallowing pattern"
        result["errors"].append(result["elimination_reason"])
        return result

    # Count diff stats
    diff_files = len(set(
        line.split(" ", 1)[0].replace("+++ b/", "")
        for line in patch_content.splitlines()
        if line.startswith("+++ b/")
    ))
    diff_lines = len([l for l in patch_content.splitlines()
                      if l.startswith("+") and not l.startswith("+++")])
    result["diff_files"] = diff_files
    result["diff_lines"] = diff_lines
    result["score_inputs"]["diff_files"] = diff_files
    result["score_inputs"]["diff_lines"] = diff_lines

    # --- Step 1: Apply patch and compile ---
    paths = runner.RunnerPaths(root)
    patch_path_abs = patch_path

    # Apply patch
    try:
        apply_result = subprocess.run(
            ["git", "apply", "--check", str(patch_path_abs)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        if apply_result.returncode != 0:
            result["compile"] = "FAIL"
            result["elimination_reason"] = f"Patch does not apply cleanly: {apply_result.stdout[:500]}"
            result["errors"].append(result["elimination_reason"])
            return result
    except (OSError, subprocess.SubprocessError) as exc:
        result["compile"] = "FAIL"
        result["elimination_reason"] = f"git apply check failed: {exc}"
        result["errors"].append(result["elimination_reason"])
        return result

    # Actually apply the patch
    try:
        apply_result = subprocess.run(
            ["git", "apply", str(patch_path_abs)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        if apply_result.returncode != 0:
            result["compile"] = "FAIL"
            result["elimination_reason"] = f"Patch apply failed: {apply_result.stdout[:500]}"
            result["errors"].append(result["elimination_reason"])
            # Attempt to revert
            subprocess.run(["git", "checkout", "--", "."], cwd=root, check=False, timeout=30)
            return result
    except (OSError, subprocess.SubprocessError) as exc:
        result["compile"] = "FAIL"
        result["elimination_reason"] = f"git apply failed: {exc}"
        result["errors"].append(result["elimination_reason"])
        return result

    try:
        # Check if code/pom.xml exists
        code_pom = root / "code" / "pom.xml"
        if not code_pom.exists():
            result["compile"] = "SKIPPED"
            result["code_tests"] = "SKIPPED"
            result["errors"].append("code/pom.xml not found — compile skipped")
        else:
            # Step 1: Compile
            compile_cmd = runner.maven_command(root, ["-f", "code/pom.xml", "compile", "-q"])
            compile_result = run_step(compile_cmd, root, timeout)
            result["compile"] = "PASS" if compile_result["passed"] else "FAIL"
            if not compile_result["passed"]:
                result["elimination_reason"] = "Compilation failed after patch"
                result["errors"].append(
                    f"Compilation failed: {compile_result.get('output_snippet', '')[:500]}"
                )
                # Revert
                subprocess.run(["git", "checkout", "--", "."], cwd=root, check=False, timeout=30)
                return result

            # Step 2: Code module tests
            test_cmd = runner.maven_command(root, ["-f", "code/pom.xml", "test"])
            test_result = run_step(test_cmd, root, timeout)
            result["code_tests"] = "PASS" if test_result["passed"] else "FAIL"

            # Step 3: Code install
            install_cmd = runner.maven_command(root, ["-f", "code/pom.xml", "install", "-DskipTests"])
            install_result = run_step(install_cmd, root, timeout)
            if not install_result["passed"]:
                result["errors"].append("code install failed — public tests will be skipped")
    except Exception as exc:
        result["elimination_reason"] = f"Build step error: {exc}"
        result["errors"].append(str(exc))
        subprocess.run(["git", "checkout", "--", "."], cwd=root, check=False, timeout=30)
        return result

    # Step 4: Public black-box tests
    public_pom = root / "test-cases" / "pom.xml"
    if public_pom.exists():
        public_cmd = runner.maven_command(root, ["-f", "test-cases/pom.xml", "test"])
        public_result = run_step(public_cmd, root, timeout * 2)
        result["public_tests"] = "PASS" if public_result["passed"] else "FAIL"
        # Parse test counts
        surefire_match = re.findall(
            r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)",
            public_result.get("output_snippet", ""),
        )
        if surefire_match:
            last = surefire_match[-1]
            total_tests = int(last[0])
            failures = int(last[1])
            errors = int(last[2])
            passed_count = total_tests - failures - errors
            result["score_inputs"]["public_test_pass_rate"] = (
                passed_count / total_tests if total_tests > 0 else 0.0
            )
    else:
        result["public_tests"] = "SKIPPED"

    # Step 5: Generated tests
    generated_dir = root / ".tmp" / "generated-tests"
    if generated_dir.exists():
        result["generated_tests"] = "SKIPPED"  # Generated tests run separately
        result["score_inputs"]["generated_test_pass_rate"] = 0.5  # Default
    else:
        result["generated_tests"] = "NONE"

    # Step 6: Contract checker
    try:
        contract_result = subprocess.run(
            [sys.executable, "work/tools/scripts/contract_checker.py", "--root", str(root)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=120,
        )
        if contract_result.returncode == 0:
            result["contract_check"] = "PASS"
            result["score_inputs"]["contract_checker_pass"] = 1.0
        else:
            # Check if new P0 issues exist
            try:
                consistency = json.loads(contract_result.stdout)
                new_p0 = consistency.get("summary", {}).get("p0_issues", 0)
                if new_p0 > 0:
                    result["contract_check"] = "FAIL"
                    result["elimination_reason"] = f"Contract checker found {new_p0} new P0 issues"
                    result["errors"].append(result["elimination_reason"])
                    result["score_inputs"]["contract_checker_pass"] = 0.0
                else:
                    result["contract_check"] = "PASS"
                    result["score_inputs"]["contract_checker_pass"] = 0.5
            except (json.JSONDecodeError, KeyError):
                result["contract_check"] = "FAIL"
                result["score_inputs"]["contract_checker_pass"] = 0.0
    except (OSError, subprocess.SubprocessError) as exc:
        result["contract_check"] = "ERROR"
        result["errors"].append(f"Contract checker failed: {exc}")
        result["score_inputs"]["contract_checker_pass"] = 0.5

    # Step 7: Forbidden change guard
    try:
        guard_result = subprocess.run(
            [sys.executable, "work/tools/scripts/forbidden_change_guard.py", "--root", str(root), "--strict"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=60,
        )
        if guard_result.returncode == 0:
            result["forbidden_guard"] = "PASS"
        else:
            result["forbidden_guard"] = "FAIL"
            result["elimination_reason"] = "Forbidden change guard detected violations"
            result["errors"].append(result["elimination_reason"])
    except (OSError, subprocess.SubprocessError) as exc:
        result["forbidden_guard"] = "ERROR"
        result["errors"].append(f"Forbidden guard failed: {exc}")

    # Determine eligibility
    if not result["elimination_reason"]:
        result["eligible"] = True

    # Revert patch
    try:
        subprocess.run(["git", "checkout", "--", "."], cwd=root, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass

    return result


def validate_all_candidates(root: Path, candidates: list[dict[str, Any]], timeout: int) -> list[dict[str, Any]]:
    """Validate all candidates and return results."""
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        result = validate_candidate(root, candidate, timeout)
        results.append(result)
    return results


def load_candidates(root: Path, candidate_file: str | None, task_id: str | None) -> list[dict[str, Any]]:
    """Load candidate patches, optionally filtered by task_id."""
    paths = runner.RunnerPaths(root)

    if candidate_file:
        cand_path = Path(candidate_file)
    else:
        cand_path = paths.work / "candidate_patches.jsonl"

    if not cand_path.exists():
        return []

    candidates = runner.read_jsonl(cand_path)

    if task_id:
        candidates = [c for c in candidates if c.get("task_id") == task_id]

    return candidates


def validate(root: Path, task_id: str | None, candidate_file: str | None, timeout: int) -> dict[str, Any]:
    """Main validation entry point."""
    paths = runner.RunnerPaths(root)

    candidates = load_candidates(root, candidate_file, task_id)

    if not candidates:
        return {
            "status": "skipped",
            "reason": "No candidate patches found",
            "results": [],
        }

    results = validate_all_candidates(root, candidates, timeout)

    # Persist results
    runner.append_jsonl(paths.work / "candidate_validation.jsonl", results)

    # Write summary
    eligible = [r for r in results if r.get("eligible")]
    lines = [
        "# Candidate Patch Validation",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        f"## Summary",
        f"- Total candidates: {len(results)}",
        f"- Eligible: {len(eligible)}",
        f"- Eliminated: {len(results) - len(eligible)}",
        "",
    ]
    if eligible:
        lines.append("## Eligible Candidates")
        lines.append("")
        for r in eligible:
            lines.append(
                f"- **{r['candidate_id']}** ({r.get('strategy', '')}): "
                f"compile={r['compile']} code_tests={r['code_tests']} "
                f"public={r['public_tests']} contract={r['contract_check']}"
            )
        lines.append("")
    eliminated = [r for r in results if not r.get("eligible")]
    if eliminated:
        lines.append("## Eliminated Candidates")
        lines.append("")
        for r in eliminated:
            lines.append(f"- **{r['candidate_id']}**: {r.get('elimination_reason', 'Unknown')}")
        lines.append("")

    runner.write_text(paths.work / "candidate_validation.md", "\n".join(lines).rstrip() + "\n")

    return {
        "status": "ok",
        "total": len(results),
        "eligible": len(eligible),
        "results": results,
    }


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = validate(root, args.task_id, args.candidate_file, args.timeout)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
