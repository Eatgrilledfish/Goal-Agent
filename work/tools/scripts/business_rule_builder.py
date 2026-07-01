#!/usr/bin/env python3
"""Business Rule Builder — deterministic extraction of business rules from design-docs.

Produces ``business_rules.json`` from the target repository's design documents.
Enhances the spec extraction logic already present in shophub_goal_runner.py.
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


# Rule type classification keywords used by the competition repair pipeline.
RULE_TYPE_KEYWORDS: dict[str, list[str]] = {
    "validation": [
        "不能为空", "必填", "不能为null", "不能为空白", "必须大于", "必须小于",
        "must not be null", "must not be blank", "required", "not null",
        "不能为0", "不能为负数", "长度", "格式", "pattern", "regex",
        "not blank", "not empty", "mandatory",
    ],
    "state_transition": [
        "状态", "流转", "state", "transition", "status change",
        "不能从", "只能从", "状态机", "state machine",
    ],
    "api_contract": [
        "接口", "API", "endpoint", "路径", "path", "HTTP",
        "返回", "response", "请求", "request", "状态码",
    ],
    "business_rule": [
        "库存", "金额", "价格", "数量", "inventory", "stock",
        "price", "amount", "quantity", "订单", "order",
        "删除", "软删除", "delete", "soft delete",
        "重复", "duplicate", "唯一", "unique",
        "权限", "permission", "auth", "鉴权",
    ],
    "pagination": [
        "分页", "排序", "pagination", "sort", "page", "size",
        "列表", "list",
    ],
    "error_handling": [
        "异常", "错误", "error", "exception", "失败",
        "400", "404", "409", "500",
    ],
}

PRIORITY_KEYWORDS: dict[str, list[str]] = {
    "P0": [
        "API契约", "接口", "状态码", "错误码", "字段", "必填",
        "核心", "关键", "critical",
    ],
    "P1": [
        "分页", "排序", "默认值", "边界", "空值", "默认",
    ],
    "P2": [
        "文案", "日志", "风格", "注释", "格式",
    ],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract business rules from design-docs.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path (default: .agent-work/business_rules.json).")
    return parser


def classify_rule_type(text: str) -> str:
    """Classify a design paragraph into a rule type based on keyword matching."""
    lowered = text.lower()
    for rule_type, keywords in RULE_TYPE_KEYWORDS.items():
        if any(kw.lower() in lowered for kw in keywords):
            return rule_type
    return "other"


def infer_priority(text: str) -> str:
    """Infer priority (P0/P1/P2) from keywords."""
    lowered = text.lower()
    for priority, keywords in PRIORITY_KEYWORDS.items():
        if any(kw.lower() in lowered for kw in keywords):
            return priority
    return "P2"


def extract_target_domain(text: str, source_file: str) -> str:
    """Infer the target domain entity from the text and source file."""
    # Try to find entity names: capitalized words near "商品"/"订单"/"用户"/etc.
    entity_patterns = re.findall(
        r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)*)\b", text
    )
    # Common domain entities in Spring Boot projects
    common_entities = {
        "product", "order", "user", "category", "inventory",
        "payment", "cart", "review", "address", "coupon",
        "商品", "订单", "用户", "分类", "库存", "支付",
    }
    for entity in entity_patterns:
        if entity.lower() in common_entities:
            return entity

    # Fallback: use source filename
    stem = Path(source_file).stem
    return stem


def extract_related_apis(text: str) -> list[str]:
    """Extract API endpoint references from the text."""
    apis: list[str] = []
    method_url = re.compile(
        r"\b(GET|POST|PUT|DELETE|PATCH)\b\s*[:：]?\s*`?(/[A-Za-z0-9_./{}?=&:%-]+)`?",
        re.I,
    )
    for match in method_url.finditer(text):
        apis.append(f"{match.group(1).upper()} {match.group(2)}")
    return apis


def generate_test_cases(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate basic test case descriptions from a rule."""
    test_cases: list[dict[str, Any]] = []
    text = rule.get("description", "")
    rule_type = rule.get("type", "")

    if rule_type == "validation":
        # Generate null/blank/zero/negative test ideas
        if "price" in text.lower() or "金额" in text or "amount" in text.lower():
            test_cases.append({
                "name": f"test_{slug(rule['target_domain'])}_zero_value_should_fail",
                "input": {"value": 0},
                "expected": {"status": 400},
            })
            test_cases.append({
                "name": f"test_{slug(rule['target_domain'])}_negative_value_should_fail",
                "input": {"value": -1},
                "expected": {"status": 400},
            })
        if "not null" in text.lower() or "必填" in text or "required" in text.lower():
            test_cases.append({
                "name": f"test_{slug(rule['target_domain'])}_null_should_fail",
                "input": {"value": None},
                "expected": {"status": 400},
            })
        if "not blank" in text.lower() or "不能为空" in text:
            test_cases.append({
                "name": f"test_{slug(rule['target_domain'])}_blank_should_fail",
                "input": {"value": ""},
                "expected": {"status": 400},
            })

    if rule_type == "state_transition":
        test_cases.append({
            "name": f"test_{slug(rule['target_domain'])}_invalid_transition_should_fail",
            "input": {"from_state": "invalid", "to_state": "target"},
            "expected": {"status": 400},
        })

    return test_cases


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:40]


def build_business_rules(root: Path) -> dict[str, Any]:
    """Build business rules from design-docs markdown files."""
    paths = runner.RunnerPaths(root)
    design_dir = root / "design-docs"

    rules_output: dict[str, Any] = {
        "generated_at": runner.now_iso(),
        "sources": [],
        "rules": [],
        "warnings": [],
    }

    if not design_dir.exists():
        rules_output["warnings"].append("design-docs/ does not exist.")
        output_path = paths.work / "business_rules.json"
        runner.write_json(output_path, rules_output)
        return rules_output

    rule_count = 0
    for doc_path in sorted(design_dir.rglob("*.md")):
        source = runner.rel(root, doc_path)
        rules_output["sources"].append(source)
        text = runner.read_text(doc_path)

        # Parse into sections and extract rules
        current_section = "概要"
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue

            # Track headings
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                current_section = runner.clean_markdown_text(heading.group(2))
                continue

            # Skip lines that are clearly not business rules
            if len(line) < 15:
                continue
            if line.startswith("```") or line.startswith("|") or line.startswith("!"):
                continue
            if re.match(r"^[-*]\s+", line) and len(line) < 20:
                continue

            # Check if this looks like a business rule
            rule_type = classify_rule_type(line)
            if rule_type == "other" and len(line) < 40:
                continue

            priority = infer_priority(line)
            rule_count += 1

            rule: dict[str, Any] = {
                "id": f"REQ-{rule_count:04d}",
                "source_file": source,
                "source_section": current_section,
                "source_line": line_no,
                "type": rule_type,
                "target_domain": extract_target_domain(line, source),
                "description": runner.clean_markdown_text(line),
                "condition": "",
                "expected_behavior": "",
                "expected_status": None,
                "related_api": extract_related_apis(line),
                "priority": priority,
                "test_cases": [],
            }

            # Generate test cases for the rule
            rule["test_cases"] = generate_test_cases(rule)

            rules_output["rules"].append(rule)

    # Persist
    output_path = paths.work / "business_rules.json"
    runner.write_json(output_path, rules_output)

    # Write index
    index_lines = [
        "# Business Rules Index",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        f"Sources: {len(rules_output['sources'])} documents",
        f"Rules extracted: {rule_count}",
        "",
    ]

    # Group by type
    by_type: dict[str, list[dict[str, Any]]] = {}
    for rule in rules_output["rules"]:
        by_type.setdefault(rule["type"], []).append(rule)

    for rule_type, rules in sorted(by_type.items()):
        index_lines.extend([f"## {rule_type} ({len(rules)})", ""])
        p0 = sum(1 for r in rules if r["priority"] == "P0")
        p1 = sum(1 for r in rules if r["priority"] == "P1")
        p2 = sum(1 for r in rules if r["priority"] == "P2")
        index_lines.append(f"P0={p0} P1={p1} P2={p2}")
        index_lines.append("")

    if rules_output["warnings"]:
        index_lines.extend(["## Warnings", ""])
        for w in rules_output["warnings"]:
            index_lines.append(f"- {w}")

    runner.write_text(
        paths.work / "03_business_rules_index.md",
        "\n".join(index_lines).rstrip() + "\n",
    )

    return rules_output


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    rules = build_business_rules(root)

    if args.output:
        runner.write_json(Path(args.output), rules)
    else:
        print(json.dumps(rules, ensure_ascii=False, indent=2))

    return 0 if not rules.get("warnings") else 1


if __name__ == "__main__":
    raise SystemExit(main())
