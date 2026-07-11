#!/usr/bin/env python3
"""Start the immutable six-hour wall clock before input materialization."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import agent_common as ac


MAX_SECONDS = 21600


def validate_clock(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["run clock must be an object"]
    errors: list[str] = []
    started_at = value.get("started_at")
    deadline_at = value.get("deadline_at")
    if not isinstance(started_at, str) or not started_at:
        errors.append("run clock started_at is missing")
    if not isinstance(deadline_at, str) or not deadline_at:
        errors.append("run clock deadline_at is missing")
    if errors:
        return errors
    try:
        started = ac.parse_iso(started_at)
        deadline = ac.parse_iso(deadline_at)
    except (TypeError, ValueError) as exc:
        return [f"run clock timestamps are invalid: {exc}"]
    if started.utcoffset() is None or deadline.utcoffset() is None:
        errors.append("run clock timestamps must include a timezone")
    if int((deadline - started).total_seconds()) != MAX_SECONDS:
        errors.append("run clock deadline must be exactly six hours after start")
    if value.get("maximum_seconds") != MAX_SECONDS:
        errors.append("run clock maximum_seconds is invalid")
    return errors


def run(args: argparse.Namespace) -> int:
    log_root = Path(args.log_root).resolve()
    state_root = ac.state_root(log_root, args.state_root)
    ac.ensure_dir(state_root)
    trace_root = ac.ensure_dir(log_root / "trace")
    clock_path = state_root / "run_clock.json"
    trace_path = trace_root / "run_clock.json"
    state_path = state_root / "agent_loop_state.json"

    if clock_path.exists():
        if clock_path.is_symlink() or not clock_path.is_file():
            errors = ["run clock path is not a regular file"]
            print(json.dumps({"started": False, "errors": errors}))
            return 2
        try:
            clock = ac.load_json(clock_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors = [f"run clock is invalid: {exc}"]
            print(json.dumps({"started": False, "errors": errors}))
            return 2
        errors = validate_clock(clock)
        if errors:
            print(json.dumps({"started": False, "errors": errors}))
            return 2
        if trace_path.is_symlink() or not trace_path.is_file():
            errors = ["run clock trace baseline is missing; refusing to recreate it"]
            print(json.dumps({"started": False, "errors": errors}))
            return 2
        try:
            trace_clock = ac.load_json(trace_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors = [f"run clock trace baseline is invalid: {exc}"]
            print(json.dumps({"started": False, "errors": errors}))
            return 2
        if trace_clock != clock:
            errors = ["run clock differs from its original trace baseline"]
            print(json.dumps({"started": False, "errors": errors}))
            return 2
        created = False
    else:
        state_artifacts = [
            path for path in state_root.iterdir()
            if path.name != "run_clock.json"
        ]
        trace_artifacts = [
            path for path in trace_root.iterdir()
            if path.name not in {"run_clock.json", "README.md"}
        ]
        if state_path.exists() or state_artifacts or trace_artifacts:
            errors = [
                "session/input artifacts exist without run_clock.json; refusing to reset elapsed time"
            ]
            print(json.dumps({"started": False, "errors": errors}))
            return 2
        started_at = ac.now_iso()
        deadline = ac.parse_iso(started_at) + timedelta(seconds=MAX_SECONDS)
        clock = {
            "clock_version": 1,
            "started_at": started_at,
            "deadline_at": deadline.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "maximum_seconds": MAX_SECONDS,
        }
        ac.save_json(clock_path, clock)
        ac.save_json(trace_path, clock)
        created = True

    print(json.dumps({"started": True, "created": created, **clock}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the Goal-Agent wall clock once.")
    parser.add_argument("--log-root", required=True)
    parser.add_argument("--state-root", default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
