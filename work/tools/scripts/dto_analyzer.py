#!/usr/bin/env python3
"""DTO Analyzer — detailed analysis of DTO fields, Bean Validation coverage, and endpoint mapping.

Produces ``dto_validation_report.json`` with per-DTO, per-field validation coverage.
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


VALIDATION_ANNOTATIONS = {
    "@NotNull": "null check",
    "@NotBlank": "blank check",
    "@NotEmpty": "empty check",
    "@Size": "length constraint",
    "@Min": "min value",
    "@Max": "max value",
    "@DecimalMin": "min decimal",
    "@DecimalMax": "max decimal",
    "@Positive": "positive check",
    "@PositiveOrZero": "non-negative check",
    "@Negative": "negative check",
    "@NegativeOrZero": "non-positive check",
    "@Email": "email format",
    "@Pattern": "regex pattern",
    "@Valid": "nested validation",
    "@Null": "must be null",
    "@AssertTrue": "must be true",
    "@AssertFalse": "must be false",
    "@Past": "past date",
    "@Future": "future date",
    "@Digits": "digit constraint",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze DTO validation coverage.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def extract_dto_fields_with_validation(text: str) -> list[dict[str, Any]]:
    """Extract all DTO fields with their validation annotations and types."""
    clean = runner.strip_java_comments(text)
    fields: list[dict[str, Any]] = []

    # Match multi-annotation fields
    # @Annotation1
    # @Annotation2(...)
    # private Type fieldName;
    multi_anno = re.compile(
        r"((?:@\w+(?:\([^)]*\))?\s*\n\s*)*)"
        r"(?:private|protected|public)\s+(?:final\s+)?"
        r"([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)",
        re.S,
    )
    for match in multi_anno.finditer(clean):
        annotations_block = match.group(1)
        field_type = runner.normalize_type(match.group(2))
        field_name = match.group(3)

        annotations = re.findall(r"@(\w+(?:\([^)]*\))?)", annotations_block)

        # Get JSON annotation if present
        json_name = field_name
        json_match = re.search(r'@JsonProperty\s*\(\s*["\']([^"\']+)["\']', annotations_block)
        if json_match:
            json_name = json_match.group(1)

        validation_annotations = [a for a in annotations if a.split("(")[0] in {
            k.lstrip("@") for k in VALIDATION_ANNOTATIONS
        }]
        json_annotations = [a for a in annotations if a.split("(")[0] in {
            "JsonProperty", "JsonAlias", "JsonIgnore", "JsonFormat", "JsonSerialize", "JsonDeserialize"
        }]

        fields.append({
            "name": field_name,
            "type": field_type,
            "json_name": json_name,
            "validations": validation_annotations,
            "json_annotations": json_annotations,
            "has_not_null": any("NotNull" in a for a in validation_annotations),
            "has_not_blank": any("NotBlank" in a for a in validation_annotations),
            "has_not_empty": any("NotEmpty" in a for a in validation_annotations),
            "has_size": any("Size" in a for a in validation_annotations),
            "has_min": any(a.startswith("Min") for a in validation_annotations),
            "has_max": any(a.startswith("Max") for a in validation_annotations),
            "has_decimal_min": any("DecimalMin" in a for a in validation_annotations),
            "has_positive": any("Positive" in a for a in validation_annotations),
            "has_valid": any("Valid" in a for a in validation_annotations),
            "has_pattern": any("Pattern" in a for a in validation_annotations),
        })

    # Also handle single-line annotated fields
    single_line = re.compile(
        r"(@\w+(?:\([^)]*\))?)\s+(?:private|protected|public)\s+(?:final\s+)?"
        r"([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)",
    )
    seen_names = {f["name"] for f in fields}
    for match in single_line.finditer(clean):
        annotation = match.group(1)
        field_type = runner.normalize_type(match.group(2))
        field_name = match.group(3)
        if field_name in seen_names:
            # Update existing with additional annotation
            for f in fields:
                if f["name"] == field_name:
                    anno_name = annotation.lstrip("@").split("(")[0]
                    if anno_name in {k.lstrip("@") for k in VALIDATION_ANNOTATIONS}:
                        if annotation not in f["validations"]:
                            f["validations"].append(annotation)
            continue

    return fields


def check_validation_gaps(fields: list[dict[str, Any]], field_name: str, field_type: str) -> list[str]:
    """Check for common validation gaps based on field name and type heuristics."""
    gaps: list[str] = []
    name_lower = field_name.lower()
    type_lower = field_type.lower()

    field_info = next((f for f in fields if f["name"] == field_name), None)
    if not field_info:
        return [f"MISSING_FIELD: {field_name} not found in DTO"]

    # String fields that look like names/titles should have @NotBlank
    if "string" in type_lower and any(kw in name_lower for kw in ("name", "title", "description", "code", "label")):
        if not field_info["has_not_blank"] and not field_info["has_not_empty"]:
            gaps.append("MISSING_NOT_BLANK")

    # Numeric fields for money/price should have @DecimalMin
    if any(kw in name_lower for kw in ("price", "amount", "total", "cost", "money", "salary")):
        if not field_info["has_decimal_min"] and not field_info["has_positive"]:
            gaps.append("MISSING_DECIMAL_MIN_OR_POSITIVE")

    # Numeric fields for quantity/stock should have @Min or @PositiveOrZero
    if any(kw in name_lower for kw in ("quantity", "stock", "count", "num", "qty")):
        if not field_info["has_min"] and not field_info["has_positive"]:
            if "positiveorzero" not in str(field_info["validations"]).lower():
                gaps.append("MISSING_MIN_OR_POSITIVE_OR_ZERO")

    # Fields that reference other entities (ending in Id) should not be freely nullable
    if name_lower.endswith("id") and not name_lower == "id":
        if not field_info["has_not_null"]:
            gaps.append("MISSING_NOT_NULL_ON_FK")

    # Email fields
    if "email" in name_lower:
        if not field_info["has_pattern"] and "Email" not in str(field_info["validations"]):
            gaps.append("MISSING_EMAIL_VALIDATION")

    return gaps


def analyze_dtos(root: Path) -> dict[str, Any]:
    """Full DTO analysis producing validation coverage report."""
    paths = runner.RunnerPaths(root)
    code_dir = root / "code"

    report: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "dtos": [],
        "summary": {
            "total_dtos": 0,
            "total_fields": 0,
            "fields_with_validation": 0,
            "fields_without_validation": 0,
            "validation_gaps": 0,
        },
        "gap_details": [],
    }

    if not code_dir.exists():
        report["warnings"] = ["code/ does not exist."]
        runner.write_json(paths.work / "dto_validation_report.json", report)
        return report

    java_files = sorted(code_dir.rglob("*.java"))
    for path in java_files:
        text = runner.read_text(path)
        if not runner.looks_like_dto(path, path.stem, text):
            continue

        class_name = path.stem
        rel_path = runner.rel(root, path)

        fields = extract_dto_fields_with_validation(text)
        if not fields:
            continue

        report["dtos"].append({
            "class_name": class_name,
            "file": rel_path,
            "fields": fields,
            "field_count": len(fields),
            "validated_field_count": sum(1 for f in fields if f["validations"]),
            "unvalidated_field_count": sum(1 for f in fields if not f["validations"]),
        })

        report["summary"]["total_dtos"] += 1
        report["summary"]["total_fields"] += len(fields)
        report["summary"]["fields_with_validation"] += sum(1 for f in fields if f["validations"])
        report["summary"]["fields_without_validation"] += sum(1 for f in fields if not f["validations"])

    # Check validation gaps against API contract if available
    api_contract_path = paths.work / "api_contract.json"
    if api_contract_path.exists():
        api_contract = runner.read_json(api_contract_path, {})
        for endpoint in api_contract.get("endpoints", []):
            for field_name, field_spec in endpoint.get("request", {}).get("body", {}).items():
                # Find which DTO corresponds
                for dto_info in report["dtos"]:
                    gaps = check_validation_gaps(
                        dto_info["fields"],
                        field_name,
                        field_spec.get("type", "string"),
                    )
                    if gaps:
                        report["gap_details"].append({
                            "endpoint": f"{endpoint['method']} {endpoint['path']}",
                            "dto": dto_info["class_name"],
                            "field": field_name,
                            "expected": f"type={field_spec.get('type')}, required={field_spec.get('required')}",
                            "gaps": gaps,
                        })
                        report["summary"]["validation_gaps"] += len(gaps)

    runner.write_json(paths.work / "dto_validation_report.json", report)
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = analyze_dtos(root)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
