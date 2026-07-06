import sys
import subprocess
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import shophub_goal_runner as runner
import feature_registry
import matrix_to_repair_tasks
import public_case_rule_builder
import repair_task_builder
import rule_issue_builder


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


def test_report_then_final_gate_sees_generated_repair_report(tmp_path):
    _minimal_target(tmp_path)

    runner.init_workspace(tmp_path)
    runner.generate_report(tmp_path)
    runner.run_final_goal_gate(tmp_path)

    final_report = runner.read_json(tmp_path / ".agent-work" / "final_goal_report.json", {})
    state = runner.read_json(tmp_path / ".agent-work" / "state.json", {})
    repair_gate = final_report.get("gates", {}).get("repair_report", {})

    assert (tmp_path / "修复报告.md").exists()
    assert repair_gate.get("passed") is True
    assert repair_gate.get("summary") == "present_with_evidence"
    assert "public_smoke" in final_report.get("gates", {})
    assert "public_matrix" not in final_report.get("gates", {})
    assert "spec_ir" in final_report.get("gates", {})
    assert "trace_coverage" in final_report.get("gates", {})
    assert "generated_spec_tests" in final_report.get("gates", {})
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


def test_missing_competition_inputs_report_and_gate_are_not_done(tmp_path):
    _write_text(tmp_path / "README.md", "# Fixture\n")
    (tmp_path / "code").mkdir()
    (tmp_path / "design-docs").mkdir()
    (tmp_path / "test-cases").mkdir()
    _write_text(tmp_path / ".agent-work" / "final_goal_report.json", '{"done": true}\n')

    runner.init_workspace(tmp_path)
    runner.generate_report(tmp_path)
    runner.run_final_goal_gate(tmp_path)

    state = runner.read_json(tmp_path / ".agent-work" / "state.json", {})
    goal_status = runner.read_json(tmp_path / ".agent-work" / "goal_status.json", {})
    final_report = runner.read_json(tmp_path / ".agent-work" / "final_goal_report.json", {})

    assert state.get("stop_reason") == "missing_competition_inputs"
    assert state.get("missing_required_paths") == ["code/pom.xml", "test-cases/pom.xml"]
    assert state.get("phase") == "WRITE_REPORT"
    assert (tmp_path / "修复报告.md").exists()
    assert goal_status.get("done") is False
    assert final_report.get("done") is False


def test_cli_help_does_not_expose_removed_runner_entry():
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "work/tools/scripts/shophub_goal_runner.py", "--help"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0
    assert "auto-run" not in result.stdout
    assert "--patch-command" not in result.stdout
    assert "SHOPHUB_PATCH_COMMAND" not in result.stdout


def test_build_repair_queue_does_not_emit_patch_prompt_directory(tmp_path):
    _minimal_target(tmp_path)
    runner.init_workspace(tmp_path)

    runner.build_repair_queue(tmp_path)

    assert not (tmp_path / ".agent-work" / "patch_prompts").exists()


def test_public_case_rule_builder_is_diagnostic_only_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GOAL_AGENT_MODE", raising=False)
    _minimal_target(tmp_path)
    _write_text(
        tmp_path / "test-cases" / "src" / "test" / "java" / "MoneyTest.java",
        "class MoneyTest { @org.junit.jupiter.api.Test void discountAmountShouldBeBounded() {} }\n",
    )
    stale = tmp_path / ".agent-work" / "public_case_rules.json"
    _write_text(stale, '{"rules": [{"id": "STALE"}]}\n')

    public_case_rule_builder.build_public_case_rules(tmp_path)

    diagnostics = runner.read_json(tmp_path / ".agent-work" / "public_diagnostics.json", {})
    assert diagnostics.get("role") == "diagnostic-smoke-only"
    assert diagnostics.get("diagnostic_rule_candidates")
    assert not stale.exists()


def test_feature_registry_ignores_public_matrix_and_public_rules_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GOAL_AGENT_MODE", raising=False)
    _minimal_target(tmp_path)
    runner.init_workspace(tmp_path)
    paths = runner.RunnerPaths(tmp_path)
    runner.write_json(
        paths.work / "business_rules.json",
        {
            "rules": [
                {
                    "id": "REQ-MONEY-001",
                    "priority": "P0",
                    "type": "money_formula",
                    "description": "Payable amount follows the design formula.",
                    "source_file": "design-docs/api.md",
                    "source_line": 3,
                }
            ]
        },
    )
    runner.write_json(paths.work / "consistency_report.json", {"issues": [], "summary": {"p0_issues": 0, "p1_issues": 0}})
    runner.write_json(
        paths.work / "public_case_rules.json",
        {"rules": [{"id": "PUBRULE-MONEY", "severity": "P0", "category": "money_formula", "description": "public symptom"}]},
    )
    runner.write_json(
        paths.test_matrix / "current_test_matrix.json",
        {
            "summary": {"total": 1, "pass": 0, "failure": 1, "error": 0, "timeout": 0, "not_run": 0, "skipped": 0},
            "matrix": [{"class_name": "PublicTest", "method_name": "fails", "outcome": "FAILURE", "message": "symptom"}],
        },
    )

    report = feature_registry.build_feature_registry(tmp_path)

    assert report["ignored_sources"] == ["public_case_rules", "public_matrix_failures"]
    assert all(feature.get("source") != "public-test" for feature in report["features"])
    design_features = [feature for feature in report["features"] if feature.get("source") == "design-doc"]
    assert design_features
    assert all(feature.get("passes") is True for feature in design_features)


def test_public_symptoms_do_not_create_issues_or_tasks_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GOAL_AGENT_MODE", raising=False)
    _minimal_target(tmp_path)
    runner.init_workspace(tmp_path)
    paths = runner.RunnerPaths(tmp_path)
    runner.write_json(
        paths.work / "feature_list.json",
        {
            "features": [
                {
                    "id": "RULE-TEST-PUBLIC",
                    "source": "public-test",
                    "category": "public_matrix",
                    "severity": "P0",
                    "description": "Public test failed.",
                    "passes": False,
                    "related_tests": ["PublicTest#fails"],
                }
            ]
        },
    )
    runner.write_json(
        paths.test_matrix / "current_test_matrix.json",
        {
            "summary": {"total": 1, "pass": 0, "failure": 1, "error": 0, "timeout": 0, "not_run": 0, "skipped": 0},
            "matrix": [{"class_name": "PublicTest", "method_name": "fails", "outcome": "FAILURE", "message": "symptom"}],
        },
    )

    issue_report = rule_issue_builder.build_issues(tmp_path)
    issues = runner.read_jsonl(paths.issues)
    risks = runner.read_json(paths.work / "public_diagnostic_risks.json", {})

    assert issue_report["public_diagnostic_risk_count"] == 2
    assert not issues
    assert len(risks.get("risks", [])) == 2

    runner.append_jsonl(
        paths.issues,
        [
            {
                "issue_id": "ISSUE-MATRIX",
                "severity": "high",
                "type": "public_matrix_failure",
                "design_basis": "public black-box matrix symptom",
                "actual_behavior": "PublicTest#fails failed",
                "status": "open",
            },
            {
                "issue_id": "ISSUE-SPEC",
                "severity": "high",
                "type": "missing_endpoint",
                "spec_id": "API-GET-PRODUCTS",
                "design_source": "README.md:3",
                "design_basis": "frozen API contract GET /api/v1/products",
                "actual_behavior": "Endpoint is missing",
                "fix_suggestion": "Implement the frozen API contract.",
                "status": "open",
            },
        ],
    )

    repair_report = repair_task_builder.build_repair_tasks(tmp_path)
    source_issues = {task.get("source_issue") for task in repair_report["tasks"]}

    assert "ISSUE-MATRIX" not in source_issues
    assert "ISSUE-SPEC" in source_issues
    assert repair_report["ignored_sources"] == ["test_symptoms", "public_case_rules", "public_matrix_symptom_issues"]


def test_matrix_to_repair_tasks_is_diagnostic_only_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GOAL_AGENT_MODE", raising=False)
    _minimal_target(tmp_path)
    runner.init_workspace(tmp_path)
    paths = runner.RunnerPaths(tmp_path)
    matrix = {
        "run_id": "public-smoke",
        "summary": {"total": 1, "pass": 0, "failure": 1},
        "matrix": [{"class_name": "PublicTest", "method_name": "fails", "outcome": "FAILURE", "message": "symptom"}],
    }

    tasks = matrix_to_repair_tasks.generate_tasks_from_matrix(tmp_path, matrix)
    issues = matrix_to_repair_tasks.generate_legacy_issues_from_matrix(tmp_path, matrix)
    diagnostics = matrix_to_repair_tasks.public_diagnostics_from_matrix(matrix)

    assert tasks == []
    assert issues == []
    assert diagnostics[0]["status"] == "diagnostic_only_unmapped"
    assert runner.read_jsonl(paths.issues) == []
