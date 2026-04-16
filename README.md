# Harness Framework

Operational harness framework for future CUDA/C++ SPH work.

이 레포의 기본 목적은 Codex가 CUDA/C++ 기반 전산해석 프로젝트를 문서 기반으로 계획하고, phase/step 단위로 자동 실행하며, 최소한의 safety checks와 validation 규칙을 강제하도록 만드는 것이다.

## 핵심 실행 흐름

현 레포의 기본 사용법은 아래 한 줄로 요약할 수 있다.

`harness` 스킬 실행 -> `AGENTS.md`와 core docs 읽기 -> 사용자와 구현 범위와 validation 계획 구체화 -> 구현 계획을 phase/step으로 분해 -> phase 파일 생성 -> `scripts/execute.py` 실행 -> step 자동 실행 -> hook / repo checks 자동 검증

조금 더 풀어 쓰면 다음 순서다.

1. `harness` workflow를 시작한다.
2. `AGENTS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/RESULTS_POLICY.md`를 읽는다.
3. 사용자와 구현 범위, 제약, validation 계획을 구체화한다.
4. 계획을 `phases/index.json`, `phases/<task-name>/index.json`, `phases/<task-name>/stepN.md` 형태의 phase 파일로 만든다.
5. `python3 scripts/execute.py <task-name>`를 실행한다.
6. executor가 step들을 순차 실행한다.
7. 각 step 완료 시 hook / repo checks가 자동 검증한다.

## 필요한 것

- Python 3
- Bash
- Git
- Codex CLI

Codex에서 이 레포를 trusted project로 쓰려면 `~/.codex/config.toml`에 아래처럼 등록한다.

```toml
[projects."/home/hjpark/Research/harness_framework"]
trust_level = "trusted"
```

## 최소 명령어

### framework self-check

```bash
python3 -m pytest -q scripts
bash scripts/codex_repo_checks.sh
```

### phase 실행

```bash
python3 scripts/execute.py <task-name>
python3 scripts/execute.py <task-name> --push
```

## phase 구조

```text
phases/
├── index.json
└── <task-name>/
    ├── index.json
    ├── step0.md
    ├── step1.md
    └── ...
```

## step 완료 조건

- step 상태는 `phases/<task-name>/index.json`에서 관리한다.
- target-project validation step은 `results_contract`를 step 항목에 선언해야 한다.
- `results_contract` 최소 필드:
  - `summary_path`
  - `output_paths`
  - `comparison_artifacts`
  - `comparison_basis`

executor는 완료 직전 아래를 검사한다.

- 위험 프롬프트 preflight
- circuit breaker
- repo self-check
- `results_contract`에 선언한 결과 파일과 요약 섹션 존재 여부

## 참고

- 실행 규칙: `AGENTS.md`
- setup / 운영 설명: `docs/CODEX_SETUP.md`
- 결과 산출물 계약: `docs/RESULTS_POLICY.md`

target CUDA/C++ 프로젝트 쪽 validation 패턴은 보통 아래 명령을 포함한다.

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```
