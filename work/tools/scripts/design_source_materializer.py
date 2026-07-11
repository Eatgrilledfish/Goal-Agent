#!/usr/bin/env python3
"""Materialize a model-selected design-source plan without semantic analysis.

The running opencode agent reads a catalog/benchmark and decides which supplied
files or authoritative URLs are design sources. This helper only validates that
plan, copies or fetches the selected bytes into a read-only review bundle, and
records hashes/provenance. It does not parse requirements or create findings.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import agent_common as ac


TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".adoc", ".asc", ".json", ".yaml", ".yml", ".toml"}
ARTIFACT_KINDS = {"inventory", "claims"}


class _VisibleHTML(HTMLParser):
    """Convert a generic HTML design page to stable, visible plain text."""

    _ignored = {"script", "style", "svg", "noscript", "template"}
    _blocks = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
        "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in self._ignored:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self._blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _normalise_document_bytes(data: bytes, content_type: str = "") -> tuple[bytes, str]:
    """Keep ordinary text unchanged and turn HTML responses into reviewable text."""
    _validate_text_bytes(data)
    text = data.decode("utf-8")
    prefix = text.lstrip()[:256].lower()
    is_html = "html" in content_type.lower() or prefix.startswith("<!doctype html") or prefix.startswith("<html")
    if not is_html:
        return data, "none"
    parser = _VisibleHTML()
    parser.feed(text)
    parser.close()
    lines: list[str] = []
    blank = False
    for raw in "".join(parser.parts).splitlines():
        line = " ".join(raw.split())
        if line:
            lines.append(line)
            blank = False
        elif lines and not blank:
            lines.append("")
            blank = True
    rendered = "\n".join(lines).strip() + "\n"
    return rendered.encode("utf-8"), "html_visible_text"


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _target_path(output_root: Path, relative: str) -> Path | None:
    if not relative or Path(relative).suffix.lower() not in TEXT_SUFFIXES:
        return None
    return ac.contained_path(output_root, relative)


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


_CATALOG_URL_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:https://)?(?:www\.)?"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z0-9-]+"
    r"(?::\d+)?(?:/[^\s<>\"'\]\)}]*)?",
    re.IGNORECASE,
)


def _canonical_catalog_url(value: str) -> tuple[str, int | None, str, str, str] | None:
    candidate = value.rstrip(".,;:!?")
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "/").rstrip("/") or "/"
    return host, port, path, parsed.query, parsed.fragment


def _catalog_cites_location(kind: str, location: str, quote: str) -> bool:
    """Bind a selected source to an equal path/URL token in exact catalog evidence."""
    normalized_quote = quote.replace("\\", "/")
    if kind == "url":
        expected = _canonical_catalog_url(location)
        return bool(expected) and any(
            _canonical_catalog_url(match.group(0)) == expected
            for match in _CATALOG_URL_RE.finditer(normalized_quote)
        )
    if kind == "local":
        path = Path(location)
        if path.is_absolute():
            return False
        key = path.as_posix()
        while key.startswith("./"):
            key = key[2:]
        if not key:
            return False
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_./-])(?:\./)?{re.escape(key)}"
            rf"(?![A-Za-z0-9_./-])"
        )
        return pattern.search(normalized_quote) is not None
    return False


def _fetch(url: str, maximum_bytes: int, timeout_seconds: int) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("only absolute https URLs are allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "goal-agent-design-source/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        data = response.read(maximum_bytes + 1)
        content_type = str(response.headers.get("Content-Type") or "")
    if len(data) > maximum_bytes:
        raise ValueError(f"remote design source exceeds {maximum_bytes} bytes")
    return data, content_type


def _validate_text_bytes(data: bytes) -> None:
    if b"\x00" in data:
        raise ValueError("design source is not a supported text document")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("design source is not valid UTF-8 text") from exc


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_source_path(design_root: Path, value: Any) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_ref.path must be a non-empty string")
    candidate = ac.contained_path(design_root, value)
    if candidate is None:
        raise ValueError(f"source_ref.path is outside design root: {value}")
    if not candidate.is_file():
        raise ValueError(f"source_ref.path does not name a file: {value}")
    return candidate, candidate.relative_to(design_root).as_posix()


def _strict_line(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"source_ref.{field} must be an integer")
    return value


_NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+\S")


def _section_heading(lines: list[str], start: int, relative_path: str) -> str:
    """Return a mechanical nearby heading, never a semantic interpretation."""
    for index in range(min(start - 1, len(lines) - 1), -1, -1):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
        if index + 1 < len(lines):
            underline = lines[index + 1].strip()
            if len(underline) >= 3 and set(underline) in ({"="}, {"-"}):
                return stripped
        if len(stripped) <= 200 and _NUMBERED_HEADING.match(stripped):
            return stripped
    return f"{relative_path} lines {start}"


def materialize_source_ref(
    item: dict[str, Any], design_root: Path, *, include_quote: bool,
) -> dict[str, Any]:
    """Materialize one model-selected path/range without adding semantic fields.

    `source_ref` is authoritative. The returned object contains the canonical
    nested source_ref plus deterministic compatibility fields used downstream.
    """
    if not isinstance(item, dict):
        raise ValueError("source item must be an object")
    raw_ref = item.get("source_ref")
    if not isinstance(raw_ref, dict):
        raise ValueError("source_ref must be an object")
    source_path, relative_path = _canonical_source_path(design_root, raw_ref.get("path"))
    start = _strict_line(raw_ref.get("line_start"), "line_start")
    end = _strict_line(raw_ref.get("line_end"), "line_end")
    if start < 1 or end < start:
        raise ValueError(f"source_ref has invalid line range {start}-{end}")
    try:
        source_text = source_path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"source_ref file is not valid UTF-8: {relative_path}") from exc
    if "\x00" in source_text:
        raise ValueError(f"source_ref file contains binary NUL bytes: {relative_path}")
    lines = source_text.splitlines()
    if end > len(lines):
        raise ValueError(
            f"source_ref line range {start}-{end} exceeds {len(lines)} lines: {relative_path}"
        )

    materialized = copy.deepcopy(item)
    source_ref = {
        "path": relative_path,
        "line_start": start,
        "line_end": end,
        "source_sha256": ac.sha256_file(source_path),
    }
    materialized["source_ref"] = source_ref
    materialized["path"] = relative_path
    materialized["line_start"] = start
    materialized["line_end"] = end
    materialized["section"] = _section_heading(lines, start, relative_path)
    if include_quote:
        materialized["quote"] = "\n".join(lines[start - 1:end])
    else:
        materialized.pop("quote", None)
    return materialized


def materialize_claims(values: list[dict[str, Any]], design_root: Path) -> list[dict[str, Any]]:
    materialized: list[dict[str, Any]] = []
    for value in values:
        claim = materialize_source_ref(value, design_root, include_quote=True)
        claim["document"] = Path(str(claim["path"])).name
        materialized.append(claim)
    return materialized


def _materialize_inventory_section(section: dict[str, Any], design_root: Path) -> dict[str, Any]:
    materialized = materialize_source_ref(section, design_root, include_quote=False)
    materialized["heading"] = materialized.pop("section")
    return materialized


def materialize_inventory(value: dict[str, Any], design_root: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("design inventory must be an object")
    groups = value.get("document_groups")
    if not isinstance(groups, list):
        raise ValueError("design inventory document_groups must be an array")
    materialized = copy.deepcopy(value)
    output_groups: list[dict[str, Any]] = []
    for index, raw_group in enumerate(groups, start=1):
        if not isinstance(raw_group, dict):
            raise ValueError(f"document_groups[{index}] must be an object")
        group = copy.deepcopy(raw_group)
        evidence = group.get("scope_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(f"document_groups[{index}].scope_evidence must be an object")
        group["scope_evidence"] = materialize_source_ref(
            evidence, design_root, include_quote=True,
        )
        sections = group.get("sections")
        if not isinstance(sections, list):
            raise ValueError(f"document_groups[{index}].sections must be an array")
        materialized_sections: list[dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict):
                raise ValueError(f"document_groups[{index}].sections entries must be objects")
            materialized_sections.append(_materialize_inventory_section(section, design_root))
        group["sections"] = materialized_sections
        group.pop("group_sha256", None)
        group["group_sha256"] = _canonical_json_sha256(group)
        output_groups.append(group)
    materialized["document_groups"] = output_groups
    return materialized


def _load_claim_input(path: Path) -> list[dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ValueError("; ".join(errors))
    return values


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def materialize_artifact(args: argparse.Namespace) -> int:
    design_root = Path(args.design_root).resolve()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    trace_path = Path(args.trace).resolve() if args.trace else None
    errors: list[str] = []
    count = 0
    if not design_root.is_dir():
        errors.append(f"design root is not a directory: {design_root}")
    if not input_path.is_file():
        errors.append(f"artifact input is missing: {input_path}")
    if _is_within(design_root, output_path):
        errors.append("artifact output must be outside the read-only design root")
    if trace_path and _is_within(design_root, trace_path):
        errors.append("artifact trace must be outside the read-only design root")
    if not errors:
        try:
            if args.materialize == "inventory":
                raw = ac.load_json(input_path)
                output = materialize_inventory(raw, design_root)
                count = len(output.get("document_groups", []))
                ac.save_json(output_path, output)
            else:
                output_claims = materialize_claims(_load_claim_input(input_path), design_root)
                count = len(output_claims)
                ac.ensure_dir(output_path.parent)
                output_path.write_text(
                    "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in output_claims),
                    encoding="utf-8",
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    report = {
        "materialized_at": ac.now_iso(),
        "passed": not errors,
        "artifact_kind": args.materialize,
        "semantic_analysis_performed": False,
        "design_root": str(design_root),
        "input_path": str(input_path),
        "output_path": str(output_path),
        "objects": count,
        "errors": errors,
    }
    if trace_path:
        ac.save_json(trace_path, report)
    print(json.dumps({"passed": not errors, "objects": count, "errors": len(errors)}))
    return 0 if not errors else 1


def materialize(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root).resolve()
    output_root = Path(args.output_root).resolve()
    plan_path = Path(args.plan).resolve()
    manifest_path = Path(args.manifest).resolve()
    approval_log = Path(args.approval_log).resolve() if args.approval_log else None
    errors: list[str] = []
    records: list[dict[str, Any]] = []

    if not source_root.is_dir():
        errors.append(f"source root is not a directory: {source_root}")
    if not plan_path.is_file():
        errors.append(f"design source plan is missing: {plan_path}")
    for label, path in (
        ("output root", output_root),
        ("design source plan", plan_path),
        ("manifest", manifest_path),
        ("approval log", approval_log),
    ):
        if path is not None and _paths_overlap(path, source_root):
            errors.append(f"{label} must be outside the supplied source root: {path}")
    if errors:
        if not _paths_overlap(manifest_path, source_root):
            ac.save_json(manifest_path, {
                "materialized_at": ac.now_iso(), "passed": False,
                "errors": errors, "sources": [],
            })
        print(json.dumps({"passed": False, "sources": 0, "errors": len(errors)}))
        return 2
    ac.ensure_dir(output_root)

    try:
        plan = ac.load_json(plan_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # A model-authored plan is untrusted input.  Preserve the normal
        # machine-readable failure contract instead of leaking a traceback and
        # leaving the orchestrator without a repairable manifest.
        errors.append(f"could not load design source plan: {exc}")
        plan = {}
    if not isinstance(plan, dict) or not isinstance(plan.get("sources"), list):
        errors.append("plan must be an object with a sources array")
        sources: list[Any] = []
    else:
        sources = plan["sources"]

    catalog_value = str(plan.get("catalog_path") or "") if isinstance(plan, dict) else ""
    catalog = ac.contained_path(source_root, catalog_value)
    if not catalog or not catalog.is_file():
        errors.append("plan catalog_path must name a file under source_root")
    else:
        catalog_target = output_root / "catalog" / catalog.name
        ac.ensure_dir(catalog_target.parent)
        shutil.copyfile(catalog, catalog_target)
        records.append({
            "source_id": "catalog",
            "kind": "local",
            "location": str(catalog),
            "bundle_path": ac.relative_path(output_root, catalog_target),
            "sha256": ac.sha256_file(catalog_target),
            "catalog_evidence": {"path": catalog_value},
        })

    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    for index, source in enumerate(sources, start=1):
        label = f"sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{label} must be an object")
            continue
        if any(not _nonempty(source.get(field)) for field in ("source_id", "kind", "location", "output_path", "catalog_evidence")):
            errors.append(f"{label} needs source_id/kind/location/output_path/catalog_evidence")
            continue
        source_id = str(source["source_id"])
        kind = str(source["kind"])
        location = str(source["location"])
        output_path = str(source["output_path"])
        target = _target_path(output_root, output_path)
        if source_id in seen_ids:
            errors.append(f"{label} duplicates source_id {source_id!r}")
            continue
        if output_path in seen_targets:
            errors.append(f"{label} duplicates output_path {output_path!r}")
            continue
        if target is None:
            errors.append(f"{label} output_path must stay under output_root and use a supported text suffix")
            continue
        catalog_evidence = source.get("catalog_evidence")
        evidence_errors = ac.validate_source_evidence(
            catalog_evidence, source_root, f"{label}.catalog_evidence", "quote",
        )
        evidence_path = ac.contained_path(
            source_root,
            str(catalog_evidence.get("path") or "") if isinstance(catalog_evidence, dict) else "",
        )
        if catalog and evidence_path != catalog:
            evidence_errors.append(f"{label}.catalog_evidence must cite plan catalog_path")
        if not evidence_errors and not _catalog_cites_location(
            kind, location, str(catalog_evidence.get("quote") or ""),
        ):
            evidence_errors.append(
                f"{label}.location is not cited by catalog_evidence.quote"
            )
        if evidence_errors:
            errors.extend(evidence_errors)
            continue
        seen_ids.add(source_id)
        seen_targets.add(output_path)
        ac.ensure_dir(target.parent)
        normalization = "none"
        try:
            if kind == "local":
                local = ac.contained_path(source_root, location)
                if not local or not local.is_file():
                    raise ValueError("local source is missing or outside source_root")
                data, normalization = _normalise_document_bytes(local.read_bytes())
                target.write_bytes(data)
            elif kind == "url":
                if not args.allow_network:
                    raise ValueError("URL source requires --allow-network after the agent records approval policy")
                if approval_log is None or approval_log.parent != output_root.parent:
                    raise ValueError("URL source requires --approval-log under the session state root")
                ac.append_jsonl(approval_log, {
                    "recorded_at": ac.now_iso(),
                    "actor": "design_source_materializer",
                    "action": "read_only_design_fetch",
                    "scope": location,
                    "decision": "auto_approved",
                    "rationale": (
                        f"Explicit --allow-network; HTTPS-only fetch limited to {args.max_bytes} bytes "
                        "and written under session state."
                    ),
                })
                data, content_type = _fetch(location, args.max_bytes, args.timeout_seconds)
                data, normalization = _normalise_document_bytes(data, content_type)
                target.write_bytes(data)
            else:
                raise ValueError("kind must be local or url")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(f"{label} could not be materialized: {exc}")
            if target.exists():
                target.unlink()
            continue
        records.append({
            "source_id": source_id,
            "kind": kind,
            "location": location,
            "bundle_path": ac.relative_path(output_root, target),
            "sha256": ac.sha256_file(target),
            "bytes": target.stat().st_size,
            "normalization": normalization,
            "catalog_evidence": source["catalog_evidence"],
        })

    manifest = {
        "materialized_at": ac.now_iso(),
        "passed": not errors and len(records) > 1,
        "semantic_analysis_performed": False,
        "source_root": str(source_root),
        "output_root": str(output_root),
        "plan_path": str(plan_path),
        "plan_sha256": ac.sha256_file(plan_path),
        "sources": records,
        "errors": errors,
    }
    ac.save_json(manifest_path, manifest)
    print(json.dumps({"passed": manifest["passed"], "sources": len(records), "errors": len(errors)}))
    return 0 if manifest["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize a model-authored design-source plan.")
    parser.add_argument("--materialize", choices=sorted(ARTIFACT_KINDS), default=None)
    parser.add_argument("--design-root", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--trace", default=None)
    parser.add_argument("--source-root", default=None)
    parser.add_argument("--plan", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--approval-log", default=None)
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    if args.materialize:
        missing = [name for name in ("design_root", "input", "output") if not getattr(args, name)]
        if missing:
            parser.error(f"--materialize requires: {', '.join('--' + name.replace('_', '-') for name in missing)}")
        return materialize_artifact(args)
    missing = [name for name in ("source_root", "plan", "output_root", "manifest") if not getattr(args, name)]
    if missing:
        parser.error(
            "source-plan mode requires: "
            + ", ".join("--" + name.replace("_", "-") for name in missing)
        )
    return materialize(args)


if __name__ == "__main__":
    raise SystemExit(main())
