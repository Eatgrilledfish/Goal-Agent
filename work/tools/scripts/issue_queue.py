#!/usr/bin/env python3
"""Inspect and update the ShopHub issue queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ShopHub issue queue helper.")
    parser.add_argument("--root", default=".", help="Project root.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List prioritized issues.")
    sub.add_parser("next", help="Print the next open issue.")
    mark = sub.add_parser("mark", help="Mark an issue status.")
    mark.add_argument("issue_id")
    mark.add_argument("status", choices=["open", "fixed", "rejected", "deferred", "blocked"])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    if args.command == "list":
        issues = runner.prioritize_issues(root)
        print(json.dumps(issues, ensure_ascii=False, indent=2))
        return 0
    if args.command == "next":
        issue = runner.select_next_issue(root)
        print(json.dumps(issue, ensure_ascii=False, indent=2))
        return 0 if issue else 1
    if args.command == "mark":
        if not runner.mark_issue_status(root, args.issue_id, args.status):
            raise SystemExit(f"Issue not found: {args.issue_id}")
        return 0
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
