#!/usr/bin/env python3
"""Static checker for state-machine and state-guard risk patterns."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shophub_goal_runner as runner


STATE_TYPES = ("OrderStatus", "PaymentStatus", "LogisticsStatus", "RefundStatus", "UserStatus")
ILLEGAL_HINTS = (("PAID", "OUTBOUND"), ("PENDING_ACTIVATION", "login"), ("FROZEN", "createOrder"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check state machine risk patterns.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def check(root: Path) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    report_dir = paths.work / "checker_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    issues: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    code_dir = root / "code"
    if code_dir.exists():
        for path in sorted(code_dir.rglob("*.java")):
            if "/src/test/" in path.as_posix():
                continue
            text = runner.strip_java_comments(runner.read_text(path))
            rel_path = runner.rel(root, path)
            if not any(st in text for st in STATE_TYPES) and not re.search(r"\bstatus\b", text, re.I):
                continue
            for match in re.finditer(r"set([A-Za-z]*Status)\s*\(\s*([A-Z_]+)", text):
                transitions.append({"file": rel_path, "target": match.group(1), "to": match.group(2)})
            if "PAID" in text and "OUTBOUND" in text and not re.search(r"PICKING|LABEL_PRINTED", text):
                issues.append({
                    "issue_id": f"ISSUE-STATE-DIRECT-OUTBOUND-{runner.stable_hash(rel_path).upper()}",
                    "severity": "P0",
                    "type": "state_machine",
                    "summary": "Order/logistics flow may jump directly from PAID to OUTBOUND",
                    "suspected_files": [rel_path],
                    "evidence": "PAID and OUTBOUND appear in the same state-changing code without PICKING/LABEL_PRINTED guard",
                    "repair_hint": "Model allowed transitions explicitly and reject illegal jumps with a conflict error.",
                })
            if re.search(r"login\s*\(", text, re.I) and "PENDING_ACTIVATION" not in text and "UserStatus" in text:
                issues.append({
                    "issue_id": f"ISSUE-STATE-PENDING-LOGIN-{runner.stable_hash(rel_path).upper()}",
                    "severity": "P0",
                    "type": "state_guard",
                    "summary": "Login path may not guard PENDING_ACTIVATION users",
                    "suspected_files": [rel_path],
                    "evidence": "login method references UserStatus without PENDING_ACTIVATION handling",
                    "repair_hint": "Return the documented inactive-user error before issuing tokens/session state.",
                })
            if re.search(r"createOrder|submitOrder|placeOrder", text) and "FROZEN" not in text and "UserStatus" in text:
                issues.append({
                    "issue_id": f"ISSUE-STATE-FROZEN-ORDER-{runner.stable_hash(rel_path).upper()}",
                    "severity": "P0",
                    "type": "state_guard",
                    "summary": "Order creation may not guard frozen users",
                    "suspected_files": [rel_path],
                    "evidence": "order creation method references UserStatus without FROZEN handling",
                    "repair_hint": "Reject frozen users with the documented USER_FROZEN-style error before order mutation.",
                })
    report = {
        "generated_at": runner.now_iso(),
        "checker": "state_machine",
        "issues": issues,
        "transitions": transitions,
        "summary": {"total": len(issues), "p0": sum(1 for i in issues if i["severity"] == "P0")},
    }
    runner.write_json(report_dir / "state_machine_checker.json", report)
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
