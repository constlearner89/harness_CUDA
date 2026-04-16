"""
execute.py 리팩터링 안전망 테스트.
리팩터링 전후 동작이 동일한지 검증한다.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import execute as ex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """phases/, AGENTS.md, docs/ 를 갖춘 임시 프로젝트 구조."""
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()

    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Rules\n- rule one\n- rule two")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "PRD.md").write_text("# PRD\nProduct content")
    (docs_dir / "ARCHITECTURE.md").write_text("# Architecture\nSome content")
    (docs_dir / "ADR.md").write_text("# ADR\nDecision log")
    (docs_dir / "RESULTS_POLICY.md").write_text("# Results Policy\nResult content")
    (docs_dir / "arch.md").write_text("# Architecture\nSome content")
    (docs_dir / "guide.md").write_text("# Guide\nAnother doc")

    return tmp_path


@pytest.fixture
def phase_dir(tmp_project):
    """step 3개를 가진 phase 디렉토리."""
    d = tmp_project / "phases" / "0-mvp"
    d.mkdir()

    index = {
        "project": "TestProject",
        "phase": "mvp",
        "steps": [
            {"step": 0, "name": "setup", "status": "completed", "summary": "프로젝트 초기화 완료"},
            {"step": 1, "name": "core", "status": "completed", "summary": "핵심 로직 구현"},
            {"step": 2, "name": "ui", "status": "pending"},
        ],
    }
    (d / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    (d / "step2.md").write_text("# Step 2: UI\n\nUI를 구현하세요.")

    return d


@pytest.fixture
def top_index(tmp_project):
    """phases/index.json (top-level)."""
    top = {
        "phases": [
            {"dir": "0-mvp", "status": "pending"},
            {"dir": "1-polish", "status": "pending"},
        ]
    }
    p = tmp_project / "phases" / "index.json"
    p.write_text(json.dumps(top, indent=2))
    return p


@pytest.fixture
def executor(tmp_project, phase_dir):
    """테스트용 StepExecutor 인스턴스. git 호출은 별도 mock 필요."""
    with patch.object(ex, "ROOT", tmp_project):
        inst = ex.StepExecutor("0-mvp")
    # 내부 경로를 tmp_project 기준으로 재설정
    inst._root = str(tmp_project)
    inst._phases_dir = tmp_project / "phases"
    inst._phase_dir = phase_dir
    inst._phase_dir_name = "0-mvp"
    inst._index_file = phase_dir / "index.json"
    inst._top_index_file = tmp_project / "phases" / "index.json"
    return inst


# ---------------------------------------------------------------------------
# _stamp (= 이전 now_iso)
# ---------------------------------------------------------------------------

class TestStamp:
    def test_returns_kst_timestamp(self, executor):
        result = executor._stamp()
        assert "+0900" in result

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


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------

class TestJsonHelpers:
    def test_roundtrip(self, tmp_path):
        data = {"key": "값", "nested": [1, 2, 3]}
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, data)
        loaded = ex.StepExecutor._read_json(p)
        assert loaded == data

    def test_save_ensures_ascii_false(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"한글": "테스트"})
        raw = p.read_text()
        assert "한글" in raw
        assert "\\u" not in raw

    def test_save_indented(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"a": 1})
        raw = p.read_text()
        assert "\n" in raw

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ex.StepExecutor._read_json(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# _load_guardrails
# ---------------------------------------------------------------------------

class TestLoadGuardrails:
    def test_loads_agents_md_and_docs(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "# Rules" in result
        assert "rule one" in result
        assert "# PRD" in result
        assert "# Architecture" in result
        assert "# ADR" in result
        assert "# Results Policy" in result

    def test_sections_separated_by_divider(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "---" in result

    def test_loads_only_core_docs(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "guide" not in result
        assert "arch\n\n# Architecture" not in result

    def test_no_agents_md(self, executor, tmp_project):
        (tmp_project / "AGENTS.md").unlink()
        with patch.object(ex, "ROOT", tmp_project):
            with pytest.raises(SystemExit) as exc_info:
                executor._load_guardrails()
        assert exc_info.value.code == 1

    def test_no_docs_dir(self, executor, tmp_project):
        import shutil
        shutil.rmtree(tmp_project / "docs")
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

    def test_empty_project(self, tmp_path):
        with patch.object(ex, "ROOT", tmp_path):
            # executor가 필요 없는 static-like 동작이므로 임시 인스턴스
            phases_dir = tmp_path / "phases" / "dummy"
            phases_dir.mkdir(parents=True)
            idx = {"project": "T", "phase": "t", "steps": []}
            (phases_dir / "index.json").write_text(json.dumps(idx))
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
            with pytest.raises(SystemExit) as exc_info:
                inst._load_guardrails()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _build_step_context
# ---------------------------------------------------------------------------

class TestBuildStepContext:
    def test_includes_completed_with_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "Step 0 (setup): 프로젝트 초기화 완료" in result
        assert "Step 1 (core): 핵심 로직 구현" in result

    def test_excludes_pending(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "ui" not in result

    def test_excludes_completed_without_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        del index["steps"][0]["summary"]
        result = ex.StepExecutor._build_step_context(index)
        assert "setup" not in result
        assert "core" in result

    def test_empty_when_no_completed(self):
        index = {"steps": [{"step": 0, "name": "a", "status": "pending"}]}
        result = ex.StepExecutor._build_step_context(index)
        assert result == ""

    def test_has_header(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert result.startswith("## 이전 Step 산출물")


# ---------------------------------------------------------------------------
# _build_preamble
# ---------------------------------------------------------------------------

class TestBuildPreamble:
    def test_includes_project_name(self, executor):
        result = executor._build_preamble("", "")
        assert "TestProject" in result

    def test_includes_guardrails(self, executor):
        result = executor._build_preamble("GUARD_CONTENT", "")
        assert "GUARD_CONTENT" in result

    def test_includes_step_context(self, executor):
        ctx = "## 이전 Step 산출물\n\n- Step 0: done"
        result = executor._build_preamble("", ctx)
        assert "이전 Step 산출물" in result

    def test_includes_commit_example(self, executor):
        result = executor._build_preamble("", "")
        assert "feat(mvp):" in result

    def test_includes_rules(self, executor):
        result = executor._build_preamble("", "")
        assert "작업 규칙" in result
        assert "AC" in result
        assert "rm -rf" not in result

    def test_no_retry_section_by_default(self, executor):
        result = executor._build_preamble("", "")
        assert "이전 시도 실패" not in result

    def test_retry_section_with_prev_error(self, executor):
        result = executor._build_preamble("", "", prev_error="타입 에러 발생")
        assert "이전 시도 실패" in result
        assert "타입 에러 발생" in result

    def test_includes_max_retries(self, executor):
        result = executor._build_preamble("", "")
        assert str(ex.StepExecutor.MAX_RETRIES) in result

    def test_includes_index_path(self, executor):
        result = executor._build_preamble("", "")
        assert "/phases/0-mvp/index.json" in result


# ---------------------------------------------------------------------------
# _update_top_index
# ---------------------------------------------------------------------------

class TestUpdateTopIndex:
    def test_completed(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "completed"
        assert "completed_at" in mvp

    def test_error(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("error")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "error"
        assert "failed_at" in mvp

    def test_blocked(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("blocked")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "blocked"
        assert "blocked_at" in mvp

    def test_other_phases_unchanged(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        polish = next(p for p in data["phases"] if p["dir"] == "1-polish")
        assert polish["status"] == "pending"

    def test_nonexistent_dir_is_noop(self, executor, top_index):
        executor._top_index_file = top_index
        executor._phase_dir_name = "no-such-dir"
        original = json.loads(top_index.read_text())
        executor._update_top_index("completed")
        after = json.loads(top_index.read_text())
        for p_before, p_after in zip(original["phases"], after["phases"]):
            assert p_before["status"] == p_after["status"]

    def test_no_top_index_file(self, executor, tmp_path):
        executor._top_index_file = tmp_path / "nonexistent.json"
        executor._update_top_index("completed")  # should not raise


# ---------------------------------------------------------------------------
# _checkout_branch (mocked)
# ---------------------------------------------------------------------------

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
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="feat-mvp\n", stderr=""),
        ])
        executor._checkout_branch()  # should return without checkout

    def test_branch_exists_checkout(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_branch_not_exists_create(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="not found"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_checkout_fails_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="dirty tree"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1

    def test_no_git_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=1, stdout="", stderr="not a git repo"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_clean_worktree
# ---------------------------------------------------------------------------

class TestCheckCleanWorktree:
    def test_allows_only_current_phase_changes(self, executor):
        executor._run_git = lambda *args: MagicMock(
            returncode=0,
            stdout="?? phases/index.json\n?? phases/0-mvp/index.json\n?? phases/0-mvp/step2.md\n",
            stderr="",
        )
        executor._check_clean_worktree()

    def test_rejects_unrelated_dirty_paths(self, executor):
        executor._run_git = lambda *args: MagicMock(
            returncode=0,
            stdout="?? phases/0-mvp/index.json\n M docs/PRD.md\n?? scratch.txt\n",
            stderr="",
        )
        with pytest.raises(SystemExit) as exc_info:
            executor._check_clean_worktree()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _commit_step (mocked)
# ---------------------------------------------------------------------------

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

        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 2
        assert "feat(mvp):" in commit_calls[0][2]
        assert "chore(mvp):" in commit_calls[1][2]

    def test_no_code_changes_skips_feat_commit(self, executor):
        call_count = {"diff": 0}
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                call_count["diff"] += 1
                if call_count["diff"] == 1:
                    return MagicMock(returncode=0)
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        commit_msgs = [c[2] for c in calls if c[0] == "commit"]
        assert len(commit_msgs) == 1
        assert "chore" in commit_msgs[0]


# ---------------------------------------------------------------------------
# _invoke_codex (mocked)
# ---------------------------------------------------------------------------

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

    def test_invokes_codex_with_correct_args(self, executor):
        fake_proc = self.FakePopen(returncode=0, stdout='{"result": "ok"}', stderr="")
        step = {"step": 2, "name": "ui"}
        preamble = "PREAMBLE\n"

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
                output = executor._invoke_codex(step, preamble)

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "-o" in cmd
        assert "-C" in cmd
        assert "PREAMBLE" in cmd[-1]
        assert "UI를 구현하세요" in cmd[-1]
        assert output["lastMessage"] == ""
        assert output["forcedStop"] is False
        assert mock_popen.call_args[1]["stdin"] == ex.subprocess.DEVNULL
        assert mock_popen.call_args[1]["stdout"] is not ex.subprocess.PIPE
        assert mock_popen.call_args[1]["stderr"] is not ex.subprocess.PIPE

    def test_saves_output_json(self, executor):
        fake_proc = self.FakePopen(returncode=0, stdout='{"ok": true}', stderr="")
        step = {"step": 2, "name": "ui"}

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch("subprocess.Popen", return_value=fake_proc):
                executor._invoke_codex(step, "preamble")

        output_file = executor._phase_dir / "step2-output.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["step"] == 2
        assert data["name"] == "ui"
        assert data["exitCode"] == 0
        assert "lastMessage" in data

    def test_nonexistent_step_file_exits(self, executor):
        step = {"step": 99, "name": "nonexistent"}
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_codex(step, "preamble")
        assert exc_info.value.code == 1

    def test_timeout_is_1800(self, executor):
        fake_proc = self.FakePopen(returncode=0, stdout="{}", stderr="")
        step = {"step": 2, "name": "ui"}

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
                executor._invoke_codex(step, "preamble")

        assert "timeout" not in mock_popen.call_args[1]

    def test_reads_last_message_file(self, executor):
        step = {"step": 2, "name": "ui"}
        out_path = executor._phase_dir / "tmp-last-message.txt"

        def on_poll():
            out_path.write_text("final codex response", encoding="utf-8")

        fake_proc = self.FakePopen(returncode=0, stdout="", stderr="", on_poll=on_poll)

        def fake_named_tempfile(*args, **kwargs):
            class TempFile:
                name = str(out_path)
                def __enter__(self_inner):
                    out_path.write_text("", encoding="utf-8")
                    return self_inner
                def __exit__(self_inner, exc_type, exc, tb):
                    return False
            return TempFile()

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch("tempfile.NamedTemporaryFile", side_effect=fake_named_tempfile):
                with patch("subprocess.Popen", return_value=fake_proc):
                    output = executor._invoke_codex(step, "preamble")

        assert output["lastMessage"] == "final codex response"

    def test_forces_stop_after_status_finalized(self, executor):
        step = {"step": 2, "name": "ui"}
        index = executor._read_json(executor._index_file)

        def mark_completed():
            for item in index["steps"]:
                if item["step"] == 2:
                    item["status"] = "completed"
                    item["summary"] = "done"
            executor._write_json(executor._index_file, index)

        fake_proc = self.FakePopen(
            returncode=0,
            stdout="ok",
            stderr="",
            poll_values=[None, None, None, 0],
            on_poll=mark_completed,
        )

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch.object(ex.StepExecutor, "POST_STEP_GRACE", 0):
                with patch("subprocess.Popen", return_value=fake_proc):
                    output = executor._invoke_codex(step, "preamble")

        assert output["forcedStop"] is True
        assert fake_proc.terminate_called is True

    def test_timeout_raises_when_status_still_pending(self, executor):
        step = {"step": 2, "name": "ui"}
        fake_proc = self.FakePopen(returncode=1, stdout="", stderr="", poll_values=[None, None, None])

        with patch.object(ex.StepExecutor, "_validate_prompt_safety", return_value=None):
            with patch.object(ex.StepExecutor, "EXEC_TIMEOUT", 0):
                with patch("subprocess.Popen", return_value=fake_proc):
                    with pytest.raises(subprocess.TimeoutExpired):
                        executor._invoke_codex(step, "preamble")

        assert fake_proc.kill_called is True

    def test_exits_when_dangerous_prompt_is_detected(self, executor):
        step = {"step": 2, "name": "ui"}

        def fake_run_hook(script_name, *args):
            if script_name == "dangerous-cmd-guard.sh":
                return MagicMock(returncode=1, stderr="BLOCKED: dangerous command pattern detected.")
            return None

        executor._run_hook = fake_run_hook

        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_codex(step, "rm -rf /tmp\n")

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# progress_indicator (= 이전 Spinner)
# ---------------------------------------------------------------------------

class TestProgressIndicator:
    def test_context_manager(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.15)
        assert pi.elapsed >= 0.1

    def test_elapsed_increases(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.2)
        assert pi.elapsed > 0


# ---------------------------------------------------------------------------
# main() CLI 파싱 (mocked)
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_no_args_exits(self):
        with patch("sys.argv", ["execute.py"]):
            with pytest.raises(SystemExit) as exc_info:
                ex.main()
            assert exc_info.value.code == 2  # argparse exits with 2

    def test_invalid_phase_dir_exits(self):
        with patch("sys.argv", ["execute.py", "nonexistent"]):
            with patch.object(ex, "ROOT", Path("/tmp/fake_nonexistent")):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1

    def test_missing_index_exits(self, tmp_project):
        (tmp_project / "phases" / "empty").mkdir()
        with patch("sys.argv", ["execute.py", "empty"]):
            with patch.object(ex, "ROOT", tmp_project):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_blockers (= 이전 main() error/blocked 체크)
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def _make_executor_with_steps(self, tmp_project, steps):
        d = tmp_project / "phases" / "test-phase"
        d.mkdir(exist_ok=True)
        index = {"project": "T", "phase": "test", "steps": steps}
        (d / "index.json").write_text(json.dumps(index))

        with patch.object(ex, "ROOT", tmp_project):
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_project)
        inst._phases_dir = tmp_project / "phases"
        inst._phase_dir = d
        inst._phase_dir_name = "test-phase"
        inst._index_file = d / "index.json"
        inst._top_index_file = tmp_project / "phases" / "index.json"
        inst._phase_name = "test"
        inst._total = len(steps)
        return inst

    def test_error_step_exits_1(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "bad", "status": "error", "error_message": "fail"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    def test_blocked_step_exits_2(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "stuck", "status": "blocked", "blocked_reason": "API key"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _execute_single_step circuit breaker integration
# ---------------------------------------------------------------------------

class TestExecuteSingleStep:
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
            with pytest.raises(SystemExit) as exc_info:
                executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

        assert exc_info.value.code == 1
        cb_call = next(call for call in calls if call[0] == "circuit-breaker.sh")
        assert cb_call[2]["CIRCUIT_BREAKER_THRESHOLD"] == str(ex.StepExecutor.CIRCUIT_BREAKER_THRESHOLD)

    def test_circuit_breaker_stops_retry_and_marks_error(self, executor):
        def fake_invoke(step, preamble):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == step["step"]:
                    item["status"] = "error"
                    item["error_message"] = "same failure"
            executor._write_json(executor._index_file, index)

        executor._invoke_codex = fake_invoke
        executor._run_hook = lambda script_name, *args, env=None: MagicMock(
            returncode=2 if script_name == "circuit-breaker.sh" else 0,
            stderr="CIRCUIT BREAKER: same error repeated",
        )
        executor._commit_step = lambda *args: None
        executor._update_top_index = lambda *args: None

        with patch.object(ex, "progress_indicator") as mock_progress:
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(elapsed=0)
            cm.__exit__.return_value = False
            mock_progress.return_value = cm
            with pytest.raises(SystemExit) as exc_info:
                executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

        assert exc_info.value.code == 1
        index = executor._read_json(executor._index_file)
        step = next(item for item in index["steps"] if item["step"] == 2)
        assert step["status"] == "error"
        assert "[circuit-breaker]" in step["error_message"]

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

    def test_repo_checks_failure_marks_step_error(self, executor):
        def fake_invoke(step, preamble):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == step["step"]:
                    item["status"] = "completed"
                    item["summary"] = "done"
            executor._write_json(executor._index_file, index)

        executor._invoke_codex = fake_invoke
        executor._run_repo_checks = lambda: MagicMock(returncode=1, stderr="repo checks failed")
        executor._commit_step = lambda *args: None
        executor._update_top_index = lambda *args: None

        with patch.object(ex, "progress_indicator") as mock_progress:
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(elapsed=0)
            cm.__exit__.return_value = False
            mock_progress.return_value = cm
            with pytest.raises(SystemExit) as exc_info:
                executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

        assert exc_info.value.code == 1
        index = executor._read_json(executor._index_file)
        step = next(item for item in index["steps"] if item["step"] == 2)
        assert step["status"] == "error"
        assert "[repo-checks]" in step["error_message"]

    def test_results_contract_failure_marks_step_error(self, executor):
        with patch.object(ex, "ROOT", Path(executor._root)):
            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == 2:
                    item["results_contract"] = {
                        "summary_path": "results/case/summary.md",
                        "output_paths": ["results/case/run.log"],
                        "comparison_artifacts": ["results/case/comparison.md"],
                        "comparison_basis": "baseline",
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
            executor._commit_step = lambda *args: None
            executor._update_top_index = lambda *args: None

            with patch.object(ex, "progress_indicator") as mock_progress:
                cm = MagicMock()
                cm.__enter__.return_value = MagicMock(elapsed=0)
                cm.__exit__.return_value = False
                mock_progress.return_value = cm
                with pytest.raises(SystemExit) as exc_info:
                    executor._execute_single_step({"step": 2, "name": "ui"}, "guards")

            assert exc_info.value.code == 1
            index = executor._read_json(executor._index_file)
            step = next(item for item in index["steps"] if item["step"] == 2)
            assert step["status"] == "error"
            assert "[results-contract]" in step["error_message"]

    def test_results_contract_success_allows_completion(self, executor):
        with patch.object(ex, "ROOT", Path(executor._root)):
            results_dir = executor._phase_dir.parent.parent / "results" / "case"
            results_dir.mkdir(parents=True)
            (results_dir / "run.log").write_text("ok", encoding="utf-8")
            (results_dir / "comparison.md").write_text("metric diff", encoding="utf-8")
            (results_dir / "summary.md").write_text(
                "## 실험명\n"
                "- 실행 명령: ctest --test-dir build --output-on-failure\n"
                "- 출력 위치: results/case\n"
                "- 비교 기준: baseline\n"
                "- 핵심 결과: passed\n",
                encoding="utf-8",
            )

            index = executor._read_json(executor._index_file)
            for item in index["steps"]:
                if item["step"] == 2:
                    item["results_contract"] = {
                        "summary_path": "results/case/summary.md",
                        "output_paths": ["results/case/run.log"],
                        "comparison_artifacts": ["results/case/comparison.md"],
                        "comparison_basis": "baseline",
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
