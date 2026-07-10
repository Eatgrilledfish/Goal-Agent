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
    "design-check": ["design_artifact_validator.py"],
    "review": ["design_artifact_validator.py", "verdict_validator.py"],
    "report": ["report_writer.py"],
    "gate": ["design_artifact_validator.py", "final_gate.py"],
    "finalize": ["design_artifact_validator.py", "verdict_validator.py", "report_writer.py", "final_gate.py"],
}


def _directories(path: Path) -> list[Path]:
    return sorted(item for item in path.iterdir() if item.is_dir() and not item.name.startswith(".")) if path.is_dir() else []


def discover_code_root(asset_root: Path) -> Path:
    code = asset_root / "code"
    projects = _directories(code)
    return projects[0] if len(projects) == 1 else code


def discover_design_root(asset_root: Path) -> Path:
    candidates = [path for path in _directories(asset_root) if path.name.lower() != "code"]
    preferred = [path for path in candidates if path.name.lower() in {"difference", "design", "design-docs", "docs", "spec", "specs"}]
    return preferred[0] if len(preferred) == 1 else (candidates[0] if len(candidates) == 1 else asset_root)


def resolve_paths(args: argparse.Namespace) -> None:
    asset_root = Path(args.asset_root).resolve()
    args.code_root = str(Path(args.code_root).resolve()) if args.code_root else str(discover_code_root(asset_root))
    args.design_root = str(Path(args.design_root).resolve()) if args.design_root else str(discover_design_root(asset_root))


def script_command(script: str, args: argparse.Namespace) -> list[str]:
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
    return command


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
    resolve_paths(args)

    for script in COMMANDS[args.command]:
        command = script_command(script, args)
        print(f"[goal-runner] {script}")
        result = subprocess.run(command)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
