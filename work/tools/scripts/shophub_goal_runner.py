#!/usr/bin/env python3
"""Local Goal Runner for design-implementation consistency repair.

The runner implements the deterministic parts of the workflow described in
design-document.md: workspace initialization, spec/API/code indexing, Maven
test execution, issue queue maintenance, round recording, continuous orchestration,
and final reporting.

It does not hard-code business fixes. In auto-run mode, repairs are delegated to
an external patch command such as Codex CLI, opencode, or another local agent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")
REQUIRED_COMPETITION_PATHS = [
    "README.md",
    "code",
    "design-docs",
    "test-cases",
]
AGENT_NAMES = [
    "spec-librarian",
    "api-guardian",
    "code-mapper",
    "test-diagnoser",
    "module-auditor",
    "patch-agent",
    "review-agent",
    "report-writer",
]
PHASES = [
    "INIT",
    "READ_SPECS",
    "READ_API_BASELINE",
    "MAP_CODE",
    "RUN_BASELINE_TESTS",
    "AUDIT_INCONSISTENCIES",
    "PRIORITIZE_ISSUES",
    "FIX_LOOP",
    "RUN_FULL_TESTS",
    "WRITE_REPORT",
    "DONE",
]
SEVERITY_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}
COMPLEXITY_WEIGHT = {"small": 1.0, "medium": 2.0, "large": 4.0}
# Submission-internal config location (work/config/), independent of PROJECT_ROOT.
WORK_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIT_PRIORITIES_PATH = WORK_ROOT / "config" / "audit_priorities.json"
# Generic engineering priority (non-business): issue types more likely to affect hidden tests.
DEFAULT_PRIORITY_TYPES = ("api_drift", "business_rule_mismatch", "state_machine_error", "money_calc_error")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(read_text(path))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Write all records, overwriting the file (full-rewrite semantics)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    """Append a single record (true append) — for fan-out writers that must not overwrite each other."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(read_text(path).splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc
    return records


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def stable_hash(text: str, length: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def slug(value: str, fallback: str = "GEN") -> str:
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    if ascii_part:
        return ascii_part[:24]
    return f"{fallback}-{stable_hash(value, 6).upper()}"


@dataclass
class RunnerPaths:
    root: Path

    @property
    def work(self) -> Path:
        return self.root / ".agent-work"

    @property
    def rounds(self) -> Path:
        return self.work / "rounds"

    @property
    def test_results(self) -> Path:
        return self.work / "test-results"

    @property
    def reports(self) -> Path:
        return self.work / "reports"

    @property
    def state(self) -> Path:
        return self.work / "state.json"

    @property
    def issues(self) -> Path:
        return self.work / "issues.jsonl"

    @property
    def specs(self) -> Path:
        return self.work / "spec_rules.jsonl"


def find_maven_settings(root: Path) -> Path | None:
    env_value = os.environ.get("SHOPHUB_MAVEN_SETTINGS") or os.environ.get("MAVEN_SETTINGS")
    if env_value:
        env_path = Path(env_value).expanduser()
        if not env_path.is_absolute():
            env_path = root / env_path
        if env_path.exists():
            return env_path

    for candidate in (root / "maven-settings.xml", root / "settings.xml"):
        if candidate.exists():
            return candidate
    return None


def maven_command(root: Path, args: list[str]) -> list[str]:
    command = ["mvn"]
    settings_path = find_maven_settings(root)
    if settings_path is not None:
        command.extend(["-s", rel(root, settings_path)])
    command.extend(args)
    return command


def command_text(command: list[str]) -> str:
    return shlex.join(command)


def default_state() -> dict[str, Any]:
    return {
        "phase": "INIT",
        "round": 0,
        "max_rounds": 20,
        "fixed_count": 0,
        "reverted_count": 0,
        "high_priority_open": 0,
        "medium_priority_open": 0,
        "low_priority_open": 0,
        "consecutive_no_progress": 0,
        "consecutive_regressions": 0,
        "api_contract_safe": True,
        "last_focused_test": "not_run",
        "last_full_test": "not_run",
        "last_score": None,
        "should_continue": True,
        "stop_reason": None,
        "auto_run_started_at": None,
        "auto_run_finished_at": None,
        "last_patch_command": None,
        "last_patch_exit_code": None,
        "missing_required_paths": [],
        "updated_at": now_iso(),
    }


def load_state(paths: RunnerPaths) -> dict[str, Any]:
    state = default_state()
    existing = read_json(paths.state, {})
    state.update(existing)
    return state


def save_state(paths: RunnerPaths, **updates: Any) -> dict[str, Any]:
    state = load_state(paths)
    state.update(updates)
    state["updated_at"] = now_iso()
    write_json(paths.state, state)
    return state


def ensure_work_layout(paths: RunnerPaths) -> None:
    for path in [
        paths.work,
        paths.rounds,
        paths.test_results,
        paths.reports,
        paths.work / "snapshots",
    ]:
        path.mkdir(parents=True, exist_ok=True)


def check_competition_layout(root: Path) -> list[str]:
    return [item for item in REQUIRED_COMPETITION_PATHS if not (root / item).exists()]


def git_status(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git status unavailable: {exc}"


def init_workspace(root: Path) -> dict[str, Any]:
    paths = RunnerPaths(root)
    ensure_work_layout(paths)
    missing = check_competition_layout(root)
    state = load_state(paths)
    if not paths.state.exists():
        state = default_state()
    state.update(
        {
            "phase": "INIT",
            "missing_required_paths": missing,
            "git_status_at_init": git_status(root),
            "should_continue": True,
            "stop_reason": "missing_competition_inputs" if missing else None,
            "updated_at": now_iso(),
        }
    )
    write_json(paths.state, state)
    write_goal(paths, missing)
    ensure_empty_runtime_files(paths)
    return state


def ensure_empty_runtime_files(paths: RunnerPaths) -> None:
    defaults = {
        paths.work / "spec_rules.jsonl": "",
        paths.work / "api_contract.json": json.dumps(
            {"generated_at": now_iso(), "source": None, "endpoints": [], "error_codes": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        paths.work / "api_snapshot_baseline.json": json.dumps(
            {"generated_at": now_iso(), "source": None, "endpoints": [], "error_codes": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        paths.work / "api_snapshot_current.json": json.dumps(
            {"generated_at": now_iso(), "source": "code", "endpoints": [], "dto_fields": {}, "error_codes": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        paths.work / "code_map.md": "# Code Map\n\n`code/` not scanned yet.\n",
        paths.work / "issues.jsonl": "",
        paths.work / "fix_plan.md": "# Fix Plan\n\nNo issues prioritized yet.\n",
        paths.work / "baseline_tests.md": "# Baseline Test Summary\n\nTests not run yet.\n",
        paths.work / "test_symptoms.jsonl": "",
    }
    for path, content in defaults.items():
        if not path.exists():
            write_text(path, content)


def write_goal(paths: RunnerPaths, missing: list[str]) -> None:
    missing_section = "\n".join(f"- `{item}`" for item in missing) if missing else "- None"
    text = f"""# ShopHub Goal Runner State

Generated: {now_iso()}

## Objective

Compare design documents, frozen REST API contract, and Spring Boot implementation. Find and repair design-code inconsistencies in small, evidence-backed rounds while preserving the API contract.

## Required Inputs Missing

{missing_section}

## Safety Rules

- Do not modify `design-docs/**`.
- Do not modify `README.md` competition instructions or API baseline sections.
- Avoid modifying `test-cases/**`.
- Do not change REST API URLs, methods, headers, request fields, response fields, or error-code semantics.

## Verification Commands

If `maven-settings.xml` exists in the project root, use it for every Maven command:

```bash
mvn -s maven-settings.xml -f code/pom.xml test
mvn -s maven-settings.xml -f code/pom.xml install -DskipTests
mvn -s maven-settings.xml -f test-cases/pom.xml test
```
"""
    write_text(paths.work / "goal.md", text)


def extract_specs(root: Path) -> list[dict[str, Any]]:
    """Split design documents into paragraph-level spec records.

    The runner does NOT interpret rules — it only segments design docs into
    records tagged with source_doc/section/source_line. The spec-librarian
    subagent fills expected_behavior/rule_kind/severity_hint, and module is
    assigned via module_mapping.json (no hard-coded design-to-module map).
    """
    design_dir = root / "design-docs"
    paths = RunnerPaths(root)
    records: list[dict[str, Any]] = []
    index_lines = ["# Spec Rule Index", "", f"Generated: {now_iso()}", ""]
    if not design_dir.exists():
        append_jsonl(paths.specs, records)
        index_lines.append("`design-docs/` does not exist. No spec rules extracted.")
        write_text(paths.work / "01_spec_index.md", "\n".join(index_lines) + "\n")
        save_state(paths, phase="READ_SPECS", stop_reason="missing_design_docs")
        return records

    for doc_path in sorted(design_dir.rglob("*.md")):
        section = "概要"
        doc_records: list[dict[str, Any]] = []
        paragraph: list[str] = []
        paragraph_start_line = 0

        def flush_paragraph() -> None:
            nonlocal paragraph, paragraph_start_line
            if not paragraph:
                return
            text = clean_markdown_text(" ".join(paragraph))
            start = paragraph_start_line
            paragraph = []
            paragraph_start_line = 0
            if len(text) < 8:
                return
            count = len(records) + 1
            spec_id = f"SPEC-{count:04d}"
            record = {
                "spec_id": spec_id,
                "module": "",
                "source_doc": rel(root, doc_path),
                "section": section,
                "source_line": start,
                "design_rule": text,
                "expected_behavior": "",
                "rule_kind": "",
                "severity_hint": "",
            }
            records.append(record)
            doc_records.append(record)

        for line_no, raw_line in enumerate(read_text(doc_path).splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                flush_paragraph()
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                flush_paragraph()
                section = clean_markdown_text(heading.group(2))
                continue
            if not paragraph:
                paragraph_start_line = line_no
            paragraph.append(line)
        flush_paragraph()

        index_lines.append(f"## {rel(root, doc_path)}")
        if doc_records:
            for record in doc_records:
                index_lines.append(f"- `{record['spec_id']}` {record['section']} (line {record['source_line']}): {record['design_rule']}")
        else:
            index_lines.append("- No paragraph-level records extracted.")
        index_lines.append("")

    append_jsonl(paths.specs, records)
    write_text(paths.work / "01_spec_index.md", "\n".join(index_lines).rstrip() + "\n")
    save_state(paths, phase="READ_SPECS", stop_reason=None if records else "no_spec_rules_extracted")
    return records


def clean_markdown_text(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -*\t\r\n")


def parse_api_baseline(root: Path) -> dict[str, Any]:
    paths = RunnerPaths(root)
    baseline_sources = find_api_baseline_sources(root)
    contract: dict[str, Any] = {
        "generated_at": now_iso(),
        "source": [rel(root, path) for path in baseline_sources],
        "endpoints": [],
        "error_codes": [],
        "dto_fields": {},
        "frozen": True,
        "warnings": [],
    }
    index_lines = ["# API Contract Index", "", f"Generated: {now_iso()}", ""]
    if not baseline_sources:
        contract["source"] = []
        contract["warnings"].append("No API baseline source found. Expected README.md and/or design-docs/*.md.")
        write_json(paths.work / "api_contract.json", contract)
        write_json(paths.work / "api_snapshot_baseline.json", contract)
        write_text(paths.work / "02_api_contract_index.md", "\n".join(index_lines + ["API baseline source is missing."]) + "\n")
        snapshot_current = snapshot_code_api(root)
        write_json(paths.work / "api_snapshot_current.json", snapshot_current)
        compare = compare_api_snapshots(contract, snapshot_current)
        write_json(paths.work / "api_compare.json", compare)
        save_state(paths, phase="READ_API_BASELINE", api_contract_safe=compare["safe"], stop_reason="missing_api_baseline_source")
        return contract

    text = "\n\n".join(read_text(path) for path in baseline_sources)
    endpoints = extract_endpoints_from_markdown(text)
    error_codes = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){1,}\b", text)))
    contract["endpoints"] = endpoints
    contract["error_codes"] = error_codes
    if not endpoints:
        contract["warnings"].append("No REST endpoints found by heuristic extraction.")
    write_json(paths.work / "api_contract.json", contract)
    write_json(paths.work / "api_snapshot_baseline.json", contract)

    if endpoints:
        for endpoint in endpoints:
            index_lines.append(f"- `{endpoint['method']} {endpoint['url']}` id=`{endpoint['endpoint_id']}`")
    else:
        index_lines.append("No endpoints found by heuristic extraction.")
    if error_codes:
        index_lines.extend(["", "## Error Codes", ""])
        for code in error_codes:
            index_lines.append(f"- `{code}`")
    write_text(paths.work / "02_api_contract_index.md", "\n".join(index_lines).rstrip() + "\n")

    snapshot_current = snapshot_code_api(root)
    write_json(paths.work / "api_snapshot_current.json", snapshot_current)
    compare = compare_api_snapshots(contract, snapshot_current)
    write_json(paths.work / "api_compare.json", compare)
    save_state(paths, phase="READ_API_BASELINE", api_contract_safe=compare["safe"], stop_reason=None if compare["safe"] else "api_contract_violation")
    return contract


def find_api_baseline_sources(root: Path) -> list[Path]:
    """Return README plus every design-docs markdown.

    The API baseline is not tied to a hard-coded appendix filename — which
    would not generalize to other competition domains. The api-guardian
    subagent semantically identifies which documents carry the frozen REST
    contract and fills endpoint DTO fields into ``api_snapshot_baseline.json``.
    """
    sources: list[Path] = []
    readme = root / "README.md"
    if readme.exists():
        sources.append(readme)
    design_dir = root / "design-docs"
    if design_dir.exists():
        sources.extend(sorted(design_dir.rglob("*.md")))
    return sources


def extract_endpoints_from_markdown(text: str) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    method_url = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\b\s*[:：]?\s*`?(/[A-Za-z0-9_./{}?=&:%-]+)`?", re.I)
    table_method_url = re.compile(
        r"\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`?(/[A-Za-z0-9_./{}?=&:%-]+)`?\s*\|",
        re.I,
    )
    for pattern in (table_method_url, method_url):
        for match in pattern.finditer(text):
            method = match.group(1).upper()
            url = normalize_url(match.group(2))
            key = (method, url)
            if key in seen:
                continue
            seen.add(key)
            endpoints.append(
                {
                    "endpoint_id": f"{method}-{slug(url, 'API')}-{stable_hash(method + url, 6).upper()}",
                    "method": method,
                    "url": url,
                    "headers": {},
                    "request_body": {},
                    "response_body": {},
                    "error_codes": [],
                    "frozen": True,
                }
            )
    return endpoints


def normalize_url(value: str) -> str:
    value = value.strip().strip("`。；;,.，")
    if not value.startswith("/"):
        value = "/" + value
    return re.sub(r"//+", "/", value)


def snapshot_code_api(root: Path) -> dict[str, Any]:
    code_dir = root / "code"
    snapshot: dict[str, Any] = {
        "generated_at": now_iso(),
        "source": "code",
        "endpoints": [],
        "dto_fields": {},
        "error_codes": [],
        "warnings": [],
    }
    if not code_dir.exists():
        snapshot["warnings"].append("code/ does not exist.")
        return snapshot

    java_files = sorted(code_dir.rglob("*.java"))
    dto_fields = collect_dto_fields(root, java_files)
    endpoints: list[dict[str, Any]] = []
    error_codes: set[str] = set()
    for path in java_files:
        text = read_text(path)
        if "@Controller" in text or "@RestController" in text:
            endpoints.extend(extract_controller_endpoints(root, path, text, dto_fields))
        if "enum" in text or "Error" in path.name or "Exception" in path.name:
            error_codes.update(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text))
    snapshot["endpoints"] = sorted(endpoints, key=lambda item: (item["url"], item["method"], item["source"]))
    snapshot["dto_fields"] = dto_fields
    snapshot["error_codes"] = sorted(error_codes)
    return snapshot


def collect_dto_fields(root: Path, java_files: list[Path]) -> dict[str, dict[str, str]]:
    dto_fields: dict[str, dict[str, str]] = {}
    field_pattern = re.compile(
        r"(?:private|protected|public)\s+(?:final\s+)?([A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)",
        re.M,
    )
    record_pattern = re.compile(r"\brecord\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)", re.S)
    class_pattern = re.compile(r"\b(?:class|enum|interface)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    for path in java_files:
        text = strip_java_comments(read_text(path))
        class_match = class_pattern.search(text)
        record_match = record_pattern.search(text)
        if record_match:
            name = record_match.group(1)
            fields: dict[str, str] = {}
            for raw_param in split_java_parameters(record_match.group(2)):
                pieces = raw_param.strip().split()
                if len(pieces) >= 2:
                    fields[pieces[-1].strip()] = " ".join(pieces[:-1]).strip()
            dto_fields[name] = fields
            continue
        if not class_match:
            continue
        class_name = class_match.group(1)
        if not looks_like_dto(path, class_name, text):
            continue
        fields = {match.group(2): normalize_type(match.group(1)) for match in field_pattern.finditer(text)}
        if fields:
            dto_fields[class_name] = fields
    return dto_fields


def looks_like_dto(path: Path, class_name: str, text: str) -> bool:
    lowered = path.as_posix().lower()
    suffixes = ("request", "response", "dto", "vo", "form", "param", "command", "result")
    if any(class_name.lower().endswith(suffix) for suffix in suffixes):
        return True
    return any(part in lowered for part in ("/dto/", "/request/", "/response/", "/vo/"))


def normalize_type(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    return value.replace(" ?", "?").replace(" ,", ",")


def strip_java_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*", "", text)
    return text


def extract_controller_endpoints(root: Path, path: Path, text: str, dto_fields: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    clean = strip_java_comments(text)
    class_base = extract_class_request_mapping(clean)
    package_match = re.search(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", clean, re.M)
    class_match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b", clean)
    class_name = class_match.group(1) if class_match else path.stem
    package_name = package_match.group(1) if package_match else ""

    method_pattern = re.compile(
        r"(?P<annotations>(?:\s*@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*(?:\([^)]*\))?\s*)+)"
        r"(?:public|protected|private)?\s*(?:static\s+)?(?P<return>[A-Za-z0-9_<>, ?.\[\]]+)\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>(?:[^()]|\([^()]*\))*)\)",
        re.S,
    )
    endpoints: list[dict[str, Any]] = []
    for match in method_pattern.finditer(clean):
        annotations = match.group("annotations")
        mapping = extract_method_mapping(annotations)
        if not mapping:
            continue
        method, method_path = mapping
        url = join_paths(class_base, method_path)
        params = match.group("params")
        request_body_type = extract_annotated_param_type(params, "RequestBody")
        response_type = unwrap_response_type(normalize_type(match.group("return")))
        headers = extract_headers(params)
        endpoints.append(
            {
                "endpoint_id": f"{slug(class_name)}-{slug(match.group('name'))}",
                "method": method,
                "url": url,
                "headers": headers,
                "request_body_type": request_body_type,
                "request_body": dto_fields.get(request_body_type, {}),
                "response_body_type": response_type,
                "response_body": dto_fields.get(response_type, {}),
                "source": f"{rel(root, path)}#{match.group('name')}",
                "controller": f"{package_name}.{class_name}" if package_name else class_name,
                "frozen": False,
            }
        )
    return endpoints


def extract_class_request_mapping(text: str) -> str:
    class_pos = text.find(" class ")
    prefix = text[:class_pos] if class_pos != -1 else text[:1000]
    matches = re.findall(r"@RequestMapping\s*(?:\(([^)]*)\))?", prefix, re.S)
    if not matches:
        return ""
    return extract_mapping_path(matches[-1])


def extract_method_mapping(annotations: str) -> tuple[str, str] | None:
    mapping_patterns = [
        ("GET", r"@GetMapping\s*(?:\(([^)]*)\))?"),
        ("POST", r"@PostMapping\s*(?:\(([^)]*)\))?"),
        ("PUT", r"@PutMapping\s*(?:\(([^)]*)\))?"),
        ("DELETE", r"@DeleteMapping\s*(?:\(([^)]*)\))?"),
        ("PATCH", r"@PatchMapping\s*(?:\(([^)]*)\))?"),
    ]
    for method, pattern in mapping_patterns:
        match = re.search(pattern, annotations, re.S)
        if match:
            return method, extract_mapping_path(match.group(1) or "")
    request_mapping = re.search(r"@RequestMapping\s*(?:\(([^)]*)\))?", annotations, re.S)
    if request_mapping:
        content = request_mapping.group(1) or ""
        method_match = re.search(r"RequestMethod\.([A-Z]+)", content)
        method = method_match.group(1) if method_match else "REQUEST"
        return method, extract_mapping_path(content)
    return None


def extract_mapping_path(content: str) -> str:
    if not content:
        return ""
    named = re.search(r"(?:value|path)\s*=\s*(?:\{)?\s*\"([^\"]+)\"", content)
    if named:
        return named.group(1)
    direct = re.search(r"\"([^\"]+)\"", content)
    if direct:
        return direct.group(1)
    return ""


def join_paths(base: str, child: str) -> str:
    parts = [part.strip("/") for part in (base, child) if part and part.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def split_java_parameters(params: str) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    depth = 0
    for char in params:
        if char in "(<[":
            depth += 1
        elif char in ")>]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        result.append("".join(current).strip())
    return result


def extract_annotated_param_type(params: str, annotation: str) -> str | None:
    for param in split_java_parameters(params):
        if f"@{annotation}" not in param:
            continue
        cleaned = re.sub(r"@\w+(?:\([^)]*\))?", "", param).strip()
        pieces = cleaned.split()
        if len(pieces) >= 2:
            return pieces[-2].split(".")[-1]
    return None


def extract_headers(params: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for param in split_java_parameters(params):
        if "@RequestHeader" not in param:
            continue
        name_match = re.search(r"@RequestHeader\s*(?:\(([^)]*)\))?", param)
        header_name = None
        if name_match:
            content = name_match.group(1) or ""
            direct = re.search(r"\"([^\"]+)\"", content)
            header_name = direct.group(1) if direct else None
        cleaned = re.sub(r"@\w+(?:\([^)]*\))?", "", param).strip()
        pieces = cleaned.split()
        if len(pieces) >= 2:
            headers[header_name or pieces[-1]] = pieces[-2].split(".")[-1]
    return headers


def unwrap_response_type(value: str) -> str:
    wrappers = ("ResponseEntity", "Result", "ApiResponse", "CommonResponse", "R")
    for wrapper in wrappers:
        match = re.match(rf"{wrapper}\s*<\s*([A-Za-z0-9_., <>?\[\]]+)\s*>", value)
        if match:
            inner = match.group(1).strip()
            if "," in inner:
                inner = inner.split(",", 1)[0].strip()
            return inner.split(".")[-1]
    return value.split(".")[-1].strip()


def diff_endpoint_fields(baseline_ep: dict[str, Any], current_ep: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare request/response body fields between a baseline and current endpoint.

    Both sides carry ``request_body``/``response_body`` as ``{field: type}`` dicts.
    The baseline DTO fields are filled by the api-guardian subagent; until then
    both dicts may be empty and no drift is reported.
    """
    drifts: list[dict[str, Any]] = []
    for direction in ("request_body", "response_body"):
        baseline_fields = baseline_ep.get(direction) or {}
        current_fields = current_ep.get(direction) or {}
        if not baseline_fields and not current_fields:
            continue
        for field in sorted(set(baseline_fields) - set(current_fields)):
            drifts.append({"direction": direction, "field": field, "baseline_type": baseline_fields.get(field), "current_type": None, "drift": "missing"})
        for field in sorted(set(baseline_fields) & set(current_fields)):
            if baseline_fields.get(field) != current_fields.get(field):
                drifts.append({"direction": direction, "field": field, "baseline_type": baseline_fields.get(field), "current_type": current_fields.get(field), "drift": "type_change"})
        for field in sorted(set(current_fields) - set(baseline_fields)):
            drifts.append({"direction": direction, "field": field, "baseline_type": None, "current_type": current_fields.get(field), "drift": "added"})
    return drifts


def compare_api_snapshots(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    baseline_endpoints = {(item.get("method"), item.get("url")): item for item in baseline.get("endpoints", [])}
    current_endpoints = {(item.get("method"), item.get("url")): item for item in current.get("endpoints", [])}
    missing = sorted([{"method": method, "url": url} for method, url in baseline_endpoints if (method, url) not in current_endpoints], key=lambda x: (x["url"], x["method"]))
    additional = sorted([{"method": method, "url": url} for method, url in current_endpoints if (method, url) not in baseline_endpoints], key=lambda x: (x["url"], x["method"]))
    baseline_error_codes = set(baseline.get("error_codes", []))
    current_error_codes = set(current.get("error_codes", []))
    missing_error_codes = sorted(code for code in baseline_error_codes if code not in current_error_codes)

    field_drifts: list[dict[str, Any]] = []
    for (method, url), baseline_ep in baseline_endpoints.items():
        current_ep = current_endpoints.get((method, url))
        if not current_ep:
            continue
        for drift in diff_endpoint_fields(baseline_ep, current_ep):
            drift.update({"endpoint_id": baseline_ep.get("endpoint_id"), "method": method, "url": url})
            field_drifts.append(drift)
    breaking_field_drifts = [drift for drift in field_drifts if drift["drift"] in ("missing", "type_change")]
    field_drifts = sorted(field_drifts, key=lambda x: (x["url"], x["method"], x["direction"], x["field"]))

    warnings: list[str] = []
    if not baseline_endpoints:
        warnings.append("Baseline endpoint set is empty; api-guardian must extract the frozen API baseline before compatibility can be verified.")
    if not current_endpoints:
        warnings.append("Current code endpoint set is empty; code/ may be missing or parser found no controllers.")
    baseline_has_fields = any(
        (ep.get("request_body") or ep.get("response_body"))
        for ep in baseline_endpoints.values()
    )
    if baseline_endpoints and not baseline_has_fields:
        warnings.append("Baseline endpoints have no request/response body fields; api-guardian must fill DTO fields before field-level drift can be detected.")

    safe = (
        bool(baseline_endpoints)
        and not missing
        and (not baseline_error_codes or not missing_error_codes)
        and not breaking_field_drifts
    )
    return {
        "generated_at": now_iso(),
        "safe": safe,
        "missing_endpoints": missing,
        "additional_current_endpoints": additional,
        "missing_error_codes": missing_error_codes,
        "field_drifts": field_drifts,
        "warnings": warnings,
    }


def write_modules_manifest(paths: RunnerPaths, modules: list[dict[str, Any]]) -> None:
    """Write the scanned code module list — the authoritative fan-out source (no seed)."""
    manifest = {
        "generated_at": now_iso(),
        "modules": [
            {
                "code_module": module.get("artifact_id") or module.get("path"),
                "path": module.get("path"),
                "artifact_id": module.get("artifact_id"),
                "packaging": module.get("packaging"),
            }
            for module in modules
        ],
    }
    write_json(paths.work / "modules.json", manifest)


def write_design_docs_manifest(root: Path, paths: RunnerPaths) -> None:
    """Write the design-docs manifest for the module-mapper subagent."""
    design_dir = root / "design-docs"
    docs: list[dict[str, Any]] = []
    if design_dir.exists():
        for doc_path in sorted(design_dir.rglob("*.md")):
            docs.append({"path": rel(root, doc_path), "stem": doc_path.stem})
    write_json(paths.work / "design_docs.json", {"generated_at": now_iso(), "docs": docs})


def load_scanned_module_names(root: Path) -> list[str]:
    """Return scanned code module names for low-confidence symptom triage."""
    paths = RunnerPaths(root)
    manifest = read_json(paths.work / "modules.json", {})
    names = [module.get("code_module") for module in manifest.get("modules", []) if module.get("code_module")]
    if names:
        return names
    code_dir = root / "code"
    if code_dir.exists():
        for pom in sorted(code_dir.rglob("pom.xml")):
            names.append(pom.parent.name)
    return names


def extract_file_structure(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract top-level class names and public/protected methods with line numbers."""
    clean = strip_java_comments(text)
    class_re = re.compile(r"\b(?:class|enum|interface)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    method_re = re.compile(r"(?:public|protected)\s+(?:static\s+)?(?:[A-Za-z0-9_<>, ?.\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    classes = [match.group(1) for match in class_re.finditer(clean)]
    methods: list[dict[str, Any]] = []
    current_class: str | None = None
    for line_no, line in enumerate(clean.splitlines(), start=1):
        class_match = class_re.search(line)
        if class_match:
            current_class = class_match.group(1)
        method_match = method_re.search(line)
        if method_match and method_match.group(1) not in ("class", "interface", "enum"):
            methods.append({"class": current_class, "method": method_match.group(1), "line": line_no})
    return classes, methods


def map_code(root: Path) -> dict[str, Any]:
    paths = RunnerPaths(root)
    code_dir = root / "code"
    code_map: dict[str, Any] = {
        "generated_at": now_iso(),
        "modules": [],
        "controllers": [],
        "services": [],
        "repositories": [],
        "dtos": [],
        "domain_models": [],
        "exceptions": [],
        "configs": [],
        "tests": [],
        "warnings": [],
    }
    if not code_dir.exists():
        code_map["warnings"].append("code/ does not exist.")
        write_text(paths.work / "code_map.md", render_code_map(root, code_map, []))
        append_jsonl(paths.work / "code_call_chains.jsonl", [])
        save_state(paths, phase="MAP_CODE", stop_reason="missing_code")
        return code_map

    code_map["modules"] = find_maven_modules(root, code_dir)
    write_modules_manifest(paths, code_map["modules"])
    write_design_docs_manifest(root, paths)
    java_files = sorted(code_dir.rglob("*.java"))
    call_chains: list[dict[str, Any]] = []
    snapshot = snapshot_code_api(root)
    endpoints_by_path: dict[str, list[dict[str, Any]]] = {}
    for endpoint in snapshot.get("endpoints", []):
        source = endpoint.get("source", "")
        file_part = source.split("#")[0]
        endpoints_by_path.setdefault(file_part, []).append({"method": endpoint["method"], "url": endpoint["url"], "source": source})
    role_to_bucket = {
        "test": "tests",
        "controller": "controllers",
        "service": "services",
        "repository": "repositories",
        "dto": "dtos",
        "exception": "exceptions",
        "config": "configs",
        "domain": "domain_models",
    }
    file_records: list[dict[str, Any]] = []
    for path in java_files:
        text = read_text(path)
        rel_path = rel(root, path)
        module = infer_module_for_path(root, path)
        classes, methods = extract_file_structure(text)
        if is_test_path(path):
            role = "test"
        elif "@RestController" in text or "@Controller" in text:
            role = "controller"
        elif "@Service" in text or path.name.endswith("Service.java") or path.name.endswith("ServiceImpl.java"):
            role = "service"
        elif "@Repository" in text or path.name.endswith("Repository.java") or path.name.endswith("Mapper.java") or path.name.endswith("Dao.java"):
            role = "repository"
        elif looks_like_dto(path, path.stem, text):
            role = "dto"
        elif "Exception" in path.name:
            role = "exception"
        elif "@Configuration" in text or path.name.endswith("Config.java") or path.name.endswith("Configuration.java"):
            role = "config"
        elif "/domain/" in path.as_posix().lower() or "/model/" in path.as_posix().lower() or "/entity/" in path.as_posix().lower():
            role = "domain"
        else:
            role = "other"
        record = {
            "path": rel_path,
            "module": module,
            "role": role,
            "classes": classes,
            "methods": methods,
            "dto_class": path.stem if role == "dto" else None,
            "api_endpoints": endpoints_by_path.get(rel_path, []) if role == "controller" else [],
        }
        file_records.append(record)
        bucket = role_to_bucket.get(role)
        if bucket:
            code_map[bucket].append({"path": rel_path, "class": path.stem, "module": module})

    for endpoint in snapshot.get("endpoints", []):
        call_chains.append(
            {
                "method": endpoint["method"],
                "url": endpoint["url"],
                "controller": endpoint.get("controller"),
                "source": endpoint.get("source"),
                "chain": [endpoint.get("source")],
                "confidence": "low",
            }
        )
    write_text(paths.work / "code_map.md", render_code_map(root, code_map, call_chains))
    append_jsonl(paths.work / "code_map.jsonl", file_records)
    append_jsonl(paths.work / "code_call_chains.jsonl", call_chains)
    save_state(paths, phase="MAP_CODE", stop_reason=None)
    return code_map


def find_maven_modules(root: Path, code_dir: Path) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for pom in sorted(code_dir.rglob("pom.xml")):
        module = {"path": rel(root, pom.parent), "artifact_id": pom.parent.name, "packaging": None}
        try:
            tree = ET.parse(pom)
            root_node = tree.getroot()
            ns = ""
            if root_node.tag.startswith("{"):
                ns = root_node.tag.split("}", 1)[0] + "}"
            artifact = root_node.findtext(f"{ns}artifactId")
            packaging = root_node.findtext(f"{ns}packaging")
            if artifact:
                module["artifact_id"] = artifact
            module["packaging"] = packaging or "jar"
        except ET.ParseError:
            module["packaging"] = "unknown"
        modules.append(module)
    return modules


def is_test_path(path: Path) -> bool:
    normalized = path.as_posix()
    return "/src/test/" in normalized or path.name.endswith("Test.java") or path.name.endswith("Tests.java")


def infer_module_for_path(root: Path, path: Path) -> str:
    parts = path.relative_to(root).parts
    if len(parts) > 1 and parts[0] == "code":
        return parts[1]
    return "code"


def render_code_map(root: Path, code_map: dict[str, Any], call_chains: list[dict[str, Any]]) -> str:
    lines = ["# Code Map", "", f"Generated: {now_iso()}", ""]
    if code_map.get("warnings"):
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in code_map["warnings"])
        lines.append("")
    lines.extend(["## Maven Modules", ""])
    modules = code_map.get("modules", [])
    if modules:
        for module in modules:
            lines.append(f"- `{module['artifact_id']}` at `{module['path']}` packaging=`{module.get('packaging')}`")
    else:
        lines.append("- None found.")
    lines.append("")
    sections = [
        ("Controllers", "controllers"),
        ("Services", "services"),
        ("Repositories", "repositories"),
        ("DTOs", "dtos"),
        ("Domain Models", "domain_models"),
        ("Exceptions", "exceptions"),
        ("Config", "configs"),
        ("Tests", "tests"),
    ]
    for title, key in sections:
        lines.extend([f"## {title}", ""])
        items = code_map.get(key, [])
        if items:
            for item in items:
                lines.append(f"- `{item['path']}`")
        else:
            lines.append("- None found.")
        lines.append("")
    lines.extend(["## API Call Chains", ""])
    if call_chains:
        for chain in call_chains:
            chain_text = " -> ".join(part for part in chain.get("chain", []) if part)
            lines.append(f"- `{chain['method']} {chain['url']}` -> {chain_text or 'unmapped'}")
    else:
        lines.append("- None found.")
    return "\n".join(lines).rstrip() + "\n"


def run_baseline_tests(root: Path, timeout: int = 900, no_tests: bool = False, test_filter: str | None = None) -> dict[str, Any]:
    paths = RunnerPaths(root)
    ensure_work_layout(paths)
    blackbox_args = ["-f", "test-cases/pom.xml"]
    if test_filter:
        blackbox_args.append(f"-Dtest={test_filter}")
    blackbox_args.append("test")
    commands = [
        ("code-test-baseline.log", "code/pom.xml", maven_command(root, ["-f", "code/pom.xml", "test"])),
        ("code-install-baseline.log", "code/pom.xml", maven_command(root, ["-f", "code/pom.xml", "install", "-DskipTests"])),
        ("blackbox-baseline.log", "test-cases/pom.xml", maven_command(root, blackbox_args)),
    ]
    results: list[dict[str, Any]] = []
    for log_name, pom_rel, command in commands:
        log_path = paths.test_results / log_name
        pom_path = root / pom_rel
        if no_tests:
            content = f"SKIPPED by --no-tests: {command_text(command)}\n"
            write_text(log_path, content)
            results.append({"command": command, "log": rel(root, log_path), "returncode": None, "skipped": True})
            continue
        if not pom_path.exists():
            content = f"SKIPPED missing {pom_rel}: {command_text(command)}\n"
            write_text(log_path, content)
            results.append({"command": command, "log": rel(root, log_path), "returncode": None, "skipped": True})
            continue
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
            )
            write_text(log_path, completed.stdout)
            results.append({"command": command, "log": rel(root, log_path), "returncode": completed.returncode, "skipped": False})
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout if isinstance(exc.stdout, str) else ""
            write_text(log_path, output + f"\nTIMEOUT after {timeout}s\n")
            results.append({"command": command, "log": rel(root, log_path), "returncode": 124, "skipped": False, "timeout": True})
    summary = summarize_test_logs(root)
    skipped_count = sum(1 for item in results if item.get("skipped"))
    executed = [item for item in results if not item.get("skipped")]
    if skipped_count == len(results):
        state_value = "skipped"
    elif any(item.get("returncode") not in (0, None) for item in executed):
        state_value = "failed"
    elif skipped_count:
        state_value = "partial_skipped"
    else:
        state_value = "passed"
    save_state(paths, phase="RUN_BASELINE_TESTS", last_full_test=state_value)
    return {"results": results, "summary": summary}


def run_verification_tests(root: Path, label: str, timeout: int = 900, no_tests: bool = False, test_filter: str | None = None) -> dict[str, Any]:
    paths = RunnerPaths(root)
    ensure_work_layout(paths)
    blackbox_args = ["-f", "test-cases/pom.xml"]
    if test_filter:
        blackbox_args.append(f"-Dtest={test_filter}")
    blackbox_args.append("test")
    commands = [
        (f"{label}-code-test.log", "code/pom.xml", maven_command(root, ["-f", "code/pom.xml", "test"])),
        (f"{label}-code-install.log", "code/pom.xml", maven_command(root, ["-f", "code/pom.xml", "install", "-DskipTests"])),
        (f"{label}-blackbox.log", "test-cases/pom.xml", maven_command(root, blackbox_args)),
    ]
    results: list[dict[str, Any]] = []
    for log_name, pom_rel, command in commands:
        log_path = paths.test_results / log_name
        pom_path = root / pom_rel
        if no_tests:
            write_text(log_path, f"SKIPPED by --no-tests: {command_text(command)}\n")
            results.append({"command": command, "log": rel(root, log_path), "returncode": None, "skipped": True})
            continue
        if not pom_path.exists():
            write_text(log_path, f"SKIPPED missing {pom_rel}: {command_text(command)}\n")
            results.append({"command": command, "log": rel(root, log_path), "returncode": None, "skipped": True})
            continue
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
            )
            write_text(log_path, completed.stdout)
            results.append({"command": command, "log": rel(root, log_path), "returncode": completed.returncode, "skipped": False})
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout if isinstance(exc.stdout, str) else ""
            write_text(log_path, output + f"\nTIMEOUT after {timeout}s\n")
            results.append({"command": command, "log": rel(root, log_path), "returncode": 124, "skipped": False, "timeout": True})

    skipped_count = sum(1 for item in results if item.get("skipped"))
    executed = [item for item in results if not item.get("skipped")]
    if skipped_count == len(results):
        status = "skipped"
    elif any(item.get("returncode") not in (0, None) for item in executed):
        status = "failed"
    elif skipped_count:
        status = "partial_skipped"
    else:
        status = "passed"
    summarize_test_logs(root)
    save_state(paths, phase="RUN_FULL_TESTS", last_full_test=status)
    return {"status": status, "results": results}


def summarize_test_logs(root: Path) -> dict[str, Any]:
    paths = RunnerPaths(root)
    module_names = load_scanned_module_names(root)
    summaries: list[dict[str, Any]] = []
    symptoms: list[dict[str, Any]] = []
    for log_path in sorted(paths.test_results.glob("*.log")):
        text = read_text(log_path)
        summary = summarize_single_log(root, log_path, text)
        summaries.append(summary)
        symptoms.extend(symptoms_from_log(summary, text, module_names))
    write_text(paths.work / "baseline_tests.md", render_test_summary(summaries))
    append_jsonl(paths.work / "test_symptoms.jsonl", symptoms)
    return {"logs": summaries, "symptoms": symptoms}


def summarize_single_log(root: Path, log_path: Path, text: str) -> dict[str, Any]:
    skipped = text.startswith("SKIPPED")
    timeout = "TIMEOUT after" in text
    compilation_error = "COMPILATION ERROR" in text or "Compilation failure" in text
    surefire_matches = re.findall(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)", text)
    failed_tests = sorted(set(re.findall(r"\b([A-Za-z0-9_.]+Test(?:s)?(?:\.[A-Za-z0-9_]+)?)\b", text)))
    error_lines = []
    for line in text.splitlines():
        if "[ERROR]" in line or "Failed tests:" in line or "Failures:" in line or "Errors:" in line:
            error_lines.append(line.strip())
        if len(error_lines) >= 20:
            break
    return {
        "log": rel(root, log_path),
        "skipped": skipped,
        "timeout": timeout,
        "compilation_error": compilation_error,
        "surefire": [
            {"tests": int(a), "failures": int(b), "errors": int(c), "skipped": int(d)}
            for a, b, c, d in surefire_matches
        ],
        "failed_tests": failed_tests[:50],
        "notable_error_lines": error_lines,
    }


def symptoms_from_log(summary: dict[str, Any], text: str, modules: list[str] | None = None) -> list[dict[str, Any]]:
    if summary["skipped"]:
        return []
    symptoms: list[dict[str, Any]] = []
    if summary["compilation_error"]:
        symptoms.append(
            {
                "test_name": Path(summary["log"]).name,
                "failure_type": "compilation_error",
                "symptom": "Maven compilation failed.",
                "likely_modules": infer_modules_from_text(text, modules),
                "related_spec_ids": [],
                "is_design_evidence": False,
            }
        )
    for failed_test in summary["failed_tests"]:
        symptoms.append(
            {
                "test_name": failed_test,
                "failure_type": "test_failure",
                "symptom": failed_test,
                "likely_modules": infer_modules_from_text(failed_test + "\n" + text[:4000], modules),
                "related_spec_ids": [],
                "is_design_evidence": False,
            }
        )
    if summary["timeout"]:
        symptoms.append(
            {
                "test_name": Path(summary["log"]).name,
                "failure_type": "timeout",
                "symptom": "Maven command timed out.",
                "likely_modules": infer_modules_from_text(text, modules),
                "related_spec_ids": [],
                "is_design_evidence": False,
            }
        )
    return symptoms


def infer_modules_from_text(text: str, modules: list[str] | None = None) -> list[str]:
    """Return scanned module names that appear in text (low-confidence triage hint).

    Uses real scanned module names instead of a hard-coded business keyword list.
    """
    if not modules:
        return []
    lowered = text.lower()
    found = [module for module in modules if module and module.lower() in lowered]
    return sorted(set(found), key=modules.index)


def render_test_summary(summaries: list[dict[str, Any]]) -> str:
    lines = ["# Baseline Test Summary", "", f"Generated: {now_iso()}", ""]
    if not summaries:
        lines.append("No test logs found.")
        return "\n".join(lines) + "\n"
    for summary in summaries:
        lines.extend([f"## {summary['log']}", ""])
        if summary["skipped"]:
            lines.append("- Result: SKIPPED")
        elif summary["timeout"]:
            lines.append("- Result: TIMEOUT")
        elif summary["compilation_error"]:
            lines.append("- Result: COMPILATION_ERROR")
        else:
            failed = any(item["failures"] or item["errors"] for item in summary["surefire"])
            lines.append(f"- Result: {'FAILED' if failed else 'PASS_OR_NO_FAILURE_SUMMARY'}")
        if summary["surefire"]:
            for item in summary["surefire"]:
                lines.append(
                    f"- Surefire: tests={item['tests']} failures={item['failures']} errors={item['errors']} skipped={item['skipped']}"
                )
        if summary["failed_tests"]:
            lines.append("- Failed tests:")
            lines.extend(f"  - `{name}`" for name in summary["failed_tests"][:20])
        if summary["notable_error_lines"]:
            lines.append("- Notable lines:")
            lines.extend(f"  - {line}" for line in summary["notable_error_lines"][:10])
        lines.append("")
    lines.append("Public black-box tests are symptoms only. They are not design evidence.")
    return "\n".join(lines).rstrip() + "\n"


def validate_issue(issue: dict[str, Any]) -> list[str]:
    """Return contract violations for an issue; empty list means acceptable.

    The runner does not fabricate issues — module-auditor subagents do. This
    only enforces the contract so downstream patch/review/report get actionable,
    code-location-specific data instead of placeholders.
    """
    errors: list[str] = []
    if not issue.get("issue_id"):
        errors.append("missing issue_id")
    if not issue.get("design_basis"):
        errors.append("missing design_basis")
    code_location = str(issue.get("code_location", ""))
    if not code_location.startswith("code/") or "#" not in code_location:
        errors.append("code_location must point to a method, e.g. code/.../XxxService.java#method")
    actual = str(issue.get("actual_behavior", ""))
    actual_lower = actual.lower()
    placeholder_markers = ("not found", "see code_map", "test symptom observed", "was found by code-mapper", "no corresponding implementation")
    if not actual or any(marker in actual_lower for marker in placeholder_markers):
        errors.append("actual_behavior must be read from code, not a placeholder")
    if not issue.get("design_behavior"):
        errors.append("missing design_behavior")
    if str(issue.get("severity", "")).lower() not in ("high", "medium", "low"):
        errors.append("severity must be one of high/medium/low")
    return errors


def audit_inconsistencies(root: Path) -> list[dict[str, Any]]:
    """Validate and deduplicate issues written by module-auditor subagents.

    The runner does not fabricate semantic inconsistencies — that is the
    module-auditor subagents' job. This enforces the issue contract (precise
    code_location, real actual_behavior), deduplicates by issue_id, records
    rejections to ``issue_rejections.jsonl``, and updates counts.
    """
    paths = RunnerPaths(root)
    issues = read_jsonl(paths.issues)
    seen: set[str] = set()
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for issue in issues:
        issue_id = issue.get("issue_id")
        if not issue_id or issue_id in seen:
            rejections.append({"issue_id": issue_id, "reason": "duplicate or missing issue_id", "issue": issue})
            continue
        errors = validate_issue(issue)
        if errors:
            rejections.append({"issue_id": issue_id, "reason": "; ".join(errors), "issue": issue})
            continue
        seen.add(issue_id)
        accepted.append(issue)
    append_jsonl(paths.issues, accepted)
    if rejections:
        write_json(paths.work / "issue_rejections.jsonl", rejections)
    update_issue_counts(paths, accepted)
    save_state(paths, phase="AUDIT_INCONSISTENCIES")
    return accepted


def update_issue_counts(paths: RunnerPaths, issues: list[dict[str, Any]] | None = None) -> None:
    issues = issues if issues is not None else read_jsonl(paths.issues)
    open_issues = [issue for issue in issues if issue.get("status", "open") == "open"]
    counts = {
        "high_priority_open": sum(1 for issue in open_issues if issue.get("severity") == "high"),
        "medium_priority_open": sum(1 for issue in open_issues if issue.get("severity") == "medium"),
        "low_priority_open": sum(1 for issue in open_issues if issue.get("severity") == "low"),
    }
    save_state(paths, **counts)


def prioritize_issues(root: Path) -> list[dict[str, Any]]:
    paths = RunnerPaths(root)
    issues = read_jsonl(paths.issues)
    prioritized: list[dict[str, Any]] = []
    for issue in issues:
        score = priority_score(issue)
        issue = dict(issue)
        issue["priority_score"] = round(score, 4)
        prioritized.append(issue)
    prioritized.sort(key=lambda issue: issue["priority_score"], reverse=True)
    write_text(paths.work / "fix_plan.md", render_fix_plan(prioritized))
    append_jsonl(paths.work / "fix_plan.jsonl", prioritized)
    update_issue_counts(paths, prioritized)
    save_state(paths, phase="PRIORITIZE_ISSUES")
    return prioritized


def rewrite_prioritized_queue(paths: RunnerPaths, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prioritized: list[dict[str, Any]] = []
    for issue in issues:
        item = dict(issue)
        item["priority_score"] = round(priority_score(item), 4)
        prioritized.append(item)
    prioritized.sort(key=lambda issue: issue["priority_score"], reverse=True)
    write_text(paths.work / "fix_plan.md", render_fix_plan(prioritized))
    append_jsonl(paths.work / "fix_plan.jsonl", prioritized)
    update_issue_counts(paths, prioritized)
    return prioritized


def mark_issue_status(root: Path, issue_id: str, status: str) -> bool:
    paths = RunnerPaths(root)
    issues = read_jsonl(paths.issues)
    found = False
    for issue in issues:
        if issue.get("issue_id") == issue_id:
            issue["status"] = status
            issue["updated_at"] = now_iso()
            found = True
    if found:
        append_jsonl(paths.issues, issues)
        rewrite_prioritized_queue(paths, issues)
    return found


def load_audit_priorities() -> dict[str, Any]:
    """Load submission-internal audit priorities (work/config/audit_priorities.json)."""
    return read_json(AUDIT_PRIORITIES_PATH, {}) or {}


def priority_score(issue: dict[str, Any]) -> float:
    severity = SEVERITY_WEIGHT.get(str(issue.get("severity", "low")).lower(), 1.0)
    confidence = float(issue.get("confidence", 0.5) or 0.5)
    complexity = COMPLEXITY_WEIGHT.get(str(issue.get("estimated_fix_effort", "medium")).lower(), 2.0)
    priorities = load_audit_priorities()
    priority_types = tuple(priorities.get("priority_types") or DEFAULT_PRIORITY_TYPES)
    type_boost = 1.2 if issue.get("type") in priority_types else 1.0
    priority_modules = [str(module).lower() for module in (priorities.get("priority_modules") or [])]
    module_boost = 1.15 if priority_modules and str(issue.get("module", "")).lower() in priority_modules else 1.0
    return severity * confidence * type_boost * module_boost / complexity


def render_fix_plan(issues: list[dict[str, Any]]) -> str:
    lines = ["# Fix Plan", "", f"Generated: {now_iso()}", ""]
    open_issues = [issue for issue in issues if issue.get("status", "open") == "open"]
    if not open_issues:
        lines.append("No open issues.")
        return "\n".join(lines) + "\n"
    lines.append("| Priority | Issue | Severity | Module | Confidence | Effort | Status |")
    lines.append("|---:|---|---|---|---:|---|---|")
    for issue in open_issues:
        lines.append(
            f"| {issue.get('priority_score', 0):.4f} | `{issue['issue_id']}` | {issue.get('severity')} | {issue.get('module')} | {float(issue.get('confidence', 0)):.2f} | {issue.get('estimated_fix_effort')} | {issue.get('status', 'open')} |"
        )
    lines.extend(["", "## Next Issue", ""])
    next_issue = open_issues[0]
    lines.extend(
        [
            f"- issue_id: `{next_issue['issue_id']}`",
            f"- design_basis: {next_issue.get('design_basis')}",
            f"- code_location: {next_issue.get('code_location')}",
            f"- design_behavior: {next_issue.get('design_behavior')}",
            f"- actual_behavior: {next_issue.get('actual_behavior')}",
            f"- fix_suggestion: {next_issue.get('fix_suggestion')}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def select_next_issue(root: Path) -> dict[str, Any] | None:
    paths = RunnerPaths(root)
    issues = read_jsonl(paths.work / "fix_plan.jsonl")
    if not issues:
        issues = prioritize_issues(root)
    for issue in issues:
        if issue.get("status", "open") == "open":
            return issue
    return None


def start_next_round(root: Path) -> Path | None:
    paths = RunnerPaths(root)
    issue = select_next_issue(root)
    if not issue:
        save_state(paths, phase="FIX_LOOP", stop_reason="no_open_issues")
        return None
    state = load_state(paths)
    round_no = int(state.get("round", 0)) + 1
    round_path = paths.rounds / f"round-{round_no:03d}.md"
    before_diff_path = paths.rounds / f"round-{round_no:03d}-before.diff"
    before_diff = run_git_diff(root)
    write_text(before_diff_path, before_diff)
    text = f"""# Round {round_no:03d}

## Issue

- issue_id: {issue.get('issue_id')}
- severity: {issue.get('severity')}
- module: {issue.get('module')}

## Design Basis

{issue.get('design_basis')}

## Before

{issue.get('actual_behavior')}

## Planned Fix

{issue.get('fix_suggestion')}

## API Impact

{issue.get('api_impact')}

## Required Patch-Agent Checklist

- Fix only this issue or a tightly related issue group.
- Do not change frozen REST API signatures.
- Prefer Service / Domain changes over Controller / DTO changes.
- Modify at most 3 business Java files, 2 test files, and 1 config file unless entering REPLAN.
- Run focused tests and record commands below.

## Modified Files

TBD

## Tests Run

TBD

## Result

IN_PROGRESS

## Risk

TBD
"""
    write_text(round_path, text)
    save_state(paths, phase="FIX_LOOP", round=round_no, stop_reason="patch_agent_required")
    return round_path


def finish_round(root: Path, round_no: int, result: str, tests: str | None, risk: str | None) -> Path:
    paths = RunnerPaths(root)
    round_path = paths.rounds / f"round-{round_no:03d}.md"
    existing = read_text(round_path) if round_path.exists() else f"# Round {round_no:03d}\n"
    diff = run_git_diff(root)
    score = score_round(result=result, api_safe=load_state(paths).get("api_contract_safe", True), diff=diff, tests=tests)
    addition = f"""

## Completion Update

- completed_at: {now_iso()}
- result: {result}
- score: {score}
- tests: {tests or 'not recorded'}
- risk: {risk or 'not recorded'}

## Diff Stat

```text
{run_git_diff_stat(root) or 'No diff.'}
```
"""
    write_text(round_path, existing.rstrip() + addition)
    state_updates: dict[str, Any] = {
        "phase": "FIX_LOOP",
        "last_score": score,
        "last_focused_test": "passed" if result.upper() == "PASS" else "failed",
    }
    if result.upper() == "PASS":
        state_updates["fixed_count"] = int(load_state(paths).get("fixed_count", 0)) + 1
        state_updates["consecutive_no_progress"] = 0
        issue_match = re.search(r"issue_id:\s*`?([A-Za-z0-9_.:-]+)`?", existing)
        if issue_match:
            mark_issue_status(root, issue_match.group(1), "fixed")
    else:
        state_updates["consecutive_no_progress"] = int(load_state(paths).get("consecutive_no_progress", 0)) + 1
    save_state(paths, **state_updates)
    return round_path


def run_git_diff(root: Path) -> str:
    try:
        completed = subprocess.run(
            # `code` pathspec already covers nested files under code/, including
            # **/application.yml and **/application.yaml allowed by INSTRUCTION.
            ["git", "diff", "--", "code", "pom.xml"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        return completed.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git diff unavailable: {exc}\n"


def run_git_diff_stat(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git diff --stat unavailable: {exc}"


def score_round(result: str, api_safe: bool, diff: str, tests: str | None) -> int:
    evidence_quality = 80
    task_completion = 100 if result.upper() == "PASS" else 50
    api_safety = 100 if api_safe else 0
    # test_effectiveness from the round result, not from whether a tests string was recorded
    test_effectiveness = 90 if result.upper() == "PASS" else (60 if tests else 30)
    changed_files = len(set(re.findall(r"^\+\+\+ b/(.+)$", diff, flags=re.M)))
    # token_efficiency: do not punish a normal multi-file fix; only lightly flag very large diffs
    token_efficiency = 100 if changed_files <= 8 else max(40, 100 - (changed_files - 8) * 5)
    score = (
        evidence_quality * 0.30
        + task_completion * 0.25
        + api_safety * 0.15
        + test_effectiveness * 0.15
        + token_efficiency * 0.15
    )
    return int(round(score))


def generate_report(root: Path) -> Path:
    paths = RunnerPaths(root)
    issues = read_jsonl(paths.issues)
    rounds = sorted(paths.rounds.glob("round-[0-9][0-9][0-9].md"))
    state = load_state(paths)
    api_compare = read_json(paths.work / "api_compare.json", {"safe": state.get("api_contract_safe", True), "warnings": []})
    test_summary = read_text(paths.work / "baseline_tests.md") if (paths.work / "baseline_tests.md").exists() else "Tests not run."
    report = render_report(root, issues, rounds, state, api_compare, test_summary)
    report_path = root / "修复报告.md"
    work_report_path = paths.reports / "修复报告.md"
    source_path = paths.work / "final_report_source.md"
    write_text(report_path, report)
    write_text(work_report_path, report)
    write_text(source_path, report)
    save_state(paths, phase="DONE", should_continue=False, stop_reason=state.get("stop_reason") or "report_generated")
    return report_path


def render_report(
    root: Path,
    issues: list[dict[str, Any]],
    rounds: list[Path],
    state: dict[str, Any],
    api_compare: dict[str, Any],
    test_summary: str,
) -> str:
    fixed = [issue for issue in issues if issue.get("status") in {"fixed", "done", "closed"}]
    open_issues = [issue for issue in issues if issue.get("status", "open") == "open"]
    lines = [
        "# 设计实现一致性检查与修复报告",
        "",
        f"生成时间：{now_iso()}",
        "",
        "## 发现的不一致点",
        "",
    ]
    if issues:
        lines.append("| 编号 | 严重程度 | 位置 | 设计描述 | 代码行为 | 类型 | 是否修复 |")
        lines.append("|------|----------|------|----------|----------|------|----------|")
        for issue in issues:
            fixed_text = "是" if issue.get("status") in {"fixed", "done", "closed"} else "否"
            lines.append(
                "| {issue_id} | {severity} | {location} | {design} | {actual} | {type_} | {fixed} |".format(
                    issue_id=escape_table(str(issue.get("issue_id", ""))),
                    severity=escape_table(str(issue.get("severity", ""))),
                    location=escape_table(str(issue.get("code_location", ""))),
                    design=escape_table(str(issue.get("design_behavior", ""))[:120]),
                    actual=escape_table(str(issue.get("actual_behavior", ""))[:120]),
                    type_=escape_table(str(issue.get("type", ""))),
                    fixed=fixed_text,
                )
            )
    else:
        lines.append("当前没有记录到已确认的不一致点。")
    lines.extend(["", "## 修复详情", ""])
    if rounds:
        for round_path in rounds:
            content = read_text(round_path).strip()
            lines.append(f"### {round_path.stem}")
            lines.append("")
            lines.append(content)
            lines.append("")
    else:
        lines.append("尚未执行修复轮次。")
        lines.append("")
    lines.extend(["## 未修复风险说明", ""])
    if open_issues:
        for issue in open_issues:
            lines.append(f"- `{issue.get('issue_id')}`：{issue.get('actual_behavior')}；建议：{issue.get('fix_suggestion')}")
    else:
        missing = state.get("missing_required_paths") or []
        if missing:
            lines.append("当前仓库缺少比赛输入，尚无法完成真实业务审计：")
            lines.extend(f"- `{item}`" for item in missing)
        else:
            lines.append("未记录开放风险。")
    api_safe = "是" if api_compare.get("safe", state.get("api_contract_safe", True)) else "否"
    final_test_result = state.get("last_full_test", "not_run")
    verification_commands = [
        command_text(maven_command(root, ["-f", "code/pom.xml", "test"])),
        command_text(maven_command(root, ["-f", "code/pom.xml", "install", "-DskipTests"])),
        command_text(maven_command(root, ["-f", "test-cases/pom.xml", "test"])),
    ]
    lines.extend(
        [
            "",
            "## 总结",
            "",
            f"- 发现总数：{len(issues)}",
            f"- 已修复数量：{len(fixed)}",
            f"- 未修复数量：{len(open_issues)}",
            f"- 仍有风险：{'是' if open_issues or state.get('missing_required_paths') else '否'}",
            f"- API 契约是否保持不变：{api_safe}",
            "- 最终验证命令：",
            *[f"  - `{command}`" for command in verification_commands],
            f"- 最终验证结果：{final_test_result}",
            "",
            "## 测试摘要",
            "",
            test_summary.strip(),
            "",
        ]
    )
    warnings = api_compare.get("warnings") or []
    if warnings:
        lines.extend(["## API 检查警告", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def run_full_workflow(root: Path, no_tests: bool, timeout: int) -> None:
    paths = RunnerPaths(root)
    init_workspace(root)
    extract_specs(root)
    parse_api_baseline(root)
    map_code(root)
    run_baseline_tests(root, timeout=timeout, no_tests=no_tests)
    audit_inconsistencies(root)
    prioritized = prioritize_issues(root)
    open_issues = [issue for issue in prioritized if issue.get("status", "open") == "open"]
    missing = check_competition_layout(root)
    if missing:
        save_state(paths, phase="WRITE_REPORT", should_continue=False, stop_reason="missing_competition_inputs")
    elif open_issues:
        start_next_round(root)
    else:
        save_state(paths, phase="WRITE_REPORT", should_continue=False, stop_reason="no_open_issues")
    generate_report(root)


def append_auto_log(paths: RunnerPaths, message: str) -> None:
    path = paths.work / "auto-run.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{now_iso()}] {message}\n")


def open_issues_by_severity(root: Path) -> dict[str, list[dict[str, Any]]]:
    issues = read_jsonl(RunnerPaths(root).issues)
    open_issues = [issue for issue in issues if issue.get("status", "open") == "open"]
    return {
        "high": [issue for issue in open_issues if issue.get("severity") == "high"],
        "medium": [issue for issue in open_issues if issue.get("severity") == "medium"],
        "low": [issue for issue in open_issues if issue.get("severity") == "low"],
        "all": open_issues,
    }


def done_decision(root: Path, no_tests: bool = False) -> tuple[bool, str | None]:
    """Decide whether the runner is DONE.

    ``no_tests`` is accepted for backward compatibility but no longer grants a
    done path — a real run requires ``last_full_test == passed`` (per SKILL.md:
    "Do not use no-tests in a real competition run"). Tests failing with no
    open issue also does not auto-stop: it returns to the orchestrator so
    auditors can be fanned out again (the queue may be empty because auditors
    have not yet covered the failing behavior).
    """
    paths = RunnerPaths(root)
    state = load_state(paths)
    missing = check_competition_layout(root)
    buckets = open_issues_by_severity(root)
    high_medium_open = len(buckets["high"]) + len(buckets["medium"])
    last_full_test = state.get("last_full_test")
    _ = no_tests  # accepted for compatibility; no-tests is not a done path

    if missing:
        return True, "missing_competition_inputs"
    if not state.get("api_contract_safe", True):
        return True, "api_contract_violation"
    if int(state.get("round", 0)) >= int(state.get("max_rounds", 20)):
        return True, "max_rounds_reached"
    if int(state.get("consecutive_no_progress", 0)) >= 3:
        return True, "consecutive_no_progress"
    if int(state.get("consecutive_regressions", 0)) >= 2:
        return True, "consecutive_regressions"
    if high_medium_open == 0 and last_full_test == "passed":
        return True, "completed_high_medium_and_tests_passed"
    return False, None


def format_patch_command(command_template: str, root: Path, round_no: int, round_path: Path, issue: dict[str, Any]) -> str:
    paths = RunnerPaths(root)
    issue_path = paths.rounds / f"round-{round_no:03d}-issue.json"
    write_json(issue_path, issue)
    values = {
        "root": shlex.quote(root.as_posix()),
        "round": str(round_no),
        "round_file": shlex.quote(round_path.as_posix()),
        "issue_id": shlex.quote(str(issue.get("issue_id", ""))),
        "issue_json": shlex.quote(issue_path.as_posix()),
    }
    return command_template.format(**values)


def run_patch_command(
    root: Path,
    command_template: str,
    round_no: int,
    round_path: Path,
    issue: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    paths = RunnerPaths(root)
    command = format_patch_command(command_template, root, round_no, round_path, issue)
    log_path = paths.rounds / f"round-{round_no:03d}-patch-command.log"
    env = os.environ.copy()
    env.update(
        {
            "SHOPHUB_ROOT": root.as_posix(),
            "SHOPHUB_ROUND": str(round_no),
            "SHOPHUB_ROUND_FILE": round_path.as_posix(),
            "SHOPHUB_ISSUE_ID": str(issue.get("issue_id", "")),
        }
    )
    append_auto_log(paths, f"round {round_no:03d}: running patch command for {issue.get('issue_id')}")
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
            env=env,
        )
        write_text(log_path, completed.stdout)
        save_state(paths, last_patch_command=command, last_patch_exit_code=completed.returncode)
        return {"returncode": completed.returncode, "log": rel(root, log_path), "timeout": False}
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        write_text(log_path, output + f"\nTIMEOUT after {timeout}s\n")
        save_state(paths, last_patch_command=command, last_patch_exit_code=124)
        return {"returncode": 124, "log": rel(root, log_path), "timeout": True}


def verification_command_summary(verification: dict[str, Any]) -> str:
    parts = []
    for item in verification.get("results", []):
        command = " ".join(item.get("command", []))
        status = "SKIPPED" if item.get("skipped") else str(item.get("returncode"))
        parts.append(f"{command} => {status} ({item.get('log')})")
    return "; ".join(parts)


def run_until_done(
    root: Path,
    no_tests: bool,
    timeout: int,
    max_rounds: int,
    patch_command: str | None,
    patch_timeout: int,
) -> None:
    """Fallback-only continuous runner for runtimes WITHOUT subagent/Task support.

    The preferred path is the orchestrator's subagent fan-out (see
    shophub-orchestrator.md). This loop cannot produce semantic issues on its
    own — ``audit_inconsistencies`` only validates/dedups issues written by
    subagents — so without an external ``--patch-command`` it stops at
    ``patch_command_required``. Use only when the runtime has no Task tool.
    """
    paths = RunnerPaths(root)
    init_workspace(root)
    save_state(
        paths,
        phase="INIT",
        max_rounds=max_rounds,
        should_continue=True,
        stop_reason=None,
        auto_run_started_at=now_iso(),
        auto_run_finished_at=None,
    )
    append_auto_log(paths, "auto-run started")

    extract_specs(root)
    parse_api_baseline(root)
    map_code(root)
    run_baseline_tests(root, timeout=timeout, no_tests=no_tests)
    audit_inconsistencies(root)
    prioritize_issues(root)

    while True:
        done, reason = done_decision(root, no_tests=no_tests)
        if done:
            append_auto_log(paths, f"stopping: {reason}")
            save_state(paths, phase="WRITE_REPORT", should_continue=False, stop_reason=reason, auto_run_finished_at=now_iso())
            break

        issue = select_next_issue(root)
        if not issue:
            reason = "no_open_issue_available"
            append_auto_log(paths, f"stopping: {reason}")
            save_state(paths, phase="WRITE_REPORT", should_continue=False, stop_reason=reason, auto_run_finished_at=now_iso())
            break

        round_path = start_next_round(root)
        if not round_path:
            reason = "unable_to_start_round"
            append_auto_log(paths, f"stopping: {reason}")
            save_state(paths, phase="WRITE_REPORT", should_continue=False, stop_reason=reason, auto_run_finished_at=now_iso())
            break

        state = load_state(paths)
        round_no = int(state.get("round", 0))
        if not patch_command:
            reason = "patch_command_required"
            append_auto_log(paths, f"stopping: {reason}")
            save_state(paths, should_continue=False, stop_reason=reason, auto_run_finished_at=now_iso())
            break

        patch_result = run_patch_command(root, patch_command, round_no, round_path, issue, patch_timeout)
        if patch_result["returncode"] != 0:
            finish_round(
                root,
                round_no,
                "FAILED",
                tests=f"patch command failed: {patch_result['log']}",
                risk="Patch command did not complete successfully.",
            )
            reason = "patch_command_failed"
            append_auto_log(paths, f"stopping: {reason}")
            save_state(paths, should_continue=False, stop_reason=reason, auto_run_finished_at=now_iso())
            break

        parse_api_baseline(root)
        state = load_state(paths)
        if not state.get("api_contract_safe", True):
            save_state(paths, consecutive_regressions=int(state.get("consecutive_regressions", 0)) + 1)
            finish_round(
                root,
                round_no,
                "REJECT_AND_REVERT",
                tests="API snapshot comparison failed.",
                risk="Patch changed frozen API contract. Manual rollback or repair is required.",
            )
            reason = "api_contract_violation_after_round"
            append_auto_log(paths, f"stopping: {reason}")
            save_state(paths, should_continue=False, stop_reason=reason, auto_run_finished_at=now_iso())
            break

        verification = run_verification_tests(root, f"round-{round_no:03d}", timeout=timeout, no_tests=no_tests)
        tests_summary = verification_command_summary(verification)
        if verification["status"] in {"passed", "skipped"}:
            finish_round(root, round_no, "PASS", tests=tests_summary, risk="Full tests skipped." if no_tests else "None recorded.")
        else:
            state = load_state(paths)
            save_state(paths, consecutive_regressions=int(state.get("consecutive_regressions", 0)) + 1)
            finish_round(root, round_no, "REWORK_REQUIRED", tests=tests_summary, risk="Verification did not pass after patch.")

        map_code(root)
        audit_inconsistencies(root)
        prioritize_issues(root)

    generate_report(root)
    append_auto_log(paths, "auto-run finished")


def command_status(root: Path) -> None:
    paths = RunnerPaths(root)
    state = load_state(paths)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ShopHub Goal Runner")
    parser.add_argument("--root", default=".", help="Project root. Defaults to current directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize .agent-work and state files.")
    sub.add_parser("read-specs", help="Extract design rules from design-docs/.")
    sub.add_parser("read-api", help="Extract API baseline and current code API snapshot.")
    sub.add_parser("map-code", help="Build Java/Spring code map.")
    baseline = sub.add_parser("baseline-tests", help="Run Maven baseline tests and summarize logs.")
    baseline.add_argument("--timeout", type=int, default=900)
    baseline.add_argument("--no-tests", action="store_true", help="Create skipped logs without running Maven.")
    baseline.add_argument("--test-filter", default=None, help="Focus black-box tests via -Dtest=<name>; test class is discovered dynamically, not hard-coded.")
    sub.add_parser("summarize-tests", help="Summarize logs under .agent-work/test-results/.")
    sub.add_parser("audit", help="Create conservative issue candidates.")
    sub.add_parser("prioritize", help="Prioritize issues into fix_plan.md.")
    sub.add_parser("next-round", help="Create the next patch-agent round file.")
    finish = sub.add_parser("finish-round", help="Record round completion.")
    finish.add_argument("--round", type=int, required=True)
    finish.add_argument("--result", required=True, choices=["PASS", "REWORK_REQUIRED", "REJECT_AND_REVERT", "FAILED"])
    finish.add_argument("--tests")
    finish.add_argument("--risk")
    run = sub.add_parser("run", help="Run deterministic workflow through report generation.")
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--no-tests", action="store_true", help="Skip Maven execution and write skipped logs.")
    auto_run = sub.add_parser("auto-run", help="Continuously run the Goal Runner until DONE or a safety stop condition.")
    auto_run.add_argument("--timeout", type=int, default=900, help="Timeout in seconds for each Maven verification command.")
    auto_run.add_argument("--patch-timeout", type=int, default=1800, help="Timeout in seconds for each external patch command.")
    auto_run.add_argument("--max-rounds", type=int, default=20, help="Maximum repair rounds before stopping.")
    auto_run.add_argument("--no-tests", action="store_true", help="Skip Maven execution. Use only for dry runs.")
    auto_run.add_argument(
        "--patch-command",
        default=os.environ.get("SHOPHUB_PATCH_COMMAND"),
        help=(
            "External command that repairs one round. Placeholders: {root}, {round}, "
            "{round_file}, {issue_id}, {issue_json}. Can also be set with SHOPHUB_PATCH_COMMAND."
        ),
    )
    add_issue = sub.add_parser("add-issue", help="Append one validated issue to issues.jsonl (for fan-out auditors).")
    add_issue.add_argument("--issue-json", required=True, help="JSON string of the issue.")
    sub.add_parser("report", help="Generate 修复报告.md.")
    sub.add_parser("status", help="Print state.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "init":
        init_workspace(root)
    elif args.command == "read-specs":
        extract_specs(root)
    elif args.command == "read-api":
        parse_api_baseline(root)
    elif args.command == "map-code":
        map_code(root)
    elif args.command == "baseline-tests":
        run_baseline_tests(root, timeout=args.timeout, no_tests=args.no_tests, test_filter=args.test_filter)
    elif args.command == "summarize-tests":
        summarize_test_logs(root)
    elif args.command == "audit":
        audit_inconsistencies(root)
    elif args.command == "prioritize":
        prioritize_issues(root)
    elif args.command == "next-round":
        round_path = start_next_round(root)
        if round_path:
            print(round_path)
    elif args.command == "finish-round":
        round_path = finish_round(root, args.round, args.result, args.tests, args.risk)
        print(round_path)
    elif args.command == "run":
        run_full_workflow(root, no_tests=args.no_tests, timeout=args.timeout)
    elif args.command == "auto-run":
        run_until_done(
            root,
            no_tests=args.no_tests,
            timeout=args.timeout,
            max_rounds=args.max_rounds,
            patch_command=args.patch_command,
            patch_timeout=args.patch_timeout,
        )
    elif args.command == "add-issue":
        issue = json.loads(args.issue_json)
        errors = validate_issue(issue)
        if errors:
            raise SystemExit("Issue rejected: " + "; ".join(errors))
        append_jsonl_record(RunnerPaths(root).issues, issue)
    elif args.command == "report":
        print(generate_report(root))
    elif args.command == "status":
        command_status(root)
    else:
        parser.error(f"Unknown command {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
