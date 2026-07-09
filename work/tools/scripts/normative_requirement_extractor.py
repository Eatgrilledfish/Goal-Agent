#!/usr/bin/env python3
"""Phase 2: extract normative requirements from cached RFC documents.

Scans each RFC markdown for RFC 2119 keywords (MUST / SHOULD / MAY ...),
records the enclosing section, requirement text, normative level, topic and
protocol area, and applies RFC supersession (section 7.1 / 9.4).

Outputs:
  .agent-work/rfc_requirements.json   (list of requirement IR records)
  .agent-work/rfc_requirements.md     (human-readable index)

This extractor never hardcodes known issues; it only derives requirements from
the RFC text plus the domain map configuration.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import rfc_common as rc

SECTION_RE = re.compile(r"^#{1,6}\s+(\d+(?:\.\d+)*\.?)\s+(.+)$")
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
REQ_ID_COUNTER: dict[str, int] = {}

# RFC 2119 boilerplate: the fixed "The key words ... are to be interpreted as
# described in RFC 2119" notice and the bare keyword enumeration appear in every
# RFC document. They define terminology rather than protocol behavior, so they
# must not be extracted as normative requirements. Matches the four blacklist
# rules in FIX-rfc-migration.md.
_KEYWORD_TOKEN_RE = re.compile(
    r"\b(MUST NOT|SHALL NOT|SHOULD NOT|MUST|SHALL|SHOULD|REQUIRED|"
    r"RECOMMENDED|OPTIONAL|MAY|NOT)\b"
)
# Filler words that survive once RFC 2119 keywords are stripped from the
# boilerplate notice / enumeration. If *only* these remain, the sentence is a
# keyword enumeration, not a requirement.
_BOILERPLATE_FILLER = {
    "the", "key", "words", "word", "are", "is", "to", "be", "interpreted",
    "as", "described", "in", "and", "or", "of", "for", "when", "they",
    "appear", "this", "that", "document", "means", "a", "an",
}


def is_rfc2119_boilerplate(text: str) -> bool:
    """True if ``text`` is the fixed RFC 2119 boilerplate, not a requirement."""
    low = text.lstrip().lower()
    if not low:
        return True
    if low.startswith("the key words"):
        return True
    if low.startswith("document are to be interpreted"):
        return True
    # The standard "... as described in RFC 2119" notice is short.
    if "rfc 2119" in low and len(text) < 200:
        return True
    # Pure keyword enumeration (e.g. ``"MUST", "MUST NOT", "REQUIRED",``):
    # strip RFC 2119 keywords + punctuation; if only filler (or nothing)
    # remains, it is a terminology list, not a behavior requirement.
    stripped = _KEYWORD_TOKEN_RE.sub(" ", text)
    stripped = re.sub(r"[^A-Za-z\s]", " ", stripped)
    words = [w for w in stripped.lower().split() if w]
    non_filler = [w for w in words if w not in _BOILERPLATE_FILLER]
    if _KEYWORD_TOKEN_RE.search(text) and not non_filler:
        return True
    return False


def current_section(lines: list[str], upto: int) -> tuple[str, str]:
    for i in range(upto, -1, -1):
        m = SECTION_RE.match(lines[i].strip())
        if m:
            section = m.group(1).rstrip(".")
            title = m.group(2).strip()
            return section, title
    return "", ""


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = SENTENCE_END_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def next_req_id(rfc: str, section: str, level: str) -> str:
    key = f"{rfc}-{section}-{level}"
    REQ_ID_COUNTER[key] = REQ_ID_COUNTER.get(key, 0) + 1
    seq = f"{REQ_ID_COUNTER[key]:03d}"
    safe_level = level.replace(" ", "-")
    return f"{rfc}-{section}-{safe_level}-{seq}"


def extract_from_doc(doc_path: Path, rfc_key: str, domain: dict,
                     superseded: set[str]) -> list[dict]:
    if not doc_path.exists():
        return []
    text = rc.read_text(doc_path)
    lines = text.splitlines()

    requirements: list[dict] = []
    full = "\n".join(lines)
    for match in rc.RFC2119_PATTERN.finditer(full):
        level = match.group(1)
        # Sentence (line) containing the keyword.
        start = full.rfind("\n", 0, match.start()) + 1
        end = full.find("\n", match.end())
        if end == -1:
            end = len(full)
        sentence = re.sub(r"\s+", " ", full[start:end]).strip()
        if not sentence or len(sentence) < 10:
            continue
        # Skip the fixed RFC 2119 boilerplate / keyword enumeration notice.
        if is_rfc2119_boilerplate(sentence):
            continue

        line_no = full.count("\n", 0, match.start()) + 1
        section, sec_title = current_section(lines, line_no - 1)

        req_id = next_req_id(rfc_key, section or "0", level)
        requirement = {
            "requirement_id": req_id,
            "rfc": rfc_key,
            "section": section,
            "title": sec_title or domain.get("title", ""),
            "normative_level": level,
            "requirement_text": sentence,
            "topic": (domain.get("topics") or ["unknown"])[0],
            "protocol_area": domain.get("protocol_area", "unknown"),
            "keywords": domain.get("keywords", []),
            "source_doc": str(doc_path),
            "source_anchor": f"section-{section}" if section else "preamble",
            "superseded_by": sorted(superseded) if superseded else [],
        }
        requirements.append(requirement)
    return requirements


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Extract RFC normative requirements.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    work = rc.agent_work_dir(Path(args.code_root))
    manifest_path = work / "rfc_manifest.json"
    if not manifest_path.exists():
        print("[normative] rfc_manifest.json missing; run load-docs first", file=sys.stderr)
        return 0
    manifest = rc.load_json(manifest_path)
    domain_map = rc.load_config("rfc_domain_map.json")
    supersession = domain_map.get("supersession", {})
    domains = domain_map.get("domains", {})

    loaded_rfcs = {e["rfc"] for e in manifest.get("rfcs", []) if e.get("status") == "ok"}

    all_reqs: list[dict] = []
    for entry in manifest.get("rfcs", []):
        rfc_key = entry["rfc"]
        if entry.get("status") != "ok":
            continue
        # Skip RFCs obsoleted by a newer RFC we also loaded.
        obsolete_by = [newer for newer, older in supersession.items() if rfc_key in older]
        if any(n in loaded_rfcs for n in obsolete_by):
            continue

        doc_path = Path(entry["doc_path"])
        superseded = set()
        for newer, older in supersession.items():
            if newer == rfc_key:
                superseded.update(older)
        domain = domains.get(rfc_key, {})
        all_reqs.extend(extract_from_doc(doc_path, rfc_key, domain, superseded))

    rc.save_json(work / "rfc_requirements.json", {
        "extracted_at": rc.now_iso(),
        "requirement_count": len(all_reqs),
        "requirements": all_reqs,
    })

    # Human-readable index.
    md_lines = ["# Normative Requirements Index", ""]
    md_lines.append(f"- Total requirements: {len(all_reqs)}")
    by_level: dict[str, int] = {}
    for r in all_reqs:
        by_level[r["normative_level"]] = by_level.get(r["normative_level"], 0) + 1
    md_lines.append("- By level: " + ", ".join(f"{k}={v}" for k, v in sorted(by_level.items())))
    md_lines.append("")
    for r in all_reqs:
        md_lines.append(f"## {r['requirement_id']}")
        md_lines.append(f"- RFC: {r['rfc']} §{r['section']} ({r['normative_level']})")
        md_lines.append(f"- Area: {r['protocol_area']} / {r['topic']}")
        md_lines.append(f"> {r['requirement_text']}")
        md_lines.append("")
    (work / "rfc_requirements.md").write_text("\n".join(md_lines), encoding="utf-8")

    print(f"[normative] extracted {len(all_reqs)} requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
