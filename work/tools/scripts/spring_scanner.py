#!/usr/bin/env python3
"""Spring Boot Code Scanner — produces structured ``repo_map.json`` per DESIGN.md §8.

Enhances the code mapping logic in shophub_goal_runner.py with:
- Service method extraction
- Entity field extraction
- Repository query method extraction
- Configuration class detection
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
    parser = argparse.ArgumentParser(description="Scan Spring Boot code and build repo map.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path (default: .agent-work/repo_map.json).")
    return parser


def extract_service_methods(text: str) -> list[dict[str, Any]]:
    """Extract public methods from service classes."""
    clean = runner.strip_java_comments(text)
    methods: list[dict[str, Any]] = []
    method_pattern = re.compile(
        r"(public|protected)\s+(?:static\s+)?(?:@\w+(?:\([^)]*\))?\s+)*"
        r"([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
        re.S,
    )
    for match in method_pattern.finditer(clean):
        name = match.group(3)
        if name in ("class", "interface", "enum"):
            continue
        return_type = runner.normalize_type(match.group(2))
        params_raw = match.group(4)
        params = [
            p.strip().split()[-1] if p.strip().split() else p.strip()
            for p in runner.split_java_parameters(params_raw)
            if p.strip()
        ]
        methods.append({
            "name": name,
            "return_type": return_type,
            "parameters": params,
        })
    return methods


def extract_entity_fields(text: str) -> list[dict[str, Any]]:
    """Extract entity fields with JPA annotations."""
    clean = runner.strip_java_comments(text)
    fields: list[dict[str, Any]] = []
    field_pattern = re.compile(
        r"@(\w+)(?:\([^)]*\))?\s*\n\s*"
        r"(?:private|protected|public)\s+(?:final\s+)?"
        r"([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)",
        re.S,
    )
    for match in field_pattern.finditer(clean):
        annotation = match.group(1)
        field_type = runner.normalize_type(match.group(2))
        field_name = match.group(3)
        fields.append({
            "name": field_name,
            "type": field_type,
            "annotation": annotation,
        })

    # Also match fields without line-break annotations
    simple_pattern = re.compile(
        r"(?:private|protected|public)\s+(?:final\s+)?"
        r"([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)",
        re.M,
    )
    if not fields:
        for match in simple_pattern.finditer(clean):
            field_type = runner.normalize_type(match.group(1))
            field_name = match.group(2)
            if field_name in ("class", "interface", "enum"):
                continue
            fields.append({
                "name": field_name,
                "type": field_type,
                "annotation": "",
            })
    return fields


def extract_repository_methods(text: str) -> list[dict[str, Any]]:
    """Extract custom query methods from repository interfaces."""
    clean = runner.strip_java_comments(text)
    methods: list[dict[str, Any]] = []

    # @Query annotations
    query_pattern = re.compile(
        r'@Query\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']',
        re.S,
    )
    queries = [(m.group(1), m.start()) for m in query_pattern.finditer(clean)]

    # Method declarations after @Query
    method_pattern = re.compile(
        r"(?:public\s+)?(?:abstract\s+)?"
        r"([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
    )

    for match in method_pattern.finditer(clean):
        name = match.group(2)
        return_type = runner.normalize_type(match.group(1))
        # Check if preceded by @Query
        preceding = clean[: match.start()]
        has_query = "@Query" in preceding[max(0, match.start() - 500): match.start()]
        methods.append({
            "name": name,
            "return_type": return_type,
            "has_custom_query": has_query,
            "is_derived": not has_query and any(
                kw in name for kw in ("findBy", "getBy", "queryBy", "readBy", "countBy", "existsBy", "deleteBy")
            ),
        })
    return methods


def extract_exception_handlers(text: str) -> list[dict[str, Any]]:
    """Extract @ExceptionHandler methods from @ControllerAdvice classes."""
    clean = runner.strip_java_comments(text)
    handlers: list[dict[str, Any]] = []

    handler_pattern = re.compile(
        r"@ExceptionHandler\s*\(\s*([A-Za-z0-9_.]+)\.class\s*\)\s*\n"
        r"(?:@ResponseStatus\s*\(\s*(?:HttpStatus\.)?(\w+)\s*\)\s*\n)?"
        r"(?:public|protected|private)\s+([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.S,
    )
    for match in handler_pattern.finditer(clean):
        exception_type = match.group(1).split(".")[-1]
        status = match.group(2) or "INTERNAL_SERVER_ERROR"
        return_type = runner.normalize_type(match.group(3))
        method_name = match.group(4)
        handlers.append({
            "exception_type": exception_type,
            "http_status": status,
            "return_type": return_type,
            "method": method_name,
        })

    # Simpler pattern: @ExceptionHandler on same line
    single_line_pattern = re.compile(
        r"@ExceptionHandler\s*\(\s*([A-Za-z0-9_.]+)\.class\s*\)",
    )
    for match in single_line_pattern.finditer(clean):
        exception_type = match.group(1).split(".")[-1]
        # Check if already captured
        if any(h["exception_type"] == exception_type for h in handlers):
            continue
        # Find the response status nearby
        nearby = clean[match.start():match.start() + 500]
        status_match = re.search(r"@ResponseStatus\s*\(\s*(?:HttpStatus\.)?(\w+)", nearby)
        handlers.append({
            "exception_type": exception_type,
            "http_status": status_match.group(1) if status_match else "UNKNOWN",
            "return_type": "UNKNOWN",
            "method": "UNKNOWN",
        })

    return handlers


def scan_code(root: Path) -> dict[str, Any]:
    """Full Spring Boot code scan producing repo_map.json."""
    paths = runner.RunnerPaths(root)
    code_dir = root / "code"

    repo_map: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "modules": [],
        "controllers": [],
        "dtos": [],
        "services": [],
        "repositories": [],
        "entities": [],
        "exceptions": [],
        "exception_handlers": [],
        "configs": [],
        "tests": [],
        "warnings": [],
    }

    if not code_dir.exists():
        repo_map["warnings"].append("code/ does not exist.")
        runner.write_json(paths.work / "repo_map.json", repo_map)
        return repo_map

    # Get modules from existing runner
    modules = runner.find_maven_modules(root, code_dir)
    repo_map["modules"] = [
        {"path": m["path"], "artifact_id": m["artifact_id"], "packaging": m.get("packaging")}
        for m in modules
    ]

    java_files = sorted(code_dir.rglob("*.java"))

    # Reuse existing snapshot for controllers/DTOs
    snapshot = runner.snapshot_code_api(root)

    # Process each Java file
    for path in java_files:
        text = runner.read_text(path)
        rel_path = runner.rel(root, path)
        module = runner.infer_module_for_path(root, path)
        class_name = path.stem

        # Determine role
        if runner.is_test_path(path):
            role = "test"
        elif "@RestController" in text or "@Controller" in text:
            role = "controller"
        elif "@Service" in text or class_name.endswith("Service") or class_name.endswith("ServiceImpl"):
            role = "service"
        elif "@Repository" in text or class_name.endswith("Repository") or class_name.endswith("Mapper"):
            role = "repository"
        elif runner.looks_like_dto(path, class_name, text):
            role = "dto"
        elif "Exception" in class_name:
            role = "exception"
        elif "@Configuration" in text or class_name.endswith("Config") or class_name.endswith("Configuration"):
            role = "config"
        elif "@Entity" in text or "@Table" in text:
            role = "entity"
        else:
            role = "other"

        record: dict[str, Any] = {
            "class_name": class_name,
            "file": rel_path,
            "module": module,
            "role": role,
        }

        if role == "controller":
            endpoints = [
                ep for ep in snapshot.get("endpoints", [])
                if ep.get("source", "").startswith(rel_path)
            ]
            record["base_path"] = ""
            record["methods"] = []
            if endpoints:
                # Determine base path
                controller_text = runner.read_text(path)
                base = runner.extract_class_request_mapping(runner.strip_java_comments(controller_text))
                record["base_path"] = base
                record["methods"] = [
                    {
                        "method_name": ep.get("source", "").split("#")[-1] if "#" in ep.get("source", "") else "",
                        "http_method": ep["method"],
                        "path": ep["url"],
                        "full_path": ep["url"],
                        "request_body": ep.get("request_body_type"),
                        "validated": "@Valid" in text,
                        "response_type": ep.get("response_body_type"),
                        "service_calls": [],
                    }
                    for ep in endpoints
                ]
            repo_map["controllers"].append(record)

        elif role == "dto":
            dto_fields = snapshot.get("dto_fields", {}).get(class_name, {})
            # Also extract validation annotations
            fields_with_annotations = extract_dto_annotations(text)
            record["fields"] = [
                {
                    "name": name,
                    "type": ftype,
                    "annotations": fields_with_annotations.get(name, []),
                    "json_name": name,
                }
                for name, ftype in dto_fields.items()
            ]
            # Include fields only in annotations
            for name in fields_with_annotations:
                if name not in dto_fields:
                    record["fields"].append({
                        "name": name,
                        "type": "UNKNOWN",
                        "annotations": fields_with_annotations[name],
                        "json_name": name,
                    })
            repo_map["dtos"].append(record)

        elif role == "service":
            record["methods"] = extract_service_methods(text)
            repo_map["services"].append(record)

        elif role == "repository":
            record["methods"] = extract_repository_methods(text)
            repo_map["repositories"].append(record)

        elif role == "entity":
            record["fields"] = extract_entity_fields(text)
            repo_map["entities"].append(record)

        elif role == "exception":
            repo_map["exceptions"].append(record)

        elif role == "config":
            repo_map["configs"].append(record)

        elif role == "test":
            repo_map["tests"].append(record)

    # Extract global exception handlers
    for path in java_files:
        text = runner.read_text(path)
        if "@ControllerAdvice" in text or "@RestControllerAdvice" in text:
            handlers = extract_exception_handlers(text)
            repo_map["exception_handlers"].append({
                "class_name": path.stem,
                "file": runner.rel(root, path),
                "handlers": handlers,
            })

    runner.write_json(paths.work / "repo_map.json", repo_map)

    # Write summary
    summary_lines = [
        "# Repo Map Summary",
        "",
        f"Generated: {runner.now_iso()}",
        "",
    ]
    for key in ("controllers", "dtos", "services", "repositories", "entities", "exceptions", "exception_handlers", "configs", "tests"):
        items = repo_map.get(key, [])
        summary_lines.append(f"- {key}: {len(items)}")
    if repo_map["warnings"]:
        summary_lines.extend(["", "## Warnings", ""])
        for w in repo_map["warnings"]:
            summary_lines.append(f"- {w}")
    runner.write_text(paths.work / "04_repo_map_summary.md", "\n".join(summary_lines).rstrip() + "\n")

    return repo_map


def extract_dto_annotations(text: str) -> dict[str, list[str]]:
    """Extract Bean Validation and JSON annotations per field from DTO source."""
    clean = runner.strip_java_comments(text)
    fields: dict[str, list[str]] = {}

    # Match: @Annotation(...) \n private Type fieldName;
    annotated_field = re.compile(
        r"@(\w+(?:\([^)]*\))?)\s*\n\s*(?:private|protected|public)\s+(?:final\s+)?"
        r"[A-Za-z0-9_<>, ?.\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.S,
    )
    for match in annotated_field.finditer(clean):
        annotation = match.group(1)
        field_name = match.group(2)
        fields.setdefault(field_name, []).append(annotation)

    # Same-line annotation: @NotNull private Type field;
    same_line = re.compile(
        r"@(\w+(?:\([^)]*\))?)\s+(?:private|protected|public)\s+(?:final\s+)?"
        r"[A-Za-z0-9_<>, ?.\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)",
    )
    for match in same_line.finditer(clean):
        annotation = match.group(1)
        field_name = match.group(2)
        if annotation not in fields.get(field_name, []):
            fields.setdefault(field_name, []).append(annotation)

    return fields


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    repo_map = scan_code(root)

    if args.output:
        runner.write_json(Path(args.output), repo_map)
    else:
        print(json.dumps(repo_map, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
