#!/usr/bin/env python3
"""Emit focused patch prompts from repair_tasks.jsonl."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit patch prompts for repair tasks.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--task-id", default=None, help="Only emit one task.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum prompts to emit.")
    return parser


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:120] or "task"


def render_prompt(task: dict[str, Any]) -> str:
    lines = [
        f"# Patch Prompt: {task.get('task_id')}",
        "",
        "## Task",
        "",
        task.get("summary", ""),
        "",
        "## Category",
        "",
        f"- Severity: {task.get('severity')}",
        f"- Category: {task.get('category')}",
        "",
        "## Suspected Files",
        "",
    ]
    files = task.get("suspected_files", []) or []
    lines.extend(f"- `{file_path}`" for file_path in files) if files else lines.append("- Not localized yet. Read code map and related tests before editing.")
    lines.extend(["", "## Related Tests", ""])
    tests = task.get("related_tests", []) or []
    lines.extend(f"- `{test}`" for test in tests) if tests else lines.append("- No focused test identified yet.")
    lines.extend(["", "## Acceptance Criteria", ""])
    for criterion in task.get("acceptance_criteria", []):
        lines.append(f"- {criterion}")
    lines.extend(["", "## Negative Constraints", ""])
    for constraint in task.get("negative_constraints", []):
        lines.append(f"- {constraint}")
    lines.extend(["", "## Repair Hint", "", task.get("repair_hint", ""), ""])
    lines.extend([
        "## Candidate Requirements",
        "",
        "- Produce a minimal git patch against `code/**` only unless configuration under `code/**` is required.",
        "- Preserve frozen `/api/v1/` paths, methods, field names, status codes, and documented error semantics.",
        "- Do not hardcode public test fixture data; repair the general business rule.",
        "- After patching, run compile, code tests, public matrix, contract checker, forbidden guard, hardcoding guard, and unmasking gate.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def emit(root: Path, task_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    prompt_dir = paths.work / "patch_prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    tasks = runner.read_jsonl(paths.work / "repair_tasks.jsonl") if (paths.work / "repair_tasks.jsonl").exists() else []
    if task_id:
        tasks = [task for task in tasks if task.get("task_id") == task_id]
    tasks = tasks[:limit]
    prompts: list[dict[str, str]] = []
    for task in tasks:
        path = prompt_dir / f"{safe_name(task.get('task_id', 'TASK'))}.md"
        runner.write_text(path, render_prompt(task))
        prompts.append({"task_id": task.get("task_id", ""), "path": runner.rel(root, path)})
    manifest = {"generated_at": runner.now_iso(), "prompt_count": len(prompts), "prompts": prompts}
    runner.write_json(paths.work / "patch_prompts_manifest.json", manifest)
    return manifest


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    manifest = emit(root, args.task_id, args.limit)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
