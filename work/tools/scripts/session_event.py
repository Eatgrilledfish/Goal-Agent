#!/usr/bin/env python3
"""Append an agent progress event and update resumable session state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import agent_common as ac


def parse_metric(values: list[str]) -> dict[str, int | float | str]:
    metrics: dict[str, int | float | str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"metric must use key=value: {value}")
        key, raw = value.split("=", 1)
        try:
            parsed: int | float | str = int(raw)
        except ValueError:
            try:
                parsed = float(raw)
            except ValueError:
                parsed = raw
        metrics[key] = parsed
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checkpoint a Goal-Agent session.")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--status", choices=["ready", "in_progress", "complete", "warning", "failed"], required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--completed-phase", action="append", default=[])
    parser.add_argument("--next", action="append", default=[])
    parser.add_argument("--metric", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args(argv)

    root = Path(args.state_root).resolve()
    state_path = root / "agent_loop_state.json"
    state = ac.load_json(state_path)
    metrics = parse_metric(args.metric)
    state["updated_at"] = ac.now_iso()
    state["status"] = args.status
    state["current_phase"] = args.phase
    completed = state.setdefault("completed_phases", [])
    for phase in args.completed_phase:
        if phase not in completed:
            completed.append(phase)
    state.setdefault("metrics", {}).update(metrics)
    if args.next:
        state["next_actions"] = args.next
    ac.save_json(state_path, state)
    ac.append_jsonl(root / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(),
        "session_id": state.get("session_id", ""),
        "event": "agent_checkpoint",
        "actor": args.actor,
        "phase": args.phase,
        "status": args.status,
        "summary": args.summary,
        "metrics": metrics,
        "artifacts": args.artifact,
        "next_actions": args.next,
    })
    print(json.dumps({"updated": str(state_path), "phase": args.phase, "status": args.status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
