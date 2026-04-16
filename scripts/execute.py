#!/usr/bin/env python3
"""
Harness Step Executor — phase 내 step을 순차 실행하고 자가 교정한다.

Usage:
    python3 scripts/execute.py <phase-dir> [--push]
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def progress_indicator(label: str):
    """터미널 진행 표시기. with 문으로 사용하며 .elapsed 로 경과 시간을 읽는다."""
    frames = "◐◓◑◒"
    stop = threading.Event()
    t0 = time.monotonic()

    def _animate():
        idx = 0
        while not stop.wait(0.12):
            sec = int(time.monotonic() - t0)
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {label} [{sec}s]")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write("\r" + " " * (len(label) + 20) + "\r")
        sys.stderr.flush()

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    info = types.SimpleNamespace(elapsed=0.0)
    try:
        yield info
    finally:
        stop.set()
        th.join()
        info.elapsed = time.monotonic() - t0


class StepExecutor:
    """Phase 디렉토리 안의 step들을 순차 실행하는 하네스."""

    MAX_RETRIES = 3
    EXEC_TIMEOUT = 1800
    POST_STEP_GRACE = 2.0
    TERMINATE_TIMEOUT = 5
    CIRCUIT_BREAKER_THRESHOLD = 2
    GUARDRail_DOCS = ("PRD.md", "ARCHITECTURE.md", "ADR.md", "RESULTS_POLICY.md")
    FEAT_MSG = "feat({phase}): step {num} — {name}"
    CHORE_MSG = "chore({phase}): step {num} output"
    TZ = timezone(timedelta(hours=9))

    def __init__(self, phase_dir_name: str, *, auto_push: bool = False):
        self._root = str(ROOT)
        self._phases_dir = ROOT / "phases"
        self._phase_dir = self._phases_dir / phase_dir_name
        self._phase_dir_name = phase_dir_name
        self._top_index_file = self._phases_dir / "index.json"
        self._auto_push = auto_push

        if not self._phase_dir.is_dir():
            print(f"ERROR: {self._phase_dir} not found")
            sys.exit(1)

        self._index_file = self._phase_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._phase_name = idx.get("phase", phase_dir_name)
        self._total = len(idx["steps"])

    def run(self):
        self._print_header()
        self._check_blockers()
        self._check_clean_worktree()
        self._checkout_branch()
        guardrails = self._load_guardrails()
        self._ensure_created_at()
        self._execute_all_steps(guardrails)
        self._finalize()

    # --- timestamps ---

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- JSON I/O ---

    @staticmethod
    def _read_json(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(p: Path, data: dict):
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _step_status(self, step_num: int) -> str:
        index = self._read_json(self._index_file)
        return next((s.get("status", "pending") for s in index["steps"] if s["step"] == step_num), "pending")

    # --- git ---

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self._root, capture_output=True, text=True)

    @staticmethod
    def _parse_porcelain_path(line: str) -> str:
        entry = line[3:] if len(line) > 3 else ""
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        return entry.strip()

    def _run_hook(self, script_name: str, *args, env: Optional[dict] = None):
        script_path = ROOT / "scripts" / "hooks" / script_name
        if not script_path.exists():
            return None
        hook_env = {**os.environ, **env} if env else None
        return subprocess.run(
            [str(script_path), *args],
            cwd=self._root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=hook_env,
        )

    def _run_repo_checks(self):
        script_path = ROOT / "scripts" / "codex_repo_checks.sh"
        if not script_path.exists():
            return None
        return subprocess.run(
            [str(script_path)],
            cwd=self._root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    def _checkout_branch(self):
        branch = f"feat-{self._phase_name}"

        r = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            print(f"  ERROR: git을 사용할 수 없거나 git repo가 아닙니다.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        if r.stdout.strip() == branch:
            return

        r = self._run_git("rev-parse", "--verify", branch)
        r = self._run_git("checkout", branch) if r.returncode == 0 else self._run_git("checkout", "-b", branch)

        if r.returncode != 0:
            print(f"  ERROR: 브랜치 '{branch}' checkout 실패.")
            print(f"  {r.stderr.strip()}")
            print(f"  Hint: 변경사항을 stash하거나 commit한 후 다시 시도하세요.")
            sys.exit(1)

        print(f"  Branch: {branch}")

    def _check_clean_worktree(self):
        status = self._run_git("status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            print("  ERROR: git status 확인 실패.")
            print(f"  {status.stderr.strip()}")
            sys.exit(1)

        allowed_prefix = f"phases/{self._phase_dir_name}/"
        allowed_paths = {"phases/index.json"}
        dirty_paths = []
        for raw_line in status.stdout.splitlines():
            path = self._parse_porcelain_path(raw_line)
            if not path:
                continue
            if path in allowed_paths or path.startswith(allowed_prefix):
                continue
            dirty_paths.append(path)

        if dirty_paths:
            print("  ERROR: 현재 worktree에 phase 외 변경사항이 있어 자동 실행을 중단합니다.")
            for path in dirty_paths[:10]:
                print(f"  - {path}")
            if len(dirty_paths) > 10:
                print(f"  ... and {len(dirty_paths) - 10} more")
            print("  Hint: 현재 phase 관련 파일만 남기고 나머지는 commit/stash 후 다시 실행하세요.")
            sys.exit(1)

    def _commit_step(self, step_num: int, step_name: str):
        output_rel = f"phases/{self._phase_dir_name}/step{step_num}-output.json"
        index_rel = f"phases/{self._phase_dir_name}/index.json"

        self._run_git("add", "-A")
        self._run_git("reset", "HEAD", "--", output_rel)
        self._run_git("reset", "HEAD", "--", index_rel)

        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(phase=self._phase_name, num=step_num, name=step_name)
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                print(f"  WARN: 코드 커밋 실패: {r.stderr.strip()}")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num)
            r = self._run_git("commit", "-m", msg)
            if r.returncode != 0:
                print(f"  WARN: housekeeping 커밋 실패: {r.stderr.strip()}")

    # --- top-level index ---

    def _update_top_index(self, status: str):
        if not self._top_index_file.exists():
            return
        top = self._read_json(self._top_index_file)
        ts = self._stamp()
        for phase in top.get("phases", []):
            if phase.get("dir") == self._phase_dir_name:
                phase["status"] = status
                ts_key = {"completed": "completed_at", "error": "failed_at", "blocked": "blocked_at"}.get(status)
                if ts_key:
                    phase[ts_key] = ts
                break
        self._write_json(self._top_index_file, top)

    # --- guardrails & context ---

    def _load_guardrails(self) -> str:
        sections = []
        agents_md = ROOT / "AGENTS.md"
        missing_docs = []
        if agents_md.exists():
            sections.append(f"## 프로젝트 규칙 (AGENTS.md)\n\n{agents_md.read_text()}")
        else:
            missing_docs.append(str(agents_md))
        docs_dir = ROOT / "docs"
        if docs_dir.is_dir():
            for name in self.GUARDRail_DOCS:
                doc = docs_dir / name
                if not doc.exists():
                    missing_docs.append(str(doc))
                    continue
                sections.append(f"## {doc.stem}\n\n{doc.read_text()}")
        else:
            missing_docs.extend(str(ROOT / "docs" / name) for name in self.GUARDRail_DOCS)

        if missing_docs:
            print("ERROR: required core docs are missing:")
            for path in missing_docs:
                print(f"  - {path}")
            sys.exit(1)
        return "\n\n---\n\n".join(sections) if sections else ""

    @staticmethod
    def _build_step_context(index: dict) -> str:
        lines = [
            f"- Step {s['step']} ({s['name']}): {s['summary']}"
            for s in index["steps"]
            if s["status"] == "completed" and s.get("summary")
        ]
        if not lines:
            return ""
        return "## 이전 Step 산출물\n\n" + "\n".join(lines) + "\n\n"

    @staticmethod
    def _validate_results_contract(step: dict) -> Optional[str]:
        contract = step.get("results_contract")
        if not contract:
            return None

        required_keys = ("summary_path", "output_paths", "comparison_artifacts", "comparison_basis")
        missing_keys = [key for key in required_keys if key not in contract]
        if missing_keys:
            return f"results_contract is missing required keys: {', '.join(missing_keys)}"

        if not isinstance(contract["output_paths"], list) or not contract["output_paths"]:
            return "results_contract.output_paths must be a non-empty list"
        if not isinstance(contract["comparison_artifacts"], list) or not contract["comparison_artifacts"]:
            return "results_contract.comparison_artifacts must be a non-empty list"

        summary_path = ROOT / contract["summary_path"]
        if not summary_path.is_file():
            return f"results summary file not found: {contract['summary_path']}"

        missing_outputs = [
            path for path in contract["output_paths"]
            if not (ROOT / path).exists()
        ]
        if missing_outputs:
            return f"results output not found: {missing_outputs[0]}"

        missing_comparisons = [
            path for path in contract["comparison_artifacts"]
            if not (ROOT / path).exists()
        ]
        if missing_comparisons:
            return f"comparison artifact not found: {missing_comparisons[0]}"

        summary_text = summary_path.read_text(encoding="utf-8")
        required_markers = ("실행 명령", "출력 위치", "비교 기준", "핵심 결과")
        missing_markers = [marker for marker in required_markers if marker not in summary_text]
        if missing_markers:
            return f"results summary is missing required sections: {', '.join(missing_markers)}"

        basis_text = str(contract["comparison_basis"]).strip()
        if not basis_text:
            return "results_contract.comparison_basis must not be empty"

        summary_basis = re.search(r"비교 기준:\s*(.+)", summary_text)
        if not summary_basis or basis_text not in summary_basis.group(1):
            return "results summary does not record the declared comparison basis"

        return None

    def _build_preamble(self, guardrails: str, step_context: str,
                        prev_error: Optional[str] = None) -> str:
        commit_example = self.FEAT_MSG.format(
            phase=self._phase_name, num="N", name="<step-name>"
        )
        retry_section = ""
        if prev_error:
            retry_section = (
                f"\n## ⚠ 이전 시도 실패 — 아래 에러를 반드시 참고하여 수정하라\n\n"
                f"{prev_error}\n\n---\n\n"
            )
        return (
            f"당신은 Codex이며 {self._project} 프로젝트의 개발자입니다. 아래 step을 수행하세요.\n\n"
            f"{guardrails}\n\n---\n\n"
            f"{step_context}{retry_section}"
            f"## 작업 규칙\n\n"
            f"1. 이전 step에서 작성된 코드를 확인하고 일관성을 유지하라.\n"
            f"2. 이 step에 명시된 작업만 수행하라. 추가 기능이나 파일을 만들지 마라.\n"
            f"3. 기존 테스트를 깨뜨리지 마라.\n"
            f"4. AC(Acceptance Criteria) 검증을 직접 실행하라.\n"
            f"5. 위험한 파괴적 명령이나 복구 불가능한 명령은 사용하지 마라.\n"
            f"6. target-project validation이 포함된 step은 `docs/RESULTS_POLICY.md`에 맞는 결과 산출물과 요약을 남기기 전에는 완료로 표시하지 마라.\n"
            f"7. 검증 결과와 최종 상태를 반영해 /phases/{self._phase_dir_name}/index.json을 직접 수정하라:\n"
            f"   - AC 통과 → \"completed\" + \"summary\" 필드에 이 step의 산출물을 한 줄로 요약\n"
            f"   - {self.MAX_RETRIES}회 수정 시도 후에도 실패 → \"error\" + \"error_message\" 기록\n"
            f"   - 사용자 개입이 필요한 경우 (API 키, 인증, 수동 설정 등) → \"blocked\" + \"blocked_reason\" 기록 후 즉시 중단\n"
            f"8. 모든 변경사항을 커밋하라:\n"
            f"   {commit_example}\n\n---\n\n"
        )

    def _validate_prompt_safety(self, prompt: str):
        result = self._run_hook("dangerous-cmd-guard.sh", prompt)
        if result and result.returncode != 0:
            print("  ERROR: dangerous command pattern found in step prompt.")
            if result.stderr:
                print(f"  {result.stderr.strip()}")
            sys.exit(1)

    # --- Codex 호출 ---

    def _invoke_codex(self, step: dict, preamble: str) -> dict:
        step_num, step_name = step["step"], step["name"]
        step_file = self._phase_dir / f"step{step_num}.md"

        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        prompt = preamble + step_file.read_text()
        self._validate_prompt_safety(prompt)
        stdout_fd, stdout_raw = tempfile.mkstemp(suffix=".stdout", dir=self._phase_dir)
        stderr_fd, stderr_raw = tempfile.mkstemp(suffix=".stderr", dir=self._phase_dir)
        os.close(stdout_fd)
        os.close(stderr_fd)
        stdout_path = Path(stdout_raw)
        stderr_path = Path(stderr_raw)
        with tempfile.NamedTemporaryFile(
            mode="w+",
            encoding="utf-8",
            suffix=".txt",
            dir=self._phase_dir,
            delete=False,
        ) as last_message_file:
            last_message_path = Path(last_message_file.name)

        cmd = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            self._root,
            "-o",
            str(last_message_path),
            prompt,
        ]

        proc = None
        forced_stop = False
        completed_with_timeout = False
        stdout = ""
        stderr = ""
        try:
            with open(stdout_path, "w", encoding="utf-8") as stdout_file, open(stderr_path, "w", encoding="utf-8") as stderr_file:
                proc = subprocess.Popen(
                    cmd,
                    cwd=self._root,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                )

                settled_at = None
                deadline = time.monotonic() + self.EXEC_TIMEOUT

                while True:
                    ret = proc.poll()
                    status = self._step_status(step_num)

                    if ret is not None:
                        exit_code = ret
                        break

                    if status in {"completed", "error", "blocked"}:
                        if settled_at is None:
                            settled_at = time.monotonic()
                        elif time.monotonic() - settled_at >= self.POST_STEP_GRACE:
                            forced_stop = True
                            proc.terminate()
                            try:
                                proc.communicate(timeout=self.TERMINATE_TIMEOUT)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.communicate()
                            exit_code = proc.returncode if proc.returncode is not None else 0
                            break
                    else:
                        settled_at = None

                    if time.monotonic() >= deadline:
                        if status in {"completed", "error", "blocked"}:
                            completed_with_timeout = True
                            forced_stop = True
                            proc.kill()
                            proc.communicate()
                            exit_code = proc.returncode if proc.returncode is not None else 0
                            break
                        proc.kill()
                        proc.communicate()
                        raise subprocess.TimeoutExpired(cmd=cmd, timeout=self.EXEC_TIMEOUT)

                    time.sleep(0.2)

            stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
            stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
            last_message = last_message_path.read_text(encoding="utf-8") if last_message_path.exists() else ""
        except subprocess.TimeoutExpired:
            print(f"\n  WARN: Codex 실행이 {self.EXEC_TIMEOUT}초를 초과했습니다.")
            raise
        finally:
            if proc and proc.poll() is None:
                proc.kill()
                proc.communicate()
            last_message_path.unlink(missing_ok=True)
            stdout_path.unlink(missing_ok=True)
            stderr_path.unlink(missing_ok=True)

        if exit_code != 0 and self._step_status(step_num) == "pending":
            print(f"\n  WARN: Codex가 비정상 종료됨 (code {exit_code})")
            if stderr:
                print(f"  stderr: {stderr[:500]}")
        elif forced_stop and completed_with_timeout:
            stderr = (stderr or "") + "\n[executor] step status finalized before Codex process exited; process was killed at timeout."
        elif forced_stop:
            stderr = (stderr or "") + "\n[executor] step status finalized before Codex process exited; process was terminated by executor."

        output = {
            "step": step_num,
            "name": step_name,
            "exitCode": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "lastMessage": last_message,
            "forcedStop": forced_stop,
        }
        out_path = self._phase_dir / f"step{step_num}-output.json"
        self._write_json(out_path, output)

        return output

    # --- 헤더 & 검증 ---

    def _print_header(self):
        print(f"\n{'='*60}")
        print(f"  Harness Step Executor")
        print(f"  Phase: {self._phase_name} | Steps: {self._total}")
        if self._auto_push:
            print(f"  Auto-push: enabled")
        print(f"{'='*60}")

    def _check_blockers(self):
        index = self._read_json(self._index_file)
        for s in reversed(index["steps"]):
            if s["status"] == "error":
                print(f"\n  ✗ Step {s['step']} ({s['name']}) failed.")
                print(f"  Error: {s.get('error_message', 'unknown')}")
                print(f"  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if s["status"] == "blocked":
                print(f"\n  ⏸ Step {s['step']} ({s['name']}) blocked.")
                print(f"  Reason: {s.get('blocked_reason', 'unknown')}")
                print(f"  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if s["status"] != "pending":
                break

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    # --- 실행 루프 ---

    def _execute_single_step(self, step: dict, guardrails: str) -> bool:
        """단일 step 실행 (재시도 포함). 완료되면 True, 실패/차단이면 False."""
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for s in self._read_json(self._index_file)["steps"] if s["status"] == "completed")
        prev_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            index = self._read_json(self._index_file)
            step_context = self._build_step_context(index)
            preamble = self._build_preamble(guardrails, step_context, prev_error)

            tag = f"Step {step_num}/{self._total - 1} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            with progress_indicator(tag) as pi:
                self._invoke_codex(step, preamble)
                elapsed = int(pi.elapsed)

            index = self._read_json(self._index_file)
            status = next((s.get("status", "pending") for s in index["steps"] if s["step"] == step_num), "pending")
            step_entry = next((s for s in index["steps"] if s["step"] == step_num), None)
            ts = self._stamp()

            if status == "completed":
                contract_error = self._validate_results_contract(step_entry or step)
                if contract_error:
                    for s in index["steps"]:
                        if s["step"] == step_num:
                            s["status"] = "error"
                            s["error_message"] = f"[results-contract] {contract_error}"
                            s["failed_at"] = ts
                    self._write_json(self._index_file, index)
                    self._commit_step(step_num, step_name)
                    print(f"  ✗ Step {step_num}: results contract failed [{elapsed}s]")
                    print(f"    Error: {contract_error}")
                    self._update_top_index("error")
                    sys.exit(1)
                repo_checks = self._run_repo_checks()
                if repo_checks and repo_checks.returncode != 0:
                    repo_msg = (repo_checks.stderr or repo_checks.stdout or "repo checks failed").strip()
                    for s in index["steps"]:
                        if s["step"] == step_num:
                            s["status"] = "error"
                            s["error_message"] = f"[repo-checks] {repo_msg}"
                            s["failed_at"] = ts
                    self._write_json(self._index_file, index)
                    self._commit_step(step_num, step_name)
                    print(f"  ✗ Step {step_num}: repo checks failed [{elapsed}s]")
                    print(f"    Error: {repo_msg}")
                    self._update_top_index("error")
                    sys.exit(1)
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["completed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name)
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]")
                return True

            if status == "blocked":
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["blocked_at"] = ts
                self._write_json(self._index_file, index)
                reason = next((s.get("blocked_reason", "") for s in index["steps"] if s["step"] == step_num), "")
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                self._update_top_index("blocked")
                sys.exit(2)

            err_msg = next(
                (s.get("error_message", "Step did not update status") for s in index["steps"] if s["step"] == step_num),
                "Step did not update status",
            )

            if attempt < self.MAX_RETRIES:
                hook_result = self._run_hook(
                    "circuit-breaker.sh",
                    err_msg,
                    env={"CIRCUIT_BREAKER_THRESHOLD": str(self.CIRCUIT_BREAKER_THRESHOLD)},
                )
                if hook_result and hook_result.returncode == 2:
                    breaker_msg = hook_result.stderr.strip()
                    for s in index["steps"]:
                        if s["step"] == step_num:
                            s["status"] = "error"
                            s["error_message"] = f"[circuit-breaker] {breaker_msg}"
                            s["failed_at"] = ts
                    self._write_json(self._index_file, index)
                    self._commit_step(step_num, step_name)
                    print(f"  ⚠ Circuit breaker: {breaker_msg}")
                    self._update_top_index("error")
                    sys.exit(1)
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "pending"
                        s.pop("error_message", None)
                self._write_json(self._index_file, index)
                prev_error = err_msg
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg}")
            else:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "error"
                        s["error_message"] = f"[{self.MAX_RETRIES}회 시도 후 실패] {err_msg}"
                        s["failed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name)
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg}")
                self._update_top_index("error")
                sys.exit(1)

        return False  # unreachable

    def _execute_all_steps(self, guardrails: str):
        while True:
            index = self._read_json(self._index_file)
            pending = next((s for s in index["steps"] if s["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            for s in index["steps"]:
                if s["step"] == step_num and "started_at" not in s:
                    s["started_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    break

            self._execute_single_step(pending, guardrails)

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._update_top_index("completed")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._phase_name}): mark phase completed"
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  ✓ {msg}")

        if self._auto_push:
            branch = f"feat-{self._phase_name}"
            r = self._run_git("push", "-u", "origin", branch)
            if r.returncode != 0:
                print(f"\n  ERROR: git push 실패: {r.stderr.strip()}")
                sys.exit(1)
            print(f"  ✓ Pushed to origin/{branch}")

        print(f"\n{'='*60}")
        print(f"  Phase '{self._phase_name}' completed!")
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. 0-mvp)")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    args = parser.parse_args()

    StepExecutor(args.phase_dir, auto_push=args.push).run()


if __name__ == "__main__":
    main()
