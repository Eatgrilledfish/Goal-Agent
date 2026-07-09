#!/usr/bin/env python3
"""Phase 4: map each RFC requirement to candidate code locations.

Uses protocol keywords, RFC names, function names, file paths, comments,
constants and macros to score code symbols against a requirement, then assigns
a trace_status of linked / unlinked / ambiguous (section 7.3 / 9.6).

Output: .agent-work/rfc_code_trace.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import rfc_common as rc

STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "is", "are",
    "be", "must", "shall", "should", "may", "not", "any", "all", "this", "that",
    "with", "when", "if", "by", "as", "at", "from", "into", "such", "its", "it",
}
GENERIC_MAX_FILES = 120
GENERIC_MAX_TERMS = 40
_FILE_TEXT_CACHE: dict[str, str] = {}
_GENERIC_FILE_SELECTION_CACHE: dict[str, list[dict]] = {}


def file_search_text(file_record: dict) -> str:
    """Cached coarse text for generic mapping prefilter."""
    file_rel = file_record.get("file", "")
    cached = _FILE_TEXT_CACHE.get(file_rel)
    if cached is not None:
        return cached
    parts = [file_rel, " ".join(file_record.get("topics", []))]
    for sym in file_record.get("symbols", []):
        parts.append(sym.get("name", ""))
        parts.append(sym.get("signature", ""))
        # Enough to catch comments like "do not support DHCPv6" without
        # turning generic fallback into a full repository text scan.
        parts.append(sym.get("snippet", "")[:1200])
    text = " ".join(parts).lower()
    _FILE_TEXT_CACHE[file_rel] = text
    return text


def select_generic_files(terms: list[str], files: list[dict], cache_key: str = "") -> list[dict]:
    """Pick a bounded set of files for cross-project fallback mapping."""
    if cache_key and cache_key in _GENERIC_FILE_SELECTION_CACHE:
        return _GENERIC_FILE_SELECTION_CACHE[cache_key]
    useful_terms = [t for t in terms if len(t) > 3][:GENERIC_MAX_TERMS]
    scored: list[tuple[int, dict]] = []
    for f in files:
        file_rel = f.get("file", "").lower()
        topics = " ".join(f.get("topics", [])).lower()
        text = file_search_text(f)
        score = 0
        for term in useful_terms:
            if term in file_rel:
                score += 4
            elif term in topics:
                score += 3
            elif term in text:
                score += 1
        if score:
            scored.append((score, f))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [f for _, f in scored[:GENERIC_MAX_FILES]]
    if cache_key:
        _GENERIC_FILE_SELECTION_CACHE[cache_key] = selected
    return selected


def requirement_terms(req: dict) -> list[str]:
    text = " ".join([
        req.get("rfc", ""),
        " ".join(req.get("keywords", [])),
        req.get("topic", ""),
        req.get("requirement_text", ""),
    ]).lower()
    raw = re.findall(r"[a-z0-9_]+", text)
    terms = [t for t in raw if t not in STOPWORDS and len(t) > 2]
    # dedup preserving order
    seen: set[str] = set()
    out = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def symbol_text(sym: dict, file_rel: str, topics: list[str]) -> str:
    return " ".join([
        file_rel,
        sym.get("name", ""),
        sym.get("signature", ""),
        sym.get("snippet", "")[:4000],
        " ".join(topics),
    ]).lower()


def score_symbol(terms: list[str], sym: dict, file_rel: str, topics: list[str],
                 domain_matched: bool = True) -> tuple[float, str]:
    hay = symbol_text(sym, file_rel, topics)
    hits = 0
    reasons = []
    name = sym.get("name", "").lower()
    snippet = sym.get("snippet", "").lower()
    for term in terms:
        if term in name:
            hits += 2
            reasons.append(f"symbol name matches '{term}'")
        elif term in snippet:
            hits += 1
            if not reasons:
                reasons.append(f"snippet/comment matches '{term}'")
        elif term in hay:
            hits += 1
            if not reasons:
                reasons.append(f"path/topic matches '{term}'")
    # File-path protocol match is a strong signal.
    file_lower = file_rel.lower()
    for term in terms:
        if len(term) > 3 and term in file_lower and term not in name:
            hits += 1
            reasons.append(f"file path contains '{term}'")
    confidence = min(0.95, hits / 8.0 + (0.1 if hits else 0.0))
    if not domain_matched:
        confidence = min(0.80, confidence - 0.10)
    if not hits:
        return 0.0, ""
    return round(confidence, 3), "; ".join(reasons[:3])


def map_requirement(req: dict, code_index: dict, domain_map: dict) -> dict:
    rfc = req.get("rfc")
    domain = rc.resolve_rfc_domain(rfc, domain_map)
    domain = domain or {}
    req_id = req.get("requirement_id")

    terms = requirement_terms(req)
    candidates: list[dict] = []
    all_files = code_index.get("files", [])
    domain_files = (
        [f for f in all_files if rc.file_matches_domain(f.get("file", ""), domain)]
        if domain else []
    )
    source_files = domain_files
    domain_matched = True
    trace_notes: list[str] = []
    if not source_files:
        # Generic fallback for unseen projects/RFCs: do not give up just
        # because rfc_domain_map lacks a project-specific path. Search the
        # indexed code with a stricter threshold and tag the trace so the
        # validator can apply stronger false-positive controls.
        if domain:
            domain_terms = [rfc or ""]
            domain_terms.extend(domain.get("topics", []))
            domain_terms.extend(domain.get("keywords", []))
            prefilter_terms = requirement_terms({
                "rfc": rfc,
                "keywords": domain_terms,
                "topic": req.get("topic", ""),
                "requirement_text": "",
            })
        else:
            prefilter_terms = terms
        cache_key = f"{rfc}:{','.join(sorted(set(prefilter_terms))[:20])}" if rfc else ""
        source_files = select_generic_files(prefilter_terms or terms, all_files, cache_key=cache_key)
        domain_matched = False
        if domain:
            trace_notes.append("domain code paths had no concrete anchors; used generic keyword/snippet search")
        else:
            trace_notes.append(f"RFC {rfc} not in domain map; used generic keyword/snippet search")

    for f in source_files:
        file_rel = f["file"]
        for sym in f.get("symbols", []):
            conf, reason = score_symbol(
                terms, sym, file_rel, f.get("topics", []),
                domain_matched=domain_matched,
            )
            threshold = 0.2 if domain_matched else 0.45
            if conf >= threshold:
                candidates.append({
                    "file": file_rel,
                    "symbol": sym.get("name", ""),
                    "line_start": sym.get("line_start", 0),
                    "line_end": sym.get("line_end", sym.get("line_start", 0)),
                    "snippet": sym.get("snippet", ""),
                    "confidence": conf,
                    "reason": reason,
                    "domain_matched": domain_matched,
                })

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    candidates = candidates[:5]

    # Validation: domain-scoped matches must still relate to the RFC protocol
    # domain. Generic fallback matches keep their explicit domain_matched=false
    # marker and are judged more strictly downstream.
    if domain_matched:
        candidates = [c for c in candidates if rc.file_matches_domain(c["file"], domain)]

    if not candidates:
        status = "unlinked"
    elif len(candidates) == 1:
        status = "linked"
    elif candidates[0]["confidence"] - candidates[-1]["confidence"] < 0.05:
        status = "ambiguous"
    else:
        status = "linked"

    return {
        "requirement_id": req_id,
        "rfc": rfc,
        "section": req.get("section"),
        "candidate_code_locations": candidates,
        "trace_status": status,
        "protocol_area": domain.get("protocol_area"),
        "domain_topics": domain.get("topics", []),
        "mapping_strategy": "domain" if domain_matched else "generic_keyword_snippet",
        "trace_note": "; ".join(trace_notes),
    }


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Map RFC requirements to code.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    work = rc.agent_work_dir(Path(args.code_root))
    req_path = work / "rfc_requirements.json"
    index_path = work / "code_index.json"
    if not req_path.exists() or not index_path.exists():
        print("[mapper] missing upstream artifacts", file=sys.stderr)
        return 0

    reqs_doc = rc.load_json(req_path)
    code_index = rc.load_json(index_path)
    domain_map = rc.load_config("rfc_domain_map.json")

    traces = [map_requirement(r, code_index, domain_map)
              for r in reqs_doc.get("requirements", [])]
    rc.save_json(work / "rfc_code_trace.json", {
        "mapped_at": rc.now_iso(),
        "requirement_count": len(traces),
        "traces": traces,
    })
    linked = sum(1 for t in traces if t["trace_status"] == "linked")
    unlinked = sum(1 for t in traces if t["trace_status"] == "unlinked")
    ambiguous = sum(1 for t in traces if t["trace_status"] == "ambiguous")
    print(f"[mapper] linked={linked} ambiguous={ambiguous} unlinked={unlinked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
