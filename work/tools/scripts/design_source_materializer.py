#!/usr/bin/env python3
"""Materialize a model-selected design-source plan without semantic analysis.

The running opencode agent reads a catalog/benchmark and decides which supplied
files or authoritative URLs are design sources. This helper only validates that
plan, copies or fetches the selected bytes into a read-only review bundle, and
records hashes/provenance. It does not parse requirements or create findings.
"""

from __future__ import annotations

import argparse
import json
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


def materialize(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root).resolve()
    output_root = ac.ensure_dir(Path(args.output_root).resolve())
    plan_path = Path(args.plan).resolve()
    manifest_path = Path(args.manifest).resolve()
    approval_log = Path(args.approval_log).resolve() if args.approval_log else None
    errors: list[str] = []
    records: list[dict[str, Any]] = []

    if not source_root.is_dir():
        errors.append(f"source root is not a directory: {source_root}")
    if not plan_path.is_file():
        errors.append(f"design source plan is missing: {plan_path}")
    if errors:
        ac.save_json(manifest_path, {"materialized_at": ac.now_iso(), "passed": False, "errors": errors, "sources": []})
        return 2

    plan = ac.load_json(plan_path)
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
        "sources": records,
        "errors": errors,
    }
    ac.save_json(manifest_path, manifest)
    print(json.dumps({"passed": manifest["passed"], "sources": len(records), "errors": len(errors)}))
    return 0 if manifest["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize a model-authored design-source plan.")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--approval-log", default=None)
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    return materialize(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
