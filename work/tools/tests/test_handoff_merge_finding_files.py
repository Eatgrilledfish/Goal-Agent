from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import handoff_merge as hm  # noqa: E402


def test_canonical_finding_owns_candidate_directory_and_metadata_is_ignored(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "TASK-001"
    candidate.mkdir()
    canonical = candidate / "TASK-001.json"
    ac.save_json(canonical, {
        "finding_id": "FINDING-TASK-001", "task_id": "TASK-001",
    })
    ac.save_json(candidate / "TASK-001.finding.json", {
        "finding_id": "FINDING-TASK-001", "task_id": "TASK-001",
    })
    ac.save_json(candidate / "TASK-001.semantic.json", {"task_id": "TASK-001"})
    ac.save_json(candidate / "TASK-001.report.json", {"passed": True})
    ac.save_json(candidate / "TASK-001.trace.json", {"passed": True})

    assert hm._finding_handoff_files(candidate) == [canonical]
    assert hm._handoff_identifiers(
        candidate, "finding_id", artifact_type="finding",
    ) == {"FINDING-TASK-001"}


def test_legacy_single_finding_remains_readable_but_metadata_never_is(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "TASK-LEGACY"
    candidate.mkdir()
    legacy = candidate / "finding.json"
    ac.save_json(legacy, {
        "finding_id": "FINDING-TASK-LEGACY", "task_id": "TASK-LEGACY",
    })
    ac.save_json(candidate / "TASK-LEGACY.semantic.json", {
        "finding_id": "FORGED-IN-SEMANTIC", "task_id": "TASK-LEGACY",
    })
    ac.save_json(candidate / "TASK-LEGACY.report.json", {"passed": False})

    assert hm._finding_handoff_files(candidate) == [legacy]
