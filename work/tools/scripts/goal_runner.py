#!/usr/bin/env python3
"""CLI for the deterministic harness around the opencode-owned agent loop."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import agent_common as ac


SCRIPT_DIR = Path(__file__).resolve().parent
COMMANDS = {
    "prepare": ["workspace_inventory.py"],
    "architecture-check": ["stage_artifact_validator.py"],
    "risk-plan-check": [
        "stage_artifact_validator.py:architecture", "risk_sweep_plan_validator.py",
    ],
    "design-check": ["design_artifact_validator.py"],
    "claim-check": ["claim_review_validator.py"],
    "task-check": ["stage_artifact_validator.py"],
    "coverage-check": ["stage_artifact_validator.py"],
    "review": [
        "design_artifact_validator.py", "claim_review_validator.py",
        "verdict_validator.py",
    ],
    "report": ["report_writer.py"],
    "gate": [
        "design_artifact_validator.py", "claim_review_validator.py",
        "stage_artifact_validator.py:architecture", "risk_sweep_plan_validator.py",
        "stage_artifact_validator.py:task",
        "stage_artifact_validator.py:coverage", "verdict_validator.py", "final_gate.py",
    ],
    "finalize": [
        "design_artifact_validator.py", "claim_review_validator.py",
        "stage_artifact_validator.py:architecture", "risk_sweep_plan_validator.py",
        "stage_artifact_validator.py:task",
        "stage_artifact_validator.py:coverage", "verdict_validator.py",
        "report_writer.py", "final_gate.py",
    ],
}
MAX_HELPER_SECONDS = 21600


def _directories(path: Path) -> list[Path]:
    return sorted(item for item in path.iterdir() if item.is_dir() and not item.name.startswith(".")) if path.is_dir() else []


def discover_code_root(asset_root: Path) -> Path:
    code = asset_root / "code"
    projects = _directories(code)
    if len(projects) != 1:
        raise ValueError(
            f"automatic code-root discovery requires exactly one project directory under {code}; "
            "the opencode orchestrator must pass its model-selected --code-root"
        )
    return projects[0]


def discover_design_root(asset_root: Path) -> Path:
    candidates = [path for path in _directories(asset_root) if path.name.lower() != "code"]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(
        "automatic design-root discovery is ambiguous; the opencode orchestrator must pass "
        "its model-selected --design-root"
    )


def resolve_paths(args: argparse.Namespace) -> None:
    asset_root = Path(args.asset_root).resolve()
    args.code_root = str(Path(args.code_root).resolve()) if args.code_root else str(discover_code_root(asset_root))
    args.design_root = str(Path(args.design_root).resolve()) if args.design_root else str(discover_design_root(asset_root))


def script_command(script_spec: str, args: argparse.Namespace) -> list[str]:
    script, separator, explicit_stage = script_spec.partition(":")
    command = [
        sys.executable, str(SCRIPT_DIR / script),
        "--code-root", args.code_root,
        "--design-root", args.design_root,
        "--result-root", args.result_root,
        "--log-root", args.log_root,
    ]
    if args.state_root:
        command.extend(["--state-root", args.state_root])
    if args.source_manifest:
        command.extend(["--source-manifest", args.source_manifest])
    for entry in args.design_entry:
        command.extend(["--design-entry", entry])
    if script == "stage_artifact_validator.py":
        command.extend([
            "--stage", explicit_stage if separator else args.command.removesuffix("-check"),
        ])
    return command


def remaining_seconds(args: argparse.Namespace) -> int:
    if args.command == "prepare":
        return MAX_HELPER_SECONDS
    root = ac.state_root(Path(args.log_root), args.state_root)
    state_path = root / "agent_loop_state.json"
    if not state_path.is_file():
        return MAX_HELPER_SECONDS
    state = ac.load_json(state_path)
    deadline_at = str(state.get("deadline_at") or "")
    if not deadline_at:
        return MAX_HELPER_SECONDS
    return int((ac.parse_iso(deadline_at) - ac.parse_iso(ac.now_iso())).total_seconds())


def record_timeout(args: argparse.Namespace, script_spec: str, reason: str) -> None:
    root = ac.state_root(Path(args.log_root), args.state_root)
    state_path = root / "agent_loop_state.json"
    if not state_path.is_file():
        return
    state = ac.load_json(state_path)
    state["updated_at"] = ac.now_iso()
    state["status"] = "blocked"
    state["current_phase"] = "time_limit"
    state["stop_reason"] = "hard_deadline_reached"
    state["next_actions"] = []
    ac.save_json(state_path, state)
    ac.append_jsonl(root / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": state.get("session_id", ""),
        "event": "helper_timeout", "actor": "goal_runner", "phase": "time_limit",
        "status": "blocked", "script": script_spec, "reason": reason,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and finalize a generic opencode design/code inconsistency review."
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--asset-root", default=ac.DEFAULT_ASSET_ROOT)
    parser.add_argument("--code-root", default=None)
    parser.add_argument("--design-root", default=None)
    parser.add_argument("--design-entry", action="append", default=[])
    parser.add_argument("--source-manifest", default=None)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    parser.add_argument("--state-root", default=None)
    args = parser.parse_args(argv)
    try:
        resolve_paths(args)
    except ValueError as exc:
        parser.error(str(exc))

    exit_code = 0
    for script_spec in COMMANDS[args.command]:
        timeout = remaining_seconds(args)
        if timeout <= 0:
            record_timeout(args, script_spec, "session deadline was reached before helper start")
            print(f"[goal-runner] deadline reached before {script_spec}", file=sys.stderr)
            return 124
        command = script_command(script_spec, args)
        print(f"[goal-runner] {script_spec}")
        try:
            result = subprocess.run(command, timeout=timeout)
        except subprocess.TimeoutExpired:
            record_timeout(args, script_spec, f"helper exceeded remaining session budget ({timeout}s)")
            print(f"[goal-runner] timed out: {script_spec}", file=sys.stderr)
            return 124
        if result.returncode:
            exit_code = exit_code or result.returncode
            if args.command != "gate":
                return exit_code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
