#!/usr/bin/env python3
"""Patch Selector — scores validated candidates and selects the optimal patch.

Reads ``candidate_validation.jsonl``, computes scores, and outputs
``selected_patch.json`` with the best candidate and fallbacks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score and select the best candidate patch.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--validation-file", default=None,
                       help="Path to candidate_validation.jsonl (default: .agent-work/candidate_validation.jsonl).")
    parser.add_argument("--output", default=None, help="Output path for selected patch.")
    return parser


def diff_minimization(diff_files: int, diff_lines: int) -> float:
    """Score based on diff size — smaller is better."""
    return max(0.0, 1.0 - (diff_files * 0.1 + diff_lines * 0.005))


def stability_score(stable_result: str | None) -> float:
    """Score based on stability verification results."""
    if stable_result is None:
        return 0.5  # Default: not yet verified
    if stable_result == "3x_pass":
        return 1.0
    if stable_result == "2x_pass_1x_fail":
        return 0.3
    if stable_result == "intermittent":
        return 0.0
    return 0.5


def compute_score(candidate: dict[str, Any]) -> float:
    """Compute composite score for a candidate.

    Weights:
      - 40% public test pass rate
      - 25% generated test pass rate
      - 15% contract checker pass
      - 10% diff minimization (smaller = better)
      - 10% stability score
    """
    score_inputs = candidate.get("score_inputs", {})

    public_rate = float(score_inputs.get("public_test_pass_rate", 0.0))
    generated_rate = float(score_inputs.get("generated_test_pass_rate", 0.0))
    contract_pass = float(score_inputs.get("contract_checker_pass", 0.0))
    diff_files = int(score_inputs.get("diff_files", 0))
    diff_lines = int(score_inputs.get("diff_lines", 0))
    stable = score_inputs.get("stable")

    diff_score = diff_minimization(diff_files, diff_lines)
    stab_score = stability_score(stable)

    return round(
        0.40 * public_rate
        + 0.25 * generated_rate
        + 0.15 * contract_pass
        + 0.10 * diff_score
        + 0.10 * stab_score,
        4,
    )


def select_patch(root: Path, validation_file: str | None) -> dict[str, Any]:
    """Select the best candidate patch."""
    paths = runner.RunnerPaths(root)

    if validation_file:
        val_path = Path(validation_file)
    else:
        val_path = paths.work / "candidate_validation.jsonl"

    if not val_path.exists():
        return {
            "status": "skipped",
            "reason": f"Validation file not found: {val_path}",
            "selected": None,
        }

    validations = runner.read_jsonl(val_path)
    if not validations:
        return {
            "status": "skipped",
            "reason": "No validation records found",
            "selected": None,
        }

    # Filter to eligible only
    eligible = [v for v in validations if v.get("eligible", False)]

    if not eligible:
        return {
            "status": "no_eligible",
            "reason": "No candidates passed validation",
            "selected": None,
            "eliminated_count": len(validations),
        }

    # Score all eligible candidates
    scored = []
    for candidate in eligible:
        score = compute_score(candidate)
        scored.append({
            "candidate_id": candidate.get("candidate_id", "unknown"),
            "task_id": candidate.get("task_id", ""),
            "strategy": candidate.get("strategy", ""),
            "score": score,
            "patch_file": candidate.get("patch_file", ""),
            "patch_file_exists": candidate.get("patch_file_exists", False),
            "public_test_pass_rate": candidate.get("score_inputs", {}).get("public_test_pass_rate", 0),
            "generated_test_pass_rate": candidate.get("score_inputs", {}).get("generated_test_pass_rate", 0),
            "contract_checker_pass": candidate.get("score_inputs", {}).get("contract_checker_pass", 0),
            "diff_files": candidate.get("diff_files", 0),
            "diff_lines": candidate.get("diff_lines", 0),
            "compile": candidate.get("compile", "UNKNOWN"),
            "code_tests": candidate.get("code_tests", "UNKNOWN"),
            "public_tests": candidate.get("public_tests", "UNKNOWN"),
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    best = scored[0]
    fallbacks = scored[1:4] if len(scored) > 1 else []

    # Use real patch_file from candidate validation, fallback to inferred path
    real_patch = best.get("patch_file", "")
    warnings: list[str] = []
    if real_patch and best.get("patch_file_exists", False):
        patch_file_out = real_patch
        patch_source = "candidate_validation"
    elif real_patch:
        patch_file_out = real_patch
        patch_source = "candidate_validation"
        warnings.append(f"patch_file from validation ({real_patch}) does not exist on disk")
    else:
        inferred = f".agent-work/patches/{best['task_id']}-{best['candidate_id']}.patch"
        patch_file_out = inferred
        patch_source = "inferred"
        warnings.append(f"patch_file not in validation result, inferred: {inferred}")

    selected: dict[str, Any] = {
        "task_id": best["task_id"],
        "selected_candidate": best["candidate_id"],
        "score": best["score"],
        "reason": (
            f"Highest score ({best['score']:.4f}): "
            f"public={best['public_test_pass_rate']:.2f}, "
            f"generated={best['generated_test_pass_rate']:.2f}, "
            f"contract={best['contract_checker_pass']:.2f}, "
            f"diff={best['diff_files']}f/{best['diff_lines']}l, "
            f"compile={best['compile']}"
        ),
        "patch_file": patch_file_out,
        "patch_file_source": patch_source,
        "fallback_candidates": [
            {"candidate_id": fb["candidate_id"], "score": fb["score"]}
            for fb in fallbacks
        ],
        "selection_details": scored,
    }
    if warnings:
        selected["warnings"] = warnings

    # Persist
    runner.write_json(paths.work / "selected_patch.json", selected)

    # Markdown summary
    lines = [
        "# Patch Selection",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        f"## Selected: {best['candidate_id']}",
        "",
        f"- **Task**: {best.get('task_id', '')}",
        f"- **Score**: {best.get('score', 0):.4f}",
        f"- **Strategy**: {best.get('strategy', '')}",
        f"- **Reason**: {selected.get('reason', '')}",
        "",
        "## All Candidates (ranked)",
        "",
        "| Rank | Candidate | Score | Public | Generated | Contract | Diff | Compile |",
        "|------|-----------|-------|--------|-----------|----------|------|---------|",
    ]
    for rank, c in enumerate(scored, 1):
        lines.append(
            f"| {rank} | {c['candidate_id']} | {c['score']:.4f} | "
            f"{c['public_test_pass_rate']:.2f} | {c['generated_test_pass_rate']:.2f} | "
            f"{c['contract_checker_pass']:.2f} | {c['diff_files']}/{c['diff_lines']} | "
            f"{c['compile']} |"
        )
    lines.append("")

    if fallbacks:
        lines.append("## Fallbacks")
        lines.append("")
        for fb in fallbacks:
            lines.append(f"- **{fb['candidate_id']}**: score={fb['score']:.4f}")
        lines.append("")

    runner.write_text(paths.work / "selected_patch.md", "\n".join(lines).rstrip() + "\n")

    return {
        "status": "ok",
        "selected": selected,
        "total_eligible": len(eligible),
        "total_validated": len(validations),
    }


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = select_patch(root, args.validation_file)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
