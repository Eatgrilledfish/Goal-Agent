#!/usr/bin/env python3
"""Validate the RFC scope plan produced by ``rfc_scope_planner.py``.

Reproducibility guard (FIX-rfc-scope-planner.md): the planner is heuristic, so
this stage sanity-checks its output before downstream phases consume it.

Checks (each violation is reported; any hard violation -> non-zero exit):
  * at least ``min_primary_rfcs`` RFCs selected as primary (policy default 4)
  * no more than ``max_primary_rfcs`` primary RFCs (policy default 8)
  * every excluded RFC carries a non-empty ``reason``
  * no RFC obsoleted-by-a-present-successor was promoted to primary
  * every primary RFC has a finite score and a defined protocol scope

Outputs ``.agent-work/rfc_scope_plan_validation.json`` and a console summary.
Returns 0 when all checks pass, 1 on any hard violation (missing plan file
counts as a hard violation).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import rfc_common as rc


def validate(plan: dict, policy: dict, domain_map: dict) -> tuple[list[dict], list[dict]]:
    """Return ``(hard_violations, warnings)`` as lists of {check, detail}."""
    hard: list[dict] = []
    warnings: list[dict] = []

    min_primary = policy.get("min_primary_rfcs", 4)
    max_primary = policy.get("max_primary_rfcs", 8)
    supersession = domain_map.get("supersession", {})

    primary = plan.get("selected_primary_rfcs", []) or []
    secondary = plan.get("secondary_rfcs", []) or []
    excluded = plan.get("excluded_rfcs", []) or []

    if len(primary) < min_primary:
        hard.append({
            "check": "min_primary_rfcs",
            "detail": f"only {len(primary)} primary RFC(s); policy requires >= {min_primary}",
        })
    if len(primary) > max_primary:
        hard.append({
            "check": "max_primary_rfcs",
            "detail": f"{len(primary)} primary RFC(s); policy caps at {max_primary}",
        })

    # Obsoleted-by-present-successor must never be primary.
    primary_set = {e.get("rfc") for e in primary}
    secondary_set = {e.get("rfc") for e in secondary}
    for newer, older in supersession.items():
        for old in older:
            if old in primary_set:
                hard.append({
                    "check": "obsoleted_as_primary",
                    "detail": f"{old} is obsoleted by {newer} but was promoted to primary",
                })

    # Every excluded RFC needs a reason.
    for e in excluded:
        rfc = e.get("rfc", "?")
        reason = (e.get("reason") or "").strip()
        if not reason:
            hard.append({
                "check": "excluded_missing_reason",
                "detail": f"{rfc} excluded without a reason",
            })

    # Primary entries must be structurally sound.
    for e in primary:
        rfc = e.get("rfc", "?")
        score = e.get("score")
        if score is None or not isinstance(score, (int, float)):
            hard.append({
                "check": "primary_missing_score",
                "detail": f"{rfc} has no numeric score",
            })
        scope = (e.get("scope") or "").strip()
        if not scope:
            warnings.append({
                "check": "primary_missing_scope",
                "detail": f"{rfc} has no protocol scope",
            })
        # An RFC that is both primary and secondary is a planner bug.
        if rfc in secondary_set:
            hard.append({
                "check": "primary_also_secondary",
                "detail": f"{rfc} appears in both primary and secondary",
            })

    # A successor and its obsoleted predecessor should not both be primary.
    for newer, older in supersession.items():
        for old in older:
            if newer in primary_set and old in primary_set:
                hard.append({
                    "check": "successor_and_predecessor_both_primary",
                    "detail": f"{newer} and its predecessor {old} are both primary",
                })

    return hard, warnings


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Validate the RFC scope plan.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    work = rc.agent_work_dir(Path(args.code_root))
    plan_path = work / "rfc_scope_plan.json"

    if not plan_path.exists():
        report = {
            "validated_at": rc.now_iso(),
            "status": "blocked",
            "reason": "rfc_scope_plan.json missing; run scope-plan first",
            "hard_violations": [{"check": "plan_missing",
                                 "detail": "rfc_scope_plan.json not found"}],
            "warnings": [],
        }
        rc.save_json(work / "rfc_scope_plan_validation.json", report)
        print("[scope_validator] rfc_scope_plan.json missing", file=sys.stderr)
        return 1

    plan = rc.load_json(plan_path)
    policy = rc.load_config("rfc_scope_policy.json")
    domain_map = rc.load_config("rfc_domain_map.json")

    hard, warnings = validate(plan, policy, domain_map)

    report = {
        "validated_at": rc.now_iso(),
        "status": "ok" if not hard else "failed",
        "primary_count": len(plan.get("selected_primary_rfcs", [])),
        "secondary_count": len(plan.get("secondary_rfcs", [])),
        "excluded_count": len(plan.get("excluded_rfcs", [])),
        "hard_violations": hard,
        "warnings": warnings,
    }
    rc.save_json(work / "rfc_scope_plan_validation.json", report)

    for v in hard:
        print(f"[scope_validator] HARD: {v['check']} -- {v['detail']}", file=sys.stderr)
    for w in warnings:
        print(f"[scope_validator] warn: {w['check']} -- {w['detail']}", file=sys.stderr)

    if hard:
        print(f"[scope_validator] FAILED with {len(hard)} hard violation(s)", file=sys.stderr)
        return 1
    print(f"[scope_validator] OK -- primary={report['primary_count']} "
          f"secondary={report['secondary_count']} excluded={report['excluded_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
