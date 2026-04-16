# Harness Framework

Operational harness framework for future CUDA/C++ SPH work.

이 레포는 solver 자체가 아니라, Codex가 CUDA/C++ 기반 전산해석 프로젝트를 문서 기반으로 계획하고 phase 단위로 자동 실행할 수 있게 만드는 framework 레포다.

## What This Repo Provides

- core docs: `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/RESULTS_POLICY.md`
- execution rules: `AGENTS.md`
- harness executor: `scripts/execute.py`
- Codex wrapper: `scripts/codex_run.sh`
- auto-validation / safety hooks:
  - `scripts/hooks/tdd-guard.sh`
  - `scripts/hooks/dangerous-cmd-guard.sh`
  - `scripts/hooks/circuit-breaker.sh`
  - `scripts/codex_repo_checks.sh`
- local Codex skills:
  - `.codex/skills/harness/SKILL.md`
  - `.codex/skills/review/SKILL.md`

## Install

### Prerequisites

- Python 3
- Bash
- Git
- Codex CLI available on `PATH`

### Codex Trust Setup

Codex에서 이 레포를 trusted project로 쓰려면 `~/.codex/config.toml`에 아래처럼 등록한다.

```toml
[projects."/home/hjpark/Research/harness_framework"]
trust_level = "trusted"
```

### Verify Setup

```bash
python3 -m pytest -q scripts
bash scripts/codex_repo_checks.sh
```

## Basic Usage

### Interactive Codex

```bash
./scripts/codex_run.sh
```

### Non-interactive Codex

```bash
./scripts/codex_run.sh exec "요청 프롬프트"
```

### Run Codex Then Repo Checks

```bash
./scripts/codex_run.sh --run-checks exec "요청 프롬프트"
```

## Harness Workflow

이 레포의 기본 운영 흐름은 다음과 같다.

1. `AGENTS.md`와 core docs를 읽는다.
2. 사용자와 구현 범위와 validation 계획을 구체화한다.
3. phase 파일을 만든다.
4. `scripts/execute.py`로 step들을 자동 실행한다.
5. hook / repo checks로 자동 검증한다.

phase 구조:

```text
phases/
├── index.json
└── <task-name>/
    ├── index.json
    ├── step0.md
    ├── step1.md
    └── ...
```

phase 실행:

```bash
python3 scripts/execute.py <task-name>
python3 scripts/execute.py <task-name> --push
```

## Step Contract

- 각 step은 `phases/<task-name>/stepN.md`에 정의한다.
- 각 step 상태는 `phases/<task-name>/index.json`에서 관리한다.
- target-project validation step은 `results_contract`를 step 항목에 선언해야 한다.
- `results_contract` 최소 필드:
  - `summary_path`
  - `output_paths`
  - `comparison_artifacts`
  - `comparison_basis`

executor는 step 완료 직전 아래를 검사한다.

- 위험 프롬프트 preflight
- circuit breaker
- repo self-check
- `results_contract`에 선언한 결과 파일과 요약 섹션 존재 여부

## Framework Commands

```bash
python3 -m pytest -q scripts
bash scripts/codex_repo_checks.sh
python3 scripts/execute.py <task-name>
bash scripts/codex_run.sh --run-checks exec "<prompt>"
```

## Target Project Validation Pattern

아래 명령은 이 framework 레포 자체가 아니라, future CUDA/C++ target project에서 step acceptance criteria에 들어갈 기본 패턴이다.

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

추가로 representative solver run, baseline comparison, results summary를 함께 남겨야 한다. 자세한 규칙은 `docs/RESULTS_POLICY.md`를 따른다.
