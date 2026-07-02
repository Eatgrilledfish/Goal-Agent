#!/usr/bin/env python3
"""Compatibility wrapper for the top-level repair_task_builder.py."""

from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "repair_task_builder.py"), run_name="__main__")
