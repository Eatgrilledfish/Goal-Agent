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
    parser.add_argument("--save-baseline", action="store_true",
                       help="Save a baseline copy as baseline_consistency_report.json for P0 comparison.")
    return parser


def contract_path(endpoint: dict[str, Any]) -> str:
    return str(endpoint.get("path") or endpoint.get("url") or "")


def contract_id(endpoint: dict[str, Any]) -> str:
    return str(endpoint.get("id") or endpoint.get("endpoint_id") or "")


def contract_request_body(endpoint: dict[str, Any]) -> dict[str, Any]:
    request = endpoint.get("request") or {}
    if isinstance(request, dict) and isinstance(request.get("body"), dict):
        return request.get("body", {})
    body = endpoint.get("request_body") or {}
    return body if isinstance(body, dict) else {}


def contract_response_body(endpoint: dict[str, Any]) -> dict[str, Any]:
    response = endpoint.get("response") or {}
    if isinstance(response, dict) and isinstance(response.get("body"), dict):
        return response.get("body", {})
    body = endpoint.get("response_body") or {}
    return body if isinstance(body, dict) else {}


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
        path = contract_path(ep)
        endpoint_id = contract_id(ep)
        key = (ep.get("method", ""), path)
        if key not in code_endpoints:
            issues.append({
                "type": "missing_endpoint",
                "severity": "P0",
                "endpoint_id": endpoint_id,
                "method": ep.get("method", ""),
                "path": path,
                "summary": ep.get("summary", ""),
                "detail": f"Endpoint {ep.get('method', '')} {path} defined in API contract but not found in code",
            })
    return issues


def check_request_fields(
    contract: dict[str, Any], code_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check request body field consistency."""
    issues: list[dict[str, Any]] = []
    dto_fields = code_snapshot.get("dto_fields", {})

    for ep in contract.get("endpoints", []):
        path = contract_path(ep)
        endpoint_id = contract_id(ep)
        method = ep.get("method", "")
        contract_fields = contract_request_body(ep)
        if not contract_fields:
            continue

        # Find matching code endpoint
        code_ep = next(
            (ce for ce in code_snapshot.get("endpoints", [])
             if ce.get("method") == method and ce.get("url") == path),
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
                "endpoint_id": endpoint_id,
                "method": method,
                "path": path,
                "detail": f"No request body type detected for {method} {path}",
            })
            continue

        code_fields = dto_fields.get(req_type, {})

        # Check each contract field
        for field_name, field_spec in contract_fields.items():
            if field_name not in code_fields:
                issues.append({
                    "type": "missing_field",
                    "severity": "P0",
                    "endpoint_id": endpoint_id,
                    "method": method,
                    "path": path,
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
                        "endpoint_id": endpoint_id,
                        "method": method,
                        "path": path,
                        "dto": req_type,
                        "field": field_name,
                        "expected_type": expected,
                        "actual_type": actual,
                        "detail": f"Type mismatch: expected {expected}, got {actual}",
                    })

    return issues


def unwrap_response_type(response_type: str) -> dict[str, Any]:
    """Parse wrapped response types into their components.

    Input:
      ResponseEntity<ApiResponse<ProductResponse>>
      ApiResponse<List<OrderResponse>>
      Result<PageResult<ProductVO>>

    Output:
      {
        "wrapper": "ApiResponse",
        "inner": "ProductResponse",
        "collection": false,
        "page": false
      }
    """
    result: dict[str, Any] = {
        "wrapper": "",
        "inner": response_type,
        "collection": False,
        "page": False,
    }

    # Strip ResponseEntity
    stripped = response_type
    resp_match = re.match(r"ResponseEntity\s*<\s*(.+)\s*>", stripped, re.I)
    if resp_match:
        stripped = resp_match.group(1).strip()

    # Detect wrapper class
    wrapper_match = re.match(
        r"(ApiResponse|Result|CommonResponse|R|BaseResponse)\s*<\s*(.+)\s*>",
        stripped, re.I,
    )
    if wrapper_match:
        result["wrapper"] = wrapper_match.group(1)
        stripped = wrapper_match.group(2).strip()

    # Detect page wrapper
    page_match = re.match(
        r"(Page|PageResult|IPage|Slice)\s*<\s*(.+)\s*>",
        stripped, re.I,
    )
    if page_match:
        result["page"] = True
        stripped = page_match.group(2).strip()

    # Detect List/Collection
    list_match = re.match(
        r"(List|Set|Collection|ArrayList)\s*<\s*(.+)\s*>",
        stripped, re.I,
    )
    if list_match:
        result["collection"] = True
        stripped = list_match.group(2).strip()

    # Clean generic params
    stripped = re.sub(r"<[^>]+>", "", stripped)
    result["inner"] = stripped.split(".")[-1].strip()
    return result


def check_response_fields(
    contract: dict[str, Any], code_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check response body field consistency, including nested fields in wrapped types."""
    issues: list[dict[str, Any]] = []
    dto_fields = code_snapshot.get("dto_fields", {})

    for ep in contract.get("endpoints", []):
        path = contract_path(ep)
        endpoint_id = contract_id(ep)
        method = ep.get("method", "")
        contract_resp = contract_response_body(ep)
        if not contract_resp:
            continue

        # Find matching code endpoint
        code_ep = next(
            (ce for ce in code_snapshot.get("endpoints", [])
             if ce.get("method") == method and ce.get("url") == path),
            None,
        )
        if not code_ep:
            continue

        resp_type = code_ep.get("response_body_type", "")
        if not resp_type:
            continue

        unwrapped = unwrap_response_type(resp_type)
        inner_type = unwrapped["inner"]
        wrapper_name = unwrapped["wrapper"]

        # Merge fields from wrapper and inner type
        code_fields: dict[str, str] = {}
        if wrapper_name and wrapper_name in dto_fields:
            code_fields.update(dto_fields[wrapper_name])
        if inner_type and inner_type in dto_fields:
            code_fields.update(dto_fields[inner_type])

        # Check each contract response field
        for field_name, field_spec in contract_resp.items():
            # Handle nested fields like data.id, data.items.*
            if "." in field_name:
                parts = field_name.split(".")
                top_field = parts[0]
                nested_field = parts[-1]

                if top_field not in code_fields and top_field not in dto_fields:
                    issues.append({
                        "type": "missing_response_field",
                        "severity": "P1",
                        "endpoint_id": endpoint_id,
                        "method": method,
                        "path": path,
                        "response_type": resp_type,
                        "field": field_name,
                        "detail": (
                            f"Response field '{field_name}' expected but "
                            f"'{top_field}' not found in response type chain"
                        ),
                    })
                    continue

                # For nested fields, check if the inner type contains them
                inner_dto_fields = dto_fields.get(inner_type, {})
                if nested_field not in inner_dto_fields and nested_field not in code_fields:
                    issues.append({
                        "type": "missing_response_field",
                        "severity": "P1",
                        "endpoint_id": endpoint_id,
                        "method": method,
                        "path": path,
                        "response_type": resp_type,
                        "field": field_name,
                        "detail": (
                            f"Contract requires {field_name} but "
                            f"{inner_type} has no '{nested_field}' field"
                        ),
                    })
            elif field_name not in code_fields:
                issues.append({
                    "type": "missing_response_field",
                    "severity": "P1",
                    "endpoint_id": endpoint_id,
                    "method": method,
                    "path": path,
                    "response_dto": resp_type,
                    "field": field_name,
                    "detail": (
                        f"Response field '{field_name}' expected but not found in {resp_type}"
                    ),
                })

        # Check for null-vs-empty-list patterns
        if unwrapped["collection"] or unwrapped["page"]:
            null_vs_empty_issue = check_null_vs_empty_list(ep, code_snapshot)
            if null_vs_empty_issue:
                issues.append(null_vs_empty_issue)

        # Check pagination metadata
        if unwrapped["page"]:
            page_issues = check_pagination_metadata(ep, inner_type, dto_fields, resp_type)
            issues.extend(page_issues)

    return issues


def check_null_vs_empty_list(
    ep: dict[str, Any], code_snapshot: dict[str, Any]
) -> dict[str, Any] | None:
    """Check for potential null-vs-empty-list inconsistencies."""
    # Heuristic: if there's no @JsonInclude(NON_NULL) configuration, flag it
    configs = code_snapshot.get("configs", [])
    return None  # Requires deeper code analysis; placeholder for future enhancement


def check_pagination_metadata(
    ep: dict[str, Any],
    inner_type: str,
    dto_fields: dict[str, dict[str, str]],
    resp_type: str,
) -> list[dict[str, Any]]:
    """Check if pagination response includes expected metadata fields."""
    issues: list[dict[str, Any]] = []
    expected_meta = ["total", "totalElements", "totalPages", "pageNum", "pageSize"]
    inner_fields = dto_fields.get(inner_type, {})

    has_metadata = any(
        field in inner_fields
        for field in expected_meta
    )
    if not has_metadata:
        issues.append({
            "type": "pagination_metadata_missing",
            "severity": "P1",
            "endpoint_id": contract_id(ep),
            "method": ep.get("method", ""),
            "path": contract_path(ep),
            "response_type": resp_type,
            "detail": (
                f"Pagination response type {resp_type} may be missing metadata fields "
                f"(expected one of: {', '.join(expected_meta)})"
            ),
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


EXPECTED_ERROR_HANDLERS = {
    400: [
        "MethodArgumentNotValidException",
        "ConstraintViolationException",
        "BindException",
        "HttpMessageNotReadableException",
    ],
    404: [
        "EntityNotFoundException",
        "ResourceNotFoundException",
        "NotFoundException",
        "NoSuchElementException",
    ],
    409: [
        "ConflictException",
        "DuplicateException",
        "DataIntegrityViolationException",
    ],
}


def check_endpoint_errors(
    contract: dict[str, Any],
    code_snapshot: dict[str, Any],
    exception_coverage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Check per-endpoint error response coverage against exception handlers."""
    issues: list[dict[str, Any]] = []

    # Build handler type index from exception_coverage
    covered_types: set[str] = set()
    handler_details: dict[str, dict[str, Any]] = {}
    if exception_coverage:
        for handler in exception_coverage.get("handlers", []):
            ex_type = handler.get("exception_type", "")
            if ex_type:
                covered_types.add(ex_type)
                handler_details[ex_type] = handler

    for ep in contract.get("endpoints", []):
        path = contract_path(ep)
        endpoint_id = contract_id(ep)
        method = ep.get("method", "")
        documented_errors = ep.get("errors", [])
        if not documented_errors:
            continue

        for error in documented_errors:
            status = error.get("status")
            code = error.get("body", {}).get("code", "")
            condition = error.get("condition", "")

            if not status:
                continue

            # Check handler coverage for documented error status
            expected_handlers = EXPECTED_ERROR_HANDLERS.get(status, [])
            if expected_handlers:
                has_handler = any(eh in covered_types for eh in expected_handlers)
                if not has_handler:
                    # Check fuzzy: any handler returning this status
                    status_handlers = [
                        h for h in exception_coverage.get("handlers", [])
                        if str(h.get("http_status", "")) == str(status)
                    ] if exception_coverage else []
                    if not status_handlers:
                        issues.append({
                            "type": "missing_error_handler",
                            "severity": "P0",
                            "endpoint_id": endpoint_id,
                            "method": method,
                            "path": path,
                            "expected_status": status,
                            "expected_code": code,
                            "detail": (
                                f"API contract documents {status} {code if code else ''} "
                                f"but no matching exception handler found. "
                                f"Expected one of: {', '.join(expected_handlers)}"
                            ),
                            "suspected_files": [
                                handler.get("file", "GlobalExceptionHandler.java")
                                for handler in exception_coverage.get("handlers", [])
                            ] if exception_coverage else ["GlobalExceptionHandler.java"],
                        })
                        continue

            # Check error code in handler body
            if code and exception_coverage:
                matching_code_handlers = [
                    h for h in exception_coverage.get("handlers", [])
                    if h.get("error_code") == code
                ]
                if not matching_code_handlers:
                    issues.append({
                        "type": "missing_error_code_in_handler",
                        "severity": "P1",
                        "endpoint_id": endpoint_id,
                        "method": method,
                        "path": path,
                        "expected_status": status,
                        "expected_code": code,
                        "detail": (
                            f"Error code '{code}' documented in API contract "
                            f"but no handler explicitly returns this code"
                        ),
                        "suspected_files": [
                            handler.get("file", "GlobalExceptionHandler.java")
                            for handler in exception_coverage.get("handlers", [])
                            if str(handler.get("http_status", "")) == str(status)
                        ] if exception_coverage else ["GlobalExceptionHandler.java"],
                    })

            # Check handler status mismatches
            if exception_coverage:
                status_mismatches = [
                    h for h in exception_coverage.get("handlers", [])
                    if h.get("exception_type") in expected_handlers
                    and str(h.get("http_status", "")) != str(status)
                    and h.get("http_status", "") not in ("UNKNOWN",)
                ]
                for h in status_mismatches:
                    issues.append({
                        "type": "wrong_status_code",
                        "severity": "P0",
                        "endpoint_id": endpoint_id,
                        "method": method,
                        "path": path,
                        "handler_file": h.get("file", ""),
                        "handler_exception": h.get("exception_type", ""),
                        "actual_status": h.get("http_status"),
                        "expected_status": status,
                        "detail": (
                            f"Handler for {h.get('exception_type')} returns {h.get('http_status')} "
                            f"but contract expects {status}"
                        ),
                    })

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


def determine_implementation_status(
    endpoint: dict[str, Any],
    code_ep: dict[str, Any] | None,
    all_issues: list[dict[str, Any]],
) -> str:
    """Determine implementation status from code presence and consistency issues.

    Returns: implemented | partial | missing | conflict
    """
    endpoint_key = (endpoint.get("method", ""), contract_path(endpoint))

    if not code_ep:
        return "missing"

    # Gather issues related to this endpoint
    endpoint_issues = [
        i for i in all_issues
        if i.get("method") == endpoint_key[0] and i.get("path") == endpoint_key[1]
    ]

    if not endpoint_issues:
        # Double-check: endpoint exists but may have issues not tied to path
        for i in all_issues:
            if i.get("endpoint_id") == endpoint.get("id"):
                endpoint_issues.append(i)

    if not endpoint_issues:
        return "implemented"

    # P0 issues with type_mismatch, wrong_http_method, wrong_path, wrong_status_code, wrong_error_code => conflict
    conflict_types = {
        "type_mismatch",
        "wrong_http_method",
        "wrong_path",
        "wrong_status_code",
        "wrong_error_code",
        "missing_endpoint",
    }
    for issue in endpoint_issues:
        if issue.get("severity") == "P0" and issue.get("type") in conflict_types:
            return "conflict"

    # Any other issues => partial
    return "partial"


def build_trace_matrix(
    contract: dict[str, Any],
    rules: dict[str, Any],
    repo_map: dict[str, Any],
    all_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build traceability matrix linking design requirements to code locations.

    Uses consistency issues to accurately determine implementation status.
    """
    if all_issues is None:
        all_issues = []

    trace_items: list[dict[str, Any]] = []

    controllers = {c.get("class_name", ""): c for c in repo_map.get("controllers", [])}
    dtos = {d.get("class_name", ""): d for d in repo_map.get("dtos", [])}
    services = {s.get("class_name", ""): s for s in repo_map.get("services", [])}

    # Map each API contract endpoint to code
    for ep in contract.get("endpoints", []):
        ep_path = contract_path(ep)
        ep_id = contract_id(ep)
        ep_method = ep.get("method", "")
        code_ep = find_code_endpoint(ep, repo_map)
        item: dict[str, Any] = {
            "requirement_id": ep_id,
            "api_id": ep_id,
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
            # Determine accurate status
            item["implementation_status"] = determine_implementation_status(ep, code_ep, all_issues)
        else:
            item["implementation_status"] = "missing"
            item["gap"] = f"No code endpoint found for {ep_method} {ep_path}"

        # Add gap description for partial/conflict
        if item["implementation_status"] in ("partial", "conflict"):
            endpoint_issues = [
                i for i in all_issues
                if i.get("method") == ep_method and i.get("path") == ep_path
            ]
            if endpoint_issues:
                gap_types = {i["type"] for i in endpoint_issues}
                item["gap"] = "; ".join(sorted(gap_types))
                severities = {i.get("severity", "P2") for i in endpoint_issues}
                if "P0" in severities:
                    item["repair_priority"] = "P0"
                elif "P1" in severities:
                    item["repair_priority"] = "P1"
                else:
                    item["repair_priority"] = "P2"

        # Map business rules
        for rule in rules.get("rules", []):
            if ep_method in str(rule.get("related_api", "")) and ep_path in str(rule.get("related_api", "")):
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
                method.get("http_method") == contract_ep.get("method")
                and method.get("full_path") == contract_path(contract_ep)
            ):
                return {
                    "file": ctrl.get("file"),
                    "method_name": method.get("method_name"),
                    "request_body_type": method.get("request_body"),
                    "response_body_type": method.get("response_type"),
                    "validated": method.get("validated", False),
                }
    return None


def check_consistency(root: Path, save_baseline: bool = False) -> dict[str, Any]:
    """Full consistency check producing report.

    If save_baseline is True, also writes baseline_consistency_report.json
    for later comparison by candidate_sandbox.py.
    """
    paths = runner.RunnerPaths(root)

    # Load all inputs
    contract = runner.read_json(paths.work / "api_contract.json", {})
    rules = runner.read_json(paths.work / "business_rules.json", {})
    repo_map = runner.read_json(paths.work / "repo_map.json", {})
    exception_coverage = runner.read_json(paths.work / "exception_coverage.json", None)

    # Get current code snapshot
    code_snapshot = runner.snapshot_code_api(root)

    # Run all checks
    all_issues: list[dict[str, Any]] = []
    all_issues.extend(check_route_existence(contract, code_snapshot))
    all_issues.extend(check_request_fields(contract, code_snapshot))
    all_issues.extend(check_response_fields(contract, code_snapshot))
    all_issues.extend(check_error_codes(contract, code_snapshot))
    all_issues.extend(check_endpoint_errors(contract, code_snapshot, exception_coverage))

    # Check for anti-patterns from exception coverage
    if exception_coverage:
        for handler in exception_coverage.get("handlers", []):
            if handler.get("has_swallow_pattern"):
                all_issues.append({
                    "type": "exception_swallow",
                    "severity": "P0",
                    "detail": (
                        f"Handler for {handler.get('exception_type')} in "
                        f"{handler.get('file')} has catch(Exception) — possible exception swallowing"
                    ),
                    "suspected_files": [handler.get("file", "")],
                })
            if handler.get("returns_200_unconditionally"):
                all_issues.append({
                    "type": "exception_returns_200",
                    "severity": "P0",
                    "detail": (
                        f"Handler for {handler.get('exception_type')} in "
                        f"{handler.get('file')} may return 200 unconditionally"
                    ),
                    "suspected_files": [handler.get("file", "")],
                })

    # Build trace matrix (pass issues for accurate status)
    trace_matrix = build_trace_matrix(contract, rules, repo_map, all_issues)

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

    # Optionally save baseline for P0 comparison
    if save_baseline:
        runner.write_json(paths.work / "baseline_consistency_report.json", report)

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

    report = check_consistency(root, save_baseline=args.save_baseline)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    # Exit non-zero if P0 issues found
    p0_count = report["summary"]["p0_issues"]
    return 1 if p0_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
