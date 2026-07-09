#!/usr/bin/env python3
"""Phase 3: index F-Stack C/C++/header source files.

Scans the code root for C-family sources and build files, extracts functions,
macros, enums, typedefs, configuration items and comments, and tags each file
with protocol topics derived from its path and content (section 7.2 / 9.5).

Outputs:
  .agent-work/code_index.json        (per-file index)
  .agent-work/code_symbols.jsonl     (one symbol record per line)
  .agent-work/code_topic_index.json  (topic -> files/symbols map)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import rfc_common as rc

FUNCTION_RE = re.compile(
    r"^(?P<ret>(?:[A-Za-z_][\w\s\*]*?))\b(?P<name>[A-Za-z_]\w+)\s*\([^;]*$"
)
DEFINE_RE = re.compile(r"^\s*#\s*define\s+(?P<name>[A-Za-z_]\w+)")
ENUM_RE = re.compile(r"^\s*enum\s+(?P<name>[A-Za-z_]\w+)")
TYPEDEF_RE = re.compile(r"^\s*typedef\s+.*?\b(?P<name>[A-Za-z_]\w+)\s*;")
PROTO_RE = re.compile(r"^(?P<ret>[A-Za-z_][\w\s\*]*?)\b(?P<name>[A-Za-z_]\w+)\s*\([^)]*\)\s*;")

TOPIC_KEYWORDS = {
    "ipv6": ["ipv6", "in6", "ip6", "inet6", "netinet6"],
    "neighbor_discovery": ["nd6", "neighbor", "nd_", "nd6_"],
    "icmpv6": ["icmp6", "icmpv6", "mld6", "mld"],
    "fragment": ["frag", "ip_frag", "fragment"],
    "dhcpv6": ["dhcp6", "dhcpv6", "duid"],
    "multicast": ["mld", "multicast", "mcast"],
    "socket": ["socket", "sockbuf", "in_pcb", "uipc"],
    "dpdk": ["dpdk", "rte_", "ff_dpdk", "kni"],
    "tcp": ["tcp_", "tcp_", "in_rmx"],
    "udp": ["udp_", "in_udp"],
}

PRIORITY_DIRS = [
    "freebsd/netinet6",
    "freebsd/netinet",
    "dpdk/lib",
    "lib",
    "include",
]


def file_topics(rel_path: str, content: str) -> list[str]:
    haystack = (rel_path + " " + content[:4000]).lower()
    found = []
    for topic, kws in TOPIC_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            found.append(topic)
    return found or ["general"]


def find_brace_end(lines: list[str], start: int) -> int:
    depth = 0
    started = False
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return i + 1
    return start + 1


# Cap stored function-body snippets so code_index.json stays bounded even for
# huge repositories (F-Stack has ~13k files). 64 lines / 4 KiB is plenty for a
# detector to reason about a function's control flow.
SNIPPET_MAX_LINES = 64
SNIPPET_MAX_CHARS = 4096


def make_snippet(lines: list[str], line_start: int, line_end: int) -> str:
    """Return the function-body source for [line_start, line_end], bounded."""
    s = max(0, (line_start or 1) - 1)
    e = max(s + 1, line_end or (s + 1))
    chunk = lines[s:e]
    if len(chunk) > SNIPPET_MAX_LINES:
        chunk = chunk[:SNIPPET_MAX_LINES]
    text = "\n".join(chunk)
    if len(text) > SNIPPET_MAX_CHARS:
        text = text[:SNIPPET_MAX_CHARS]
    return text


def index_file(path: Path, code_root: Path) -> dict:
    rel = str(path.relative_to(code_root))
    try:
        text = rc.read_text(path)
    except OSError:
        return {"file": rel, "language": "c", "symbols": [], "macros": [], "topics": []}
    lines = text.splitlines()
    symbols: list[dict] = []
    macros: list[dict] = []
    enums: list[dict] = []
    typedefs: list[dict] = []

    for i, line in enumerate(lines, start=1):
        m = DEFINE_RE.match(line)
        if m:
            macros.append({"name": m.group("name"), "line": i})
            continue
        m = ENUM_RE.match(line)
        if m:
            enums.append({"name": m.group("name"), "line": i})
            continue
        m = TYPEDEF_RE.match(line)
        if m:
            typedefs.append({"name": m.group("name"), "line": i})
            continue
        m = PROTO_RE.match(line)
        if m and "(" in line and line.rstrip().endswith(";"):
            symbols.append({"name": m.group("name"), "kind": "prototype",
                            "line_start": i, "line_end": i,
                            "snippet": lines[i - 1].strip()})
            continue
        # Function definition: signature line followed by '{' on same or next line.
        if "(" in line and not line.strip().endswith(";") and not line.strip().startswith("#"):
            fm = FUNCTION_RE.match(line)
            if fm and fm.group("name") not in {"if", "for", "while", "switch", "sizeof", "return"}:
                brace_line = i - 1
                end = find_brace_end(lines, brace_line)
                if end > i:
                    symbols.append({
                        "name": fm.group("name"),
                        "kind": "function",
                        "line_start": i,
                        "line_end": end,
                        "snippet": make_snippet(lines, i, end),
                    })

    topics = file_topics(rel, text)
    return {
        "file": rel,
        "language": "c" if path.suffix in (".c", ".h") else "cpp",
        "symbols": symbols,
        "macros": macros,
        "enums": enums,
        "typedefs": typedefs,
        "topics": topics,
    }


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Index F-Stack C/C++ source files.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    code_root = Path(args.code_root)
    work = rc.agent_work_dir(code_root)

    # Verify the code root and the expected protocol directories before
    # scanning. The F-Stack layout is code_root/freebsd/netinet6/nd6.c etc.;
    # a missing root means upstream passed the wrong --code-root, so we report
    # exactly what was checked instead of silently emitting an empty index.
    if not code_root.exists():
        top = list(code_root.parent.glob("*")) if code_root.parent.exists() else []
        siblings = [p.name for p in top if p.is_dir()]
        rc.save_json(work / "code_index.json", {"indexed_at": rc.now_iso(),
                   "status": "blocked", "reason": "code root missing",
                   "code_root": str(code_root),
                   "siblings_of_code_root": siblings, "files": []})
        print(f"[indexer] code root missing: {code_root}", file=sys.stderr)
        print(f"[indexer] siblings of code root: {siblings}", file=sys.stderr)
        return 0

    # ls-style verification of the directories we actually intend to walk.
    top_entries = sorted(p.name for p in code_root.iterdir()) if code_root.is_dir() else []
    print(f"[indexer] code_root OK: {code_root} ({len(top_entries)} top-level entries)")
    for sub in PRIORITY_DIRS:
        d = code_root / sub
        flag = "OK" if d.exists() else "absent"
        print(f"[indexer]   {flag}: {sub}")

    files_index: list[dict] = []
    symbols_lines: list[str] = []
    topic_index: dict[str, list[str]] = {}

    # Walk priority dirs first, then the rest, dedup by path.
    seen: set[str] = set()
    candidates: list[Path] = []
    for sub in PRIORITY_DIRS:
        d = code_root / sub
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file() and p.suffix in rc.C_SOURCE_SUFFIXES:
                    candidates.append(p)
    for p in code_root.rglob("*"):
        if p.is_file() and p.suffix in rc.C_SOURCE_SUFFIXES:
            candidates.append(p)

    for path in candidates:
        rel = str(path.relative_to(code_root))
        if rel in seen:
            continue
        seen.add(rel)
        record = index_file(path, code_root)
        files_index.append(record)
        for sym in record["symbols"]:
            sym_record = {"file": rel, **sym, "topics": record["topics"]}
            symbols_lines.append(json.dumps(sym_record, ensure_ascii=False))
        for topic in record["topics"]:
            topic_index.setdefault(topic, []).append(rel)

    rc.save_json(work / "code_index.json", {
        "indexed_at": rc.now_iso(),
        "code_root": str(code_root),
        "file_count": len(files_index),
        "files": files_index,
    })
    (work / "code_symbols.jsonl").write_text("\n".join(symbols_lines) + ("\n" if symbols_lines else ""),
                                             encoding="utf-8")
    rc.save_json(work / "code_topic_index.json", {
        "indexed_at": rc.now_iso(),
        "topics": topic_index,
    })
    total_syms = sum(len(f["symbols"]) for f in files_index)
    print(f"[indexer] indexed {len(files_index)} files, {total_syms} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
