"""
execute.py 리팩터링 안전망 테스트.
"""

import json
import subprocess
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import execute as ex


@pytest.fixture
def tmp_project(tmp_path):
    """steps/, AGENTS.md, docs/ 를 갖춘 임시 프로젝트 구조."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()

    (tmp_path / "AGENTS.md").write_text("# Rules\n- rule one\n- rule two", encoding="utf-8")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "PRD.md").write_text("# PRD\nProduct content", encoding="utf-8")
    (docs_dir / "ARCHITECTURE.md").write_text("# Architecture\nSome content", encoding="utf-8")
    (docs_dir / "ADR.md").write_text("# ADR\nDecision log", encoding="utf-8")
    (docs_dir / "RESULTS_POLICY.md").write_text("# Results Policy\nResult content", encoding="utf-8")
    (docs_dir / "arch.md").write_text("# Architecture\nSome content", encoding="utf-8")
    (docs_dir / "guide.md").write_text("# Guide\nAnother doc", encoding="utf-8")

    return tmp_path


@pytest.fixture
def steps_dir(tmp_project):
    """step 3개를 가진 steps 디렉토리."""
    index = {
        "project": "TestProject",
        "goal": "mvp",
        "validation_scope": "framework",
        "steps": [
            {"step": 0, "name": "setup", "type": "implementation", "status": "completed", "summary": "프로젝트 초기화 완료"},
            {"step": 1, "name": "core", "type": "implementation", "status": "completed", "summary": "핵심 로직 구현"},
            {"step": 2, "name": "ui", "type": "implementation", "status": "pending"},
        ],
    }
    (tmp_project / "steps" / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    (tmp_project / "steps" / "step2.md").write_text("# Step 2: UI\n\nUI를 구현하세요.", encoding="utf-8")
    return tmp_project / "steps"


@pytest.fixture
def executor(tmp_project, steps_dir):
    with patch.object(ex, "ROOT", tmp_project):
        inst = ex.StepExecutor()
    inst._root = str(tmp_project)
    inst._steps_dir = steps_dir
    inst._index_file = steps_dir / "index.json"
    return inst


class TestStamp:
    def test_returns_kst_timestamp(self, executor):
        assert "+0900" in executor._stamp()

    def test_format_is_iso(self, executor):
        result = executor._stamp()
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert dt.tzinfo is not None

    def test_is_current_time(self, executor):
        before = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0)
        result = executor._stamp()
        after = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0) + timedelta(seconds=1)
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert before <= parsed <= after


class TestJsonHelpers:
    def test_roundtrip(self, tmp_path):
        data = {"key": "값", "nested": [1, 2, 3]}
        path = tmp_path / "test.json"
        ex.StepExecutor._write_json(path, data)
        assert ex.StepExecutor._read_json(path) == data

    def test_save_ensures_ascii_false(self, tmp_path):
        path = tmp_path / "test.json"
        ex.StepExecutor._write_json(path, {"한글": "테스트"})
        raw = path.read_text(encoding="utf-8")
        assert "한글" in raw
        assert "\\u" not in raw

    def test_save_indented(self, tmp_path):
        path = tmp_path / "test.json"
        ex.StepExecutor._write_json(path, {"a": 1})
        assert "\n" in path.read_text(encoding="utf-8")

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ex.StepExecutor._read_json(tmp_path / "nope.json")


class TestLoadGuardrails:
    def test_loads_agents_md_and_docs(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "# Rules" in result
        assert "# PRD" in result
        assert "# Architecture" in result
        assert "# ADR" in result
        assert "# Results Policy" in result

    def test_loads_only_core_docs(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "guide" not in result
        assert "arch\n\n# Architecture" not in result

    def test_missing_agents_md_exits(self, executor, tmp_project):
        (tmp_project / "AGENTS.md").unlink()
        with patch.object(ex, "ROOT", tmp_project):
            with pytest.raises(SystemExit) as exc_info:
                executor._load_guardrails()
        assert exc_info.value.code == 1

    def test_missing_core_doc_exits(self, executor, tmp_project):
        (tmp_project / "docs" / "RESULTS_POLICY.md").unlink()
        with patch.object(ex, "ROOT", tmp_project):
            with pytest.raises(SystemExit) as exc_info:
                executor._load_guardrails()
        assert exc_info.value.code == 1


class TestBuildStepContext:
    def test_includes_completed_with_summary(self, steps_dir):
        index = json.loads((steps_dir / "index.json").read_text(encoding="utf-8"))
        result = ex.StepExecutor._build_step_context(index)
        assert "Step 0 (setup): 프로젝트 초기화 완료" in result
        assert "Step 1 (core): 핵심 로직 구현" in result

    def test_empty_when_no_completed(self):
        result = ex.StepExecutor._build_step_context({"steps": [{"step": 0, "name": "a", "type": "implementation", "status": "pending"}]})
        assert result == ""


class TestBuildPreamble:
    def test_includes_project_name(self, executor):
        assert "TestProject" in executor._build_preamble("", "")

    def test_includes_commit_example(self, executor):
        assert "feat(mvp):" in executor._build_preamble("", "")

    def test_includes_rules(self, executor):
        step = {"step": 2, "name": "ui", "type": "implementation"}
        result = executor._build_preamble("", "", step=step)
        assert "작업 규칙" in result
        assert "steps/artifacts/reference" in result
        assert "해당 정보가 필요할 때" in result
        assert "step type" in result
        assert "non-interactive one-shot command" in result
        assert "Python 기반 추출" in result

    def test_retry_section_with_prev_error(self, executor):
        result = executor._build_preamble("", "", prev_error="타입 에러")
        assert "이전 시도 실패" in result
        assert "타입 에러" in result

    def test_includes_index_path(self, executor):
        assert "/steps/index.json" in executor._build_preamble("", "")


class TestCheckoutBranch:
    def _mock_git(self, executor, responses):
        call_idx = {"i": 0}

        def fake_git(*args):
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx < len(responses):
                return responses[idx]
            return MagicMock(returncode=0, stdout="", stderr="")

        executor._run_git = fake_git

    def test_already_on_branch(self, executor):
        self._mock_git(executor, [MagicMock(returncode=0, stdout="feat-mvp\n", stderr="")])
        executor._checkout_branch()

    def test_branch_exists_checkout(self, executor):
        self._mock_git(
            executor,
            [
                MagicMock(returncode=0, stdout="main\n", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ],
        )
        executor._checkout_branch()

    def test_checkout_fails_exits(self, executor):
        self._mock_git(
            executor,
            [
                MagicMock(returncode=0, stdout="main\n", stderr=""),
                MagicMock(returncode=1, stdout="", stderr=""),
                MagicMock(returncode=1, stdout="", stderr="dirty tree"),
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1


class TestCheckCleanWorktree:
    def test_allows_only_steps_changes(self, executor):
        executor._run_git = lambda *args: MagicMock(
            returncode=0,
            stdout="?? steps/index.json\n?? steps/step2.md\n",
            stderr="",
        )
        executor._check_clean_worktree()

    def test_rejects_unrelated_dirty_paths(self, executor):
        executor._run_git = lambda *args: MagicMock(
            returncode=0,
            stdout="?? steps/index.json\n M docs/PRD.md\n?? scratch.txt\n",
            stderr="",
        )
        with pytest.raises(SystemExit) as exc_info:
            executor._check_clean_worktree()
        assert exc_info.value.code == 1

    def test_rejects_tracked_raw_files(self, executor):
        executor._run_git = lambda *args: MagicMock(
            returncode=0,
            stdout=" M raw/README.md\n?? steps/step2.md\n",
            stderr="",
        )
        with pytest.raises(SystemExit) as exc_info:
            executor._check_clean_worktree()
        assert exc_info.value.code == 1

    def test_allows_untracked_generated_outputs(self, executor):
        executor._run_git = lambda *args: MagicMock(
            returncode=0,
            stdout="?? build/CMakeCache.txt\n?? results/case/summary.md\n?? steps/index.json\n",
            stderr="",
        )
        executor._check_clean_worktree()


class TestValidationScope:
    def test_framework_scope_rejects_external_target_commands(self, executor):
        index = executor._read_json(executor._index_file)
        for item in index["steps"]:
            if item["step"] == 2:
                item["type"] = "validation"
                item["validation_commands"] = ["cmake -S . -B build", "ctest --test-dir build --output-on-failure"]
                item["results_contract"] = {
                    "summary_path": "results/case/summary.md",
                    "output_paths": ["results/case/run.log"],
                    "comparison_artifacts": ["results/case/comparison.svg"],
                    "comparison_basis": "baseline",
                    "validation_log_paths": ["results/case/validation.log"],
                }
        executor._write_json(executor._index_file, index)

        error = executor._validate_validation_scope(index)
        assert "framework validation_scope" in error

    def test_external_target_requires_existing_cmake_project(self, executor, tmp_project):
        index = executor._read_json(executor._index_file)
        index["validation_scope"] = "external-target"
        index["target_root"] = "target"
        target_root = tmp_project / "target"
        target_root.mkdir()
        executor._write_json(executor._index_file, index)

        error = executor._validate_validation_scope(index)
        assert "CMakeLists.txt" in error

    def test_external_target_accepts_existing_cmake_project(self, executor, tmp_project):
        index = executor._read_json(executor._index_file)
        index["validation_scope"] = "external-target"
        index["target_root"] = "target"
        target_root = tmp_project / "target"
        target_root.mkdir()
        (target_root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
        executor._write_json(executor._index_file, index)

        assert executor._validate_validation_scope(index) is None


class TestCommitStep:
    def test_two_phase_commit(self, executor):
        calls = []

        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")

        executor._run_git = fake_git
        executor._commit_step(2, "ui")
        commit_calls = [call for call in calls if call[0] == "commit"]
        assert len(commit_calls) == 2
        assert "feat(mvp):" in commit_calls[0][2]
        assert "chore(mvp):" in commit_calls[1][2]

    def test_resets_raw_reference_folder_from_auto_commit(self, executor):
        calls = []

        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="", stderr="")

        executor._run_git = fake_git
        executor._commit_step(2, "ui")
        assert ("reset", "HEAD", "--", "raw") in calls

    def test_code_commit_failure_raises(self, executor):
        def fake_git(*args):
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=1)
            if args[0] == "commit":
                return MagicMock(returncode=1, stdout="", stderr="author identity unknown")
            return MagicMock(returncode=0, stdout="", stderr="")

        executor._run_git = fake_git
        with pytest.raises(ex.CommitStepError) as exc_info:
            executor._commit_step(2, "ui")
        assert "code commit failed" in str(exc_info.value)

    def test_housekeeping_commit_failure_raises(self, executor):
        diff_calls = {"count": 0}
        commit_calls = {"count": 0}

        def fake_git(*args):
            if args[:2] == ("diff", "--cached"):
                diff_calls["count"] += 1
                return MagicMock(returncode=1)
            if args[0] == "commit":
                commit_calls["count"] += 1
                if commit_calls["count"] == 1:
                    return MagicMock(returncode=0, stdout="", stderr="")
                return MagicMock(returncode=1, stdout="", stderr="hook rejected")
            return MagicMock(returncode=0, stdout="", stderr="")

        executor._run_git = fake_git
        with pytest.raises(ex.CommitStepError) as exc_info:
            executor._commit_step(2, "ui")
        assert "housekeeping commit failed" in str(exc_info.value)


class TestInvokeCodex:
    class FakePopen:
        def __init__(self, *, returncode=0, stdout="", stderr="", poll_values=None, on_poll=None):
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr
            self._poll_values = list(poll_values or [returncode])
            self._on_poll = on_poll
            self.terminate_called = False
            self.kill_called = False

        def poll(self):
            if self._on_poll:
                self._on_poll()
                self._on_poll = None
            if len(self._poll_values) > 1:
                return self._poll_values.pop(0)
            return self._poll_values[0]

        def communicate(self, timeout=None):
            return self._stdout, self._stderr

        def terminate(self):
            self.terminate_called = True
            self._poll_values = [self.returncode]

        def kill(self):
            self.kill_called = True
            self._poll_values = [self.returncode]

    def test_open_codex_stdin_prefers_parent_tty(self, executor):
        fake_stdin = SimpleNamespace()
        fake_stdin.fileno = lambda: 0

        with patch.object(sys, "stdin", fake_stdin):
            with patch("os.isatty", return_value=True):
                stdin_handle, cleanup_handles, mode = executor._open_codex_stdin()

        assert stdin_handle is fake_stdin
        assert cleanup_handles == []
        assert mode == "parent-tty"

    def test_open_codex_stdin_uses_pty_without_parent_tty(self, executor):
        fake_master = MagicMock()
        fake_slave = MagicMock()

        with patch("os.isatty", return_value=False):
            with patch("pty.openpty", return_value=(11, 12)):
                with patch("os.fdopen", side_effect=[fake_master, fake_slave]):
                    stdin_handle, cleanup_handles, mode = executor._open_codex_stdin()

        assert stdin_handle is fake_slave
        assert cleanup_handles == [fake_master, fake_slave]
        assert mode == "pty"

    def test_invokes_codex_with_correct_args(self, executor):
        fake_proc = self.FakePopen(returncode=0, stdout='{"result": "ok"}', stderr="")
        step = {"step": 2, "name": "ui"}

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch.object(ex.StepExecutor, "_open_codex_stdin", return_value=(subprocess.DEVNULL, [], "mock")):
                with patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
                    output = executor._invoke_codex(step, "PREAMBLE\n")

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert "-C" in cmd
        assert "PREAMBLE" in cmd[-1]
        assert "UI를 구현하세요" in cmd[-1]
        assert output["forcedStop"] is False
        assert output["failureCategory"] is None

    def test_records_failure_metadata_for_stall_pattern(self, executor):
        fake_proc = self.FakePopen(returncode=1, stdout="", stderr="")
        step = {"step": 2, "name": "ui"}

        def fake_popen(*args, **kwargs):
            kwargs["stderr"].write("ERROR write_stdin failed: stdin is closed for this session\n")
            kwargs["stderr"].flush()
            return fake_proc

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch.object(ex.StepExecutor, "_open_codex_stdin", return_value=(subprocess.DEVNULL, [], "mock")):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    output = executor._invoke_codex(step, "PREAMBLE\n")

        assert output["failureCategory"] == "stall"
        assert "stdin is closed" in output["stderrTail"]
        assert output["lastKnownStatus"] == "pending"
        assert output["startedAt"]
        assert output["endedAt"]

    def test_saves_output_json(self, executor):
        fake_proc = self.FakePopen(returncode=0, stdout='{"ok": true}', stderr="")
        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch.object(ex.StepExecutor, "_open_codex_stdin", return_value=(subprocess.DEVNULL, [], "mock")):
                with patch("subprocess.Popen", return_value=fake_proc):
                    executor._invoke_codex({"step": 2, "name": "ui"}, "preamble")
        output_file = executor._steps_dir / "step2-output.json"
        assert output_file.exists()
        written = json.loads(output_file.read_text(encoding="utf-8"))
        assert written["exitCode"] == 0
        assert written["failureCategory"] is None
        assert written["stdinMode"] == "mock"

    def test_nonexistent_step_file_exits(self, executor):
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_codex({"step": 99, "name": "missing"}, "preamble")
        assert exc_info.value.code == 1

    def test_forces_stop_after_status_finalized(self, executor):
        index = executor._read_json(executor._index_file)

        def mark_completed():
            for item in index["steps"]:
                if item["step"] == 2:
                    item["status"] = "completed"
                    item["summary"] = "done"
            executor._write_json(executor._index_file, index)

        fake_proc = self.FakePopen(returncode=0, stdout="ok", stderr="", poll_values=[None, None, None, 0], on_poll=mark_completed)
        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch.object(ex.StepExecutor, "_open_codex_stdin", return_value=(subprocess.DEVNULL, [], "mock")):
                with patch.object(ex.StepExecutor, "POST_STEP_GRACE", 0):
                    with patch("subprocess.Popen", return_value=fake_proc):
                        output = executor._invoke_codex({"step": 2, "name": "ui"}, "preamble")
        assert output["forcedStop"] is True
        assert fake_proc.terminate_called is True
        assert output["failureCategory"] is None


class TestProgressIndicator:
    def test_context_manager(self):
        import time

        with ex.progress_indicator("test") as progress:
            time.sleep(0.15)
        assert progress.elapsed >= 0.1


class TestMainCli:
    def test_no_steps_dir_exits(self, tmp_path):
        with patch("sys.argv", ["execute.py"]):
            with patch.object(ex, "ROOT", tmp_path):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
        assert exc_info.value.code == 1

    def test_missing_index_exits(self, tmp_project):
        (tmp_project / "steps" / "index.json").unlink(missing_ok=True)
        with patch("sys.argv", ["execute.py"]):
            with patch.object(ex, "ROOT", tmp_project):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
        assert exc_info.value.code == 1


class TestCheckBlockers:
    def _make_executor_with_steps(self, tmp_project, steps):
        steps_dir = tmp_project / "steps"
        steps_dir.mkdir(exist_ok=True)
        index = {"project": "T", "goal": "test", "steps": steps}
        (steps_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

        with patch.object(ex, "ROOT", tmp_project):
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_project)
        inst._steps_dir = steps_dir
        inst._index_file = steps_dir / "index.json"
        inst._goal_name = "test"
        inst._total = len(steps)
        return inst

    def test_error_step_exits_1(self, tmp_project):
        inst = self._make_executor_with_steps(
            tmp_project,
            [{"step": 0, "name": "bad", "status": "error", "error_message": "fail"}],
        )
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    def test_blocked_step_exits_2(self, tmp_project):
        inst = self._make_executor_with_steps(
            tmp_project,
            [{"step": 0, "name": "stuck", "status": "blocked", "blocked_reason": "API key"}],
        )
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 2


class TestExecuteSingleStep:
    def test_emits_stage_logs_for_completed_step(self, executor, capsys):
        def fake_invoke(step, preamble):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == step["step"]:
                    item["status"] = "completed"
                    item["summary"] = "done"
            executor._write_json(executor._index_file, index)

        executor._invoke_codex = fake_invoke
        executor._run_repo_checks = lambda: MagicMock(returncode=0, stderr="")
        executor._commit_step = lambda *args: None

        with patch.object(ex, "progress_indicator") as mock_progress:
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(elapsed=0)
            cm.__exit__.return_value = False
            mock_progress.return_value = cm
            assert executor._execute_single_step({"step": 2, "name": "ui"}, "guards") is True

        out = capsys.readouterr().out
        assert "starting (attempt 1/3)" in out
        assert "running Codex executor" in out
        assert "running post-step repo checks" in out
        assert "recording completion commit" in out
        assert "completed successfully" in out

    def test_validation_step_without_commands_fails_fast(self, executor):
        index = executor._read_json(executor._index_file)
        for item in index["steps"]:
            if item["step"] == 2:
                item["type"] = "validation"
                item["results_contract"] = {
                    "summary_path": "results/case/summary.md",
                    "output_paths": ["results/case/run.log"],
                    "comparison_artifacts": ["results/case/comparison.md"],
                    "comparison_basis": "baseline",
                }
        executor._write_json(executor._index_file, index)

        with pytest.raises(SystemExit) as exc_info:
            executor._execute_single_step({"step": 2, "name": "ui"}, "guards")
        assert exc_info.value.code == 1

    def test_progress_tag_uses_human_friendly_step_counts(self, executor):
        def fake_invoke(step, preamble):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == step["step"]:
                    item["status"] = "completed"
                    item["summary"] = "done"
            executor._write_json(executor._index_file, index)

        executor._invoke_codex = fake_invoke
        executor._run_repo_checks = lambda: MagicMock(returncode=0, stderr="")
        executor._commit_step = lambda *args: None

        with patch.object(ex, "progress_indicator") as mock_progress:
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(elapsed=0)
            cm.__exit__.return_value = False
            mock_progress.return_value = cm
            assert executor._execute_single_step({"step": 2, "name": "ui"}, "guards") is True
        assert mock_progress.call_args[0][0].startswith("Step 3/3")

    def test_uses_lower_circuit_breaker_threshold(self, executor):
        calls = []

        def fake_invoke(step, preamble):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == step["step"]:
                    item["status"] = "error"
                    item["error_message"] = "same failure"
            executor._write_json(executor._index_file, index)

        def fake_run_hook(script_name, *args, env=None):
            calls.append((script_name, args, env))
            return MagicMock(returncode=0, stderr="")

        executor._invoke_codex = fake_invoke
        executor._run_hook = fake_run_hook

        with patch.object(ex, "progress_indicator") as mock_progress:
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(elapsed=0)
            cm.__exit__.return_value = False
            mock_progress.return_value = cm
            with pytest.raises(SystemExit):
                executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

        cb_call = next(call for call in calls if call[0] == "circuit-breaker.sh")
        assert cb_call[2]["CIRCUIT_BREAKER_THRESHOLD"] == str(ex.StepExecutor.CIRCUIT_BREAKER_THRESHOLD)

    def test_completed_step_runs_repo_checks(self, executor):
        calls = []

        def fake_invoke(step, preamble):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == step["step"]:
                    item["status"] = "completed"
                    item["summary"] = "done"
            executor._write_json(executor._index_file, index)

        executor._invoke_codex = fake_invoke
        executor._run_repo_checks = lambda: calls.append("repo-checks") or MagicMock(returncode=0, stderr="")
        executor._commit_step = lambda *args: None

        with patch.object(ex, "progress_indicator") as mock_progress:
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(elapsed=0)
            cm.__exit__.return_value = False
            mock_progress.return_value = cm
            assert executor._execute_single_step({"step": 2, "name": "ui"}, "guards") is True
        assert calls == ["repo-checks"]

    def test_reference_contract_success_allows_completion(self, executor):
        with patch.object(ex, "ROOT", Path(executor._root)):
            raw_dir = Path(executor._root) / "raw"
            raw_dir.mkdir(exist_ok=True)
            (raw_dir / "paper.pdf").write_text("pdf placeholder", encoding="utf-8")
            reference_dir = executor._steps_dir / "artifacts" / "reference"
            reference_dir.mkdir(parents=True)
            (reference_dir / "paper-notes.md").write_text("Eq. (12)\nrho0 = 1000\n", encoding="utf-8")

            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == 2:
                    item["type"] = "reference"
                    item["reference_contract"] = {
                        "source_files": ["raw/paper.pdf"],
                        "output_paths": ["steps/artifacts/reference/paper-notes.md"],
                        "required_items": ["Eq. (12)", "rho0"],
                    }
            executor._write_json(executor._index_file, index)

            def fake_invoke(step, preamble):
                index = executor._read_json(executor._index_file)
                for item in index["steps"]:
                    if item["step"] == step["step"]:
                        item["status"] = "completed"
                        item["summary"] = "done"
                executor._write_json(executor._index_file, index)

            executor._invoke_codex = fake_invoke
            executor._run_repo_checks = lambda: MagicMock(returncode=0, stderr="")
            executor._commit_step = lambda *args: None

            with patch.object(ex, "progress_indicator") as mock_progress:
                cm = MagicMock()
                cm.__enter__.return_value = MagicMock(elapsed=0)
                cm.__exit__.return_value = False
                mock_progress.return_value = cm
                assert executor._execute_single_step({"step": 2, "name": "ui"}, "guards") is True

    def test_results_contract_success_allows_completion(self, executor):
        with patch.object(ex, "ROOT", Path(executor._root)):
            (Path(executor._root) / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
            results_dir = Path(executor._root) / "results" / "case"
            results_dir.mkdir(parents=True)
            (results_dir / "run.log").write_text("ok", encoding="utf-8")
            (results_dir / "comparison.md").write_text("metric diff", encoding="utf-8")
            (results_dir / "validation.log").write_text(
                "$ ctest --test-dir build --output-on-failure\nall passed\n",
                encoding="utf-8",
            )
            (results_dir / "summary.md").write_text(
                "## 실험명\n"
                "- 실행 명령: ctest --test-dir build --output-on-failure\n"
                "- 실행 로그 위치: results/case/validation.log\n"
                "- 출력 위치: results/case\n"
                "- 비교 기준: baseline\n"
                "- 핵심 결과: passed\n",
                encoding="utf-8",
            )

            index = executor._read_json(executor._index_file)
            index["validation_scope"] = "external-target"
            index["target_root"] = "."
            for item in index["steps"]:
                if item["step"] == 2:
                    item["type"] = "validation"
                    item["validation_commands"] = ["ctest --test-dir build --output-on-failure"]
                    item["results_contract"] = {
                        "summary_path": "results/case/summary.md",
                        "output_paths": ["results/case/run.log"],
                        "comparison_artifacts": ["results/case/comparison.md"],
                        "comparison_basis": "baseline",
                        "validation_log_paths": ["results/case/validation.log"],
                    }
            executor._write_json(executor._index_file, index)

            def fake_invoke(step, preamble):
                index = executor._read_json(executor._index_file)
                for item in index["steps"]:
                    if item["step"] == step["step"]:
                        item["status"] = "completed"
                        item["summary"] = "done"
                executor._write_json(executor._index_file, index)

            executor._invoke_codex = fake_invoke
            executor._run_repo_checks = lambda: MagicMock(returncode=0, stderr="")
            executor._commit_step = lambda *args: None

            with patch.object(ex, "progress_indicator") as mock_progress:
                cm = MagicMock()
                cm.__enter__.return_value = MagicMock(elapsed=0)
                cm.__exit__.return_value = False
                mock_progress.return_value = cm
                assert executor._execute_single_step({"step": 2, "name": "ui"}, "guards") is True

    def test_results_contract_missing_validation_log_fails_completion(self, executor):
        with patch.object(ex, "ROOT", Path(executor._root)):
            (Path(executor._root) / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
            results_dir = Path(executor._root) / "results" / "case"
            results_dir.mkdir(parents=True)
            (results_dir / "run.log").write_text("ok", encoding="utf-8")
            (results_dir / "comparison.md").write_text("metric diff", encoding="utf-8")
            (results_dir / "summary.md").write_text(
                "## 실험명\n"
                "- 실행 명령: ctest --test-dir build --output-on-failure\n"
                "- 실행 로그 위치: results/case/validation.log\n"
                "- 출력 위치: results/case\n"
                "- 비교 기준: baseline\n"
                "- 핵심 결과: passed\n",
                encoding="utf-8",
            )

            index = executor._read_json(executor._index_file)
            index["validation_scope"] = "external-target"
            index["target_root"] = "."
            for item in index["steps"]:
                if item["step"] == 2:
                    item["type"] = "validation"
                    item["validation_commands"] = ["ctest --test-dir build --output-on-failure"]
                    item["results_contract"] = {
                        "summary_path": "results/case/summary.md",
                        "output_paths": ["results/case/run.log"],
                        "comparison_artifacts": ["results/case/comparison.md"],
                        "comparison_basis": "baseline",
                        "validation_log_paths": ["results/case/validation.log"],
                    }
            executor._write_json(executor._index_file, index)

            def fake_invoke(step, preamble):
                index = executor._read_json(executor._index_file)
                for item in index["steps"]:
                    if item["step"] == step["step"]:
                        item["status"] = "completed"
                        item["summary"] = "done"
                executor._write_json(executor._index_file, index)

            executor._invoke_codex = fake_invoke
            executor._run_repo_checks = lambda: MagicMock(returncode=0, stderr="")
            executor._commit_step = lambda *args: None

            with patch.object(ex, "progress_indicator") as mock_progress:
                cm = MagicMock()
                cm.__enter__.return_value = MagicMock(elapsed=0)
                cm.__exit__.return_value = False
                mock_progress.return_value = cm
                with pytest.raises(SystemExit) as exc_info:
                    executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

            assert exc_info.value.code == 1
            updated = executor._read_json(executor._index_file)
            step_entry = next(item for item in updated["steps"] if item["step"] == 2)
            assert step_entry["status"] == "error"
            assert "validation log not found" in step_entry["error_message"]

    def test_results_contract_missing_command_evidence_fails_completion(self, executor):
        with patch.object(ex, "ROOT", Path(executor._root)):
            (Path(executor._root) / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
            results_dir = Path(executor._root) / "results" / "case"
            results_dir.mkdir(parents=True)
            (results_dir / "run.log").write_text("ok", encoding="utf-8")
            (results_dir / "comparison.md").write_text("metric diff", encoding="utf-8")
            (results_dir / "validation.log").write_text("all passed\n", encoding="utf-8")
            (results_dir / "summary.md").write_text(
                "## 실험명\n"
                "- 실행 명령: cmake --build build\n"
                "- 실행 로그 위치: results/case/validation.log\n"
                "- 출력 위치: results/case\n"
                "- 비교 기준: baseline\n"
                "- 핵심 결과: passed\n",
                encoding="utf-8",
            )

            index = executor._read_json(executor._index_file)
            index["validation_scope"] = "external-target"
            index["target_root"] = "."
            for item in index["steps"]:
                if item["step"] == 2:
                    item["type"] = "validation"
                    item["validation_commands"] = ["ctest --test-dir build --output-on-failure"]
                    item["results_contract"] = {
                        "summary_path": "results/case/summary.md",
                        "output_paths": ["results/case/run.log"],
                        "comparison_artifacts": ["results/case/comparison.md"],
                        "comparison_basis": "baseline",
                        "validation_log_paths": ["results/case/validation.log"],
                    }
            executor._write_json(executor._index_file, index)

            def fake_invoke(step, preamble):
                index = executor._read_json(executor._index_file)
                for item in index["steps"]:
                    if item["step"] == step["step"]:
                        item["status"] = "completed"
                        item["summary"] = "done"
                executor._write_json(executor._index_file, index)

            executor._invoke_codex = fake_invoke
            executor._run_repo_checks = lambda: MagicMock(returncode=0, stderr="")
            executor._commit_step = lambda *args: None

            with patch.object(ex, "progress_indicator") as mock_progress:
                cm = MagicMock()
                cm.__enter__.return_value = MagicMock(elapsed=0)
                cm.__exit__.return_value = False
                mock_progress.return_value = cm
                with pytest.raises(SystemExit) as exc_info:
                    executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

            assert exc_info.value.code == 1
            updated = executor._read_json(executor._index_file)
            step_entry = next(item for item in updated["steps"] if item["step"] == 2)
            assert step_entry["status"] == "error"
            assert "missing executed validation command" in step_entry["error_message"]

    def test_commit_failure_marks_step_error(self, executor):
        def fake_invoke(step, preamble):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == step["step"]:
                    item["status"] = "completed"
                    item["summary"] = "done"
            executor._write_json(executor._index_file, index)

        executor._invoke_codex = fake_invoke
        executor._run_repo_checks = lambda: MagicMock(returncode=0, stderr="")
        executor._commit_step = lambda *args: (_ for _ in ()).throw(ex.CommitStepError("code commit failed: nope"))

        with patch.object(ex, "progress_indicator") as mock_progress:
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(elapsed=0)
            cm.__exit__.return_value = False
            mock_progress.return_value = cm
            with pytest.raises(SystemExit) as exc_info:
                executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

        assert exc_info.value.code == 1
        updated = executor._read_json(executor._index_file)
        step_entry = next(item for item in updated["steps"] if item["step"] == 2)
        assert step_entry["status"] == "error"
        assert "[commit]" in step_entry["error_message"]


class TestFinalize:
    def test_prints_completion_notice(self, executor, capsys):
        executor._run_git = lambda *args: MagicMock(returncode=0, stdout="", stderr="")
        executor._finalize()
        out = capsys.readouterr().out
        assert "run completed at" in out
        assert "Goal 'mvp' completed!" in out
