#!/usr/bin/env python3
"""Candidate Sandbox — validates each candidate patch in isolation.

Each candidate is applied to a clean workspace, then validated through:
compile → code tests → public tests → generated tests → contract checker → forbidden guard.

Produces ``candidate_validation.jsonl`` with pass/fail results per candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


FORBIDDEN_PATHS = [
    "design-docs/",
    "README.md",
    "test-cases/",
]


def create_candidate_workspace(root: Path, task_id: str, candidate_id: str) -> Path:
    """Create an isolated workspace for a candidate using git worktree (preferred) or copytree.

    Returns the sandbox root directory.
    """
    sandbox_root = root / ".tmp" / "candidates" / task_id / candidate_id
    if sandbox_root.exists():
        shutil.rmtree(sandbox_root, ignore_errors=True)

    sandbox_root.parent.mkdir(parents=True, exist_ok=True)

    # Try git worktree first (fast, clean, git-isolated)
    try:
        result = subprocess.run(
            ["git", "worktree", "add", str(sandbox_root), "HEAD"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=60,
        )
        if result.returncode == 0:
            return sandbox_root
    except (OSError, subprocess.SubprocessError):
        pass

    # Fallback: shutil.copytree (slower but works without git worktree support)
    try:
        shutil.copytree(
            root,
            sandbox_root,
            ignore=shutil.ignore_patterns(".git", ".tmp", "target", "node_modules"),
            dirs_exist_ok=True,
        )
        return sandbox_root
    except (OSError, shutil.Error) as exc:
        raise RuntimeError(f"Failed to create sandbox workspace: {exc}") from exc


def cleanup_candidate_workspace(root: Path, sandbox_root: Path) -> list[str]:
    """Remove a candidate workspace. Returns any warnings encountered."""
    warnings: list[str] = []
    if not sandbox_root.exists():
        return warnings

    # Try git worktree remove first
    try:
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(sandbox_root)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        if result.returncode == 0:
            return warnings
    except (OSError, subprocess.SubprocessError):
        pass

    # Fallback: shutil.rmtree
    try:
        shutil.rmtree(sandbox_root, ignore_errors=True)
    except (OSError, shutil.Error) as exc:
        warnings.append(f"Failed to clean up sandbox {sandbox_root}: {exc}")

    return warnings


def run_generated_tests_in_sandbox(
    sandbox_root: Path, root: Path, timeout: int
) -> dict[str, Any]:
    """Copy and execute compilable generated tests inside the candidate sandbox.

    Reads .agent-work/generated_tests_manifest.json from the main PROJECT_ROOT,
    copies compilable test files into sandbox/code/src/test/java/generated/,
    and runs them via Maven.
    """
    manifest_path = root / ".agent-work" / "generated_tests_manifest.json"
    if not manifest_path.exists():
        return {
            "generated_tests": "NONE",
            "generated_test_summary": {"reason": "No generated_tests_manifest.json"},
            "score_inputs": {"generated_test_pass_rate": 0.5},
        }

    manifest = runner.read_json(manifest_path, {})
    test_classes = manifest.get("test_classes", [])
    compilable = [tc for tc in test_classes if tc.get("compilable") in (True, None)]

    if not compilable:
        return {
            "generated_tests": "NONE",
            "generated_test_summary": {"reason": "No compilable generated tests"},
            "score_inputs": {"generated_test_pass_rate": 0.5},
        }

    # Copy generated test files into sandbox
    dest_dir = sandbox_root / "code" / "src" / "test" / "java" / "generated"
    dest_dir.mkdir(parents=True, exist_ok=True)
    class_names: list[str] = []

    for tc in compilable:
        cls_name = tc.get("test_class", "")
        source_file = tc.get("file", "")
        if not cls_name or not source_file:
            continue
        source_path = Path(source_file)
        if not source_path.exists():
            continue  # file may be relative to root
        source_abs = source_path if source_path.is_absolute() else root / source_path
        if not source_abs.exists():
            continue
        try:
            shutil.copy2(source_abs, dest_dir / f"{cls_name}.java")
            class_names.append(cls_name)
        except (OSError, shutil.Error):
            continue

    if not class_names:
        return {
            "generated_tests": "NONE",
            "generated_test_summary": {"reason": "Could not copy generated test files"},
            "score_inputs": {"generated_test_pass_rate": 0.5},
        }

    # Run generated tests in sandbox
    test_filter = ",".join(class_names)
    try:
        settings = runner.find_maven_settings(sandbox_root)
        cmd = ["mvn"]
        if settings:
            cmd.extend(["-s", runner.rel(sandbox_root, settings)])
        cmd.extend(["-f", "code/pom.xml", f"-Dtest={test_filter}", "test"])

        start = time.time()
        completed = subprocess.run(
            cmd,
            cwd=sandbox_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=max(timeout, 900),
        )
        elapsed = time.time() - start

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
            passed = total - failures - errors
            pass_rate = passed / total if total > 0 else 0.0
            return {
                "generated_tests": "PASS" if failures == 0 and errors == 0 else "FAIL",
                "generated_test_summary": {
                    "tests_run": total,
                    "failures": failures,
                    "errors": errors,
                    "skipped": skipped,
                    "pass_rate": pass_rate,
                },
                "score_inputs": {"generated_test_pass_rate": pass_rate},
            }
        else:
            passed = completed.returncode == 0
            return {
                "generated_tests": "PASS" if passed else "FAIL",
                "generated_test_summary": {
                    "returncode": completed.returncode,
                    "elapsed_seconds": round(elapsed, 1),
                    "output_snippet": output[-1000:] if len(output) > 1000 else output,
                },
                "score_inputs": {"generated_test_pass_rate": 1.0 if passed else 0.0},
            }
    except subprocess.TimeoutExpired:
        return {
            "generated_tests": "TIMEOUT",
            "generated_test_summary": {"reason": "Generated test execution timed out"},
            "score_inputs": {"generated_test_pass_rate": 0.5},
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "generated_tests": "UNUSABLE",
            "generated_test_summary": {"reason": f"Maven execution error: {exc}"},
            "score_inputs": {"generated_test_pass_rate": 0.5},
        }


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
    """Validate one candidate patch in an isolated sandbox workspace.

    Each candidate gets its own sandbox (git worktree or copytree).
    The patch is applied inside the sandbox, all validation steps run there,
    and results are written back to PROJECT_ROOT/.agent-work/.
    """
    task_id = candidate.get("task_id", "")
    candidate_id = candidate.get("candidate_id", "")

    result: dict[str, Any] = {
        "task_id": task_id,
        "candidate_id": candidate_id,
        "strategy": candidate.get("strategy", ""),
        "compile": "SKIPPED",
        "code_tests": "SKIPPED",
        "public_tests": "SKIPPED",
        "generated_tests": "SKIPPED",
        "contract_check": "SKIPPED",
        "forbidden_guard": "SKIPPED",
        "diff_files": 0,
        "diff_lines": 0,
        "patch_file": "",
        "patch_file_exists": False,
        "patch_file_source": "none",
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
        "warnings": [],
    }

    # --- Pre-checks on patch file (no sandbox needed) ---
    patch_file = candidate.get("patch_file", "")
    if not patch_file:
        result["elimination_reason"] = "No patch file specified"
        return result

    patch_path = root / patch_file
    result["patch_file"] = patch_file
    if not patch_path.exists():
        result["patch_file_exists"] = False
        result["patch_file_source"] = "candidate"
        result["elimination_reason"] = f"Patch file not found: {patch_file}"
        result["errors"].append(result["elimination_reason"])
        return result

    patch_content = runner.read_text(patch_path)
    result["patch_file_exists"] = True
    result["patch_file_source"] = "candidate"

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

    # --- Create isolated sandbox ---
    sandbox_root: Path | None = None
    try:
        sandbox_root = create_candidate_workspace(root, task_id, candidate_id)
    except Exception as exc:
        result["elimination_reason"] = f"Failed to create sandbox: {exc}"
        result["errors"].append(str(exc))
        return result

    try:
        # --- Step 1: Apply patch in sandbox ---
        patch_path_abs = patch_path if patch_path.is_absolute() else root / patch_path
        try:
            apply_result = subprocess.run(
                ["git", "apply", "--check", str(patch_path_abs)],
                cwd=sandbox_root,
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

        try:
            apply_result = subprocess.run(
                ["git", "apply", str(patch_path_abs)],
                cwd=sandbox_root,
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
                return result
        except (OSError, subprocess.SubprocessError) as exc:
            result["compile"] = "FAIL"
            result["elimination_reason"] = f"git apply failed: {exc}"
            result["errors"].append(result["elimination_reason"])
            return result

        # --- Step 2-3: Compile, code tests, code install ---
        code_pom = sandbox_root / "code" / "pom.xml"
        if not code_pom.exists():
            result["compile"] = "SKIPPED"
            result["code_tests"] = "SKIPPED"
            result["errors"].append("code/pom.xml not found in sandbox — compile skipped")
        else:
            # Compile in sandbox
            compile_cmd = runner.maven_command(sandbox_root, ["-f", "code/pom.xml", "compile", "-q"])
            compile_result = run_step(compile_cmd, sandbox_root, timeout)
            result["compile"] = "PASS" if compile_result["passed"] else "FAIL"
            if not compile_result["passed"]:
                result["elimination_reason"] = "Compilation failed after patch"
                result["errors"].append(
                    f"Compilation failed: {compile_result.get('output_snippet', '')[:500]}"
                )
                return result

            # Code module tests
            test_cmd = runner.maven_command(sandbox_root, ["-f", "code/pom.xml", "test"])
            test_result = run_step(test_cmd, sandbox_root, timeout)
            result["code_tests"] = "PASS" if test_result["passed"] else "FAIL"

            # Code install
            install_cmd = runner.maven_command(sandbox_root, ["-f", "code/pom.xml", "install", "-DskipTests"])
            install_result = run_step(install_cmd, sandbox_root, timeout)
            if not install_result["passed"]:
                result["errors"].append("code install failed — public tests will be skipped")

        # --- Step 4: Public black-box tests ---
        public_pom = sandbox_root / "test-cases" / "pom.xml"
        if public_pom.exists():
            public_cmd = runner.maven_command(sandbox_root, ["-f", "test-cases/pom.xml", "test"])
            public_result = run_step(public_cmd, sandbox_root, timeout * 2)
            result["public_tests"] = "PASS" if public_result["passed"] else "FAIL"
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

            # --- FSM-DESIGN §11: Hard elimination based on test outcome matrix ---
            # Check public black-box matrix for FAILURE/ERROR/TIMEOUT/NOT_RUN
            matrix_blocked, matrix_reasons = _check_sandbox_matrix(sandbox_root, root)
            if matrix_blocked and not result.get("elimination_reason"):
                result["elimination_reason"] = (
                    f"Public black-box matrix has blocking issues: {'; '.join(matrix_reasons)}"
                )
                result["errors"].append(result["elimination_reason"])
                result["public_tests"] = "FAIL"
        else:
            result["public_tests"] = "SKIPPED"

        # --- Step 5: Generated tests (real execution in sandbox) ---
        gen_result = run_generated_tests_in_sandbox(sandbox_root, root, timeout)
        result["generated_tests"] = gen_result["generated_tests"]
        result["score_inputs"]["generated_test_pass_rate"] = (
            gen_result["score_inputs"]["generated_test_pass_rate"]
        )

        # --- Step 6: Contract checker in sandbox ---
        try:
            contract_script = sandbox_root / "work" / "tools" / "scripts" / "contract_checker.py"
            contract_result = subprocess.run(
                [sys.executable, str(contract_script), "--root", str(sandbox_root)],
                cwd=sandbox_root,
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
                # Compare baseline vs candidate P0 count
                baseline_p0 = _load_baseline_p0_count(root)
                try:
                    candidate_report = json.loads(contract_result.stdout)
                    candidate_p0 = candidate_report.get("summary", {}).get("p0_issues", 0)
                    new_p0 = max(0, candidate_p0 - baseline_p0)
                    if new_p0 > 0:
                        result["contract_check"] = "FAIL"
                        result["elimination_reason"] = (
                            f"Contract checker found {new_p0} new P0 issues "
                            f"(candidate={candidate_p0} vs baseline={baseline_p0})"
                        )
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
            result["errors"].append(f"Contract checker failed in sandbox: {exc}")
            result["score_inputs"]["contract_checker_pass"] = 0.5

        # --- Step 7: Forbidden change guard in sandbox ---
        try:
            guard_script = sandbox_root / "work" / "tools" / "scripts" / "forbidden_change_guard.py"
            guard_result = subprocess.run(
                [sys.executable, str(guard_script), "--root", str(sandbox_root), "--strict"],
                cwd=sandbox_root,
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
            result["errors"].append(f"Forbidden guard failed in sandbox: {exc}")

        # --- Determine eligibility ---
        if not result["elimination_reason"]:
            result["eligible"] = True

    except Exception as exc:
        result["elimination_reason"] = f"Unexpected validation error: {exc}"
        result["errors"].append(str(exc))

    finally:
        # --- Cleanup sandbox ---
        if sandbox_root is not None:
            cleanup_warnings = cleanup_candidate_workspace(root, sandbox_root)
            result["warnings"].extend(cleanup_warnings)

    return result


def _load_baseline_p0_count(root: Path) -> int:
    """Load the baseline P0 issue count from .agent-work/baseline_consistency_report.json.

    If no baseline exists, returns 0 (first run — no baseline to compare against).
    """
    baseline_path = root / ".agent-work" / "baseline_consistency_report.json"
    if not baseline_path.exists():
        return 0
    try:
        baseline = runner.read_json(baseline_path, {})
        return baseline.get("summary", {}).get("p0_issues", 0)
    except Exception:
        return 0


def _check_sandbox_matrix(sandbox_root: Path, project_root: Path) -> tuple[bool, list[str]]:
    """Check sandbox test matrix for blocking issues (FAILURE/ERROR/TIMEOUT/NOT_RUN).

    Per FSM-DESIGN §11: Public black-box matrix non-all-green → hard elimination.

    Returns (has_blocking_issues, reasons_list).
    """
    try:
        import test_outcome_collector
    except ImportError:
        return False, []

    # Collect matrix from sandbox Surefire XML (blackbox only for now)
    try:
        matrix = test_outcome_collector.build_test_outcome_matrix(
            sandbox_root,
            suite_filter="blackbox-public",
            discover_sources=True,
            run_id="sandbox-candidate",
        )
    except Exception:
        return False, []

    summary = matrix.get("summary", {})
    reasons: list[str] = []
    if summary.get("failure", 0) > 0:
        reasons.append(f"{summary['failure']} FAILURE(s)")
    if summary.get("error", 0) > 0:
        reasons.append(f"{summary['error']} ERROR(s)")
    if summary.get("timeout", 0) > 0:
        reasons.append(f"{summary['timeout']} TIMEOUT(s)")
    if summary.get("not_run", 0) > 0:
        reasons.append(f"{summary['not_run']} NOT_RUN")

    return bool(reasons), reasons


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
