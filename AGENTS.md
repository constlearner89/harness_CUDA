# 프로젝트: Harness Framework

## Harness Workflow

이 레포는 Codex가 연구개발용 하네스를 안정적으로 생성하고 실행할 수 있게 만드는 framework 레포다.

### Layer 1 — Docs

- 먼저 `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/RESULTS_POLICY.md`를 읽고 현재 framework의 목적과 제약을 확인한다.
- 문서가 비어 있거나 서로 충돌하면 구현 전에 먼저 문서를 보강한다.

### Layer 2 — Execution Rules

- `/AGENTS.md`는 Codex가 항상 따라야 하는 실행 규칙의 단일 기준이다.
- 여기 정의된 CRITICAL 규칙, 검증 규칙, 커밋 규칙은 harness step 프롬프트에도 그대로 주입된다.
- `/docs/*.md`는 프로젝트 목적, 아키텍처, 검증 계약, 결과 산출물 계약의 source 문서다.
- 충돌 시 우선순위는 `/AGENTS.md`가 가장 높다. docs와 `/AGENTS.md`가 어긋나면 step은 `/AGENTS.md`를 따르되 docs 보강이 필요한 상태로 간주한다.

### Layer 3 — Harness

- 사용자가 `harness` workflow를 요청하면 Codex는 다음 순서를 따른다.
  1. `/AGENTS.md`, `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/RESULTS_POLICY.md`를 읽는다.
  2. 사용자와 논의해 구현 범위와 계획을 구체화한다.
  3. 구현 계획을 phase와 step으로 쪼개 `phases/index.json`, `phases/<task>/index.json`, `phases/<task>/stepN.md`를 만든다.
  4. 사용자가 중단시키지 않는 한 `python3 scripts/execute.py <task-name>`를 실행해 phase를 순차 자동 실행한다.
  5. 실행 결과를 검토하고 필요하면 docs와 규칙을 다시 보강한다.

### Layer 4 — Hooks / Auto Validation

- hook/guard 계층은 phase 실행 전후의 자동 검증과 안전 장치를 담당한다.
- 기본 구성은 아래 스크립트다.
  - `scripts/hooks/tdd-guard.sh`
  - `scripts/hooks/dangerous-cmd-guard.sh`
  - `scripts/hooks/circuit-breaker.sh`
  - `scripts/codex_repo_checks.sh`
- Layer 4의 목적은 아래 네 가지다.
  - 구현 파일 변경 시 대응 테스트 누락 감지
  - 위험 명령 또는 위험 프롬프트 차단
  - 반복 실패 시 전략 변경 경고
  - 실행 후 자동 검증
- 자동 실행 경로에서 `scripts/execute.py`는 step 프롬프트 실행 전 위험 명령 preflight를 적용하고, 실패 반복 시 circuit breaker를 적용하며, step이 `completed`로 표시된 직후 `scripts/codex_repo_checks.sh`를 실행해 post-step framework validation을 강제한다.
- `scripts/codex_repo_checks.sh` 안에서 `scripts/hooks/tdd-guard.sh`가 실행되므로, TDD guard는 post-step validation 체인에 포함된다.

## 기술 스택
- Python 3 기반 harness executor 및 테스트
- Bash 기반 hook / validation 스크립트
- Markdown 기반 PRD / ARCHITECTURE / ADR 문서
- 대상 프로젝트 기준 검증 인터페이스: CMake + CTest

## 아키텍처 규칙
- CRITICAL: `scripts/execute.py`는 generic executor여야 하며 특정 task 내용이나 프로젝트별 예외를 하드코딩하지 않는다.
- CRITICAL: harness step에 주입되는 규칙은 반드시 `/AGENTS.md`, `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/RESULTS_POLICY.md`에서 읽어 구성한다.
- CRITICAL: 위험 명령 차단, TDD guard, circuit breaker는 우회 경로 없이 자동 실행 경로에 연결한다.
- `scripts/hooks/`는 guardrail만 담당하고, 오케스트레이션 책임은 `scripts/execute.py`와 `scripts/codex_run.sh`에 둔다.
- framework 자체 테스트와 target project 검증 규칙을 혼동하지 않는다.

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 실패하는 테스트 또는 재현 가능한 검증부터 추가하고, 그 다음 구현을 수정한다.
- CRITICAL: phase step은 framework 자체 검증만으로 완료 처리하면 안 된다. 대상 CUDA/C++ 프로젝트에 대해 최소한 빌드, 테스트, 대표 케이스 실행 또는 결과 비교를 포함한 검증을 수행해야 한다.
- CRITICAL: target-project validation의 기본 모델은 `cmake -S . -B build`, `cmake --build build`, `ctest --test-dir build --output-on-failure`, 대표 solver 실행, 결과 비교 스크립트 실행이다. step은 이 중 해당 작업에 필요한 명령을 Acceptance Criteria에 명시해야 한다.
- 변경 후에는 최소한 `bash scripts/codex_repo_checks.sh`를 통과시킨다.
- framework 검증과 target-project 검증은 둘 다 필요하다. `scripts/codex_repo_checks.sh`는 framework self-check일 뿐 target scientific validation을 대체하지 않는다.
- target-project validation이 포함된 step은 `results_contract`를 `phases/<task>/index.json` step 항목에 명시해야 한다. 최소 필드는 `summary_path`, `output_paths`, `comparison_artifacts`, `comparison_basis`다.
- target-project validation이 포함된 step은 `docs/RESULTS_POLICY.md`의 최소 산출물 계약을 충족해야 완료로 간주한다. 최소한 실행 명령, 출력 위치, 비교 기준, 비교 산출물, 요약 기록이 남아 있어야 하며, executor는 `results_contract`에 선언된 경로와 요약 파일 필수 섹션을 검증한다.
- `harness` workflow에서는 계획 수립 뒤 사용자가 중단시키지 않는 한 `scripts/execute.py` 실행까지 이어서 처리한다.
- 커밋 메시지는 conventional commits 형식을 따른다.

## Framework Repo Commands
python3 -m pytest -q scripts
bash scripts/codex_repo_checks.sh
python3 scripts/execute.py <task-name>
bash scripts/codex_run.sh --run-checks exec "<prompt>"

## Target Project Validation Pattern
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
