#!/usr/bin/env python3
"""Exception Analyzer — scans ExceptionHandler coverage and error response compliance.

Produces ``exception_coverage.json`` comparing code exception handling against API baseline.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


EXPECTED_HANDLERS = {
    "MethodArgumentNotValidException": {
        "status": 400,
        "code": "VALIDATION_ERROR",
        "description": "Request body validation failure",
    },
    "ConstraintViolationException": {
        "status": 400,
        "code": "VALIDATION_ERROR",
        "description": "Path/query parameter validation failure",
    },
    "BindException": {
        "status": 400,
        "code": "VALIDATION_ERROR",
        "description": "Request binding failure",
    },
    "HttpMessageNotReadableException": {
        "status": 400,
        "code": "BAD_REQUEST",
        "description": "Malformed JSON / unreadable request",
    },
    "HttpRequestMethodNotSupportedException": {
        "status": 405,
        "code": "METHOD_NOT_ALLOWED",
        "description": "Wrong HTTP method",
    },
    "HttpMediaTypeNotSupportedException": {
        "status": 415,
        "code": "UNSUPPORTED_MEDIA_TYPE",
        "description": "Wrong Content-Type",
    },
    "MissingServletRequestParameterException": {
        "status": 400,
        "code": "BAD_REQUEST",
        "description": "Missing required request parameter",
    },
    "DataIntegrityViolationException": {
        "status": 409,
        "code": "CONFLICT",
        "description": "Database constraint violation (duplicate, etc.)",
    },
    "EntityNotFoundException": {
        "status": 404,
        "code": "NOT_FOUND",
        "description": "Entity not found (JPA)",
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze exception handler coverage.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def scan_exception_handlers(code_dir: Path, root: Path) -> list[dict[str, Any]]:
    """Scan all @ExceptionHandler methods across @ControllerAdvice classes."""
    handlers: list[dict[str, Any]] = []
    for path in sorted(code_dir.rglob("*.java")):
        text = runner.read_text(path)
        if "@ControllerAdvice" not in text and "@RestControllerAdvice" not in text:
            continue

        clean = runner.strip_java_comments(text)
        rel_path = runner.rel(root, path)

        # Enhanced handler extraction with status detection
        handler_blocks = re.split(r"@ExceptionHandler", clean)[1:]  # Skip text before first handler

        for block in handler_blocks:
            # Exception type
            type_match = re.match(r"\s*\(\s*([A-Za-z0-9_.]+)\.class\s*\)", block)
            if not type_match:
                continue
            exception_type = type_match.group(1).split(".")[-1]

            # Response status from @ResponseStatus
            status_match = re.search(
                r"@ResponseStatus\s*\(\s*(?:value\s*=\s*)?(?:HttpStatus\.)?(\w+)",
                block[:300],
            )
            http_status = status_match.group(1) if status_match else None

            # Response status from ResponseEntity
            response_match = re.search(
                r"return\s+(?:new\s+)?ResponseEntity\s*(?:<[^>]*>)?\s*\([^,]+,\s*(?:HttpStatus\.)?(\w+)",
                block,
            )
            if not http_status and response_match:
                http_status = response_match.group(1)

            # Extract error code from ApiResponse.error("CODE", ...)
            code_match = re.search(r'ApiResponse\.error\s*\(\s*"([A-Z_]+)"', block)
            error_code = code_match.group(1) if code_match else None

            # Check for swallow patterns
            has_swallow = bool(re.search(r"catch\s*\(\s*Exception\s", block))
            returns_200 = bool(re.search(r'return.*ok\(\)', block, re.I)) and http_status is None

            handlers.append({
                "file": rel_path,
                "exception_type": exception_type,
                "http_status": http_status or "UNKNOWN",
                "error_code": error_code,
                "has_swallow_pattern": has_swallow,
                "returns_200_unconditionally": returns_200,
            })

    return handlers


def analyze_exceptions(root: Path) -> dict[str, Any]:
    """Full exception analyzer producing coverage report."""
    paths = runner.RunnerPaths(root)
    code_dir = root / "code"

    report: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "handlers": [],
        "coverage": {},
        "gaps": [],
        "warnings": [],
    }

    if not code_dir.exists():
        report["warnings"].append("code/ does not exist.")
        runner.write_json(paths.work / "exception_coverage.json", report)
        return report

    handlers = scan_exception_handlers(code_dir, root)
    report["handlers"] = handlers

    # Check coverage against expected handlers
    handled_types = {h["exception_type"] for h in handlers}
    for ex_type, expected in EXPECTED_HANDLERS.items():
        coverage = "covered" if ex_type in handled_types else "missing"
        report["coverage"][ex_type] = coverage
        if coverage == "missing":
            report["gaps"].append({
                "exception_type": ex_type,
                "expected_status": expected["status"],
                "expected_code": expected["code"],
                "description": expected["description"],
                "impact": f"No handler for {ex_type} — may result in 500 error or unfriendly response",
            })
        else:
            # Check status and code for covered handlers
            handler = next((h for h in handlers if h["exception_type"] == ex_type), None)
            if handler:
                issues = []
                status_str = str(handler["http_status"])
                if status_str != str(expected["status"]) and status_str not in (
                    "BAD_REQUEST", "NOT_FOUND", "CONFLICT", "UNKNOWN"
                ):
                    issues.append(f"status={handler['http_status']}, expected={expected['status']}")
                if handler["error_code"] and handler["error_code"] != expected["code"]:
                    issues.append(f"code={handler['error_code']}, expected={expected['code']}")
                if issues:
                    report["gaps"].append({
                        "exception_type": ex_type,
                        "handler_file": handler["file"],
                        "issues": issues,
                    })

    # Check for anti-patterns
    for handler in handlers:
        if handler["has_swallow_pattern"]:
            report["warnings"].append(
                f"WARNING: {handler['file']} handler for {handler['exception_type']} "
                f"has catch(Exception) — possible exception swallowing"
            )
        if handler["returns_200_unconditionally"]:
            report["warnings"].append(
                f"WARNING: {handler['file']} handler for {handler['exception_type']} "
                f"may return 200 unconditionally — violates error semantics"
            )

    # Check if ApiResponse wrapper is consistent
    has_api_response = any(
        "@RestControllerAdvice" in runner.read_text(code_dir / "**" / "*.java")
        for _ in [1]  # Simple flag
    )

    runner.write_json(paths.work / "exception_coverage.json", report)

    # Summary
    summary_lines = [
        "# Exception Handler Coverage",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        f"Handlers found: {len(handlers)}",
        f"Coverage gaps: {len(report['gaps'])}",
        f"Warnings: {len(report['warnings'])}",
        "",
    ]
    for ex_type, cov in sorted(report["coverage"].items()):
        summary_lines.append(f"- {ex_type}: **{cov}**")
    if report["gaps"]:
        summary_lines.extend(["", "## Gaps", ""])
        for gap in report["gaps"]:
            summary_lines.append(f"- `{gap['exception_type']}` — {gap.get('description', gap.get('issues', ''))}")
    if report["warnings"]:
        summary_lines.extend(["", "## Warnings", ""])
        for w in report["warnings"]:
            summary_lines.append(f"- {w}")

    runner.write_text(paths.work / "05_exception_coverage.md", "\n".join(summary_lines).rstrip() + "\n")

    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = analyze_exceptions(root)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
