#!/usr/bin/env python3
"""Generate and compare ShopHub API snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate current code API snapshot and compare with baseline.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--current-only", action="store_true", help="Only write api_snapshot_current.json.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)
    current = runner.snapshot_code_api(root)
    runner.write_json(paths.work / "api_snapshot_current.json", current)
    if args.current_only:
        print(json.dumps(current, ensure_ascii=False, indent=2))
        return 0
    contract = runner.parse_api_baseline(root)
    compare = runner.compare_api_snapshots(contract, current)
    runner.write_json(paths.work / "api_compare.json", compare)
    print(json.dumps(compare, ensure_ascii=False, indent=2))
    return 0 if compare.get("safe") else 1


if __name__ == "__main__":
    raise SystemExit(main())
