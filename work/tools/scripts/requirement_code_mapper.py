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
    return " ".join([file_rel, sym.get("name", ""), " ".join(topics)]).lower()


def score_symbol(terms: list[str], sym: dict, file_rel: str, topics: list[str]) -> tuple[float, str]:
    hay = symbol_text(sym, file_rel, topics)
    hits = 0
    reasons = []
    name = sym.get("name", "").lower()
    for term in terms:
        if term in name:
            hits += 2
            reasons.append(f"symbol name matches '{term}'")
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
    if not hits:
        return 0.0, ""
    return round(confidence, 3), "; ".join(reasons[:3])


def map_requirement(req: dict, code_index: dict, domain_map: dict) -> dict:
    rfc = req.get("rfc")
    domain = rc.resolve_rfc_domain(rfc, domain_map)
    req_id = req.get("requirement_id")

    # Step (a): resolve the RFC's protocol domain from rfc_domain_map.json.
    # Without a domain entry we cannot anchor the requirement to code without
    # falling back to global keyword matching -- which is exactly what produced
    # bogus links like RFC3122 -> tcp_ecn.c. Be honest and mark it unlinked.
    if not domain:
        return {
            "requirement_id": req_id,
            "rfc": rfc,
            "section": req.get("section"),
            "candidate_code_locations": [],
            "trace_status": "unlinked",
            "trace_note": f"RFC {rfc} not in domain map; cannot anchor to a protocol domain.",
        }

    # Step (b): only consider code files that belong to this RFC's domain, then
    # rank their symbols by keyword relevance to the requirement text.
    terms = requirement_terms(req)
    candidates: list[dict] = []
    domain_files = [f for f in code_index.get("files", [])
                    if rc.file_matches_domain(f.get("file", ""), domain)]

    for f in domain_files:
        file_rel = f["file"]
        for sym in f.get("symbols", []):
            conf, reason = score_symbol(terms, sym, file_rel, f.get("topics", []))
            if conf >= 0.2:
                candidates.append({
                    "file": file_rel,
                    "symbol": sym.get("name", ""),
                    "line_start": sym.get("line_start", 0),
                    "line_end": sym.get("line_end", sym.get("line_start", 0)),
                    "snippet": sym.get("snippet", ""),
                    "confidence": conf,
                    "reason": reason,
                })

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    candidates = candidates[:5]

    # Validation: every surviving location's path must still relate to the
    # RFC protocol domain. (They do by construction, but this is the explicit
    # guard the spec asks for -- never trust a candidate whose file drifted.)
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
