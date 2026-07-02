#!/usr/bin/env python3
"""Blackbox Explorer — suite/class/method-level black-box test exploration.

Runs black-box tests at multiple granularities to discover masked/hidden failures
that a simple `mvn test` would not reveal because earlier tests abort the run.

Modes:
  suite   — mvn -f test-cases/pom.xml test (full suite)
  class   — mvn -f test-cases/pom.xml -Dtest=ClassName test (per class)
  method  — mvn -f test-cases/pom.xml -Dtest=ClassName#methodName test (per method)
  replay  — targeted replay of NOT_RUN / newly-ERROR tests

Outputs:
  .agent-work/test_matrix/blackbox_explorer_runs.jsonl  — raw run records
  .agent-work/test_matrix/current_test_matrix.json        — merged test matrix
  .agent-work/test_matrix/unmasked_failures.jsonl         — newly discovered failures

Usage:
  python3 blackbox_explorer.py --root . --mode baseline
  python3 blackbox_explorer.py --root . --mode sweep --previous-matrix .agent-work/test_matrix/previous_test_matrix.json
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
import test_outcome_collector as collector


OUTCOME_RANK = {
    "ERROR": 60,
    "FAILURE": 50,
    "TIMEOUT": 45,
    "SKIPPED": 30,
    "EXPECTED_SKIPPED": 20,
    "PASS": 10,
    "NOT_RUN": 0,
}


# ---------------------------------------------------------------------------
# Test class discovery
# ---------------------------------------------------------------------------

def discover_test_classes(root: Path) -> list[dict[str, Any]]:
    """Discover black-box test classes and their methods from test-cases/ source.

    Returns a list of {class_name, source_file, methods: [{name, line}]}.
    """
    classes: list[dict[str, Any]] = []
    for test_file in sorted(root.glob("test-cases/**/*Test.java")):
        cls_info = _parse_test_class(root, test_file)
        if cls_info:
            classes.append(cls_info)
    for test_file in sorted(root.glob("test-cases/**/*Tests.java")):
        cls_info = _parse_test_class(root, test_file)
        if cls_info:
            classes.append(cls_info)
    return classes


def _parse_test_class(root: Path, test_file: Path) -> dict[str, Any] | None:
    """Parse a single test Java file, extracting class name and @Test methods."""
    try:
        text = test_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    class_match = re.search(r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b', text)
    if not class_match:
        return None

    class_name = class_match.group(1)
    methods: list[dict[str, Any]] = []

    test_annotation = re.compile(r'@(Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b')
    method_decl = re.compile(
        r'(?:public|protected|private)?\s*(?:static\s+)?'
        r'(?:[A-Za-z0-9_<>, ?.\[\]]+)\s+'
        r'([A-Za-z_][A-Za-z0-9_]*)\s*\('
    )

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if test_annotation.search(line):
            for j in range(i + 1, min(i + 6, len(lines))):
                m = method_decl.search(lines[j])
                if m and m.group(1) not in ("class", "interface", "enum"):
                    methods.append({"name": m.group(1), "line": j + 1})
                    break

    return {
        "class_name": class_name,
        "source_file": runner.rel(root, test_file),
        "methods": methods,
    }


# ---------------------------------------------------------------------------
# Maven execution helpers
# ---------------------------------------------------------------------------

def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:120] or "run"


def clear_surefire_reports(root: Path) -> None:
    for pattern in (
        "test-cases/**/target/surefire-reports/TEST-*.xml",
        "test-cases/**/target/failsafe-reports/TEST-*.xml",
    ):
        for xml_path in root.glob(pattern):
            try:
                xml_path.unlink()
            except OSError:
                pass


def snapshot_surefire_reports(root: Path, run_label: str) -> Path:
    paths = runner.RunnerPaths(root)
    snapshot_dir = paths.test_matrix / "runs" / _safe_label(run_label)
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for pattern in (
        "test-cases/**/target/surefire-reports/TEST-*.xml",
        "test-cases/**/target/failsafe-reports/TEST-*.xml",
    ):
        for xml_path in root.glob(pattern):
            rel_name = "__".join(xml_path.relative_to(root).parts)
            shutil.copy2(xml_path, snapshot_dir / rel_name)
    return snapshot_dir


def build_matrix_from_snapshot(
    root: Path,
    snapshot_dir: Path,
    run_id: str,
    discover_sources: bool = False,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    xml_paths = sorted(snapshot_dir.glob("*.xml"))
    for xml_path in xml_paths:
        parsed = collector.parse_surefire_xml(xml_path)
        for record in parsed:
            record["run_id"] = run_id
            record["source_xml"] = str(xml_path)
        records.extend(parsed)

    if discover_sources:
        records = collector._annotate_with_context(root, records)

    return {
        "generated_at": runner.now_iso(),
        "run_id": run_id,
        "suite_filter": "blackbox-public",
        "source_xml_count": len(xml_paths),
        "matrix": records,
        "summary": collector._build_summary(records),
    }


def merge_run_matrices(
    root: Path,
    matrices: list[dict[str, Any]],
    run_id: str,
    suite_filter: str = "blackbox-public",
) -> dict[str, Any]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    for matrix in matrices:
        for record in matrix.get("matrix", []):
            key = (record.get("class_name", ""), record.get("method_name", ""))
            if not key[0] or not key[1]:
                continue
            current = merged.get(key)
            if current is None:
                merged[key] = dict(record)
                continue
            old_rank = OUTCOME_RANK.get(current.get("outcome", "NOT_RUN"), 0)
            new_rank = OUTCOME_RANK.get(record.get("outcome", "NOT_RUN"), 0)
            if new_rank > old_rank:
                merged[key] = dict(record)
            elif new_rank == old_rank:
                if not current.get("message") and record.get("message"):
                    current["message"] = record["message"]
                if not current.get("stack_top") and record.get("stack_top"):
                    current["stack_top"] = record["stack_top"]

    records = list(merged.values())
    discovered = collector.discover_test_sources(root, suite_filter)
    existing_keys = {(record["class_name"], record["method_name"]) for record in records}

    for discovered_record in discovered:
        key = (discovered_record["class_name"], discovered_record["method_name"])
        if key in existing_keys:
            continue
        expected_skip = bool(discovered_record.get("is_conditionally_disabled"))
        records.append({
            "suite": discovered_record["suite"],
            "class_name": discovered_record["class_name"],
            "full_class_name": discovered_record["class_name"],
            "method_name": discovered_record["method_name"],
            "outcome": "EXPECTED_SKIPPED" if expected_skip else "NOT_RUN",
            "failure_kind": "",
            "message": "Conditionally disabled test method"
            if expected_skip else "Test method not observed in any suite/class/method run",
            "stack_top": "",
            "time_seconds": 0.0,
            "source_xml": "",
            "source_file": discovered_record["source_file"],
            "run_id": run_id,
            "masked_by": "",
            "is_conditionally_disabled": expected_skip,
        })

    records = collector._annotate_with_context(root, records)
    records.sort(key=lambda item: (item.get("class_name", ""), item.get("method_name", "")))
    return {
        "generated_at": runner.now_iso(),
        "run_id": run_id,
        "suite_filter": suite_filter,
        "source_xml_count": sum(m.get("source_xml_count", 0) for m in matrices),
        "summary": collector._build_summary(records),
        "matrix": records,
        "merged_from": [m.get("run_id", "") for m in matrices],
    }


def run_maven_test(
    root: Path,
    test_filter: str | None = None,
    timeout: int = 600,
    label: str = "",
    extra_maven_args: list[str] | None = None,
) -> dict[str, Any]:
    """Run Maven black-box tests, optionally filtered to a class or method.

    Args:
        root: Project root.
        test_filter: If set, passed as -Dtest=<value>.  Can be "ClassName" or
                     "ClassName#methodName".
        timeout: Max seconds.
        label: Human-readable label for logging.

    Returns:
        Dict with keys: returncode, passed, elapsed_seconds, stdout_snippet,
        test_filter, label.
    """
    settings = runner.find_maven_settings(root)
    cmd = ["mvn"]
    if settings:
        cmd.extend(["-s", runner.rel(root, settings)])
    cmd.extend(["-f", "test-cases/pom.xml"])
    if test_filter:
        cmd.append(f"-Dtest={test_filter}")
    if extra_maven_args:
        cmd.extend(extra_maven_args)
    cmd.append("test")

    clear_surefire_reports(root)
    start_time = time.time()
    try:
        completed = subprocess.run(
            cmd,
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
            "passed": completed.returncode == 0,
            "elapsed_seconds": round(elapsed, 1),
            "stdout_snippet": completed.stdout[-3000:] if len(completed.stdout) > 3000 else completed.stdout,
            "test_filter": test_filter or "(full suite)",
            "label": label,
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - start_time
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "returncode": 124,
            "passed": False,
            "elapsed_seconds": round(elapsed, 1),
            "stdout_snippet": output[-3000:] if len(output) > 3000 else output,
            "test_filter": test_filter or "(full suite)",
            "label": label,
            "timeout": True,
        }


# ---------------------------------------------------------------------------
# Exploration strategy
# ---------------------------------------------------------------------------

def run_baseline_exploration(root: Path, timeout: int) -> dict[str, Any]:
    """Baseline mode: run suite-level + class-level discovery.

    Returns:
        Dict with keys: runs (list of run records), matrix, unmasked.
    """
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"
    runs: list[dict[str, Any]] = []
    run_matrices: list[dict[str, Any]] = []

    # --- Suite-level run ---
    suite_result = run_maven_test(root, timeout=timeout, label="suite")
    runs.append(suite_result)
    snapshot = snapshot_surefire_reports(root, "001-suite")
    run_matrices.append(build_matrix_from_snapshot(root, snapshot, "001-suite"))

    # --- Class-level runs ---
    classes = discover_test_classes(root)
    class_results: list[dict[str, Any]] = []
    for cls in classes:
        class_name = cls["class_name"]
        result = run_maven_test(
            root, test_filter=class_name, timeout=timeout,
            label=f"class:{class_name}",
        )
        result["class_name"] = class_name
        result["method_count"] = len(cls.get("methods", []))
        runs.append(result)
        class_results.append(result)
        snapshot = snapshot_surefire_reports(root, f"class-{class_name}")
        run_matrices.append(build_matrix_from_snapshot(root, snapshot, f"class-{class_name}"))

    # --- Merge per-run matrices after all runs ---
    matrix = merge_run_matrices(root, run_matrices, run_id="explorer-baseline")
    save_matrix(matrix_dir / "baseline_test_matrix.json", matrix)
    save_matrix(matrix_dir / "current_test_matrix.json", matrix)

    # --- Identify unmasked failures ---
    unmasked = _find_unmasked_failures(matrix, class_results)

    # Persist
    runner.append_jsonl(matrix_dir / "blackbox_explorer_runs.jsonl", runs)
    if unmasked:
        runner.append_jsonl(matrix_dir / "unmasked_failures.jsonl", unmasked)

    return {"runs": runs, "matrix": matrix, "unmasked_failures": unmasked}


def run_sweep_exploration(
    root: Path,
    timeout: int,
    previous_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sweep mode: re-run NOT_RUN + previously-failed tests after a repair.

    Focuses on:
      - Tests that were NOT_RUN in the previous matrix.
      - Tests that were FAILURE/ERROR in the previous matrix.
      - Tests related to recently modified modules.
    """
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"
    runs: list[dict[str, Any]] = []
    run_matrices: list[dict[str, Any]] = []

    # Determine focus targets
    focus_methods: list[tuple[str, str]] = []  # (class_name, method_name)
    focus_classes: set[str] = set()

    if previous_matrix:
        for record in previous_matrix.get("matrix", []):
            outcome = record.get("outcome", "")
            class_name = record.get("class_name", "")
            method_name = record.get("method_name", "")
            if outcome in ("NOT_RUN", "ERROR", "FAILURE", "SKIPPED", "TIMEOUT"):
                focus_methods.append((class_name, method_name))
                focus_classes.add(class_name)

    # Also add classes from recently modified modules (best-effort)
    git_diff = runner.run_git_diff(root)
    for line in git_diff.splitlines():
        if line.startswith("+++ b/") and "code/" in line:
            focus_classes.add(_infer_test_class_from_code_path(line))

    # --- Suite-level run first ---
    suite_result = run_maven_test(root, timeout=timeout, label="sweep-suite")
    runs.append(suite_result)
    snapshot = snapshot_surefire_reports(root, "001-sweep-suite")
    run_matrices.append(build_matrix_from_snapshot(root, snapshot, "001-sweep-suite"))

    # --- Class-level runs for focus classes ---
    for class_name in sorted(focus_classes):
        if not class_name:
            continue
        result = run_maven_test(
            root, test_filter=class_name, timeout=timeout,
            label=f"sweep-class:{class_name}",
        )
        result["class_name"] = class_name
        runs.append(result)
        snapshot = snapshot_surefire_reports(root, f"sweep-class-{class_name}")
        run_matrices.append(build_matrix_from_snapshot(root, snapshot, f"sweep-class-{class_name}"))

    # --- Method-level runs for focus methods ---
    for class_name, method_name in focus_methods[:30]:  # limit to avoid explosion
        if not class_name or not method_name:
            continue
        result = run_maven_test(
            root, test_filter=f"{class_name}#{method_name}", timeout=timeout,
            label=f"sweep-method:{class_name}#{method_name}",
        )
        result["class_name"] = class_name
        result["method_name"] = method_name
        runs.append(result)
        snapshot = snapshot_surefire_reports(root, f"sweep-method-{class_name}-{method_name}")
        run_matrices.append(build_matrix_from_snapshot(root, snapshot, f"sweep-method-{class_name}-{method_name}"))

    # --- Merge per-run matrices ---
    matrix = merge_run_matrices(root, run_matrices, run_id="explorer-sweep")
    save_matrix(matrix_dir / "current_test_matrix.json", matrix)

    # --- Find unmasked (newly visible) failures ---
    unmasked = _find_newly_visible(matrix, previous_matrix) if previous_matrix else []

    # Persist
    runner.append_jsonl(matrix_dir / "blackbox_explorer_runs.jsonl", runs)
    if unmasked:
        runner.append_jsonl(matrix_dir / "unmasked_failures.jsonl", unmasked)

    return {"runs": runs, "matrix": matrix, "unmasked_failures": unmasked}


def run_shuffle_exploration(root: Path, timeout: int, seeds: list[int]) -> dict[str, Any]:
    """Run public tests with Surefire random run order and merge outcomes."""
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"
    runs: list[dict[str, Any]] = []
    run_matrices: list[dict[str, Any]] = []

    for seed in seeds:
        result = run_maven_test(
            root,
            timeout=timeout,
            label=f"shuffle:{seed}",
            extra_maven_args=["-Dsurefire.runOrder=random", f"-Dsurefire.runOrder.random.seed={seed}"],
        )
        result["shuffle_seed"] = seed
        runs.append(result)
        snapshot = snapshot_surefire_reports(root, f"shuffle-{seed}")
        run_matrices.append(build_matrix_from_snapshot(root, snapshot, f"shuffle-{seed}"))

    matrix = merge_run_matrices(root, run_matrices, run_id="explorer-shuffle")
    save_matrix(matrix_dir / "current_test_matrix.json", matrix)
    runner.append_jsonl(matrix_dir / "blackbox_explorer_runs.jsonl", runs)
    return {"runs": runs, "matrix": matrix, "unmasked_failures": []}


def run_focused_exploration(root: Path, timeout: int, focused: str) -> dict[str, Any]:
    """Run one focused class or method filter and write a current matrix."""
    paths = runner.RunnerPaths(root)
    matrix_dir = paths.work / "test_matrix"
    result = run_maven_test(root, test_filter=focused, timeout=timeout, label=f"focused:{focused}")
    snapshot = snapshot_surefire_reports(root, f"focused-{focused}")
    matrix = merge_run_matrices(
        root,
        [build_matrix_from_snapshot(root, snapshot, f"focused-{focused}")],
        run_id="explorer-focused",
    )
    save_matrix(matrix_dir / "current_test_matrix.json", matrix)
    runner.append_jsonl(matrix_dir / "blackbox_explorer_runs.jsonl", [result])
    return {"runs": [result], "matrix": matrix, "unmasked_failures": []}


def _find_unmasked_failures(
    matrix: dict[str, Any],
    class_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Identify failures that appear in class-level but not suite-level runs."""
    unmasked: list[dict[str, Any]] = []
    suite_failed_classes = {
        r["class_name"]
        for r in matrix.get("matrix", [])
        if r["outcome"] in ("FAILURE", "ERROR")
    }

    # Class-level results that failed but class wasn't in suite failures
    # (suggesting they were masked in the suite run)
    for cr in class_results:
        class_name = cr.get("class_name", "")
        if not cr["passed"] and class_name not in suite_failed_classes:
            unmasked.append({
                "class_name": class_name,
                "discovery": "class_level_unmasked",
                "suite_passed": True,
                "class_failed": True,
                "label": cr.get("label", ""),
            })

    return unmasked


def _find_newly_visible(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Find failures newly visible compared to previous matrix."""
    if not previous:
        return []

    newly_visible: list[dict[str, Any]] = []
    prev_map: dict[tuple[str, str], str] = {}
    for r in previous.get("matrix", []):
        key = (r["class_name"], r["method_name"])
        prev_map[key] = r.get("outcome", "")

    for r in current.get("matrix", []):
        key = (r["class_name"], r["method_name"])
        prev_outcome = prev_map.get(key, "NOT_SEEN")
        current_outcome = r.get("outcome", "")

        # Previously NOT_RUN or not seen, now FAILURE/ERROR → UNMASKED
        if prev_outcome in ("NOT_RUN", "NOT_SEEN", "") and current_outcome in ("FAILURE", "ERROR"):
            newly_visible.append({
                "class_name": r["class_name"],
                "method_name": r["method_name"],
                "previous_outcome": prev_outcome,
                "current_outcome": current_outcome,
                "change": "UNMASKED",
                "failure_kind": r.get("failure_kind", ""),
                "message": r.get("message", ""),
                "stack_top": r.get("stack_top", ""),
            })

    return newly_visible


def _infer_test_class_from_code_path(code_diff_line: str) -> str:
    """Best-effort: infer likely test class from code path changes."""
    # e.g., "+++ b/code/.../UserController.java" → maybe "UserControllerTest"
    path = code_diff_line.replace("+++ b/", "").strip()
    stem = Path(path).stem
    if stem.endswith("Controller"):
        return stem.replace("Controller", "") + "ControllerTest"
    if stem.endswith("Service") or stem.endswith("ServiceImpl"):
        return stem.replace("ServiceImpl", "").replace("Service", "") + "ServiceTest"
    return ""


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_matrix(output_path: Path, matrix: dict[str, Any]) -> Path:
    runner.write_json(output_path, matrix)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore black-box tests at suite/class/method granularity."
    )
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--mode", default="baseline",
                        choices=["baseline", "sweep", "shuffle", "focused"],
                        help="Exploration mode (default: baseline).")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Timeout per Maven run in seconds.")
    parser.add_argument("--previous-matrix", default=None,
                        help="Path to previous test matrix JSON (for sweep mode).")
    parser.add_argument("--output-dir", default=None,
                        help="Override output directory for matrix files.")
    parser.add_argument("--focused", default=None,
                        help="Focused test filter for --mode focused, e.g. ClassName#methodName.")
    parser.add_argument("--shuffle-seeds", default="1,2,3",
                        help="Comma-separated seeds for --mode shuffle.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    # Ensure test_matrix directory exists
    matrix_dir = paths.work / "test_matrix"
    matrix_dir.mkdir(parents=True, exist_ok=True)

    previous = None
    if args.previous_matrix:
        previous = runner.read_json(Path(args.previous_matrix), {})

    if args.mode == "baseline":
        result = run_baseline_exploration(root, timeout=args.timeout)
    elif args.mode == "sweep":
        result = run_sweep_exploration(root, timeout=args.timeout, previous_matrix=previous)
    elif args.mode == "shuffle":
        seeds = [int(part) for part in args.shuffle_seeds.split(",") if part.strip()]
        result = run_shuffle_exploration(root, timeout=args.timeout, seeds=seeds or [1, 2, 3])
    else:
        if not args.focused:
            print("ERROR: --mode focused requires --focused ClassName[#methodName]", file=sys.stderr)
            return 2
        result = run_focused_exploration(root, timeout=args.timeout, focused=args.focused)

    # Print summary
    matrix = result["matrix"]
    summary = matrix.get("summary", {})
    print(f"Blackbox Explorer ({args.mode}): {summary.get('total', 0)} methods")
    print(f"  PASS={summary.get('pass', 0)} FAILURE={summary.get('failure', 0)} "
          f"ERROR={summary.get('error', 0)} SKIPPED={summary.get('skipped', 0)} "
          f"NOT_RUN={summary.get('not_run', 0)}")
    unmasked = result.get("unmasked_failures", [])
    if unmasked:
        print(f"  ⚠️  {len(unmasked)} newly unmasked failure(s):")
        for uf in unmasked[:10]:
            print(f"    - {uf.get('class_name')}#{uf.get('method_name', '')}: "
                  f"{uf.get('previous_outcome')} → {uf.get('current_outcome')}")

    return 0 if summary.get("all_green", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
