from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402


def test_source_evidence_requires_integer_lines_and_the_complete_selected_range(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("first obligation\nsecond obligation\n", encoding="utf-8")
    valid = {
        "path": "source.txt", "line_start": 1, "line_end": 2,
        "quote": "first obligation\nsecond obligation",
    }
    assert ac.validate_source_evidence(valid, tmp_path, "evidence", "quote") == []

    partial = {**valid, "quote": "first obligation"}
    assert any(
        "does not match" in error
        for error in ac.validate_source_evidence(partial, tmp_path, "evidence", "quote")
    )
    string_lines = {**valid, "line_start": "1"}
    assert any(
        "must be integers" in error
        for error in ac.validate_source_evidence(
            string_lines, tmp_path, "evidence", "quote",
        )
    )
    float_lines = {**valid, "line_end": 2.0}
    assert any(
        "must be integers" in error
        for error in ac.validate_source_evidence(
            float_lines, tmp_path, "evidence", "quote",
        )
    )
