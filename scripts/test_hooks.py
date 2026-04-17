import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _copy_hook(tmp_path: Path, name: str) -> Path:
    dst = tmp_path / "scripts" / "hooks" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "scripts" / "hooks" / name, dst)
    os.chmod(dst, 0o755)
    return dst


class TestDangerousCmdGuard:
    def test_blocks_dangerous_command(self, tmp_path):
        script = _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        proc = subprocess.run(
            [str(script), "rm -rf build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 1
        assert "BLOCKED" in proc.stderr

    def test_allows_harmless_command(self, tmp_path):
        script = _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        proc = subprocess.run(
            [str(script), "pytest -q scripts"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 0

    def test_blocks_force_with_lease_and_clean_fdx(self, tmp_path):
        script = _copy_hook(tmp_path, "dangerous-cmd-guard.sh")

        push_proc = subprocess.run(
            [str(script), "git push --force-with-lease origin branch"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        clean_proc = subprocess.run(
            [str(script), "git clean -fdx"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        rm_proc = subprocess.run(
            [str(script), "rm -r -f build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert push_proc.returncode == 1
        assert clean_proc.returncode == 1
        assert rm_proc.returncode == 1

    def test_allows_instructional_text_with_dangerous_example(self, tmp_path):
        script = _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        proc = subprocess.run(
            [str(script), "금지사항: git reset --hard 를 사용하지 마라"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 0


class TestTddGuard:
    def test_python_impl_requires_python_test(self, tmp_path):
        script = _copy_hook(tmp_path, "tdd-guard.sh")
        (tmp_path / "module").mkdir()
        (tmp_path / "module" / "foo.py").write_text("print('x')\n", encoding="utf-8")
        (tmp_path / "module" / "test_foo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

        proc = subprocess.run(
            [str(script), "module/foo.py"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 0

    def test_native_impl_requires_related_test(self, tmp_path):
        script = _copy_hook(tmp_path, "tdd-guard.sh")
        (tmp_path / "src").mkdir()
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        (tmp_path / "src" / "solver.cu").write_text("__global__ void run() {}\n", encoding="utf-8")
        (tmp_path / "tests" / "unit" / "solver_test.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

        proc = subprocess.run(
            [str(script), "src/solver.cu"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 0

    def test_native_impl_without_test_fails(self, tmp_path):
        script = _copy_hook(tmp_path, "tdd-guard.sh")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "solver.cu").write_text("__global__ void run() {}\n", encoding="utf-8")

        proc = subprocess.run(
            [str(script), "src/solver.cu"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 1
        assert "solver" in proc.stderr

    def test_native_diff_is_detected_in_default_mode(self, tmp_path):
        script = _copy_hook(tmp_path, "tdd-guard.sh")
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True, text=True)

        (tmp_path / "src").mkdir()
        tracked = tmp_path / "src" / "solver.cu"
        tracked.write_text("__global__ void step1() {}\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/solver.cu"], cwd=tmp_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

        tracked.write_text("__global__ void step2() {}\n", encoding="utf-8")

        proc = subprocess.run(
            [str(script)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 1
        assert "solver.cu" in proc.stderr

    def test_cmakelists_change_does_not_bypass_native_guard(self, tmp_path):
        script = _copy_hook(tmp_path, "tdd-guard.sh")
        (tmp_path / "src").mkdir()
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        (tmp_path / "src" / "solver.cu").write_text("__global__ void run() {}\n", encoding="utf-8")
        (tmp_path / "tests" / "unit" / "CMakeLists.txt").write_text("add_executable(dummy dummy.cpp)\n", encoding="utf-8")

        proc = subprocess.run(
            [str(script), "src/solver.cu", "tests/unit/CMakeLists.txt"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 1
        assert "solver" in proc.stderr

    def test_raw_reference_code_is_ignored(self, tmp_path):
        script = _copy_hook(tmp_path, "tdd-guard.sh")
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "solver.cu").write_text("__global__ void run() {}\n", encoding="utf-8")

        proc = subprocess.run(
            [str(script), "raw/solver.cu"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 0

    def test_generated_build_output_is_ignored(self, tmp_path):
        script = _copy_hook(tmp_path, "tdd-guard.sh")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "generated.cu").write_text("__global__ void run() {}\n", encoding="utf-8")

        proc = subprocess.run(
            [str(script), "build/generated.cu"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 0


class TestCodexRun:
    def test_exec_prompt_is_guarded(self, tmp_path):
        script = tmp_path / "scripts" / "codex_run.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / "codex_run.sh", script)
        os.chmod(script, 0o755)
        _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        _copy_hook(tmp_path, "circuit-breaker.sh")

        proc = subprocess.run(
            [str(script), "exec", "rm -rf build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 1
        assert "BLOCKED" in proc.stderr

    def test_exec_guard_checks_all_payload_args(self, tmp_path):
        script = tmp_path / "scripts" / "codex_run.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / "codex_run.sh", script)
        os.chmod(script, 0o755)
        _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        _copy_hook(tmp_path, "circuit-breaker.sh")

        proc = subprocess.run(
            [str(script), "exec", "safe", "rm -rf build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        assert proc.returncode == 1
        assert "BLOCKED" in proc.stderr

    def test_failed_codex_still_triggers_circuit_breaker(self, tmp_path):
        script = tmp_path / "scripts" / "codex_run.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / "codex_run.sh", script)
        os.chmod(script, 0o755)
        _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        _copy_hook(tmp_path, "circuit-breaker.sh")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        codex_bin = bin_dir / "codex"
        codex_bin.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
        os.chmod(codex_bin, 0o755)

        proc = subprocess.run(
            [str(script), "exec", "safe prompt"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert proc.returncode == 7
        state_file = tmp_path / ".codex" / "tmp" / "circuit_breaker.log"
        assert state_file.exists()
        assert "codex_run exit code 7" in state_file.read_text(encoding="utf-8")

    def test_prints_start_and_finish_messages(self, tmp_path):
        script = tmp_path / "scripts" / "codex_run.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / "codex_run.sh", script)
        os.chmod(script, 0o755)
        _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        _copy_hook(tmp_path, "circuit-breaker.sh")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        codex_bin = bin_dir / "codex"
        codex_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(codex_bin, 0o755)

        proc = subprocess.run(
            [str(script), "exec", "safe prompt"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert proc.returncode == 0
        assert "[codex_run] starting:" in proc.stderr
        assert "[codex_run] Codex finished successfully." in proc.stderr

    def test_run_checks_preserves_original_codex_failure(self, tmp_path):
        script = tmp_path / "scripts" / "codex_run.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / "codex_run.sh", script)
        os.chmod(script, 0o755)
        _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        _copy_hook(tmp_path, "circuit-breaker.sh")

        repo_checks = tmp_path / "scripts" / "codex_repo_checks.sh"
        repo_checks.write_text("#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
        os.chmod(repo_checks, 0o755)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        codex_bin = bin_dir / "codex"
        codex_bin.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
        os.chmod(codex_bin, 0o755)

        proc = subprocess.run(
            [str(script), "--run-checks", "exec", "safe prompt"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert proc.returncode == 7
        assert "repo checks failed" in proc.stderr

    def test_run_checks_returns_repo_check_failure_after_successful_codex(self, tmp_path):
        script = tmp_path / "scripts" / "codex_run.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / "codex_run.sh", script)
        os.chmod(script, 0o755)
        _copy_hook(tmp_path, "dangerous-cmd-guard.sh")
        _copy_hook(tmp_path, "circuit-breaker.sh")

        repo_checks = tmp_path / "scripts" / "codex_repo_checks.sh"
        repo_checks.write_text("#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
        os.chmod(repo_checks, 0o755)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        codex_bin = bin_dir / "codex"
        codex_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(codex_bin, 0o755)

        proc = subprocess.run(
            [str(script), "--run-checks", "exec", "safe prompt"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

        assert proc.returncode == 9
