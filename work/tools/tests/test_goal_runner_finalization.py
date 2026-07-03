import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import shophub_goal_runner as runner


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_target(root: Path) -> None:
    _write_text(
        root / "README.md",
        "# ShopHub Fixture\n\n## API\n\nGET /api/v1/products\n\nERROR_NOT_FOUND\n",
    )
    _write_text(
        root / "code" / "pom.xml",
        (
            '<project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<modelVersion>4.0.0</modelVersion>"
            "<groupId>x</groupId><artifactId>code</artifactId><version>1</version>"
            "</project>\n"
        ),
    )
    _write_text(
        root / "test-cases" / "pom.xml",
        (
            '<project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<modelVersion>4.0.0</modelVersion>"
            "<groupId>x</groupId><artifactId>tests</artifactId><version>1</version>"
            "</project>\n"
        ),
    )
    _write_text(
        root / "design-docs" / "api.md",
        "# API Reference\n\nGET /api/v1/products\n\n# Business\n\nProduct list must return active products.\n",
    )


def test_auto_run_final_gate_sees_generated_repair_report(tmp_path):
    _minimal_target(tmp_path)

    runner.run_until_done(
        tmp_path,
        no_tests=True,
        timeout=5,
        max_rounds=1,
        patch_command=None,
        patch_timeout=5,
    )

    final_report = runner.read_json(tmp_path / ".agent-work" / "final_goal_report.json", {})
    state = runner.read_json(tmp_path / ".agent-work" / "state.json", {})
    repair_gate = final_report.get("gates", {}).get("repair_report", {})

    assert (tmp_path / "修复报告.md").exists()
    assert repair_gate.get("passed") is True
    assert repair_gate.get("summary") == "present_with_evidence"
    assert final_report.get("done") is False
    assert state.get("phase") == "WRITE_REPORT"


def test_competition_layout_requires_maven_poms(tmp_path):
    _write_text(tmp_path / "README.md", "# Fixture\n")
    (tmp_path / "code").mkdir()
    (tmp_path / "design-docs").mkdir()
    (tmp_path / "test-cases").mkdir()

    missing = runner.check_competition_layout(tmp_path)

    assert "code/pom.xml" in missing
    assert "test-cases/pom.xml" in missing
    assert "code" not in missing
    assert "test-cases" not in missing


def test_auto_run_stops_on_missing_competition_inputs(tmp_path):
    _write_text(tmp_path / "README.md", "# Fixture\n")
    (tmp_path / "code").mkdir()
    (tmp_path / "design-docs").mkdir()
    (tmp_path / "test-cases").mkdir()
    _write_text(tmp_path / ".agent-work" / "final_goal_report.json", '{"done": true}\n')

    runner.run_until_done(
        tmp_path,
        no_tests=True,
        timeout=5,
        max_rounds=1,
        patch_command=None,
        patch_timeout=5,
    )

    state = runner.read_json(tmp_path / ".agent-work" / "state.json", {})
    goal_status = runner.read_json(tmp_path / ".agent-work" / "goal_status.json", {})
    final_report = runner.read_json(tmp_path / ".agent-work" / "final_goal_report.json", {})

    assert state.get("stop_reason") == "missing_competition_inputs"
    assert state.get("missing_required_paths") == ["code/pom.xml", "test-cases/pom.xml"]
    assert state.get("phase") == "WRITE_REPORT"
    assert (tmp_path / "修复报告.md").exists()
    assert goal_status.get("done") is False
    assert final_report.get("done") is False
    assert not (tmp_path / ".agent-work" / "patch_prompts").exists() or not list(
        (tmp_path / ".agent-work" / "patch_prompts").glob("*.md")
    )

