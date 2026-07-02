#!/usr/bin/env python3
"""Static checker for post-transaction failure isolation risks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shophub_goal_runner as runner


SIDE_EFFECT_HINTS = ("notify", "notification", "event", "publish", "message", "points", "inventory", "coupon", "通知", "事件")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check failure isolation risk patterns.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


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
            text = runner.strip_java_comments(runner.read_text(path))
            rel_path = runner.rel(root, path)
            lowered = text.lower()
            if not any(hint.lower() in lowered for hint in SIDE_EFFECT_HINTS):
                continue
            transactional_block = "@Transactional" in text
            if transactional_block and re.search(r"(pay|paid|payment|success)[A-Za-z0-9_]*\s*\(", text, re.I):
                if re.search(r"(notify|publish|send|event|message)[A-Za-z0-9_]*\s*\(", text, re.I) and "AFTER_COMMIT" not in text and "@Async" not in text:
                    issues.append({
                        "issue_id": f"ISSUE-ISOLATION-POSTACTION-{runner.stable_hash(rel_path).upper()}",
                        "severity": "P1",
                        "type": "failure_isolation",
                        "summary": "Payment success flow may run post actions inside the main transaction",
                        "suspected_files": [rel_path],
                        "evidence": "transactional payment logic calls notification/event side effects",
                        "repair_hint": "Move notifications/events to after-commit or catch/log them without rolling back the payment state.",
                    })
            for catch in re.finditer(r"catch\s*\(\s*(?:Exception|Throwable|RuntimeException)\s+\w+\s*\)\s*\{(?P<body>[^}]*)\}", text, re.S):
                body = catch.group("body")
                if "log." not in body and "throw" not in body:
                    issues.append({
                        "issue_id": f"ISSUE-ISOLATION-SWALLOW-{runner.stable_hash(rel_path + str(catch.start())).upper()}",
                        "severity": "P0",
                        "type": "failure_isolation",
                        "summary": "Exception catch block may swallow side-effect failures without evidence",
                        "suspected_files": [rel_path],
                        "evidence": body.strip()[:240],
                        "repair_hint": "Do not silently swallow core exceptions; isolate optional side effects with explicit logging/status.",
                    })
    report = {
        "generated_at": runner.now_iso(),
        "checker": "failure_isolation",
        "issues": issues,
        "summary": {"total": len(issues), "p0": sum(1 for i in issues if i["severity"] == "P0")},
    }
    runner.write_json(report_dir / "failure_isolation_checker.json", report)
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
