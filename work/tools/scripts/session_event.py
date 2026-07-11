#!/usr/bin/env python3
"""Append an agent progress event and update resumable session state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path

import agent_common as ac


TRACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


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


def nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to zero")
    return parsed


def positive_int(value: str) -> int:
    parsed = nonnegative_int(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def provider_attempt(value: str) -> int:
    parsed = positive_int(value)
    if parsed > 2:
        raise argparse.ArgumentTypeError("must not exceed two provider attempts")
    return parsed


def repair_count(value: str) -> int:
    parsed = nonnegative_int(value)
    if parsed > 1:
        raise argparse.ArgumentTypeError("must not exceed one semantic repair")
    return parsed


def nonempty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _symlink_component(path: Path) -> Path | None:
    """Return the first symlink in an absolute lexical path, if any."""
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            return cursor
    return None


def materialize_input_artifacts(values: list[str]) -> tuple[list[dict[str, object]], str]:
    """Hash real regular files and derive an order-independent snapshot digest."""
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        path = Path(os.path.abspath(os.path.expanduser(value)))
        symlink = _symlink_component(path)
        if symlink is not None:
            raise ValueError(f"input-artifact path contains a symlink: {symlink}")
        try:
            file_stat = os.stat(path, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ValueError(f"input-artifact does not exist: {path}") from exc
        except OSError as exc:
            raise ValueError(f"cannot inspect input-artifact {path}: {exc}") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"input-artifact must be a regular file: {path}")
        canonical_path = str(path)
        if canonical_path in seen:
            raise ValueError(f"input-artifact is repeated: {path}")
        seen.add(canonical_path)
        try:
            file_sha256 = ac.sha256_file(path)
        except OSError as exc:
            raise ValueError(f"cannot read input-artifact {path}: {exc}") from exc
        records.append({
            "path": canonical_path,
            "sha256": file_sha256,
            "size_bytes": file_stat.st_size,
        })

    records.sort(key=lambda item: str(item["path"]))
    return records, input_artifacts_sha256(records)


def input_artifacts_sha256(records: list[dict[str, object]]) -> str:
    digest_payload = [
        {"path": item["path"], "sha256": item["sha256"]}
        for item in records
    ]
    canonical = json.dumps(
        digest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def checkpoint_event_errors(
    event: dict[str, object], *, session_id: str, role: str, phase: str,
) -> list[str]:
    label = f"trace checkpoint {phase}/{role}"
    errors: list[str] = []
    if event.get("session_id") != session_id:
        errors.append(f"{label}: session_id does not match")
    if event.get("role") != role or event.get("phase") != phase:
        errors.append(f"{label}: role/phase does not match")
    if event.get("status") != "complete":
        errors.append(f"{label}: status must be complete")
    for field in (
        "event", "actor", "summary", "scope", "provider_session_id", "outcome",
        "stop_reason",
    ):
        if not isinstance(event.get(field), str) or not str(event.get(field)).strip():
            errors.append(f"{label}: {field} must be non-empty")
    records = event.get("input_artifacts")
    if not isinstance(records, list) or not records:
        errors.append(f"{label}: input_artifacts must be a non-empty array")
        records = []
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"{label}: input_artifacts[{index}] must be an object")
            continue
        path = record.get("path")
        sha256 = record.get("sha256")
        size = record.get("size_bytes")
        if not isinstance(path, str) or not Path(path).is_absolute() or path in seen:
            errors.append(f"{label}: input_artifacts[{index}].path must be unique and absolute")
            continue
        seen.add(path)
        if (
            not isinstance(sha256, str) or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            errors.append(f"{label}: input_artifacts[{index}].sha256 is invalid")
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append(f"{label}: input_artifacts[{index}].size_bytes is invalid")
        normalized.append({"path": path, "sha256": sha256, "size_bytes": size})
    normalized.sort(key=lambda item: str(item["path"]))
    if normalized and event.get("input_sha256") != input_artifacts_sha256(normalized):
        errors.append(f"{label}: input_sha256 does not match input_artifacts")
    try:
        started = parse_timestamp(str(event.get("started_at") or ""), "started_at")
        ended = parse_timestamp(str(event.get("ended_at") or ""), "ended_at")
        expected_wall = (ended - started).total_seconds()
        if ended < started or event.get("wall_time_seconds") != expected_wall:
            errors.append(f"{label}: timing/wall_time_seconds is inconsistent")
    except ValueError as exc:
        errors.append(f"{label}: {exc}")
    for field, minimum in (("provider_attempt", 1), ("output_count", 0), ("repair_count", 0)):
        value = event.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            errors.append(f"{label}: {field} must be an integer >= {minimum}")
    if isinstance(event.get("provider_attempt"), int) and event["provider_attempt"] > 2:
        errors.append(f"{label}: provider_attempt exceeds the two-attempt policy")
    if isinstance(event.get("repair_count"), int) and event["repair_count"] > 1:
        errors.append(f"{label}: repair_count exceeds the one-repair policy")
    categories = event.get("validation_error_categories")
    if categories is not None:
        if not isinstance(categories, dict) or not categories or any(
            not isinstance(code, str) or not ERROR_CODE_RE.fullmatch(code)
            or not isinstance(count, int) or isinstance(count, bool) or count <= 0
            for code, count in categories.items()
        ):
            errors.append(f"{label}: validation_error_categories is invalid")
        elif event.get("validation_error_count") != sum(categories.values()):
            errors.append(f"{label}: validation_error_count does not match categories")
    return errors


def parse_error_categories(values: list[str]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"error category must use CODE=count: {value}")
        code, raw_count = value.split("=", 1)
        if not ERROR_CODE_RE.fullmatch(code):
            raise ValueError(
                f"error category code must match {ERROR_CODE_RE.pattern}: {code}"
            )
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise ValueError(f"error category count must be an integer: {value}") from exc
        if count <= 0:
            raise ValueError(f"error category count must be greater than zero: {value}")
        categories[code] = categories.get(code, 0) + count
    return categories


def parse_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = ac.parse_iso(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp: {value}") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone: {value}")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checkpoint a Goal-Agent session.")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--actor", type=nonempty_text, required=True)
    parser.add_argument("--role", type=nonempty_text, required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--status", choices=["ready", "in_progress", "complete", "warning", "failed"], required=True)
    parser.add_argument("--summary", type=nonempty_text, required=True)
    parser.add_argument("--event", type=nonempty_text, required=True)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--scope", type=nonempty_text, required=True)
    parser.add_argument("--input-artifact", action="append", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--ended-at", required=True)
    parser.add_argument("--provider-attempt", type=provider_attempt, required=True)
    parser.add_argument("--provider-session-id", type=nonempty_text, required=True)
    parser.add_argument("--output-count", type=nonnegative_int, required=True)
    parser.add_argument("--error-category", action="append", default=[])
    parser.add_argument("--repair-count", type=repair_count, required=True)
    parser.add_argument("--outcome", type=nonempty_text, required=True)
    parser.add_argument("--stop-reason", type=nonempty_text, required=True)
    parser.add_argument("--completed-phase", action="append", default=[])
    parser.add_argument("--next", action="append", default=[])
    parser.add_argument("--metric", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args(argv)

    for label, value in (
        ("event", args.event),
        ("role", args.role),
        ("task-id", args.task_id),
    ):
        if value is not None and not TRACE_ID_RE.fullmatch(value):
            parser.error(f"{label} must match {TRACE_ID_RE.pattern}: {value}")
    try:
        started_at = parse_timestamp(args.started_at, "started-at")
        ended_at = parse_timestamp(args.ended_at, "ended-at")
    except ValueError as exc:
        parser.error(str(exc))
    if ended_at < started_at:
        parser.error("ended-at must not be earlier than started-at")
    wall_time_seconds = (ended_at - started_at).total_seconds()
    try:
        input_artifacts, input_sha256 = materialize_input_artifacts(args.input_artifact)
        error_categories = parse_error_categories(args.error_category)
        metrics = parse_metric(args.metric)
    except ValueError as exc:
        parser.error(str(exc))
    if args.status == "complete" and args.output_count == 0:
        parser.error("complete checkpoint output-count must be greater than zero")

    root = Path(args.state_root).resolve()
    state_path = root / "agent_loop_state.json"
    state = ac.load_json(state_path)
    recorded_at = ac.now_iso()
    state["updated_at"] = recorded_at
    state["status"] = args.status
    state["current_phase"] = args.phase
    completed = state.setdefault("completed_phases", [])
    for phase in args.completed_phase:
        if phase not in completed:
            completed.append(phase)
    state.setdefault("metrics", {}).update(metrics)
    if args.next:
        state["next_actions"] = args.next
    state["stop_reason"] = args.stop_reason
    ac.save_json(state_path, state)
    event = {
        "recorded_at": recorded_at,
        "session_id": state.get("session_id", ""),
        "event": args.event,
        "actor": args.actor,
        "role": args.role,
        "phase": args.phase,
        "status": args.status,
        "outcome": args.outcome,
        "summary": args.summary,
        "metrics": metrics,
        "artifacts": args.artifact,
        "next_actions": args.next,
    }
    event.update({
        "scope": args.scope,
        "input_artifacts": input_artifacts,
        "input_sha256": input_sha256,
        "started_at": args.started_at,
        "ended_at": args.ended_at,
        "wall_time_seconds": wall_time_seconds,
        "provider_attempt": args.provider_attempt,
        "provider_session_id": args.provider_session_id,
        "output_count": args.output_count,
        "repair_count": args.repair_count,
        "stop_reason": args.stop_reason,
    })
    if args.task_id is not None:
        event["task_id"] = args.task_id
    if error_categories:
        event["validation_error_categories"] = error_categories
        event["validation_error_count"] = sum(error_categories.values())
    ac.append_jsonl(root / "agent_run_ledger.jsonl", event)
    print(json.dumps({"updated": str(state_path), "phase": args.phase, "status": args.status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
