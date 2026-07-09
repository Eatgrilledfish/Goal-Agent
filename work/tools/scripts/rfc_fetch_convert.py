#!/usr/bin/env python3
"""Phase 1b: fetch and convert RFC documents into cached markdown/txt.

Strategy (section 9.3):
  1. Prefer RFC md/txt already present under the design root.
  2. Otherwise download from rfc-editor.org.
  3. On download failure, record ``blocked`` but keep going.
  4. Cache every RFC under ``.agent-work/rfcs/`` to avoid re-downloading.

The runner calls this as part of the ``load-docs`` phase after
``benchmark_reader.py`` has produced ``benchmark_index.json``.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import rfc_common as rc

RFC_EDITOR_URL = "https://www.rfc-editor.org/rfc/rfc{num}.txt"


def find_local_rfc(design_root: Path, num: str) -> Path | None:
    patterns = [f"rfc{num}.md", f"rfc{num}.txt", f"RFC{num}.md", f"RFC{num}.txt",
                f"rfc-{num}.md", f"rfc-{num}.txt"]
    for pattern in patterns:
        for path in design_root.rglob(pattern):
            return path
    return None


def download_rfc(num: str, dest: Path) -> bool:
    url = RFC_EDITOR_URL.format(num=num)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "goal-agent-rfc-diff/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[rfc_fetch] download failed for RFC{num}: {exc}", file=sys.stderr)
        return False
    dest.write_text(data, encoding="utf-8")
    return True


def convert_txt_to_md(txt_path: Path, md_path: Path) -> None:
    """Lightweight conversion: preserve text, wrap section headings in markdown."""
    text = rc.read_text(txt_path)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        # RFC section headings like "4.6.2.  Option Processing"
        if re.match(r"^\d+(\.\d+)*\.\s+\S", stripped) and len(stripped) < 80:
            lines.append(f"## {stripped}")
        else:
            lines.append(line)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Fetch and convert RFC documents.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    code_root = Path(args.code_root)
    design_root = Path(args.design_root)
    work = rc.agent_work_dir(code_root)
    rfc_cache = rc.ensure_dir(work / "rfcs")

    index_path = work / "benchmark_index.json"
    if not index_path.exists():
        print("[rfc_fetch] benchmark_index.json missing; nothing to fetch", file=sys.stderr)
        return 0
    index = rc.load_json(index_path)

    manifest = []
    for entry in index.get("rfcs", []):
        rfc_key = entry["rfc"]
        num = str(entry["number"])
        local = find_local_rfc(design_root, num)
        record = {"rfc": rfc_key, "number": num, "status": "pending", "doc_path": ""}

        if local is not None:
            md_dest = rfc_cache / f"rfc{num}.md"
            if local.suffix.lower() == ".md":
                md_dest.write_text(rc.read_text(local), encoding="utf-8")
            else:
                convert_txt_to_md(local, md_dest)
            record.update(status="ok", source="local", doc_path=str(md_dest))
        else:
            txt_dest = rfc_cache / f"rfc{num}.txt"
            md_dest = rfc_cache / f"rfc{num}.md"
            if download_rfc(num, txt_dest):
                convert_txt_to_md(txt_dest, md_dest)
                record.update(status="ok", source="download", doc_path=str(md_dest))
            else:
                record.update(status="blocked", source="none", doc_path="")
        manifest.append(record)

    rc.save_json(work / "rfc_manifest.json", {
        "fetched_at": rc.now_iso(),
        "rfcs": manifest,
        "ok_count": sum(1 for r in manifest if r["status"] == "ok"),
        "blocked_count": sum(1 for r in manifest if r["status"] == "blocked"),
    })
    ok = sum(1 for r in manifest if r["status"] == "ok")
    print(f"[rfc_fetch] {ok}/{len(manifest)} RFC document(s) available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
