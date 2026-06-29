#!/usr/bin/env python3
"""Start and finish ShopHub repair rounds."""

from __future__ import annotations

import argparse
from pathlib import Path

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ShopHub round recorder.")
    parser.add_argument("--root", default=".", help="Project root.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="Create the next round file.")
    finish = sub.add_parser("finish", help="Append completion details to a round.")
    finish.add_argument("--round", type=int, required=True)
    finish.add_argument("--result", required=True, choices=["PASS", "REWORK_REQUIRED", "REJECT_AND_REVERT", "FAILED"])
    finish.add_argument("--tests")
    finish.add_argument("--risk")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    if args.command == "start":
        path = runner.start_next_round(root)
        if path:
            print(path)
            return 0
        return 1
    if args.command == "finish":
        path = runner.finish_round(root, args.round, args.result, args.tests, args.risk)
        print(path)
        return 0
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
