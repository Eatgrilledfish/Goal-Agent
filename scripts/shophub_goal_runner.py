#!/usr/bin/env python3
"""Local Goal Runner for the ShopHub consistency competition.

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
    "code",
    "design-docs",
    "test-cases",
    "API基线文档.md",
    "黑盒用例说明.md",
    "比赛说明.md",
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
MODULE_ORDER = [
    "order",
    "inventory",
    "stock",
    "payment",
    "pay",
    "amount",
    "price",
    "status",
    "error",
    "validation",
    "idempotent",
    "query",
]


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
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

Compare ShopHub design documents, frozen REST API contract, and Spring Boot implementation. Find and repair design-code inconsistencies in small, evidence-backed rounds while preserving the API contract.

## Required Inputs Missing

{missing_section}

## Safety Rules

- Do not modify `design-docs/**`.
- Do not modify `API基线文档.md`.
- Do not modify `比赛说明.md` or `黑盒用例说明.md`.
- Avoid modifying `test-cases/**`.
- Do not change REST API URLs, methods, headers, request fields, response fields, or error-code semantics.

## Verification Commands

```bash
mvn -f code/pom.xml test
mvn -f code/pom.xml install
mvn -f test-cases/pom.xml test
```
"""
    write_text(paths.work / "goal.md", text)


def extract_specs(root: Path) -> list[dict[str, Any]]:
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

    rule_markers = re.compile(
        r"(应当|应该|应|必须|不得|禁止|需要|要求|当|如果|若|则|校验|释放|扣减|恢复|支付|取消|退款|库存|订单|金额|状态|错误码|边界|幂等)"
    )
    boundary_markers = re.compile(r"(不得|禁止|不能|边界|异常|失败|为空|不存在|重复|超限|已支付|已发货|已取消)")
    api_pattern = re.compile(r"(/api/[A-Za-z0-9_./{}?=&:-]+)")

    for doc_path in sorted(design_dir.rglob("*.md")):
        module = doc_path.stem.lower()
        module_slug = slug(module)
        section = "概要"
        doc_records: list[dict[str, Any]] = []
        paragraph: list[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph
            if not paragraph:
                return
            text = clean_markdown_text(" ".join(paragraph))
            paragraph = []
            if len(text) < 8 or not rule_markers.search(text):
                return
            count = len([r for r in records if r["module"] == module]) + 1
            spec_id = f"{module_slug}-{count:03d}"
            boundaries = split_boundary_conditions(text, boundary_markers)
            record = {
                "spec_id": spec_id,
                "module": module,
                "source_doc": rel(root, doc_path),
                "section": section,
                "design_rule": text,
                "expected_behavior": infer_expected_behavior(text),
                "boundary_conditions": boundaries,
                "related_api_if_any": sorted(set(api_pattern.findall(text))),
                "severity_hint": infer_severity(text),
            }
            records.append(record)
            doc_records.append(record)

        for raw_line in read_text(doc_path).splitlines():
            line = raw_line.strip()
            if not line:
                flush_paragraph()
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                flush_paragraph()
                section = clean_markdown_text(heading.group(2))
                continue
            if line.startswith("|") and line.endswith("|"):
                cells = [clean_markdown_text(cell) for cell in line.strip("|").split("|")]
                if len(cells) > 1 and any(rule_markers.search(cell) for cell in cells):
                    paragraph.append("；".join(cell for cell in cells if cell and not set(cell) <= {"-", ":"}))
                    flush_paragraph()
                continue
            if re.match(r"^[-*+]\s+", line) or re.match(r"^\d+[.)、]\s+", line):
                flush_paragraph()
                paragraph.append(re.sub(r"^([-*+]|\d+[.)、])\s+", "", line))
                flush_paragraph()
                continue
            paragraph.append(line)
        flush_paragraph()

        index_lines.append(f"## {rel(root, doc_path)}")
        if doc_records:
            for record in doc_records:
                index_lines.append(f"- `{record['spec_id']}` {record['section']}: {record['design_rule']}")
        else:
            index_lines.append("- No explicit rule-like statements found by heuristic extraction.")
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


def split_boundary_conditions(text: str, pattern: re.Pattern[str]) -> list[str]:
    parts = re.split(r"[；;。.!?？]", text)
    return [part.strip() for part in parts if part.strip() and pattern.search(part)]


def infer_expected_behavior(text: str) -> str:
    if "应" in text or "必须" in text or "需要" in text:
        return text
    if "不得" in text or "禁止" in text:
        return f"系统应拒绝或阻止该场景：{text}"
    return text


def infer_severity(text: str) -> str:
    high_terms = ("订单", "库存", "支付", "金额", "状态", "错误码", "扣减", "释放", "退款")
    medium_terms = ("校验", "查询", "边界", "幂等", "重复", "分页")
    if any(term in text for term in high_terms):
        return "high"
    if any(term in text for term in medium_terms):
        return "medium"
    return "low"


def parse_api_baseline(root: Path) -> dict[str, Any]:
    paths = RunnerPaths(root)
    baseline = root / "API基线文档.md"
    contract: dict[str, Any] = {
        "generated_at": now_iso(),
        "source": rel(root, baseline),
        "endpoints": [],
        "error_codes": [],
        "frozen": True,
        "warnings": [],
    }
    index_lines = ["# API Contract Index", "", f"Generated: {now_iso()}", ""]
    if not baseline.exists():
        contract["source"] = None
        contract["warnings"].append("API基线文档.md does not exist.")
        write_json(paths.work / "api_contract.json", contract)
        write_json(paths.work / "api_snapshot_baseline.json", contract)
        write_text(paths.work / "02_api_contract_index.md", "\n".join(index_lines + ["API baseline file is missing."]) + "\n")
        snapshot_current = snapshot_code_api(root)
        write_json(paths.work / "api_snapshot_current.json", snapshot_current)
        compare = compare_api_snapshots(contract, snapshot_current)
        write_json(paths.work / "api_compare.json", compare)
        save_state(paths, phase="READ_API_BASELINE", api_contract_safe=compare["safe"], stop_reason="missing_api_baseline")
        return contract

    text = read_text(baseline)
    endpoints = extract_endpoints_from_markdown(text)
    error_codes = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text)))
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


def compare_api_snapshots(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    baseline_endpoints = {(item.get("method"), item.get("url")): item for item in baseline.get("endpoints", [])}
    current_endpoints = {(item.get("method"), item.get("url")): item for item in current.get("endpoints", [])}
    missing = sorted([{"method": method, "url": url} for method, url in baseline_endpoints if (method, url) not in current_endpoints], key=lambda x: (x["url"], x["method"]))
    additional = sorted([{"method": method, "url": url} for method, url in current_endpoints if (method, url) not in baseline_endpoints], key=lambda x: (x["url"], x["method"]))
    baseline_error_codes = set(baseline.get("error_codes", []))
    current_error_codes = set(current.get("error_codes", []))
    missing_error_codes = sorted(code for code in baseline_error_codes if code not in current_error_codes)

    warnings: list[str] = []
    if not baseline_endpoints:
        warnings.append("Baseline endpoint set is empty; API compatibility cannot be fully verified.")
    if not current_endpoints:
        warnings.append("Current code endpoint set is empty; code/ may be missing or parser found no controllers.")
    safe = not missing and (not baseline_error_codes or not missing_error_codes)
    return {
        "generated_at": now_iso(),
        "safe": safe,
        "missing_endpoints": missing,
        "additional_current_endpoints": additional,
        "missing_error_codes": missing_error_codes,
        "warnings": warnings,
    }


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
    java_files = sorted(code_dir.rglob("*.java"))
    call_chains: list[dict[str, Any]] = []
    snapshot = snapshot_code_api(root)
    for path in java_files:
        text = read_text(path)
        item = {"path": rel(root, path), "class": path.stem, "module": infer_module_for_path(root, path)}
        if is_test_path(path):
            code_map["tests"].append(item)
        elif "@RestController" in text or "@Controller" in text:
            code_map["controllers"].append(item)
        elif "@Service" in text or path.name.endswith("Service.java") or path.name.endswith("ServiceImpl.java"):
            code_map["services"].append(item)
        elif "@Repository" in text or path.name.endswith("Repository.java") or path.name.endswith("Mapper.java") or path.name.endswith("Dao.java"):
            code_map["repositories"].append(item)
        elif looks_like_dto(path, path.stem, text):
            code_map["dtos"].append(item)
        elif "Exception" in path.name:
            code_map["exceptions"].append(item)
        elif "@Configuration" in text or path.name.endswith("Config.java") or path.name.endswith("Configuration.java"):
            code_map["configs"].append(item)
        elif "/domain/" in path.as_posix().lower() or "/model/" in path.as_posix().lower() or "/entity/" in path.as_posix().lower():
            code_map["domain_models"].append(item)

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


def run_baseline_tests(root: Path, timeout: int = 900, no_tests: bool = False) -> dict[str, Any]:
    paths = RunnerPaths(root)
    ensure_work_layout(paths)
    commands = [
        ("code-test-baseline.log", ["mvn", "-f", "code/pom.xml", "test"]),
        ("code-install-baseline.log", ["mvn", "-f", "code/pom.xml", "install"]),
        ("blackbox-baseline.log", ["mvn", "-f", "test-cases/pom.xml", "test"]),
    ]
    results: list[dict[str, Any]] = []
    for log_name, command in commands:
        log_path = paths.test_results / log_name
        pom_path = root / command[2]
        if no_tests:
            content = f"SKIPPED by --no-tests: {' '.join(command)}\n"
            write_text(log_path, content)
            results.append({"command": command, "log": rel(root, log_path), "returncode": None, "skipped": True})
            continue
        if not pom_path.exists():
            content = f"SKIPPED missing {command[2]}: {' '.join(command)}\n"
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


def run_verification_tests(root: Path, label: str, timeout: int = 900, no_tests: bool = False) -> dict[str, Any]:
    paths = RunnerPaths(root)
    ensure_work_layout(paths)
    commands = [
        (f"{label}-code-test.log", ["mvn", "-f", "code/pom.xml", "test"]),
        (f"{label}-code-install.log", ["mvn", "-f", "code/pom.xml", "install"]),
        (f"{label}-blackbox.log", ["mvn", "-f", "test-cases/pom.xml", "test"]),
    ]
    results: list[dict[str, Any]] = []
    for log_name, command in commands:
        log_path = paths.test_results / log_name
        pom_path = root / command[2]
        if no_tests:
            write_text(log_path, f"SKIPPED by --no-tests: {' '.join(command)}\n")
            results.append({"command": command, "log": rel(root, log_path), "returncode": None, "skipped": True})
            continue
        if not pom_path.exists():
            write_text(log_path, f"SKIPPED missing {command[2]}: {' '.join(command)}\n")
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
    summaries: list[dict[str, Any]] = []
    symptoms: list[dict[str, Any]] = []
    for log_path in sorted(paths.test_results.glob("*.log")):
        text = read_text(log_path)
        summary = summarize_single_log(root, log_path, text)
        summaries.append(summary)
        symptoms.extend(symptoms_from_log(summary, text))
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


def symptoms_from_log(summary: dict[str, Any], text: str) -> list[dict[str, Any]]:
    if summary["skipped"]:
        return []
    symptoms: list[dict[str, Any]] = []
    if summary["compilation_error"]:
        symptoms.append(
            {
                "test_name": Path(summary["log"]).name,
                "failure_type": "compilation_error",
                "symptom": "Maven compilation failed.",
                "likely_modules": infer_modules_from_text(text),
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
                "likely_modules": infer_modules_from_text(failed_test + "\n" + text[:4000]),
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
                "likely_modules": infer_modules_from_text(text),
                "related_spec_ids": [],
                "is_design_evidence": False,
            }
        )
    return symptoms


def infer_modules_from_text(text: str) -> list[str]:
    lowered = text.lower()
    modules = [module for module in MODULE_ORDER if module in lowered]
    return sorted(set(modules), key=modules.index)


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


def audit_inconsistencies(root: Path) -> list[dict[str, Any]]:
    """Create conservative issue candidates from missing implementation signals.

    The runner does not fabricate semantic inconsistencies. It only creates
    low-risk audit issues when a design module has no corresponding code map, or
    when test symptoms point to a module that has design rules and code files.
    A human/Codex auditor should refine these into concrete code-location issues.
    """

    paths = RunnerPaths(root)
    specs = read_jsonl(paths.specs)
    existing = read_jsonl(paths.issues)
    by_id = {item.get("issue_id"): item for item in existing if item.get("issue_id")}
    code_map_text = read_text(paths.work / "code_map.md") if (paths.work / "code_map.md").exists() else ""
    symptoms = read_jsonl(paths.work / "test_symptoms.jsonl")
    issues: list[dict[str, Any]] = list(existing)

    modules_with_specs = sorted(set(str(spec["module"]) for spec in specs))
    for module in modules_with_specs:
        module_specs = [spec for spec in specs if spec["module"] == module]
        module_present = module.lower() in code_map_text.lower()
        if not module_present and module_specs:
            spec = module_specs[0]
            issue_id = f"{slug(module)}-MAP-{stable_hash(spec['spec_id'], 4).upper()}"
            if issue_id not in by_id:
                issues.append(
                    {
                        "issue_id": issue_id,
                        "severity": "medium" if spec.get("severity_hint") == "high" else "low",
                        "module": module,
                        "design_basis": f"{spec['source_doc']}#{spec['section']}",
                        "code_location": "code/ (module not found in code map)",
                        "design_behavior": spec["expected_behavior"],
                        "actual_behavior": "No corresponding implementation module was found by code-mapper.",
                        "type": "implementation_mapping_gap",
                        "api_impact": "unknown",
                        "fix_suggestion": "Run module-auditor to confirm whether implementation exists under a different module name before patching.",
                        "test_suggestion": "Run full baseline tests after confirming module mapping.",
                        "confidence": 0.55,
                        "estimated_fix_effort": "medium",
                        "status": "open",
                    }
                )
    for symptom in symptoms:
        for module in symptom.get("likely_modules", []):
            module_specs = [spec for spec in specs if module in str(spec["module"]).lower()]
            if not module_specs:
                continue
            spec = module_specs[0]
            issue_id = f"{slug(module)}-TEST-{stable_hash(symptom['test_name'] + spec['spec_id'], 4).upper()}"
            if issue_id in by_id:
                continue
            issues.append(
                {
                    "issue_id": issue_id,
                    "severity": spec.get("severity_hint", "medium"),
                    "module": module,
                    "design_basis": f"{spec['source_doc']}#{spec['section']}",
                    "code_location": "See code_map.md for likely module files.",
                    "design_behavior": spec["expected_behavior"],
                    "actual_behavior": f"Test symptom observed: {symptom['symptom']}",
                    "type": "test_symptom_requires_design_audit",
                    "api_impact": "unknown",
                    "fix_suggestion": "Use module-auditor to compare the failing behavior with the cited design rule before editing code.",
                    "test_suggestion": "Re-run the failing test plus related module tests after a confirmed fix.",
                    "confidence": 0.6,
                    "estimated_fix_effort": "medium",
                    "status": "open",
                }
            )
    append_jsonl(paths.issues, issues)
    update_issue_counts(paths, issues)
    save_state(paths, phase="AUDIT_INCONSISTENCIES")
    return issues


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


def priority_score(issue: dict[str, Any]) -> float:
    severity = SEVERITY_WEIGHT.get(str(issue.get("severity", "low")).lower(), 1.0)
    confidence = float(issue.get("confidence", 0.5) or 0.5)
    complexity = COMPLEXITY_WEIGHT.get(str(issue.get("estimated_fix_effort", "medium")).lower(), 2.0)
    module = str(issue.get("module", "")).lower()
    module_rank = next((index for index, key in enumerate(MODULE_ORDER) if key in module), len(MODULE_ORDER))
    hidden_likelihood = max(0.5, 1.2 - module_rank * 0.05)
    return severity * confidence * hidden_likelihood / complexity


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
    test_effectiveness = 85 if tests else 40
    changed_files = len(set(re.findall(r"^\+\+\+ b/(.+)$", diff, flags=re.M)))
    token_efficiency = min(100, int(3 / max(1, changed_files) * 100))
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
        "# ShopHub 设计实现一致性检查与修复报告",
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
            "  - `mvn -f code/pom.xml test`",
            "  - `mvn -f code/pom.xml install`",
            "  - `mvn -f test-cases/pom.xml test`",
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
    paths = RunnerPaths(root)
    state = load_state(paths)
    missing = check_competition_layout(root)
    buckets = open_issues_by_severity(root)
    high_medium_open = len(buckets["high"]) + len(buckets["medium"])
    last_full_test = state.get("last_full_test")

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
    if no_tests and not buckets["all"] and last_full_test in {"skipped", "partial_skipped", "passed"}:
        return True, "no_open_issues_tests_not_required"
    if not buckets["all"] and last_full_test == "failed":
        return True, "tests_failed_without_open_issue"
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
        run_baseline_tests(root, timeout=args.timeout, no_tests=args.no_tests)
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
    elif args.command == "report":
        print(generate_report(root))
    elif args.command == "status":
        command_status(root)
    else:
        parser.error(f"Unknown command {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
