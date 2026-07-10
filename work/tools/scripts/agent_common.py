#!/usr/bin/env python3
"""Shared, dependency-free utilities for the agent-driven review harness."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_ASSET_ROOT = "/app/code/judge-assets/01_03_ai_implementation_design_difference_detection"
DEFAULT_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".tox", ".venv", "venv",
    "__pycache__",
}
DESIGN_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst", ".adoc", ".asc", ".pdf", ".docx",
    ".yaml", ".yml", ".json", ".toml",
}
REVIEW_GIT_BARRIER_CONTENT = "gitdir: .goal-agent-no-vcs\n"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    values: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return values, [f"missing artifact: {path}"]
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_number}: expected a JSON object")
            continue
        values.append(value)
    return values, errors


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def stable_id(*parts: str, length: int = 16) -> str:
    raw = "\0".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:length]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def slugify(value: str, max_length: int = 72) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return (slug or "issue")[:max_length]


def contained_path(root: Path, value: str) -> Path | None:
    """Resolve an absolute or root-relative path and enforce root containment."""
    root = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def lexical_path_errors(root: Path, candidate: Path, label: str) -> list[str]:
    """Require lexical containment and reject every symlink in the relative path."""
    root = root.resolve()
    candidate = Path(os.path.abspath(str(candidate)))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return [f"{label} is outside session state: {candidate}"]
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return [f"{label} path contains a symlink: {cursor}"]
    return []


def review_git_barrier_errors(review_code_root: Path) -> list[str]:
    barrier = review_code_root / ".git"
    if barrier.is_symlink() or not barrier.is_file():
        return ["review code Git isolation barrier is missing or not a regular file"]
    if read_text(barrier) != REVIEW_GIT_BARRIER_CONTENT:
        return ["review code Git isolation barrier content changed"]
    return []


def validate_source_evidence(item: Any, root: Path, label: str, text_field: str) -> list[str]:
    """Verify that a cited excerpt exists in the declared source line range."""
    if not isinstance(item, dict):
        return [f"{label}: evidence item must be an object"]
    path_value = str(item.get("path") or item.get("file") or "")
    if not path_value:
        return [f"{label}: missing path"]
    path = contained_path(root, path_value)
    if path is None:
        return [f"{label}: path is outside root: {path_value}"]
    if not path.is_file():
        return [f"{label}: cited file does not exist: {path_value}"]
    try:
        start = int(item.get("line_start") or 0)
        end = int(item.get("line_end") or start)
    except (TypeError, ValueError):
        return [f"{label}: line range must be integers"]
    if start < 1 or end < start:
        return [f"{label}: invalid line range {start}-{end}"]
    lines = read_text(path).splitlines()
    if end > len(lines):
        return [f"{label}: line range {start}-{end} exceeds {len(lines)} lines"]
    cited = str(item.get(text_field) or "")
    if not cited:
        return [f"{label}: missing {text_field}"]
    source = "\n".join(lines[start - 1:end])
    if normalize_text(cited) not in normalize_text(source):
        return [f"{label}: {text_field} does not match cited source lines"]
    return []


def relative_path(root: Path, path: Path) -> str:
    return str(path.absolute().relative_to(root.resolve()))


def iter_files(root: Path, ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS) -> Iterable[Path]:
    ignored = set(ignored_dirs)
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        base = Path(current)
        visible_dirs = sorted(d for d in dirs if d not in ignored and not d.startswith(".cache"))
        directory_links = [base / name for name in visible_dirs if (base / name).is_symlink()]
        dirs[:] = visible_dirs
        for path in directory_links:
            yield path
        for name in sorted(files):
            if name in {".git", ".hg", ".svn"}:
                continue
            yield base / name


def state_root(log_root: Path, explicit: str | None = None) -> Path:
    return Path(explicit).resolve() if explicit else (log_root.resolve() / "state")


def session_path_errors(
    root: Path,
    *,
    code_root: Path,
    design_root: Path,
    result_root: Path,
    log_root: Path,
) -> list[str]:
    manifest_path = root / "workspace_manifest.json"
    if not manifest_path.is_file():
        return [f"missing session manifest: {manifest_path}"]
    manifest = load_json(manifest_path)
    paths = manifest.get("paths", {}) if isinstance(manifest, dict) else {}
    expected = {
        "code_root": str(code_root.resolve()),
        "design_root": str(design_root.resolve()),
        "result_root": str(result_root.resolve()),
        "log_root": str(log_root.resolve()),
        "state_root": str(root.resolve()),
    }
    return [f"{name} does not match prepared session" for name, value in expected.items() if paths.get(name) != value]


def add_common_arguments(parser: Any, include_state: bool = True) -> None:
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--design-entry", action="append", default=[])
    parser.add_argument("--source-manifest", default=None)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    if include_state:
        parser.add_argument("--state-root", default=None)
