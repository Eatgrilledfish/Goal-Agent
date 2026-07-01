#!/usr/bin/env python3
"""API Contract Builder — deterministic extraction of REST endpoints from README/design-docs.

Produces ``api_contract.json`` per DESIGN.md §6.3 format.
Enhances the extraction logic already present in shophub_goal_runner.py.
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


HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract API contract from README and design-docs.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path (default: .agent-work/api_contract.json).")
    return parser


def find_api_baseline_sources(root: Path) -> list[Path]:
    """Return README + design-docs markdown files as API baseline sources."""
    sources: list[Path] = []
    readme = root / "README.md"
    if readme.exists():
        sources.append(readme)
    design_dir = root / "design-docs"
    if design_dir.exists():
        sources.extend(sorted(design_dir.rglob("*.md")))
    return sources


def extract_endpoints_enhanced(text: str) -> list[dict[str, Any]]:
    """Enhanced endpoint extraction beyond the basic method+URL heuristic.

    Extracts: method, path, summary, request fields (from markdown tables),
    response fields, error codes, and success status.
    """
    endpoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # --- Method + URL patterns ---
    method_url_patterns = [
        # Table row: | GET | /path | description |
        re.compile(
            r"\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`?(/[A-Za-z0-9_./{}?=&:%-]+)`?\s*\|",
            re.I,
        ),
        # Inline: GET /path
        re.compile(
            r"\b(GET|POST|PUT|DELETE|PATCH)\b\s*[:：]?\s*`?(/[A-Za-z0-9_./{}?=&:%-]+)`?",
            re.I,
        ),
    ]

    for pattern in method_url_patterns:
        for match in pattern.finditer(text):
            method = match.group(1).upper()
            url = normalize_url(match.group(2))
            key = (method, url)
            if key in seen:
                continue
            seen.add(key)

            endpoint: dict[str, Any] = {
                "id": f"API-{len(endpoints) + 1:03d}",
                "method": method,
                "path": url,
                "summary": "",
                "request": {"content_type": "application/json", "body": {}},
                "response": {"success_status": [200], "body": {}},
                "errors": [],
            }

            # Try to extract summary from the same line or next line
            summary = extract_summary_from_context(text, match.start(), match.end())
            if summary:
                endpoint["summary"] = summary

            # Extract request fields from nearby markdown tables
            req_fields = extract_fields_from_nearby_table(text, match.end(), "request")
            if req_fields:
                endpoint["request"]["body"] = req_fields

            # Extract response fields
            resp_fields = extract_fields_from_nearby_table(text, match.end(), "response")
            if resp_fields:
                endpoint["response"]["body"] = resp_fields

            # Extract error codes from nearby context
            error_codes = extract_error_codes_from_context(text, match.end())
            endpoint["errors"] = error_codes

            endpoints.append(endpoint)

    return endpoints


def normalize_url(value: str) -> str:
    value = value.strip().strip("`。；;,.，")
    if not value.startswith("/"):
        value = "/" + value
    return re.sub(r"//+", "/", value)


def extract_summary_from_context(text: str, start_pos: int, end_pos: int) -> str:
    """Extract endpoint summary from the line containing the match or the next line."""
    # Get the match line
    line_start = text.rfind("\n", 0, start_pos) + 1
    line_end = text.find("\n", end_pos)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()

    # Remove the method+URL part, keep the description
    # e.g., "| GET | /api/products | Create a product |" -> "Create a product"
    parts = [p.strip() for p in line.split("|") if p.strip()]
    for part in parts:
        if not re.match(r"^(GET|POST|PUT|DELETE|PATCH)\b", part, re.I) and not part.startswith("/"):
            cleaned = re.sub(r"`([^`]+)`", r"\1", part)
            if len(cleaned) > 3:
                return cleaned

    return ""


def extract_markdown_tables(context: str) -> list[list[list[str]]]:
    """Parse markdown tables from text into list of tables, each a list of rows of cells."""
    tables: list[list[list[str]]] = []
    lines = context.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("|"):
            i += 1
            continue
        block: list[str] = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i].strip())
            i += 1
        if len(block) >= 2:
            rows: list[list[str]] = []
            for row in block:
                cells = [c.strip().strip("`") for c in row.strip("|").split("|")]
                rows.append(cells)
            # Skip separator rows (|---|)
            if not all(re.match(r"^[-: ]+$", c) for c in rows[0]):
                tables.append(rows)
    return tables


def find_col(headers: list[str], candidates: list[str]) -> int:
    """Find the index of a column matching one of the candidate header names."""
    for idx, h in enumerate(headers):
        h_lower = h.strip().lower()
        for cand in candidates:
            if cand.lower() == h_lower:
                return idx
    return -1


def is_separator_row(row: list[str]) -> bool:
    """Check if a markdown table row is a separator row like |---|---|."""
    return all(re.match(r"^[-: ]+$", c.strip()) for c in row if c.strip())


def parse_required(value: str) -> bool:
    """Parse required field value from Chinese or English markers."""
    v = value.strip().lower()
    return v in {"是", "必填", "必须", "y", "yes", "true", "required", "mandatory"}


def extract_fields_from_nearby_table(text: str, after_pos: int, direction: str) -> dict[str, dict[str, Any]]:
    """Extract request/response fields from markdown tables near the endpoint.

    Supports flexible headers:
      - Field: 字段, 字段名, 名称, name, field, 参数名
      - Type: 类型, type
      - Required: 是否必填, 必填, required, 是否必须
      - Description: 说明, 描述, description
    """
    search_end = min(after_pos + 3000, len(text))
    context = text[after_pos:search_end]

    tables = extract_markdown_tables(context)
    fields: dict[str, dict[str, Any]] = {}

    for table in tables:
        if len(table) < 2:
            continue
        headers = table[0]
        field_col = find_col(headers, ["字段", "字段名", "名称", "field", "name", "参数名"])
        type_col = find_col(headers, ["类型", "type"])
        required_col = find_col(headers, ["是否必填", "必填", "required", "是否必须"])
        desc_col = find_col(headers, ["说明", "描述", "description"])

        if field_col < 0:
            continue  # can't parse without field name column

        for row in table[1:]:
            if is_separator_row(row):
                continue
            if len(row) <= field_col:
                continue
            field_name = row[field_col].strip()
            if not field_name:
                continue
            if field_name.lower() in ("field", "字段", "name", "名称", "type", "类型", "parameter"):
                continue

            field_type = "string"
            if type_col >= 0 and type_col < len(row):
                field_type = normalize_field_type(row[type_col].strip())

            required = False
            if required_col >= 0 and required_col < len(row):
                required = parse_required(row[required_col])

            description = ""
            if desc_col >= 0 and desc_col < len(row):
                description = row[desc_col].strip()

            fields[field_name] = {
                "type": field_type,
                "required": required,
                "constraints": infer_constraints(field_name, field_type),
                "description": description,
            }

    # Fallback: if no tables found, try regex-based extraction
    if not fields:
        table_pattern = re.compile(
            r"\|\s*`?([A-Za-z_][A-Za-z0-9_.]*)`?\s*\|\s*([A-Za-z0-9_<>\[\],. ]+?)\s*\|"
            r"\s*(?:是|必填|[Yy]es|[Rr]equired|true|否|选填|[Nn]o|[Oo]ptional|false)?\s*\|",
            re.M,
        )
        for match in table_pattern.finditer(context):
            field_name = match.group(1).strip()
            field_type = normalize_field_type(match.group(2).strip())
            if field_name.lower() in ("field", "字段", "name", "名称", "type", "类型", "parameter"):
                continue
            required = False
            if match.lastindex and match.lastindex >= 3:
                req_text = match.group(3).strip() if match.group(3) else ""
                required = req_text.lower() in ("是", "必填", "yes", "required", "true")
            fields[field_name] = {
                "type": field_type,
                "required": required,
                "constraints": infer_constraints(field_name, field_type),
            }

    return fields


def normalize_field_type(raw: str) -> str:
    """Normalize field type descriptions to standard types."""
    raw = raw.strip().lower()
    mapping = {
        "string": "string",
        "varchar": "string",
        "text": "string",
        "integer": "integer",
        "int": "integer",
        "long": "long",
        "bigint": "long",
        "decimal": "decimal",
        "bigdecimal": "decimal",
        "double": "decimal",
        "float": "decimal",
        "boolean": "boolean",
        "bool": "boolean",
        "date": "string",
        "datetime": "string",
        "timestamp": "string",
        "array": "array",
        "list": "array",
        "object": "object",
        "json": "object",
    }
    for key, value in mapping.items():
        if key in raw:
            return value
    return raw


def infer_constraints(field_name: str, field_type: str) -> list[str]:
    """Infer common constraints from field name and type."""
    constraints: list[str] = []
    name_lower = field_name.lower()
    if field_type in ("string",) and any(kw in name_lower for kw in ("name", "title", "description", "code")):
        constraints.append("not_blank")
    if any(kw in name_lower for kw in ("price", "amount", "total", "money", "cost")):
        constraints.append("min:0.01")
    if any(kw in name_lower for kw in ("quantity", "stock", "count", "num")):
        constraints.append("min:0")
    if any(kw in name_lower for kw in ("id",)) and not name_lower.endswith("s"):
        constraints.append("exists")
    if any(kw in name_lower for kw in ("email",)):
        constraints.append("email_format")
    if any(kw in name_lower for kw in ("phone", "mobile")):
        constraints.append("pattern")
    return constraints


def extract_error_codes_from_context(text: str, after_pos: int) -> list[dict[str, Any]]:
    """Extract error response descriptions from context near an endpoint."""
    search_end = min(after_pos + 3000, len(text))
    context = text[after_pos:search_end]

    errors: list[dict[str, Any]] = []
    # Pattern: HTTP status + error code + description
    # e.g., "400 VALIDATION_ERROR - invalid request"
    # e.g., "| 400 | VALIDATION_ERROR | invalid input |"
    error_patterns = [
        re.compile(
            r"\|\s*(\d{3})\s*\|\s*`?([A-Z][A-Z0-9_]+)`?\s*\|\s*([^|\n]+)",
            re.I,
        ),
        re.compile(
            r"\b(\d{3})\b[:\s-]*`?([A-Z][A-Z0-9_]+)`?[:\s-]*([^\n,;]+)",
            re.I,
        ),
    ]

    seen_codes: set[str] = set()
    for pattern in error_patterns:
        for match in pattern.finditer(context):
            status = int(match.group(1))
            code = match.group(2).strip()
            description = match.group(3).strip() if match.lastindex and match.lastindex >= 3 else ""
            if code in seen_codes:
                continue
            seen_codes.add(code)
            errors.append({
                "condition": description or f"HTTP {status}",
                "status": status,
                "body": {"code": code, "message": "string"},
            })

    return errors


def extract_global_error_codes(text: str) -> list[str]:
    """Extract all uppercase error code identifiers from the text."""
    return sorted(set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){1,}\b", text)))


def build_api_contract(root: Path) -> dict[str, Any]:
    """Build the complete API contract from baseline sources."""
    paths = runner.RunnerPaths(root)
    baseline_sources = find_api_baseline_sources(root)

    contract: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "source": [runner.rel(root, p) for p in baseline_sources],
        "endpoints": [],
        "error_codes": [],
        "frozen": True,
        "warnings": [],
    }

    if not baseline_sources:
        contract["warnings"].append("No API baseline source found. Expected README.md and/or design-docs/*.md.")
        return contract

    combined_text = "\n\n".join(runner.read_text(p) for p in baseline_sources)

    # Extract endpoints with enhanced parser
    endpoints = extract_endpoints_enhanced(combined_text)
    contract["endpoints"] = endpoints

    # Extract global error codes
    contract["error_codes"] = extract_global_error_codes(combined_text)

    if not endpoints:
        contract["warnings"].append("No REST endpoints found by enhanced extraction.")

    # Persist
    output_path = paths.work / "api_contract.json"
    runner.write_json(output_path, contract)

    # Also write the index
    index_lines = [
        "# API Contract Index",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        f"Sources: {', '.join(contract['source']) or 'none'}",
        "",
    ]
    if endpoints:
        for ep in endpoints:
            index_lines.append(
                f"- `{ep['method']} {ep['path']}` id=`{ep['id']}` — {ep.get('summary', '')}"
            )
            if ep["request"]["body"]:
                index_lines.append(f"  - Request fields: {', '.join(ep['request']['body'].keys())}")
            if ep["response"]["body"]:
                index_lines.append(f"  - Response fields: {', '.join(ep['response']['body'].keys())}")
            if ep["errors"]:
                codes = [e["body"]["code"] for e in ep["errors"]]
                index_lines.append(f"  - Errors: {', '.join(codes)}")
    else:
        index_lines.append("No endpoints found.")
    if contract["error_codes"]:
        index_lines.extend(["", "## Global Error Codes", ""])
        for code in contract["error_codes"]:
            index_lines.append(f"- `{code}`")
    if contract["warnings"]:
        index_lines.extend(["", "## Warnings", ""])
        for w in contract["warnings"]:
            index_lines.append(f"- {w}")

    runner.write_text(paths.work / "02_api_contract_index.md", "\n".join(index_lines).rstrip() + "\n")

    return contract


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    contract = build_api_contract(root)

    if args.output:
        runner.write_json(Path(args.output), contract)
    else:
        print(json.dumps(contract, ensure_ascii=False, indent=2))

    return 0 if not contract.get("warnings") else 1


if __name__ == "__main__":
    raise SystemExit(main())
