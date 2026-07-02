#!/usr/bin/env python3
"""Static checker for money calculation risk patterns."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shophub_goal_runner as runner


MONEY_HINTS = ("amount", "price", "total", "payable", "discount", "coupon", "refund", "invoice", "fee", "金额", "价格", "折扣")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check money formula risk patterns.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def is_money_file(path: Path, text: str) -> bool:
    haystack = f"{path.as_posix().lower()}\n{text.lower()}"
    return any(hint.lower() in haystack for hint in MONEY_HINTS)


def issue(issue_id: str, issue_type: str, severity: str, summary: str, path: Path, root: Path, evidence: str, hint: str) -> dict[str, Any]:
    return {
        "issue_id": issue_id,
        "severity": severity,
        "type": issue_type,
        "summary": summary,
        "suspected_files": [runner.rel(root, path)],
        "evidence": evidence.strip()[:500],
        "repair_hint": hint,
    }


def check(root: Path) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    report_dir = paths.work / "checker_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    issues: list[dict[str, Any]] = []
    code_dir = root / "code"
    if code_dir.exists():
        for path in sorted(code_dir.rglob("*.java")):
            if "/src/test/" in path.as_posix():
                continue
            text = runner.read_text(path)
            if not is_money_file(path, text):
                continue
            clean = runner.strip_java_comments(text)
            rel_path = runner.rel(root, path)
            if re.search(r"new\s+BigDecimal\s*\(\s*(?:\d+\.\d+|[A-Za-z_][A-Za-z0-9_]*\s*)\)", clean):
                issues.append(issue(
                    f"ISSUE-MONEY-BIGDECIMAL-{runner.stable_hash(rel_path).upper()}",
                    "money_formula",
                    "P1",
                    "Money logic may construct BigDecimal from floating-point values",
                    path,
                    root,
                    "new BigDecimal(...) pattern in money-related code",
                    "Use BigDecimal.valueOf or string constants; keep scale/rounding explicit.",
                ))
            if re.search(r"\bBigDecimal\b[\s\S]{0,120}\.equals\s*\(", clean):
                issues.append(issue(
                    f"ISSUE-MONEY-COMPARE-{runner.stable_hash(rel_path).upper()}",
                    "money_formula",
                    "P1",
                    "BigDecimal equality may be scale-sensitive in money comparison",
                    path,
                    root,
                    ".equals(...) used near BigDecimal",
                    "Use compareTo for monetary comparisons unless exact scale equality is specified.",
                ))
            if re.search(r"payable\w*\s*=|setPayable\w*\s*\(", clean, re.I):
                has_total = re.search(r"total|itemTotal|subtotal", clean, re.I)
                has_discount = re.search(r"discount|coupon|pointsDeduction", clean, re.I)
                has_fee = re.search(r"shipping|fee|freight", clean, re.I)
                if has_total and (not has_discount or not has_fee):
                    issues.append(issue(
                        f"ISSUE-MONEY-PAYABLE-{runner.stable_hash(rel_path).upper()}",
                        "money_formula",
                        "P0",
                        "Payable amount formula may omit discount/points deduction or shipping fee",
                        path,
                        root,
                        "payable assignment detected without all expected money components nearby",
                        "Verify payableAmount = itemTotal + shippingFee - discountAmount - pointsDeductionAmount.",
                    ))
            if re.search(r"(refundAmount|invoiceAmount|setRefundAmount|setInvoiceAmount)", clean) and not re.search(r"compareTo\s*\([^)]*\)\s*[<>]=?\s*0|min\s*\(", clean):
                issues.append(issue(
                    f"ISSUE-MONEY-BOUND-{runner.stable_hash(rel_path).upper()}",
                    "money_formula",
                    "P0",
                    "Refund or invoice amount may lack an upper-bound check",
                    path,
                    root,
                    "refund/invoice amount logic without compareTo/min bound pattern",
                    "Ensure refund/invoice amount cannot exceed the eligible paid/refundable/invoiceable amount.",
                ))

    report = {
        "generated_at": runner.now_iso(),
        "checker": "money_formula",
        "issues": issues,
        "summary": {"total": len(issues), "p0": sum(1 for i in issues if i["severity"] == "P0")},
    }
    runner.write_json(report_dir / "money_formula_checker.json", report)
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    report = check(root)
    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
