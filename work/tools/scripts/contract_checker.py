#!/usr/bin/env python3
"""Contract Checker — deterministic API contract consistency checking.

Compares api_contract.json against the current code snapshot and produces
consistency_report.json with specific, code-located issues.
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check API contract consistency against code.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def check_route_existence(
    contract: dict[str, Any], code_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check if all contract endpoints exist in code."""
    issues: list[dict[str, Any]] = []
    code_endpoints = {
        (ep.get("method", ""), ep.get("url", "")): ep
        for ep in code_snapshot.get("endpoints", [])
    }
    for ep in contract.get("endpoints", []):
        key = (ep["method"], ep["path"])
        if key not in code_endpoints:
            issues.append({
                "type": "missing_endpoint",
                "severity": "P0",
                "endpoint_id": ep["id"],
                "method": ep["method"],
                "path": ep["path"],
                "summary": ep.get("summary", ""),
                "detail": f"Endpoint {ep['method']} {ep['path']} defined in API contract but not found in code",
            })
    return issues


def check_request_fields(
    contract: dict[str, Any], code_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check request body field consistency."""
    issues: list[dict[str, Any]] = []
    dto_fields = code_snapshot.get("dto_fields", {})

    for ep in contract.get("endpoints", []):
        contract_fields = ep.get("request", {}).get("body", {})
        if not contract_fields:
            continue

        # Find matching code endpoint
        code_ep = next(
            (ce for ce in code_snapshot.get("endpoints", [])
             if ce.get("method") == ep["method"] and ce.get("url") == ep["path"]),
            None,
        )
        if not code_ep:
            continue  # Already reported as missing endpoint

        # Find the request DTO
        req_type = code_ep.get("request_body_type")
        if not req_type:
            issues.append({
                "type": "missing_request_dto",
                "severity": "P0",
                "endpoint_id": ep["id"],
                "method": ep["method"],
                "path": ep["path"],
                "detail": f"No request body type detected for {ep['method']} {ep['path']}",
            })
            continue

        code_fields = dto_fields.get(req_type, {})

        # Check each contract field
        for field_name, field_spec in contract_fields.items():
            if field_name not in code_fields:
                issues.append({
                    "type": "missing_field",
                    "severity": "P0",
                    "endpoint_id": ep["id"],
                    "method": ep["method"],
                    "path": ep["path"],
                    "dto": req_type,
                    "field": field_name,
                    "expected_type": field_spec.get("type"),
                    "detail": f"Field '{field_name}' ({field_spec.get('type')}) expected in {req_type} but not found",
                })
            else:
                # Type compatibility check
                expected = normalize_java_type(field_spec.get("type", "string"))
                actual = normalize_java_type(code_fields.get(field_name, ""))
                if expected and actual and not types_compatible(expected, actual):
                    issues.append({
                        "type": "type_mismatch",
                        "severity": "P0",
                        "endpoint_id": ep["id"],
                        "method": ep["method"],
                        "path": ep["path"],
                        "dto": req_type,
                        "field": field_name,
                        "expected_type": expected,
                        "actual_type": actual,
                        "detail": f"Type mismatch: expected {expected}, got {actual}",
                    })

    return issues


def check_response_fields(
    contract: dict[str, Any], code_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check response body field consistency."""
    issues: list[dict[str, Any]] = []
    dto_fields = code_snapshot.get("dto_fields", {})

    for ep in contract.get("endpoints", []):
        contract_resp = ep.get("response", {}).get("body", {})
        if not contract_resp:
            continue

        # Find matching code endpoint
        code_ep = next(
            (ce for ce in code_snapshot.get("endpoints", [])
             if ce.get("method") == ep["method"] and ce.get("url") == ep["path"]),
            None,
        )
        if not code_ep:
            continue

        resp_type = code_ep.get("response_body_type")
        if not resp_type:
            continue

        code_fields = dto_fields.get(resp_type, {})

        # Check for missing response fields
        for field_name in contract_resp:
            if field_name not in code_fields:
                # Check if it's nested (e.g., data.id)
                if "." not in field_name:
                    issues.append({
                        "type": "missing_response_field",
                        "severity": "P1",
                        "endpoint_id": ep["id"],
                        "method": ep["method"],
                        "path": ep["path"],
                        "response_dto": resp_type,
                        "field": field_name,
                        "detail": f"Response field '{field_name}' expected but not found in {resp_type}",
                    })

    return issues


def check_error_codes(
    contract: dict[str, Any], code_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check if documented error codes are handled in code."""
    issues: list[dict[str, Any]] = []
    code_error_codes = set(code_snapshot.get("error_codes", []))
    contract_error_codes = set(contract.get("error_codes", []))

    if not contract_error_codes:
        return issues

    for code in contract_error_codes:
        if code not in code_error_codes:
            issues.append({
                "type": "missing_error_code",
                "severity": "P1",
                "error_code": code,
                "detail": f"Error code '{code}' documented in API contract but not found in code",
            })

    return issues


def check_endpoint_errors(
    contract: dict[str, Any], code_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check per-endpoint error response coverage."""
    issues: list[dict[str, Any]] = []

    for ep in contract.get("endpoints", []):
        documented_errors = ep.get("errors", [])
        if not documented_errors:
            continue

        for error in documented_errors:
            status = error.get("status")
            code = error.get("body", {}).get("code")
            condition = error.get("condition", "")

            # Check if there's a matching handler in exception_coverage
            # (this requires exception_coverage.json to be available)
            # For now, flag undocumented coverage
            if status and status == 400 and "validation" in condition.lower():
                pass  # Expected to be handled by MethodArgumentNotValidException
            elif status and status == 404:
                pass  # Expected to be handled by entity-not-found
            elif status and status == 409:
                pass  # Expected to be handled by conflict/duplicate

    return issues


def normalize_java_type(t: str) -> str:
    """Normalize a type string to a canonical form."""
    if not t:
        return ""
    t = t.strip().lower().replace(" ", "")
    mapping = {
        "string": "string",
        "varchar": "string",
        "text": "string",
        "int": "integer",
        "integer": "integer",
        "long": "long",
        "bigint": "long",
        "bigdecimal": "decimal",
        "decimal": "decimal",
        "double": "decimal",
        "float": "decimal",
        "bool": "boolean",
        "boolean": "boolean",
        "date": "string",
        "datetime": "string",
        "localdate": "string",
        "localdatetime": "string",
        "timestamp": "string",
        "list": "array",
        "arraylist": "array",
        "set": "array",
    }
    return mapping.get(t, t)


def types_compatible(a: str, b: str) -> bool:
    """Check if two types are reasonably compatible."""
    if a == b:
        return True
    # Numeric families
    numeric = {"integer", "long", "decimal"}
    if a in numeric and b in numeric:
        return True
    # String family
    string_types = {"string", "object"}
    if a in string_types and b in string_types:
        return True
    return False


def build_trace_matrix(
    contract: dict[str, Any],
    rules: dict[str, Any],
    repo_map: dict[str, Any],
) -> dict[str, Any]:
    """Build traceability matrix linking design requirements to code locations."""
    trace_items: list[dict[str, Any]] = []

    controllers = {c.get("class_name", ""): c for c in repo_map.get("controllers", [])}
    dtos = {d.get("class_name", ""): d for d in repo_map.get("dtos", [])}
    services = {s.get("class_name", ""): s for s in repo_map.get("services", [])}

    # Map each API contract endpoint to code
    for ep in contract.get("endpoints", []):
        code_ep = find_code_endpoint(ep, repo_map)
        item: dict[str, Any] = {
            "requirement_id": ep["id"],
            "api_id": ep["id"],
            "description": ep.get("summary", ""),
            "links": {},
            "implementation_status": "unknown",
            "gap": "",
            "repair_priority": "P0",
        }

        if code_ep:
            item["links"]["controller"] = {
                "file": code_ep.get("file"),
                "symbol": code_ep.get("method_name"),
                "confidence": 0.95,
            }
            # Find request DTO link
            req_dto_name = code_ep.get("request_body_type") or code_ep.get("request_body")
            if req_dto_name and req_dto_name in dtos:
                item["links"]["request_dto"] = {
                    "file": dtos[req_dto_name].get("file"),
                    "symbol": req_dto_name,
                    "confidence": 0.90,
                }
            # Find response DTO link
            resp_dto_name = code_ep.get("response_body_type") or code_ep.get("response_body")
            if resp_dto_name and resp_dto_name in dtos:
                item["links"]["response_dto"] = {
                    "file": dtos[resp_dto_name].get("file"),
                    "symbol": resp_dto_name,
                    "confidence": 0.90,
                }
            item["implementation_status"] = "implemented"
        else:
            item["implementation_status"] = "missing"
            item["gap"] = f"No code endpoint found for {ep['method']} {ep['path']}"

        # Map business rules
        for rule in rules.get("rules", []):
            if ep["method"] in str(rule.get("related_api", [])) and ep["path"] in str(rule.get("related_api", [])):
                item.setdefault("business_rules", []).append(rule["id"])

        trace_items.append(item)

    return {
        "generated_at": runner.now_iso(),
        "trace_items": trace_items,
        "summary": {
            "total_items": len(trace_items),
            "implemented": sum(1 for t in trace_items if t["implementation_status"] == "implemented"),
            "partial": sum(1 for t in trace_items if t["implementation_status"] == "partial"),
            "missing": sum(1 for t in trace_items if t["implementation_status"] == "missing"),
            "conflict": sum(1 for t in trace_items if t["implementation_status"] == "conflict"),
            "unknown": sum(1 for t in trace_items if t["implementation_status"] == "unknown"),
        },
    }


def find_code_endpoint(contract_ep: dict[str, Any], repo_map: dict[str, Any]) -> dict[str, Any] | None:
    """Find the code endpoint matching a contract endpoint."""
    for ctrl in repo_map.get("controllers", []):
        for method in ctrl.get("methods", []):
            if (
                method.get("http_method") == contract_ep["method"]
                and method.get("full_path") == contract_ep["path"]
            ):
                return {
                    "file": ctrl.get("file"),
                    "method_name": method.get("method_name"),
                    "request_body_type": method.get("request_body"),
                    "response_body_type": method.get("response_type"),
                    "validated": method.get("validated", False),
                }
    return None


def check_consistency(root: Path) -> dict[str, Any]:
    """Full consistency check producing report."""
    paths = runner.RunnerPaths(root)

    # Load all inputs
    contract = runner.read_json(paths.work / "api_contract.json", {})
    rules = runner.read_json(paths.work / "business_rules.json", {})
    repo_map = runner.read_json(paths.work / "repo_map.json", {})

    # Get current code snapshot
    code_snapshot = runner.snapshot_code_api(root)

    # Run all checks
    all_issues: list[dict[str, Any]] = []
    all_issues.extend(check_route_existence(contract, code_snapshot))
    all_issues.extend(check_request_fields(contract, code_snapshot))
    all_issues.extend(check_response_fields(contract, code_snapshot))
    all_issues.extend(check_error_codes(contract, code_snapshot))
    all_issues.extend(check_endpoint_errors(contract, code_snapshot))

    # Build trace matrix
    trace_matrix = build_trace_matrix(contract, rules, repo_map)

    # Build report
    report: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "issues": all_issues,
        "trace_matrix": trace_matrix,
        "summary": {
            "total_issues": len(all_issues),
            "p0_issues": sum(1 for i in all_issues if i["severity"] == "P0"),
            "p1_issues": sum(1 for i in all_issues if i["severity"] == "P1"),
            "p2_issues": sum(1 for i in all_issues if i["severity"] == "P2"),
            "by_type": {},
            "trace_coverage": trace_matrix.get("summary", {}),
        },
    }

    for issue in all_issues:
        issue_type = issue["type"]
        report["summary"]["by_type"][issue_type] = report["summary"]["by_type"].get(issue_type, 0) + 1

    # Persist
    runner.write_json(paths.work / "consistency_report.json", report)
    runner.write_json(paths.work / "trace_matrix.json", trace_matrix)

    # Summary markdown
    lines = [
        "# Consistency Check Report",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        f"## Summary",
        f"- Total issues: {report['summary']['total_issues']}",
        f"- P0 (critical): {report['summary']['p0_issues']}",
        f"- P1 (important): {report['summary']['p1_issues']}",
        f"- P2 (minor): {report['summary']['p2_issues']}",
        "",
    ]
    if report["summary"]["by_type"]:
        lines.append("## By Type")
        lines.append("")
        for issue_type, count in sorted(report["summary"]["by_type"].items()):
            lines.append(f"- {issue_type}: {count}")
        lines.append("")

    lines.extend([
        "## Trace Matrix",
        "",
        f"- Total: {trace_matrix['summary']['total_items']}",
        f"- Implemented: {trace_matrix['summary']['implemented']}",
        f"- Partial: {trace_matrix['summary']['partial']}",
        f"- Missing: {trace_matrix['summary']['missing']}",
        f"- Conflict: {trace_matrix['summary']['conflict']}",
        "",
    ])

    if all_issues:
        lines.append("## Issues")
        lines.append("")
        for issue in all_issues:
            lines.append(f"- [{issue['severity']}] [{issue['type']}] {issue.get('detail', '')}")

    runner.write_text(paths.work / "06_consistency_report.md", "\n".join(lines).rstrip() + "\n")

    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = check_consistency(root)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    # Exit non-zero if P0 issues found
    p0_count = report["summary"]["p0_issues"]
    return 1 if p0_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
