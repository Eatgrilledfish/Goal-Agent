#!/usr/bin/env python3
"""Phase 1a: read ``Difference/benchmark.md`` and index its RFC set.

Outputs ``.agent-work/benchmark_index.json`` describing the RFCs referenced by
the benchmark, the F-Stack commit/version info, and the protocol areas to
scan (section 9.2 of FIX-rfc-migration.md).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import rfc_common as rc

RFC_TOKEN_RE = re.compile(r"\bRFC\s*0*(\d{1,5})\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)>\]]+")
COMMIT_RE = re.compile(r"\b([0-9a-f]{7,40})\b")


def parse_benchmark(text: str, domain_map: dict) -> dict:
    rfcs: dict[str, dict] = {}
    for match in RFC_TOKEN_RE.finditer(text):
        num = match.group(1)
        rfc_key = f"RFC{num}"
        if rfc_key not in rfcs:
            domain = domain_map.get("domains", {}).get(rfc_key, {})
            rfcs[rfc_key] = {
                "rfc": rfc_key,
                "number": int(num),
                "protocol_area": domain.get("protocol_area", "unknown"),
                "topics": domain.get("topics", []),
                "keywords": domain.get("keywords", []),
                "code_paths": domain.get("code_paths", []),
                "title": domain.get("title", ""),
                "found_in_benchmark": True,
            }

    urls = sorted(set(URL_RE.findall(text)))
    rfc_urls = [u for u in urls if "rfc" in u.lower()]

    commits = sorted(set(COMMIT_RE.findall(text)))
    # filter out RFC numbers that matched as hex-ish
    commits = [c for c in commits if not c.isdigit()]

    sections = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+\S", stripped):
            sections.append(stripped)

    # protocol areas implied by the RFC set actually present
    areas = sorted({info["protocol_area"] for info in rfcs.values()
                    if info["protocol_area"] != "unknown"})

    return {
        "rfcs": list(rfcs.values()),
        "rfc_count": len(rfcs),
        "rfc_urls": rfc_urls,
        "other_urls": [u for u in urls if u not in rfc_urls],
        "fstack_commits": commits,
        "headings": sections,
        "protocol_areas": areas,
        "char_count": len(text),
    }


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Index benchmark.md RFC set.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    benchmark_path = Path(args.benchmark)
    work = rc.agent_work_dir(Path(args.code_root))

    if not benchmark_path.exists():
        index = {
            "parsed_at": rc.now_iso(),
            "benchmark": str(benchmark_path),
            "status": "blocked",
            "reason": "benchmark.md not found",
            "rfcs": [],
            "rfc_count": 0,
        }
        rc.save_json(work / "benchmark_index.json", index)
        print(f"[benchmark_reader] benchmark.md missing: {benchmark_path}", file=sys.stderr)
        return 0  # non-fatal; downstream phases degrade gracefully

    text = rc.read_text(benchmark_path)
    domain_map = rc.load_config("rfc_domain_map.json")
    index = parse_benchmark(text, domain_map)
    index.update({
        "parsed_at": rc.now_iso(),
        "benchmark": str(benchmark_path),
        "status": "ok",
    })
    rc.save_json(work / "benchmark_index.json", index)
    print(f"[benchmark_reader] indexed {index['rfc_count']} RFC(s); "
          f"areas={index['protocol_areas']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
