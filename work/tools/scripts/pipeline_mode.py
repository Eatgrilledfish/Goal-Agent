#!/usr/bin/env python3
"""Shared Goal-Agent pipeline mode helpers.

The default mode is competition-final, where public black-box tests are
diagnostic smoke only.  local-public-debug keeps the older exploratory behavior
for local investigation.
"""

from __future__ import annotations

import os
from typing import Final


COMPETITION_FINAL: Final = "competition-final"
LOCAL_PUBLIC_DEBUG: Final = "local-public-debug"
VALID_MODES: Final = {COMPETITION_FINAL, LOCAL_PUBLIC_DEBUG}


def current_mode() -> str:
    raw = (
        os.environ.get("GOAL_AGENT_MODE")
        or os.environ.get("SHOPHUB_GOAL_AGENT_MODE")
        or COMPETITION_FINAL
    )
    mode = raw.strip().lower()
    return mode if mode in VALID_MODES else COMPETITION_FINAL


def is_competition_final() -> bool:
    return current_mode() == COMPETITION_FINAL


def allows_public_derived_requirements() -> bool:
    """Whether public tests may feed feature/issue/task generation."""
    return current_mode() == LOCAL_PUBLIC_DEBUG


def public_role() -> str:
    if allows_public_derived_requirements():
        return "local-public-debug"
    return "diagnostic-smoke-only"
