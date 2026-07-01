import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import candidate_sandbox


def _completed(command, returncode=0, stdout=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout)


class CandidateSandboxGateTest(unittest.TestCase):
    def _validate(
        self,
        run_step_results,
        *,
        public_pom=False,
        local_matrix=(False, []),
        contract_returncode=0,
        contract_stdout='{"summary": {"p0_issues": 0}}',
        contract_exception=None,
        guard_returncode=0,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "root"
            sandbox = Path(tmpdir) / "sandbox"
            (root / "patches").mkdir(parents=True)
            (root / "patches" / "candidate.patch").write_text(
                "\n".join(
                    [
                        "diff --git a/code/src/main/java/Foo.java b/code/src/main/java/Foo.java",
                        "--- a/code/src/main/java/Foo.java",
                        "+++ b/code/src/main/java/Foo.java",
                        "@@",
                        "+// candidate change",
                    ]
                ),
                encoding="utf-8",
            )
            (root / ".agent-work").mkdir(parents=True)
            (sandbox / "code").mkdir(parents=True)
            (sandbox / "code" / "pom.xml").write_text("<project />", encoding="utf-8")
            if public_pom:
                (sandbox / "test-cases").mkdir(parents=True)
                (sandbox / "test-cases" / "pom.xml").write_text("<project />", encoding="utf-8")

            def fake_subprocess_run(command, **kwargs):
                if command[:2] == ["git", "apply"]:
                    return _completed(command)
                command_text = " ".join(str(part) for part in command)
                if "contract_checker.py" in command_text:
                    if contract_exception is not None:
                        raise contract_exception
                    return _completed(command, contract_returncode, contract_stdout)
                if "forbidden_change_guard.py" in command_text:
                    return _completed(command, guard_returncode)
                return _completed(command)

            with patch.object(candidate_sandbox, "create_candidate_workspace", return_value=sandbox), \
                patch.object(candidate_sandbox, "cleanup_candidate_workspace", return_value=[]), \
                patch.object(candidate_sandbox, "run_generated_tests_in_sandbox", return_value={
                    "generated_tests": "NONE",
                    "score_inputs": {"generated_test_pass_rate": 0.5},
                }), \
                patch.object(candidate_sandbox, "_check_sandbox_local_matrix", return_value=local_matrix), \
                patch.object(candidate_sandbox.subprocess, "run", side_effect=fake_subprocess_run), \
                patch.object(candidate_sandbox, "run_step", side_effect=run_step_results):
                return candidate_sandbox.validate_candidate(
                    root,
                    {
                        "task_id": "T1",
                        "candidate_id": "C1",
                        "patch_file": "patches/candidate.patch",
                    },
                    timeout=1,
                    gate_mode="local",
                    previous_matrix={"summary": {}},
                )

    def test_code_tests_failure_eliminates_candidate(self):
        result = self._validate(
            [
                {"passed": True, "output_snippet": "compile ok"},
                {"passed": False, "output_snippet": "unit failure"},
            ]
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["code_tests"], "FAIL")
        self.assertEqual(result["elimination_reason"], "Code module tests failed after patch")

    def test_code_install_failure_eliminates_candidate(self):
        result = self._validate(
            [
                {"passed": True, "output_snippet": "compile ok"},
                {"passed": True, "output_snippet": "tests ok"},
                {"passed": False, "output_snippet": "install failure"},
            ]
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["code_install"], "FAIL")
        self.assertEqual(result["elimination_reason"], "Code install failed after patch")

    def test_unparseable_contract_failure_eliminates_candidate(self):
        result = self._validate(
            [
                {"passed": True, "output_snippet": "compile ok"},
                {"passed": True, "output_snippet": "tests ok"},
                {"passed": True, "output_snippet": "install ok"},
            ],
            contract_returncode=1,
            contract_stdout="not-json",
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["contract_check"], "FAIL")
        self.assertEqual(
            result["elimination_reason"],
            "Contract checker failed and output was not parseable",
        )

    def test_contract_execution_error_eliminates_candidate(self):
        result = self._validate(
            [
                {"passed": True, "output_snippet": "compile ok"},
                {"passed": True, "output_snippet": "tests ok"},
                {"passed": True, "output_snippet": "install ok"},
            ],
            contract_exception=OSError("boom"),
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["contract_check"], "ERROR")
        self.assertIn("Contract checker execution failed in sandbox", result["elimination_reason"])

    def test_local_gate_eliminates_hard_regression(self):
        result = self._validate(
            [
                {"passed": True, "output_snippet": "compile ok"},
                {"passed": True, "output_snippet": "tests ok"},
                {"passed": True, "output_snippet": "install ok"},
                {"passed": False, "output_snippet": "Tests run: 1, Failures: 0, Errors: 1"},
            ],
            public_pom=True,
            local_matrix=(True, ["1 hard regression(s): previous PASS now failing"]),
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["matrix_gate"], "FAIL")
        self.assertIn("Public black-box matrix gate failed", result["elimination_reason"])

    def test_local_gate_allows_existing_public_failure_without_hard_regression(self):
        result = self._validate(
            [
                {"passed": True, "output_snippet": "compile ok"},
                {"passed": True, "output_snippet": "tests ok"},
                {"passed": True, "output_snippet": "install ok"},
                {"passed": False, "output_snippet": "Tests run: 1, Failures: 1, Errors: 0"},
            ],
            public_pom=True,
            local_matrix=(False, []),
        )

        self.assertTrue(result["eligible"])
        self.assertEqual(result["public_tests"], "FAIL")
        self.assertEqual(result["matrix_gate"], "PASS")
        self.assertEqual(result["code_install"], "PASS")


if __name__ == "__main__":
    unittest.main()
