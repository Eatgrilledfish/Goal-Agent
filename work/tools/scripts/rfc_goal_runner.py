#!/usr/bin/env python3
"""RFC implementation-difference-detection goal runner (CLI entry).

Implements the deterministic backbone of the pipeline described in
FIX-rfc-migration.md sections 4 and 9.1. It manages the ``.agent-work/``
state directory and dispatches each phase to a specialist helper script.

This runner only *identifies* inconsistencies; it never modifies the target
code. Final results are written under ``/result`` and trace artifacts under
``/logs``.

Subcommands mirror the phases:
    init | load-docs | extract-spec | index-code | map |
    detect | review | report | gate | run-all
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import rfc_common as rc

SCRIPT_DIR = rc.SCRIPT_DIR

PHASE_SCRIPTS: dict[str, list[str]] = {
    "load-docs": ["benchmark_reader.py", "rfc_fetch_convert.py"],
    # Phase 2.5: build a lite code inventory, then dynamically scope which
    # RFCs enter first-round detection, then validate that scope
    # (FIX-rfc-scope-planner.md). Runs before extract-spec so requirement
    # extraction only touches selected_primary_rfcs.
    "scope-plan": ["code_inventory_lite.py", "rfc_scope_planner.py",
                   "rfc_scope_plan_validator.py"],
    "extract-spec": ["normative_requirement_extractor.py"],
    "index-code": ["c_code_indexer.py"],
    "map": ["requirement_code_mapper.py"],
    "detect": ["protocol_inconsistency_detector.py"],
    "review": ["evidence_validator.py", "issue_ranker.py"],
    "report": ["issue_report_writer.py"],
    "gate": ["final_detection_gate.py"],
}

PHASE_ORDER = [
    "init",
    "load-docs",
    "scope-plan",
    "extract-spec",
    "index-code",
    "map",
    "detect",
    "review",
    "report",
    "gate",
]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rfc_goal_runner.py",
        description="RFC implementation-difference-detection pipeline runner.",
    )
    p.add_argument("--code-root", required=False, default=rc.DEFAULT_ASSET_ROOT + "/code/f-stack",
                   help="F-Stack code repository root.")
    p.add_argument("--design-root", required=False, default=rc.DEFAULT_ASSET_ROOT + "/Difference",
                   help="Design / RFC document root.")
    p.add_argument("--benchmark", required=False,
                   default=rc.DEFAULT_ASSET_ROOT + "/Difference/benchmark.md",
                   help="Path to benchmark.md.")
    p.add_argument("--result-root", required=False, default="/result",
                   help="Final result output root.")
    p.add_argument("--log-root", required=False, default="/logs",
                   help="Log output root.")
    p.add_argument("command", choices=list(PHASE_ORDER) + ["run-all"],
                   help="Pipeline phase to run, or 'run-all'.")
    return p


def run_script(name: str, args: argparse.Namespace) -> int:
    """Invoke a sibling phase script with the shared path arguments.

    When the scope-plan phase has produced ``rfc_scope_plan.json``, pass
    ``--scope-plan`` to the normative extractor so it only processes the
    selected primary RFCs rather than the full benchmark set.
    """
    script = SCRIPT_DIR / name
    if not script.exists():
        print(f"[runner] missing phase script: {script}", file=sys.stderr)
        return 2
    cmd = [
        sys.executable, str(script),
        "--code-root", args.code_root,
        "--design-root", args.design_root,
        "--benchmark", args.benchmark,
        "--result-root", args.result_root,
        "--log-root", args.log_root,
    ]
    if name == "normative_requirement_extractor.py":
        work = rc.agent_work_dir(Path(args.code_root))
        if (work / "rfc_scope_plan.json").exists():
            cmd.append("--scope-plan")
    print(f"[runner] phase -> {name}")
    proc = subprocess.run(cmd)
    return proc.returncode


def run_phase(phase: str, args: argparse.Namespace) -> int:
    """Run every script attached to a phase; fail fast on first non-zero exit."""
    for name in PHASE_SCRIPTS[phase]:
        status = run_script(name, args)
        if status != 0:
            return status
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root)
    design_root = Path(args.design_root)
    benchmark = Path(args.benchmark)
    result_root = Path(args.result_root)
    log_root = Path(args.log_root)
    work = rc.agent_work_dir(code_root)

    rc.ensure_dir(result_root)
    rc.ensure_dir(log_root)
    rc.ensure_dir(log_root / "trace")
    rc.ensure_dir(work / "rfcs")

    state = {
        "initialized_at": rc.now_iso(),
        "code_root": str(code_root),
        "design_root": str(design_root),
        "benchmark": str(benchmark),
        "result_root": str(result_root),
        "log_root": str(log_root),
        "agent_work": str(work),
        "phases": {ph: "pending" for ph in PHASE_ORDER if ph != "init"},
    }
    state["phases"]["init"] = "done"
    rc.save_json(work / "pipeline_state.json", state)

    # Preflight checks (do not fail hard; report and continue per section 9.3 strategy).
    checks = {
        "code_root_exists": code_root.exists(),
        "design_root_exists": design_root.exists(),
        "benchmark_exists": benchmark.exists(),
    }
    rc.save_json(work / "preflight.json", {
        "checked_at": rc.now_iso(),
        "checks": checks,
        "asset_root_default": rc.DEFAULT_ASSET_ROOT,
    })
    for key, ok in checks.items():
        flag = "OK" if ok else "MISSING"
        print(f"[init] {key}: {flag}")
    print(f"[init] agent-work -> {work}")
    return 0


def mark_phase(args: argparse.Namespace, phase: str, status: str) -> None:
    work = rc.agent_work_dir(Path(args.code_root))
    state_path = work / "pipeline_state.json"
    if not state_path.exists():
        return
    state = rc.load_json(state_path)
    state.setdefault("phases", {})[phase] = status
    state["phases"][phase] = status
    rc.save_json(state_path, state)


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    args = build_arg_parser().parse_args(argv)

    if args.command == "run-all":
        failures = []
        for phase in PHASE_ORDER:
            if phase == "init":
                status = cmd_init(args)
            else:
                status = run_phase(phase, args)
            mark_phase(args, phase, "done" if status == 0 else "failed")
            if status != 0:
                failures.append(phase)
        if failures:
            print(f"[runner] run-all completed with failures: {failures}", file=sys.stderr)
            return 1
        print("[runner] run-all completed successfully")
        return 0

    if args.command == "init":
        return cmd_init(args)

    status = run_phase(args.command, args)
    mark_phase(args, args.command, "done" if status == 0 else "failed")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
