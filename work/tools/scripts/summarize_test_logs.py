#!/usr/bin/env python3
"""Summarize Maven logs under .agent-work/test-results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize ShopHub test logs.")
    parser.add_argument("--root", default=".", help="Project root.")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    summary = runner.summarize_test_logs(root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
