#!/usr/bin/env python3
"""Build public diagnostics, with optional local-public-debug rule output.

In competition-final mode the output is diagnostic-only and cannot feed
business_rules, feature_list, or repair_tasks.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_mode
import shophub_goal_runner as runner


RULE_CLUSTERS: list[dict[str, Any]] = [
    {
        "id": "PUBRULE-MONEY-FORMULA",
        "category": "money_formula",
        "severity": "P0",
        "keywords": ["discountAmount", "payableAmount", "coupon", "shippingFee", "pointsDeductionAmount", "refundAmount", "invoiceAmount", "金额", "折扣", "优惠券", "应付", "退款", "发票"],
        "description": "金额字段必须遵循设计语义，折扣、运费、积分抵扣、退款和发票金额不得反向或超额计算。",
    },
    {
        "id": "PUBRULE-USER-STATE-GUARD",
        "category": "state_guard",
        "severity": "P0",
        "keywords": ["PENDING_ACTIVATION", "FROZEN", "USER_NOT_ACTIVE", "USER_FROZEN", "冻结", "待激活", "不可登录", "不可下单"],
        "description": "用户状态必须被核心业务入口稳定拦截，待激活用户不可登录，冻结用户不可下单。",
    },
    {
        "id": "PUBRULE-ORDER-LOGISTICS-STATE-MACHINE",
        "category": "state_machine",
        "severity": "P0",
        "keywords": ["OrderStatus", "LogisticsStatus", "PAID", "OUTBOUND", "PICKING", "LABEL_PRINTED", "状态机", "流转", "跳步"],
        "description": "订单、支付、物流、退款状态只能按设计状态机流转，非法转换必须稳定返回冲突类错误。",
    },
    {
        "id": "PUBRULE-RISK-CONTROL",
        "category": "risk_control",
        "severity": "P0",
        "keywords": ["risk", "风控", "高风险", "REJECT", "拒绝", "RISK"],
        "description": "高风险订单必须被拒绝，且被拒绝订单不能继续支付、发货或进入后续状态。",
    },
    {
        "id": "PUBRULE-FAILURE-ISOLATION",
        "category": "failure_isolation",
        "severity": "P1",
        "keywords": ["notification", "event", "通知", "事件", "后置动作", "回滚", "rollback", "支付成功"],
        "description": "支付主事务成功后，通知或事件发布失败不得回滚主支付状态，失败应被记录或可查询。",
    },
    {
        "id": "PUBRULE-CLOCK-USAGE",
        "category": "clock",
        "severity": "P1",
        "keywords": ["LocalDateTime.now", "Instant.now", "System.currentTimeMillis", "Clock", "测试时钟", "业务时间"],
        "description": "核心业务时间必须通过可控时钟抽象获取，不能绕过测试时钟直接读取系统时间。",
    },
    {
        "id": "PUBRULE-SORTING-PAGINATION",
        "category": "sorting_pagination",
        "severity": "P1",
        "keywords": ["page", "size", "sort", "order by", "分页", "排序", "列表", "历史", "统计"],
        "description": "列表、搜索、历史记录和统计接口必须具有稳定排序，并正确处理分页边界。",
    },
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build public diagnostics or local public-case rules.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def read_sources(root: Path) -> tuple[str, list[dict[str, Any]]]:
    texts: list[str] = []
    evidence: list[dict[str, Any]] = []
    doc_paths = [root / "README.md"]
    if (root / "design-docs").exists():
        doc_paths.extend(sorted((root / "design-docs").rglob("*.md")))
    for path in doc_paths:
        if not path.exists():
            continue
        text = runner.read_text(path)
        texts.append(text)
        evidence.append({"source": runner.rel(root, path), "kind": "spec", "snippet": ""})

    for path in sorted((root / "test-cases").rglob("*.java")) if (root / "test-cases").exists() else []:
        text = runner.read_text(path)
        scrubbed = scrub_test_fixture_literals(text)
        texts.append(scrubbed)
        evidence.append({"source": runner.rel(root, path), "kind": "public-test", "snippet": extract_test_evidence(scrubbed)})

    return "\n".join(texts), evidence


def scrub_test_fixture_literals(text: str) -> str:
    text = re.sub(r'"[^"\n]{4,}"', '"<literal>"', text)
    text = re.sub(r"\b\d{5,}\b", "<number>", text)
    return text


def extract_test_evidence(text: str) -> str:
    names = re.findall(r"\b(?:void|fun)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    assertions = re.findall(r"\bassert[A-Za-z0-9_]*\s*\(", text)
    pieces = names[:5] + assertions[:5]
    return ", ".join(pieces)[:240]


def infer_expected_behavior(text: str, cluster: dict[str, Any]) -> dict[str, Any]:
    nearby = text
    status_codes = sorted(set(int(code) for code in re.findall(r"\b(400|401|403|404|409|422|500)\b", nearby)))
    error_codes = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", nearby)))
    expected: dict[str, Any] = {}
    if status_codes:
        expected["status_candidates"] = status_codes
    if error_codes:
        expected["error_code_candidates"] = error_codes[:20]
    if cluster["category"] == "money_formula":
        expected["invariant"] = "payableAmount = itemTotal + shippingFee - discountAmount - pointsDeductionAmount; bounded amounts cannot exceed eligible amount"
    if cluster["category"] == "state_machine":
        expected["invariant"] = "illegal state transitions return conflict and do not mutate state"
    return expected


def build_public_case_rules(root: Path) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    paths.work.mkdir(parents=True, exist_ok=True)
    combined_text, source_evidence = read_sources(root)
    lowered = combined_text.lower()

    rules: list[dict[str, Any]] = []
    for cluster in RULE_CLUSTERS:
        hits = [kw for kw in cluster["keywords"] if kw.lower() in lowered]
        if not hits:
            continue
        rules.append(
            {
                "id": cluster["id"],
                "category": cluster["category"],
                "severity": cluster["severity"],
                "description": cluster["description"],
                "expected_behavior": infer_expected_behavior(combined_text, cluster),
                "evidence": [
                    ev for ev in source_evidence
                    if any(kw.lower() in (runner.read_text(root / ev["source"]) if (root / ev["source"]).exists() else "").lower() for kw in hits[:5])
                ][:10],
                "matched_keywords": hits[:20],
                "anti_overfit_note": "不得硬编码公开测试 userId、testRunId、手机号、商品名、订单号或只在 test-cases 中出现的 literal。",
            }
        )

    report = {
        "generated_at": runner.now_iso(),
        "mode": pipeline_mode.current_mode(),
        "role": pipeline_mode.public_role(),
        "rules": rules,
        "summary": {
            "total": len(rules),
            "p0": sum(1 for rule in rules if rule["severity"] == "P0"),
            "p1": sum(1 for rule in rules if rule["severity"] == "P1"),
        },
        "policy": {
            "public_tests_are_oracle": pipeline_mode.allows_public_derived_requirements(),
            "competition_final_effect": (
                "diagnostic_only; public rules must not feed business_rules, feature_list, or repair_tasks"
            ),
        },
    }
    if pipeline_mode.allows_public_derived_requirements():
        output = paths.work / "public_case_rules.json"
        runner.write_json(output, report)
    else:
        diagnostic = dict(report)
        diagnostic["diagnostic_rule_candidates"] = diagnostic.pop("rules")
        output = paths.work / "public_diagnostics.json"
        runner.write_json(output, diagnostic)
        stale_rules = paths.work / "public_case_rules.json"
        if stale_rules.exists():
            stale_rules.unlink()
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    report = build_public_case_rules(root)
    if args.output:
        if pipeline_mode.allows_public_derived_requirements():
            runner.write_json(Path(args.output), report)
        else:
            diagnostic = dict(report)
            diagnostic["diagnostic_rule_candidates"] = diagnostic.pop("rules")
            runner.write_json(Path(args.output), diagnostic)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
