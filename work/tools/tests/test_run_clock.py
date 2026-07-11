from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "work" / "tools" / "scripts" / "goal_runner.py"


def _start(logs: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(RUNNER), "start-clock",
            "--log-root", str(logs), "--state-root", str(logs / "state"),
        ],
        text=True, capture_output=True,
    )


def test_run_clock_is_idempotent_and_cannot_reset_a_prepared_session(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    first = _start(logs)
    assert first.returncode == 0, first.stdout + first.stderr
    clock_path = logs / "state" / "run_clock.json"
    before = clock_path.read_bytes()

    second = _start(logs)
    assert second.returncode == 0, second.stdout + second.stderr
    assert clock_path.read_bytes() == before
    clock = json.loads(before)
    assert clock["maximum_seconds"] == 21600

    (logs / "state" / "agent_loop_state.json").write_text(
        '{"session_id":"prepared"}\n', encoding="utf-8",
    )
    clock_path.unlink()
    reset = _start(logs)
    assert reset.returncode == 2
    assert "refusing to reset elapsed time" in reset.stdout
    assert not clock_path.exists()


def test_run_clock_refuses_state_or_trace_rebaseline(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    first = _start(logs)
    assert first.returncode == 0, first.stdout + first.stderr
    clock_path = logs / "state" / "run_clock.json"
    trace_path = logs / "trace" / "run_clock.json"
    original_clock = clock_path.read_bytes()
    original_trace = trace_path.read_bytes()

    value = json.loads(original_clock)
    value["started_at"] = "2000-01-01T00:00:00Z"
    value["deadline_at"] = "2000-01-01T06:00:00Z"
    clock_path.write_text(json.dumps(value), encoding="utf-8")
    changed_state = _start(logs)
    assert changed_state.returncode == 2
    assert "differs from its original trace baseline" in changed_state.stdout
    assert trace_path.read_bytes() == original_trace

    clock_path.write_bytes(original_clock)
    trace_path.unlink()
    missing_trace = _start(logs)
    assert missing_trace.returncode == 2
    assert "trace baseline is missing" in missing_trace.stdout
    assert not trace_path.exists()


def test_run_clock_cannot_restart_after_preprepare_artifacts_exist(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    first = _start(logs)
    assert first.returncode == 0, first.stdout + first.stderr
    (logs / "state" / "run_clock.json").unlink()
    (logs / "trace" / "run_clock.json").unlink()
    (logs / "state" / "design_source_plan.json").write_text(
        '{"catalog_path":"benchmark.md"}\n', encoding="utf-8",
    )

    reset = _start(logs)

    assert reset.returncode == 2
    assert "session/input artifacts exist" in reset.stdout
    assert not (logs / "state" / "run_clock.json").exists()
    assert not (logs / "trace" / "run_clock.json").exists()
