#!/usr/bin/env python3
"""Submission-local runtime path helpers.

Tools in this directory are part of the submitted Goal-Agent package.  They
must be callable while ``--root`` points at an arbitrary target repository or a
candidate sandbox that does not contain ``work/``.
"""

from __future__ import annotations

from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR.parent
WORK_DIR = TOOLS_DIR.parent
SUBMISSION_ROOT = WORK_DIR.parent


def script_path(name: str) -> Path:
    return SCRIPT_DIR / name


def checker_path(name: str) -> Path:
    return SCRIPT_DIR / "checkers" / name


def review_path(name: str) -> Path:
    return SCRIPT_DIR / "review" / name


def patch_path(name: str) -> Path:
    return SCRIPT_DIR / "patch" / name
