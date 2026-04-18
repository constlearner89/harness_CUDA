#!/usr/bin/env python3
"""
Harness Step Executor — 단일 goal의 step들을 순차 실행하고 자가 교정한다.

Usage:
    python3 scripts/execute.py [--push]
"""

import argparse
import contextlib
import importlib.util
import json
import os
import pty
import re
import shutil
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


class CommitStepError(RuntimeError):
    """Raised when the executor cannot persist a step commit."""


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
    """steps/ 디렉토리 안의 step들을 순차 실행하는 하네스."""

    MAX_RETRIES = 3
    EXEC_TIMEOUT = 1800
    POST_STEP_GRACE = 2.0
    TERMINATE_TIMEOUT = 5
    CIRCUIT_BREAKER_THRESHOLD = 2
    GUARDRAIL_DOCS = ("PRD.md", "ARCHITECTURE.md", "ADR.md", "RESULTS_POLICY.md")
    STEP_TYPES = ("reference", "implementation", "validation")
    VALIDATION_SCOPES = ("framework", "external-target")
    GENERATED_PATH_PREFIXES = ("build/", "results/", "cmake-build-")
    GENERATED_PATH_PARTS = ("/CMakeFiles/",)
    STALL_PATTERNS = ("write_stdin failed", "stdin is closed for this session")
    FEAT_MSG = "feat({goal}): step {num} - {name}"
    CHORE_MSG = "chore({goal}): step {num} output"
    TZ = timezone(timedelta(hours=9))

    def __init__(self, *, auto_push: bool = False):
        self._root = str(ROOT)
        self._steps_dir = ROOT / "steps"
        self._auto_push = auto_push

        if not self._steps_dir.is_dir():
            print(f"ERROR: {self._steps_dir} not found")
            sys.exit(1)

        self._index_file = self._steps_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._goal_name = idx.get("goal", idx.get("project", "goal"))
        self._total = len(idx["steps"])

    def run(self):
        self._print_header()
        self._check_blockers()
        self._check_clean_worktree()
        self._checkout_branch()
        guardrails = self._load_guardrails()
        self._ensure_created_at()
        self._validate_run_preflight()
        self._execute_all_steps(guardrails)
        self._finalize()

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, data: dict):
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _step_status(self, step_num: int) -> str:
        index = self._read_json(self._index_file)
        return next((s.get("status", "pending") for s in index["steps"] if s["step"] == step_num), "pending")

    def _log(self, message: str):
        print(f"  [{self._stamp()}] {message}")

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self._root, capture_output=True, text=True)

    @staticmethod
    def _parse_porcelain_path(line: str) -> str:
        entry = line[3:] if len(line) > 3 else ""
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        return entry.strip()

    @classmethod
    def _is_generated_path(cls, path: str) -> bool:
        normalized = path.strip()
        if not normalized:
            return False
        if normalized.startswith(cls.GENERATED_PATH_PREFIXES):
            return True
        return any(part in normalized for part in cls.GENERATED_PATH_PARTS)

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
        branch = f"feat-{self._goal_name}"
        current = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if current.returncode != 0:
            print("  ERROR: git을 사용할 수 없거나 git repo가 아닙니다.")
            print(f"  {current.stderr.strip()}")
            sys.exit(1)
        if current.stdout.strip() == branch:
            return

        exists = self._run_git("rev-parse", "--verify", branch)
        result = self._run_git("checkout", branch) if exists.returncode == 0 else self._run_git("checkout", "-b", branch)
        if result.returncode != 0:
            print(f"  ERROR: 브랜치 '{branch}' checkout 실패.")
            print(f"  {result.stderr.strip()}")
            print("  Hint: 변경사항을 stash하거나 commit한 후 다시 시도하세요.")
            sys.exit(1)
        print(f"  Branch: {branch}")

    def _check_clean_worktree(self):
        status = self._run_git("status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            print("  ERROR: git status 확인 실패.")
            print(f"  {status.stderr.strip()}")
            sys.exit(1)

        dirty_paths = []
        for raw_line in status.stdout.splitlines():
            path = self._parse_porcelain_path(raw_line)
            if not path:
                continue
            if path.startswith("steps/"):
                continue
            if raw_line.startswith("??") and self._is_generated_path(path):
                continue
            dirty_paths.append(path)

        if dirty_paths:
            print("  ERROR: 현재 worktree에 steps 외 변경사항이 있어 자동 실행을 중단합니다.")
            for path in dirty_paths[:10]:
                print(f"  - {path}")
            if len(dirty_paths) > 10:
                print(f"  ... and {len(dirty_paths) - 10} more")
            print("  Hint: 현재 steps 관련 파일만 남기고 나머지는 commit/stash 후 다시 실행하세요.")
            sys.exit(1)

    def _commit_step(self, step_num: int, step_name: str):
        output_rel = f"steps/step{step_num}-output.json"
        index_rel = "steps/index.json"

        self._run_git("add", "-A")
        self._run_git("reset", "HEAD", "--", output_rel)
        self._run_git("reset", "HEAD", "--", index_rel)
        self._run_git("reset", "HEAD", "--", "raw")

        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(goal=self._goal_name, num=step_num, name=step_name)
            result = self._run_git("commit", "-m", msg)
            if result.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                raise CommitStepError(f"code commit failed: {result.stderr.strip()}")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(goal=self._goal_name, num=step_num)
            result = self._run_git("commit", "-m", msg)
            if result.returncode != 0:
                raise CommitStepError(f"housekeeping commit failed: {result.stderr.strip()}")

    def _load_guardrails(self) -> str:
        sections = []
        agents_md = ROOT / "AGENTS.md"
        missing_docs = []

        if agents_md.exists():
            sections.append(f"## 프로젝트 규칙 (AGENTS.md)\n\n{agents_md.read_text(encoding='utf-8')}")
        else:
            missing_docs.append(str(agents_md))

        docs_dir = ROOT / "docs"
        if docs_dir.is_dir():
            for name in self.GUARDRAIL_DOCS:
                doc = docs_dir / name
                if not doc.exists():
                    missing_docs.append(str(doc))
                    continue
                sections.append(f"## {doc.stem}\n\n{doc.read_text(encoding='utf-8')}")
        else:
            missing_docs.extend(str(ROOT / "docs" / name) for name in self.GUARDRAIL_DOCS)

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
    def _external_validation_command(command: str) -> bool:
        normalized = str(command).strip().lower()
        if not normalized:
            return False
        patterns = ("cmake ", "ctest ", "./build/", "build/")
        return any(token in normalized for token in patterns)

    def _resolve_target_path(self, target_root: str) -> Path:
        target_path = Path(target_root)
        if not target_path.is_absolute():
            target_path = Path(self._root) / target_path
        return target_path

    @staticmethod
    def _has_src_tree(target_path: Path) -> bool:
        return (target_path / "src").is_dir()

    @staticmethod
    def _next_pending_step(index: dict) -> Optional[dict]:
        return next((step for step in index.get("steps", []) if step.get("status") == "pending"), None)

    @staticmethod
    def _has_later_pending_implementation_step(index: dict) -> bool:
        seen_pending = False
        for step in index.get("steps", []):
            if step.get("status") != "pending":
                continue
            if not seen_pending:
                seen_pending = True
                continue
            if step.get("type") == "implementation":
                return True
        return False

    def _allow_external_target_bootstrap(self, index: dict, target_path: Path) -> bool:
        if (target_path / "CMakeLists.txt").is_file():
            return False
        if not self._has_src_tree(target_path):
            return False
        next_step = self._next_pending_step(index)
        return bool(next_step and next_step.get("type") == "implementation")

    def _cmake_bootstrap_target(self, index: Optional[dict] = None) -> Optional[Path]:
        current_index = index or self._read_json(self._index_file)
        if current_index.get("validation_scope", "framework") != "external-target":
            return None
        target_root = current_index.get("target_root")
        if not target_root:
            return None
        target_path = self._resolve_target_path(target_root)
        if not self._has_src_tree(target_path):
            return None
        if (target_path / "CMakeLists.txt").is_file():
            return None
        return target_path

    def _validate_validation_scope(self, index: dict) -> Optional[str]:
        scope = index.get("validation_scope", "framework")
        if scope not in self.VALIDATION_SCOPES:
            return f"validation_scope must be one of: {', '.join(self.VALIDATION_SCOPES)}"

        if scope == "framework":
            for step in index.get("steps", []):
                commands = [str(cmd).strip() for cmd in step.get("validation_commands", []) if str(cmd).strip()]
                offending = next((cmd for cmd in commands if self._external_validation_command(cmd)), None)
                if offending:
                    return f"framework validation_scope cannot run external target command: {offending}"
            return None

        target_root = index.get("target_root")
        if not target_root:
            return "external-target validation_scope requires top-level target_root"

        target_path = self._resolve_target_path(target_root)
        if not target_path.is_dir():
            return f"external target root not found: {target_root}"
        if not (target_path / "CMakeLists.txt").is_file():
            if self._allow_external_target_bootstrap(index, target_path):
                return None
            if self._has_src_tree(target_path):
                next_step = self._next_pending_step(index)
                if next_step and next_step.get("type") != "implementation" and self._has_later_pending_implementation_step(index):
                    return (
                        f"external target root has src/ but no CMakeLists.txt: {target_root} "
                        "(next pending step must bootstrap CMakeLists.txt before validation)"
                    )
                return (
                    f"external target root has src/ but no CMakeLists.txt: {target_root} "
                    "(bootstrap step must create CMakeLists.txt before validation)"
                )
            return f"external target root does not contain CMakeLists.txt: {target_root}"
        return None

    def _tool_capabilities(self) -> dict:
        python3_path = shutil.which("python3")
        return {
            "python3": bool(python3_path),
            "pypdf": importlib.util.find_spec("pypdf") is not None,
            "pdfplumber": importlib.util.find_spec("pdfplumber") is not None,
            "pdfinfo": bool(shutil.which("pdfinfo")),
            "pdftotext": bool(shutil.which("pdftotext")),
            "pdftoppm": bool(shutil.which("pdftoppm")),
        }

    def _capability_summary(self, capabilities: dict) -> str:
        ordered = ("python3", "pypdf", "pdfplumber", "pdfinfo", "pdftotext", "pdftoppm")
        return ", ".join(f"{name}={'yes' if capabilities[name] else 'no'}" for name in ordered)

    def _has_pdf_reference_step(self, index: dict) -> bool:
        for step in index.get("steps", []):
            contract = step.get("reference_contract") or {}
            source_files = contract.get("source_files", [])
            if any(str(path).lower().endswith(".pdf") for path in source_files):
                return True
        return False

    def _bootstrap_guidance(self, index: Optional[dict] = None) -> Optional[str]:
        target_path = self._cmake_bootstrap_target(index)
        if target_path is None:
            return None
        rel_target = os.path.relpath(target_path, self._root)
        rel_src = os.path.join(rel_target, "src") if rel_target != "." else "src"
        rel_cmake = os.path.join(rel_target, "CMakeLists.txt") if rel_target != "." else "CMakeLists.txt"
        location = "repo root" if rel_target == "." else f"`{rel_target}/` target root"
        return (
            f"- {location}에 `{rel_src}/`가 있고 `{rel_cmake}`가 없다면 `{rel_cmake}`를 먼저 생성하라.\n"
            "- 이 경우 `framework` 분석만 하고 끝내지 마라. bootstrap 후 `cmake`, `ctest`, 가능한 대표 시뮬레이터 실행까지 이어질 수 있게 준비하라.\n"
            "- 자동 생성 범위는 최소 `CMake` + `CTest` 기준이다. `tests/CMakeLists.txt`가 있으면 `enable_testing()`과 테스트 연결을 포함하라.\n"
        )

    def _validate_run_preflight(self):
        index = self._read_json(self._index_file)
        scope_error = self._validate_validation_scope(index)
        if scope_error:
            print(f"  ERROR: invalid validation scope: {scope_error}")
            sys.exit(1)

        scope = index.get("validation_scope", "framework")
        self._log(f"validation scope: {scope}")
        if scope == "external-target":
            self._log(f"external target root: {index['target_root']}")
            if self._allow_external_target_bootstrap(index, self._resolve_target_path(index["target_root"])):
                self._log("external target bootstrap required: src/ detected without CMakeLists.txt; implementation step must create it")

        if self._has_pdf_reference_step(index):
            capabilities = self._tool_capabilities()
            self._log(f"pdf capability summary: {self._capability_summary(capabilities)}")

    @classmethod
    def _validate_step_schema(cls, step: dict) -> Optional[str]:
        step_type = step.get("type")
        if step_type not in cls.STEP_TYPES:
            return f"step type must be one of: {', '.join(cls.STEP_TYPES)}"

        if step_type == "reference" and not step.get("reference_contract"):
            return "reference step requires reference_contract"

        if step_type == "validation":
            commands = step.get("validation_commands")
            if not isinstance(commands, list) or not commands or not all(str(cmd).strip() for cmd in commands):
                return "validation step requires non-empty validation_commands"
            if not step.get("results_contract"):
                return "validation step requires results_contract"

        return None

    @staticmethod
    def _validate_reference_contract(step: dict) -> Optional[str]:
        contract = step.get("reference_contract")
        if not contract:
            return None

        required_keys = ("source_files", "output_paths", "required_items")
        missing_keys = [key for key in required_keys if key not in contract]
        if missing_keys:
            return f"reference_contract is missing required keys: {', '.join(missing_keys)}"

        for key in required_keys:
            value = contract[key]
            if not isinstance(value, list) or not value:
                return f"reference_contract.{key} must be a non-empty list"

        missing_sources = [path for path in contract["source_files"] if not (ROOT / path).is_file()]
        if missing_sources:
            return f"reference source file not found: {missing_sources[0]}"

        missing_outputs = [path for path in contract["output_paths"] if not (ROOT / path).is_file()]
        if missing_outputs:
            return f"reference artifact not found: {missing_outputs[0]}"

        corpus = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in contract["output_paths"])
        missing_items = [item for item in contract["required_items"] if str(item).strip() and str(item) not in corpus]
        if missing_items:
            return f"reference artifact is missing required item: {missing_items[0]}"
        return None

    @staticmethod
    def _validate_results_contract(step: dict) -> Optional[str]:
        contract = step.get("results_contract")
        if not contract:
            return None

        required_keys = ("summary_path", "output_paths", "comparison_artifacts", "comparison_basis", "validation_log_paths")
        missing_keys = [key for key in required_keys if key not in contract]
        if missing_keys:
            return f"results_contract is missing required keys: {', '.join(missing_keys)}"
        if not isinstance(contract["output_paths"], list) or not contract["output_paths"]:
            return "results_contract.output_paths must be a non-empty list"
        if not isinstance(contract["comparison_artifacts"], list) or not contract["comparison_artifacts"]:
            return "results_contract.comparison_artifacts must be a non-empty list"
        if not isinstance(contract["validation_log_paths"], list) or not contract["validation_log_paths"]:
            return "results_contract.validation_log_paths must be a non-empty list"

        summary_path = ROOT / contract["summary_path"]
        if not summary_path.is_file():
            return f"results summary file not found: {contract['summary_path']}"

        missing_outputs = [path for path in contract["output_paths"] if not (ROOT / path).exists()]
        if missing_outputs:
            return f"results output not found: {missing_outputs[0]}"

        missing_comparisons = [path for path in contract["comparison_artifacts"] if not (ROOT / path).exists()]
        if missing_comparisons:
            return f"comparison artifact not found: {missing_comparisons[0]}"

        missing_validation_logs = [path for path in contract["validation_log_paths"] if not (ROOT / path).is_file()]
        if missing_validation_logs:
            return f"validation log not found: {missing_validation_logs[0]}"

        summary_text = summary_path.read_text(encoding="utf-8")
        required_markers = ("실행 명령", "실행 로그 위치", "출력 위치", "비교 기준", "핵심 결과")
        missing_markers = [marker for marker in required_markers if marker not in summary_text]
        if missing_markers:
            return f"results summary is missing required sections: {', '.join(missing_markers)}"

        basis_text = str(contract["comparison_basis"]).strip()
        if not basis_text:
            return "results_contract.comparison_basis must not be empty"

        summary_basis = re.search(r"비교 기준:\s*(.+)", summary_text)
        if not summary_basis or basis_text not in summary_basis.group(1):
            return "results summary does not record the declared comparison basis"

        commands = [str(cmd).strip() for cmd in step.get("validation_commands", []) if str(cmd).strip()]
        missing_summary_commands = [cmd for cmd in commands if cmd not in summary_text]
        if missing_summary_commands:
            return f"results summary is missing executed validation command: {missing_summary_commands[0]}"

        summary_logs = re.search(r"실행 로그 위치:\s*(.+)", summary_text)
        if not summary_logs:
            return "results summary does not record validation log paths"
        missing_summary_logs = [path for path in contract["validation_log_paths"] if path not in summary_logs.group(1)]
        if missing_summary_logs:
            return f"results summary is missing validation log path: {missing_summary_logs[0]}"

        log_corpus = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in contract["validation_log_paths"])
        missing_logged_commands = [cmd for cmd in commands if cmd not in log_corpus]
        if missing_logged_commands:
            return f"validation log is missing command evidence: {missing_logged_commands[0]}"
        return None

    @staticmethod
    def _extract_acceptance_commands(step_text: str) -> list[str]:
        section_match = re.search(
            r"(^|\n)#+\s*Acceptance Criteria\s*\n(.*?)(?=\n#+\s|\Z)",
            step_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not section_match:
            return []

        section = section_match.group(2)
        commands: list[str] = []
        commands.extend(match.group(1).strip() for match in re.finditer(r"`([^`\n]+)`", section))

        for block in re.finditer(r"```(?:[^\n]*)\n(.*?)```", section, flags=re.DOTALL):
            for line in block.group(1).splitlines():
                stripped = line.strip()
                if stripped:
                    commands.append(stripped)

        deduped = []
        for command in commands:
            if command not in deduped:
                deduped.append(command)
        return deduped

    def _build_preamble(
        self,
        guardrails: str,
        step_context: str,
        prev_error: Optional[str] = None,
        step: Optional[dict] = None,
    ) -> str:
        commit_example = self.FEAT_MSG.format(goal=self._goal_name, num="N", name="<step-name>")
        retry_section = ""
        step_requirements = ""

        if prev_error:
            retry_section = (
                "\n## ⚠ 이전 시도 실패 — 아래 에러를 반드시 참고하여 수정하라\n\n"
                f"{prev_error}\n\n---\n\n"
            )

        if step:
            step_type = step.get("type", "implementation")
            step_requirements = f"## Step 계약\n\n- step type: `{step_type}`\n"
            if step_type == "validation":
                commands = step.get("validation_commands", [])
                if commands:
                    step_requirements += "- 아래 validation commands를 모두 직접 실행하라:\n"
                    step_requirements += "\n".join(f"- `{cmd}`" for cmd in commands) + "\n"
            if step.get("reference_contract"):
                outputs = "\n".join(f"- `{path}`" for path in step["reference_contract"].get("output_paths", []))
                step_requirements += f"- reference artifact output:\n{outputs}\n"
            if step.get("results_contract"):
                outputs = "\n".join(f"- `{path}`" for path in step["results_contract"].get("output_paths", []))
                step_requirements += f"- validation result output:\n{outputs}\n"
            guidance = self._bootstrap_guidance()
            if guidance and step_type != "validation":
                step_requirements += guidance
            step_requirements += "\n---\n\n"

        return (
            f"당신은 Codex이며 {self._project} 프로젝트의 개발자입니다. 아래 step을 수행하세요.\n\n"
            f"{guardrails}\n\n---\n\n"
            f"{step_context}{retry_section}{step_requirements}"
            "## 작업 규칙\n\n"
            "1. 이전 step에서 작성된 코드를 확인하고 일관성을 유지하라.\n"
            "2. 이 step에 명시된 작업만 수행하라. 추가 기능이나 파일을 만들지 마라.\n"
            "3. 기존 테스트를 깨뜨리지 마라.\n"
            "4. AC(Acceptance Criteria) 검증을 직접 실행하라.\n"
            "5. 위험한 파괴적 명령이나 복구 불가능한 명령은 사용하지 마라.\n"
            "6. 가능하면 non-interactive one-shot command를 우선 사용하고, stdin이 필요한 장기 대화형 흐름은 피하라.\n"
            "7. `raw/` 아래 PDF 논문을 읽는 step이면 로컬 `pdf` 스킬을 사용하라. 텍스트 추출은 Python 기반 추출(`pypdf`, `pdfplumber`)을 먼저 시도하고, Poppler CLI는 시각 검토가 필요할 때만 보조적으로 사용하라.\n"
            "8. Poppler CLI가 없다고 바로 `blocked` 처리하지 마라. 먼저 Python 기반 추출 경로를 사용하라.\n"
            "9. 이전 step에서 `steps/artifacts/reference/` 아래 reference artifact가 만들어졌다면, 후속 step은 해당 정보가 필요할 때 특별한 이유가 없는 한 `raw/` 원본보다 그 추출 산출물을 우선 읽어라.\n"
            "10. target-project validation이 포함된 step은 `docs/RESULTS_POLICY.md`에 맞는 결과 산출물과 요약을 남기기 전에는 완료로 표시하지 마라.\n"
            "11. 검증 결과와 최종 상태를 반영해 `/steps/index.json`을 직접 수정하라:\n"
            "   - AC 통과 -> \"completed\" + \"summary\" 필드에 이 step의 산출물을 한 줄로 요약\n"
            f"   - {self.MAX_RETRIES}회 수정 시도 후에도 실패 -> \"error\" + \"error_message\" 기록\n"
            "   - 사용자 개입이 필요한 경우 (API 키, 인증, 수동 설정 등) -> \"blocked\" + \"blocked_reason\" 기록 후 즉시 중단\n"
            "12. 모든 변경사항을 커밋하라:\n"
            f"   {commit_example}\n\n---\n\n"
        )

    def _validate_prompt_safety(self, step: dict, step_text: str):
        commands = self._extract_acceptance_commands(step_text)
        commands.extend(str(cmd).strip() for cmd in step.get("validation_commands", []) if str(cmd).strip())

        deduped = []
        for command in commands:
            if command and command not in deduped:
                deduped.append(command)

        for command in deduped:
            result = self._run_hook("dangerous-cmd-guard.sh", command)
            if result and result.returncode != 0:
                print("  ERROR: dangerous command pattern found in executable step command.")
                print(f"  Command: {command}")
                if result.stderr:
                    print(f"  {result.stderr.strip()}")
                sys.exit(1)

    def _open_codex_stdin(self):
        stdin_obj = getattr(sys, "stdin", None)
        fileno = getattr(stdin_obj, "fileno", None)
        if stdin_obj is not None and callable(fileno):
            try:
                if os.isatty(stdin_obj.fileno()):
                    return stdin_obj, [], "parent-tty"
            except (OSError, ValueError):
                pass

        master_fd, slave_fd = pty.openpty()
        master_handle = os.fdopen(master_fd, "rb", buffering=0)
        slave_handle = os.fdopen(slave_fd, "rb", buffering=0)
        return slave_handle, [master_handle, slave_handle], "pty"

    @staticmethod
    def _read_text_tail(path: Path, limit: int = 2000) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-limit:]

    def _detect_stall_reason(self, stderr_text: str) -> Optional[str]:
        lowered = stderr_text.lower()
        for pattern in self.STALL_PATTERNS:
            if pattern in lowered:
                return pattern
        return None

    def _invoke_codex(self, step: dict, preamble: str) -> dict:
        step_num, step_name = step["step"], step["name"]
        step_file = self._steps_dir / f"step{step_num}.md"
        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        step_text = step_file.read_text(encoding="utf-8")
        prompt = preamble + step_text
        self._validate_prompt_safety(step, step_text)
        stdout_fd, stdout_raw = tempfile.mkstemp(suffix=".stdout", dir=self._steps_dir)
        stderr_fd, stderr_raw = tempfile.mkstemp(suffix=".stderr", dir=self._steps_dir)
        os.close(stdout_fd)
        os.close(stderr_fd)
        stdout_path = Path(stdout_raw)
        stderr_path = Path(stderr_raw)

        with tempfile.NamedTemporaryFile(
            mode="w+",
            encoding="utf-8",
            suffix=".txt",
            dir=self._steps_dir,
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
        timed_out = False
        stdout = ""
        stderr = ""
        settled_logged = False
        last_known_status = self._step_status(step_num)
        failure_category = None
        stall_reason = None
        started_at = self._stamp()
        ended_at = None
        stdin_handle = None
        cleanup_handles = []
        stdin_mode = "devnull"
        try:
            with open(stdout_path, "w", encoding="utf-8") as stdout_file, open(stderr_path, "w", encoding="utf-8") as stderr_file:
                stdin_handle, cleanup_handles, stdin_mode = self._open_codex_stdin()
                proc = subprocess.Popen(
                    cmd,
                    cwd=self._root,
                    stdin=stdin_handle,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                )
                self._log(f"step {step_num}: Codex process started for '{step_name}' (stdin={stdin_mode})")
                settled_at = None
                deadline = time.monotonic() + self.EXEC_TIMEOUT
                stderr_probe_tick = 0

                while True:
                    ret = proc.poll()
                    status = self._step_status(step_num)
                    last_known_status = status
                    if ret is not None:
                        exit_code = ret
                        break

                    stderr_probe_tick += 1
                    if status == "pending" and stderr_probe_tick % 5 == 0:
                        stderr_tail = self._read_text_tail(stderr_path)
                        stall_reason = self._detect_stall_reason(stderr_tail)
                        if stall_reason:
                            forced_stop = True
                            failure_category = "stall"
                            self._log(f"step {step_num}: detected stalled Codex subprocess ({stall_reason})")
                            proc.terminate()
                            try:
                                proc.communicate(timeout=self.TERMINATE_TIMEOUT)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.communicate()
                            exit_code = proc.returncode if proc.returncode is not None else 1
                            break

                    if status in {"completed", "error", "blocked"}:
                        if settled_at is None:
                            settled_at = time.monotonic()
                            if not settled_logged:
                                self._log(f"step {step_num}: status changed to '{status}', waiting for Codex process to settle")
                                settled_logged = True
                        elif time.monotonic() - settled_at >= self.POST_STEP_GRACE:
                            forced_stop = True
                            self._log(f"step {step_num}: terminating background Codex process after status '{status}'")
                            proc.terminate()
                            try:
                                proc.communicate(timeout=self.TERMINATE_TIMEOUT)
                            except subprocess.TimeoutExpired:
                                self._log(f"step {step_num}: Codex process did not terminate cleanly; killing it")
                                proc.kill()
                                proc.communicate()
                            exit_code = proc.returncode if proc.returncode is not None else 0
                            break
                    else:
                        settled_at = None
                        settled_logged = False

                    if time.monotonic() >= deadline:
                        if status in {"completed", "error", "blocked"}:
                            completed_with_timeout = True
                            forced_stop = True
                            self._log(f"step {step_num}: timeout reached after final status '{status}', killing Codex process")
                            proc.kill()
                            proc.communicate()
                            exit_code = proc.returncode if proc.returncode is not None else 0
                            break
                        proc.kill()
                        proc.communicate()
                        timed_out = True
                        exit_code = None
                        break

                    time.sleep(0.2)

            stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
            stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
            last_message = last_message_path.read_text(encoding="utf-8") if last_message_path.exists() else ""
        finally:
            if proc and proc.poll() is None:
                proc.kill()
                proc.communicate()
            for handle in cleanup_handles:
                try:
                    handle.close()
                except OSError:
                    pass
            ended_at = self._stamp()
            last_message_path.unlink(missing_ok=True)
            stdout_path.unlink(missing_ok=True)
            stderr_path.unlink(missing_ok=True)

        stderr_tail = stderr[-2000:]
        if timed_out:
            print(f"\n  WARN: Codex 실행이 {self.EXEC_TIMEOUT}초를 초과했습니다.")
            failure_category = failure_category or "timeout"

        if exit_code != 0 and self._step_status(step_num) == "pending":
            print(f"\n  WARN: Codex가 비정상 종료됨 (code {exit_code})")
            if stderr:
                print(f"  stderr: {stderr[:500]}")
        elif forced_stop and completed_with_timeout:
            stderr = (stderr or "") + "\n[executor] step status finalized before Codex process exited; process was killed at timeout."
        elif forced_stop:
            stderr = (stderr or "") + "\n[executor] step status finalized before Codex process exited; process was terminated by executor."

        if failure_category is None and exit_code not in (None, 0) and last_known_status == "pending":
            failure_category = "stall" if self._detect_stall_reason(stderr_tail) else "tooling"

        output = {
            "step": step_num,
            "name": step_name,
            "exitCode": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stderrTail": stderr_tail,
            "lastMessage": last_message,
            "forcedStop": forced_stop,
            "startedAt": started_at,
            "endedAt": ended_at,
            "lastKnownStatus": last_known_status,
            "failureCategory": failure_category,
            "stdinMode": stdin_mode,
        }
        self._write_json(self._steps_dir / f"step{step_num}-output.json", output)
        if timed_out:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=self.EXEC_TIMEOUT)
        return output

    def _record_commit_failure(self, step_num: int, message: str):
        index = self._read_json(self._index_file)
        ts = self._stamp()
        for item in index["steps"]:
            if item["step"] == step_num:
                item["status"] = "error"
                item["error_message"] = f"[commit] {message}"
                item["failed_at"] = ts
        self._write_json(self._index_file, index)
        print(f"  ✗ Step {step_num}: commit failed")
        print(f"    Error: {message}")

    def _commit_step_or_fail(self, step_num: int, step_name: str):
        try:
            self._commit_step(step_num, step_name)
        except CommitStepError as exc:
            self._record_commit_failure(step_num, str(exc))
            sys.exit(1)

    def _print_header(self):
        print(f"\n{'=' * 60}")
        print("  Harness Step Executor")
        print(f"  Goal: {self._goal_name} | Steps: {self._total}")
        if self._auto_push:
            print("  Auto-push: enabled")
        print(f"{'=' * 60}")

    def _check_blockers(self):
        index = self._read_json(self._index_file)
        for step in reversed(index["steps"]):
            if step["status"] == "error":
                print(f"\n  ✗ Step {step['step']} ({step['name']}) failed.")
                print(f"  Error: {step.get('error_message', 'unknown')}")
                print("  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if step["status"] == "blocked":
                print(f"\n  ⏸ Step {step['step']} ({step['name']}) blocked.")
                print(f"  Reason: {step.get('blocked_reason', 'unknown')}")
                print("  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if step["status"] != "pending":
                break

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    def _execute_single_step(self, step: dict, guardrails: str) -> bool:
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for item in self._read_json(self._index_file)["steps"] if item["status"] == "completed")
        prev_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            index = self._read_json(self._index_file)
            scope_error = self._validate_validation_scope(index)
            if scope_error:
                print(f"  ERROR: invalid validation scope: {scope_error}")
                sys.exit(1)
            step_entry = next((item for item in index["steps"] if item["step"] == step_num), step)
            schema_error = self._validate_step_schema(step_entry)
            if schema_error:
                print(f"  ERROR: invalid step schema for step {step_num}: {schema_error}")
                sys.exit(1)

            preamble = self._build_preamble(
                guardrails,
                self._build_step_context(index),
                prev_error,
                step_entry,
            )
            tag = f"Step {step_num + 1}/{self._total} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            self._log(f"step {step_num}: '{step_name}' starting (attempt {attempt}/{self.MAX_RETRIES})")
            self._log(f"step {step_num}: running Codex executor")
            with progress_indicator(tag) as progress:
                self._invoke_codex(step, preamble)
                elapsed = int(progress.elapsed)

            index = self._read_json(self._index_file)
            status = next((item.get("status", "pending") for item in index["steps"] if item["step"] == step_num), "pending")
            step_entry = next((item for item in index["steps"] if item["step"] == step_num), None)
            ts = self._stamp()

            if status == "completed":
                self._log(f"step {step_num}: validating declared output contracts")
                reference_error = self._validate_reference_contract(step_entry or step)
                if reference_error:
                    for item in index["steps"]:
                        if item["step"] == step_num:
                            item["status"] = "error"
                            item["error_message"] = f"[reference-contract] {reference_error}"
                            item["failed_at"] = ts
                    self._write_json(self._index_file, index)
                    self._commit_step_or_fail(step_num, step_name)
                    print(f"  ✗ Step {step_num}: reference contract failed [{elapsed}s]")
                    print(f"    Error: {reference_error}")
                    sys.exit(1)

                results_error = self._validate_results_contract(step_entry or step)
                if results_error:
                    for item in index["steps"]:
                        if item["step"] == step_num:
                            item["status"] = "error"
                            item["error_message"] = f"[results-contract] {results_error}"
                            item["failed_at"] = ts
                    self._write_json(self._index_file, index)
                    self._commit_step_or_fail(step_num, step_name)
                    print(f"  ✗ Step {step_num}: results contract failed [{elapsed}s]")
                    print(f"    Error: {results_error}")
                    sys.exit(1)

                self._log(f"step {step_num}: running post-step repo checks")
                repo_checks = self._run_repo_checks()
                if repo_checks and repo_checks.returncode != 0:
                    repo_msg = (repo_checks.stderr or repo_checks.stdout or "repo checks failed").strip()
                    for item in index["steps"]:
                        if item["step"] == step_num:
                            item["status"] = "error"
                            item["error_message"] = f"[repo-checks] {repo_msg}"
                            item["failed_at"] = ts
                    self._write_json(self._index_file, index)
                    self._commit_step_or_fail(step_num, step_name)
                    print(f"  ✗ Step {step_num}: repo checks failed [{elapsed}s]")
                    print(f"    Error: {repo_msg}")
                    sys.exit(1)

                for item in index["steps"]:
                    if item["step"] == step_num:
                        item["completed_at"] = ts
                self._write_json(self._index_file, index)
                self._log(f"step {step_num}: recording completion commit")
                self._commit_step_or_fail(step_num, step_name)
                self._log(f"step {step_num}: completed successfully")
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]")
                return True

            if status == "blocked":
                for item in index["steps"]:
                    if item["step"] == step_num:
                        item["blocked_at"] = ts
                self._write_json(self._index_file, index)
                reason = next((item.get("blocked_reason", "") for item in index["steps"] if item["step"] == step_num), "")
                self._log(f"step {step_num}: blocked - {reason or 'no reason recorded'}")
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                sys.exit(2)

            err_msg = next(
                (item.get("error_message", "Step did not update status") for item in index["steps"] if item["step"] == step_num),
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
                    for item in index["steps"]:
                        if item["step"] == step_num:
                            item["status"] = "error"
                            item["error_message"] = f"[circuit-breaker] {breaker_msg}"
                            item["failed_at"] = ts
                    self._write_json(self._index_file, index)
                    self._commit_step_or_fail(step_num, step_name)
                    print(f"  ⚠ Circuit breaker: {breaker_msg}")
                    sys.exit(1)

                for item in index["steps"]:
                    if item["step"] == step_num:
                        item["status"] = "pending"
                        item.pop("error_message", None)
                self._write_json(self._index_file, index)
                prev_error = err_msg
                self._log(f"step {step_num}: retry scheduled after failure - {err_msg}")
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg}")
            else:
                for item in index["steps"]:
                    if item["step"] == step_num:
                        item["status"] = "error"
                        item["error_message"] = f"[{self.MAX_RETRIES}회 시도 후 실패] {err_msg}"
                        item["failed_at"] = ts
                self._write_json(self._index_file, index)
                self._log(f"step {step_num}: exhausted retries, recording failure")
                self._commit_step_or_fail(step_num, step_name)
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg}")
                sys.exit(1)
        return False

    def _execute_all_steps(self, guardrails: str):
        while True:
            index = self._read_json(self._index_file)
            pending = next((item for item in index["steps"] if item["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            for item in index["steps"]:
                if item["step"] == step_num and "started_at" not in item:
                    item["started_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    break
            self._execute_single_step(pending, guardrails)

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._log(f"run completed at {index['completed_at']}")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._goal_name}): mark steps completed"
            result = self._run_git("commit", "-m", msg)
            if result.returncode == 0:
                print(f"  ✓ {msg}")

        if self._auto_push:
            branch = f"feat-{self._goal_name}"
            result = self._run_git("push", "-u", "origin", branch)
            if result.returncode != 0:
                print(f"\n  ERROR: git push 실패: {result.stderr.strip()}")
                sys.exit(1)
            print(f"  ✓ Pushed to origin/{branch}")

        print(f"\n{'=' * 60}")
        print(f"  Goal '{self._goal_name}' completed!")
        print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    args = parser.parse_args()
    StepExecutor(auto_push=args.push).run()


if __name__ == "__main__":
    main()
