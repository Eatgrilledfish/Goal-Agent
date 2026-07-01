#!/usr/bin/env python3
"""Test Outcome Collector — parse Surefire XML, output method-level test outcome matrix.

Replaces coarse Maven-stdout-based test result interpretation with structured,
method-level outcomes. Each test method gets a record with:
  - suite, class_name, method_name
  - outcome: PASS / FAILURE / ERROR / SKIPPED / TIMEOUT / NOT_RUN
  - failure_kind, message, stack_top
  - related_endpoint, related_module, related_design_rule (best-effort)
  - run_id, source_xml, source_log

Usage:
  python3 test_outcome_collector.py --root . --suite blackbox
  python3 test_outcome_collector.py --root . --suite blackbox --output .agent-work/test_matrix/baseline_test_matrix.json
  python3 test_outcome_collector.py --root . --suite all --discover-tests
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


TEST_ANNOTATION_RE = re.compile(r'@(Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b')
DISABLED_ANNOTATION_RE = re.compile(
    r'@(Disabled|DisabledIf|DisabledOnOs|DisabledOnJre|EnabledIf|EnabledOnOs|EnabledOnJre)\b'
)


# ---------------------------------------------------------------------------
# Surefire XML discovery
# ---------------------------------------------------------------------------

def find_surefire_reports(root: Path, suite_filter: str = "all") -> list[Path]:
    """Discover TEST-*.xml files under code/ and test-cases/ target directories.

    Args:
        root: Project root.
        suite_filter: One of "all", "code-unit", "blackbox-public", "generated-spec".

    Returns:
        Sorted list of absolute paths to TEST-*.xml files.
    """
    reports: list[Path] = []

    # code module Surefire reports (unit tests + generated spec tests)
    if suite_filter in ("all", "code-unit", "generated-spec"):
        for pattern in ("code/**/target/surefire-reports/TEST-*.xml",
                         "code/**/target/failsafe-reports/TEST-*.xml"):
            reports.extend(sorted(root.glob(pattern)))

    # test-cases black-box Surefire reports
    if suite_filter in ("all", "blackbox-public"):
        for pattern in ("test-cases/**/target/surefire-reports/TEST-*.xml",
                         "test-cases/**/target/failsafe-reports/TEST-*.xml"):
            reports.extend(sorted(root.glob(pattern)))

    return sorted(set(reports))


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def parse_surefire_xml(xml_path: Path) -> list[dict[str, Any]]:
    """Parse a single TEST-*.xml and return a list of testcase outcome records.

    Each record is a dict with keys:
      class_name, method_name, outcome, failure_kind, message, stack_top,
      time_seconds, source_xml
    """
    records: list[dict[str, Any]] = []
    try:
        tree = ET.parse(str(xml_path))
        root_node = tree.getroot()
    except ET.ParseError as exc:
        # Corrupted or empty XML — emit a warning record
        return [{
            "class_name": xml_path.stem.replace("TEST-", ""),
            "method_name": "__parse_error__",
            "outcome": "ERROR",
            "failure_kind": "XMLParseError",
            "message": f"Failed to parse {xml_path.name}: {exc}",
            "stack_top": "",
            "time_seconds": 0.0,
            "source_xml": str(xml_path),
        }]

    for testcase in root_node.findall("testcase"):
        class_name = testcase.get("classname", "")
        method_name = _normalize_method_name(testcase.get("name", ""))
        time_seconds = float(testcase.get("time", 0) or 0)

        failure = testcase.find("failure")
        error = testcase.find("error")
        skipped = testcase.find("skipped")

        if failure is not None:
            outcome = "FAILURE"
            failure_kind = _classify_failure(failure.get("type", ""),
                                              failure.get("message", ""),
                                              failure.text or "")
            message = _truncate(failure.get("message", ""), 500)
            stack_top = _extract_stack_top(failure.text or "")
        elif error is not None:
            outcome = "ERROR"
            failure_kind = _classify_failure(error.get("type", ""),
                                              error.get("message", ""),
                                              error.text or "")
            message = _truncate(error.get("message", ""), 500)
            stack_top = _extract_stack_top(error.text or "")
        elif skipped is not None:
            outcome = "SKIPPED"
            failure_kind = "Skipped"
            message = _truncate(skipped.get("message", ""), 500)
            stack_top = ""
        else:
            outcome = "PASS"
            failure_kind = ""
            message = ""
            stack_top = ""

        # Derive simple class name (strip package prefix)
        simple_class = class_name.split(".")[-1] if class_name else ""

        records.append({
            "suite": _infer_suite(str(xml_path)),
            "class_name": simple_class or class_name,
            "full_class_name": class_name,
            "method_name": method_name,
            "outcome": outcome,
            "failure_kind": failure_kind,
            "message": message,
            "stack_top": stack_top,
            "time_seconds": time_seconds,
            "source_xml": str(xml_path),
            "run_id": "",
        })

    return records


def _infer_suite(xml_path: str) -> str:
    """Infer test suite from XML path."""
    normalized = xml_path.replace("\\", "/")
    if "test-cases" in normalized:
        return "blackbox-public"
    if "generated" in normalized.lower():
        return "generated-spec"
    return "code-unit"


def _normalize_method_name(value: str) -> str:
    """Normalize JUnit5 parameterized display names back to source method names."""
    value = value.strip()
    value = re.sub(r"\(.*\)$", "", value)
    value = re.sub(r"\[[^\]]+\]$", "", value)
    return value


def _classify_failure(exception_type: str, message: str, stack_text: str) -> str:
    """Classify failure kind from exception type and message."""
    combined = f"{exception_type}\n{message}\n{stack_text}"

    if "AssertionError" in exception_type or "AssertionFailedError" in exception_type:
        return "AssertionError"
    if "NullPointerException" in exception_type:
        return "NullPointerException"
    if "SQL" in exception_type or "DataIntegrity" in exception_type or "ConstraintViolation" in exception_type:
        return "SQLConstraintViolation"
    if "Timeout" in exception_type:
        return "TimeoutException"
    if "ApplicationContext" in combined or "BeanCreation" in combined:
        return "ApplicationContextFailure"
    if "NoSuchBean" in combined or "NoSuchBeanDefinition" in combined:
        return "NoSuchBeanDefinition"
    if "404" in combined or "NotFound" in exception_type or "NoHandlerFound" in exception_type:
        return "Http404NotFound"
    if "400" in combined or "BadRequest" in exception_type:
        return "Http400BadRequest"
    if "403" in combined or "AccessDenied" in exception_type or "Forbidden" in exception_type:
        return "Http403Forbidden"
    if "409" in combined or "Conflict" in exception_type:
        return "Http409Conflict"
    if "500" in combined or "InternalServerError" in exception_type:
        return "Http500InternalError"
    if "MethodArgumentNotValid" in exception_type or "BindException" in exception_type:
        return "ValidationError"
    if "HttpMessageNotReadable" in exception_type or "Json" in exception_type:
        return "SerializationError"
    if "ClassNotFound" in exception_type or "NoClassDefFound" in exception_type:
        return "ClassNotFoundError"
    if "OutOfMemory" in exception_type:
        return "OutOfMemoryError"
    if "IO" in exception_type or "IOException" in exception_type:
        return "IOException"
    if "IllegalArgument" in exception_type or "IllegalState" in exception_type:
        return "IllegalStateError"

    return exception_type or "UnknownError"


def _extract_stack_top(stack_text: str) -> str:
    """Extract the top-most meaningful stack frame."""
    if not stack_text:
        return ""
    lines = stack_text.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("at ") and "java" not in stripped.split("(")[0]:
            return stripped
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("at "):
            return stripped
    return lines[0].strip() if lines else ""


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Test source discovery (for NOT_RUN detection)
# ---------------------------------------------------------------------------

def discover_test_sources(root: Path, suite_filter: str = "all") -> list[dict[str, Any]]:
    """Scan test Java source files to build an expected-test inventory.

    Returns a list of {class_name, method_name, source_file, suite}.
    """
    discovered: list[dict[str, Any]] = []

    # Black-box tests under test-cases/
    if suite_filter in ("all", "blackbox-public"):
        for test_file in sorted(root.glob("test-cases/**/*Test.java")):
            discovered.extend(_extract_test_methods(root, test_file, "blackbox-public"))

        for test_file in sorted(root.glob("test-cases/**/*Tests.java")):
            discovered.extend(_extract_test_methods(root, test_file, "blackbox-public"))

    # Code unit tests under code/
    if suite_filter in ("all", "code-unit"):
        for test_file in sorted(root.glob("code/**/src/test/java/**/*Test.java")):
            discovered.extend(_extract_test_methods(root, test_file, "code-unit"))

        for test_file in sorted(root.glob("code/**/src/test/java/**/*Tests.java")):
            discovered.extend(_extract_test_methods(root, test_file, "code-unit"))

    return discovered


def _extract_test_methods(root: Path, test_file: Path, suite: str) -> list[dict[str, Any]]:
    """Extract @Test-annotated methods from a Java test source file."""
    try:
        text = test_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    # Find class name
    class_match = re.search(r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b', text)
    if not class_match:
        return []
    class_name = class_match.group(1)

    # Find test methods
    methods: list[dict[str, Any]] = []
    method_decl = re.compile(
        r'(?:public|protected|private)?\s*(?:static\s+)?'
        r'(?:[A-Za-z0-9_<>, ?.\[\]]+)\s+'
        r'([A-Za-z_][A-Za-z0-9_]*)\s*\('
    )

    lines = text.splitlines()
    pending_annotations: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("@"):
            pending_annotations.append(stripped)
            continue
        if not pending_annotations:
            continue
        annotation_block = "\n".join(pending_annotations)
        method_match = method_decl.search(line)
        if method_match and TEST_ANNOTATION_RE.search(annotation_block):
            method_name = method_match.group(1)
            if method_name not in ("class", "interface", "enum"):
                methods.append({
                    "class_name": class_name,
                    "method_name": method_name,
                    "source_file": str(test_file),
                    "suite": suite,
                    "is_conditionally_disabled": bool(DISABLED_ANNOTATION_RE.search(annotation_block)),
                })
        pending_annotations = []

    return methods


# ---------------------------------------------------------------------------
# Matrix assembly
# ---------------------------------------------------------------------------

def build_test_outcome_matrix(
    root: Path,
    suite_filter: str = "all",
    discover_sources: bool = True,
    run_id: str = "",
    log_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Build the full test outcome matrix.

    Args:
        root: Project root.
        suite_filter: Filter to specific test suite.
        discover_sources: Whether to scan test sources for NOT_RUN detection.
        run_id: Identifier for this run (e.g., "baseline", "round-003").
        log_paths: Optional list of Maven log paths for context.

    Returns:
        A dict with keys: generated_at, run_id, summary, matrix (list of records).
    """
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Phase 1: Parse Surefire XML
    xml_paths = find_surefire_reports(root, suite_filter)
    records: list[dict[str, Any]] = []

    for xml_path in xml_paths:
        parsed = parse_surefire_xml(xml_path)
        for record in parsed:
            record["run_id"] = run_id
            record["source_log"] = log_paths[-1] if log_paths else ""
        records.extend(parsed)

    # Phase 2: Discover test sources and fill NOT_RUN
    if discover_sources:
        discovered = discover_test_sources(root, suite_filter)

        # Build a lookup set of (class_name, method_name) that exist in XML
        xml_keys = {(r["class_name"], r["method_name"]) for r in records}

        for d in discovered:
            key = (d["class_name"], d["method_name"])
            if key not in xml_keys:
                expected_skipped = bool(d.get("is_conditionally_disabled"))
                records.append({
                    "suite": d["suite"],
                    "class_name": d["class_name"],
                    "full_class_name": d["class_name"],
                    "method_name": d["method_name"],
                    "outcome": "EXPECTED_SKIPPED" if expected_skipped else "NOT_RUN",
                    "failure_kind": "",
                    "message": "Conditionally disabled test method"
                    if expected_skipped else "Test method not found in Surefire XML reports",
                    "stack_top": "",
                    "time_seconds": 0.0,
                    "source_xml": "",
                    "source_log": log_paths[-1] if log_paths else "",
                    "source_file": d["source_file"],
                    "run_id": run_id,
                    "is_conditionally_disabled": expected_skipped,
                    "masked_by": _guess_masked_by(records, d["class_name"]),
                })

    # Phase 3: Annotate with best-effort module/endpoint associations
    records = _annotate_with_context(root, records)

    # Phase 4: Build summary
    summary = _build_summary(records)

    return {
        "generated_at": runner.now_iso(),
        "run_id": run_id,
        "suite_filter": suite_filter,
        "source_xml_count": len(xml_paths),
        "summary": summary,
        "matrix": records,
    }


def _guess_masked_by(records: list[dict[str, Any]], class_name: str) -> str:
    """Guess which test might mask this NOT_RUN test.

    If another test in the same class failed, it may have prevented this test from running.
    """
    for r in records:
        if r["class_name"] == class_name and r["outcome"] in ("FAILURE", "ERROR"):
            return f"{class_name}#{r['method_name']} ({r['outcome']})"
    return ""


def _annotate_with_context(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate records with module and endpoint context (best-effort)."""
    discovered = {
        (d.get("class_name"), d.get("method_name")): d
        for d in discover_test_sources(root, "all")
    }
    # Load code map for cross-referencing
    code_map_path = root / ".agent-work" / "code_map.jsonl"
    api_contract_path = root / ".agent-work" / "api_contract.json"

    endpoints_by_class: dict[str, list[str]] = {}
    if api_contract_path.exists():
        contract = runner.read_json(api_contract_path, {})
        for ep in contract.get("endpoints", []):
            url = ep.get("url", "")
            method = ep.get("method", "")
            ep_str = f"{method} {url}"
            endpoints_by_class.setdefault("", []).append(ep_str)

    modules: set[str] = set()
    if code_map_path.exists():
        code_records = runner.read_jsonl(code_map_path)
        for cr in code_records:
            module = cr.get("module", "")
            if module:
                modules.add(module)

    for record in records:
        source_record = discovered.get((record.get("class_name"), record.get("method_name")))
        if source_record:
            record.setdefault("source_file", source_record.get("source_file", ""))
            record["is_conditionally_disabled"] = bool(source_record.get("is_conditionally_disabled"))
            if record.get("outcome") == "SKIPPED" and source_record.get("is_conditionally_disabled"):
                record["outcome"] = "EXPECTED_SKIPPED"

        record.setdefault("related_endpoint", "")
        record.setdefault("related_module", "")
        record.setdefault("related_design_rule", "")

        # Best-effort module matching from class name
        class_lower = record["class_name"].lower()
        for module in sorted(modules):
            module_lower = module.lower()
            if module_lower in class_lower or class_lower in module_lower:
                record["related_module"] = module
                break

    return records


def _build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate summary from matrix records."""
    outcomes = [r["outcome"] for r in records]
    return {
        "total": len(records),
        "pass": outcomes.count("PASS"),
        "failure": outcomes.count("FAILURE"),
        "error": outcomes.count("ERROR"),
        "skipped": outcomes.count("SKIPPED"),
        "expected_skipped": outcomes.count("EXPECTED_SKIPPED"),
        "timeout": outcomes.count("TIMEOUT"),
        "not_run": outcomes.count("NOT_RUN"),
        "all_green": bool(records) and all(o in ("PASS", "EXPECTED_SKIPPED") for o in outcomes),
        "has_failures": "FAILURE" in outcomes,
        "has_errors": "ERROR" in outcomes,
        "has_timeouts": "TIMEOUT" in outcomes,
        "has_unexpected_skipped": "SKIPPED" in outcomes,
        "has_not_run": "NOT_RUN" in outcomes,
    }


# ---------------------------------------------------------------------------
# Matrix persistence helpers
# ---------------------------------------------------------------------------

def save_matrix(matrix: dict[str, Any], output_path: Path) -> Path:
    """Persist test outcome matrix to JSON."""
    runner.write_json(output_path, matrix)
    return output_path


def load_matrix(matrix_path: Path) -> dict[str, Any]:
    """Load a previously saved test outcome matrix."""
    return runner.read_json(matrix_path, {"matrix": [], "summary": {}})


def matrix_is_all_green(matrix: dict[str, Any]) -> bool:
    """Check if the matrix shows all-pass with no anomalies."""
    summary = matrix.get("summary", {})
    return (
        summary.get("all_green", False)
        and summary.get("failure", 0) == 0
        and summary.get("error", 0) == 0
        and summary.get("timeout", 0) == 0
        and summary.get("not_run", 0) == 0
        and summary.get("has_unexpected_skipped", False) is False
    )


def matrix_has_blocking_issues(matrix: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check if matrix has blocking issues. Returns (has_issues, reasons)."""
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse Surefire XML and produce method-level test outcome matrix."
    )
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--suite", default="all",
                        choices=["all", "code-unit", "blackbox-public", "generated-spec"],
                        help="Filter to specific test suite (default: all).")
    parser.add_argument("--output", default=None,
                        help="Output JSON path (default: .agent-work/test_matrix/<run_id>_test_matrix.json).")
    parser.add_argument("--run-id", default="",
                        help="Run identifier (default: auto-generated timestamp).")
    parser.add_argument("--no-discover", action="store_true",
                        help="Skip test source discovery (no NOT_RUN detection).")
    parser.add_argument("--log-paths", nargs="*", default=None,
                        help="Associated Maven log paths for traceability.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    matrix = build_test_outcome_matrix(
        root,
        suite_filter=args.suite,
        discover_sources=not args.no_discover,
        run_id=run_id,
        log_paths=args.log_paths,
    )

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        matrix_dir = paths.work / "test_matrix"
        output_path = matrix_dir / f"{run_id}_test_matrix.json"
        # Also write as current_test_matrix.json for diff comparison
        save_matrix(matrix, matrix_dir / "current_test_matrix.json")

    save_matrix(matrix, output_path)

    # Brief stdout summary
    summary = matrix["summary"]
    print(f"Test Outcome Matrix: {summary['total']} methods")
    print(f"  PASS={summary['pass']} FAILURE={summary['failure']} "
          f"ERROR={summary['error']} SKIPPED={summary['skipped']} "
          f"TIMEOUT={summary['timeout']} NOT_RUN={summary['not_run']}")
    print(f"  All green: {'YES' if summary['all_green'] else 'NO'}")
    print(f"  Output: {output_path}")

    return 0 if summary["all_green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
