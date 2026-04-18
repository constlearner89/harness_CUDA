# Harness Framework

Operational harness framework for future CUDA/C++ SPH work.

이 레포의 기본 목적은 Codex가 CUDA/C++ 기반 전산해석 프로젝트를 문서 기반으로 계획하고, step 단위로 자동 실행하며, 최소한의 safety checks와 validation 규칙을 강제하도록 만드는 것이다.

## 핵심 실행 흐름

현 레포의 기본 사용법은 아래 한 줄로 요약할 수 있다.

`harness` 스킬 실행 -> `AGENTS.md`와 core docs 읽기 -> 사용자와 구현 범위와 validation 계획 구체화 -> 구현 계획을 step으로 분해 -> `steps/` 파일 생성 -> `scripts/execute.py` 실행 -> step 자동 실행 -> hook / repo checks 자동 검증

조금 더 풀어 쓰면 다음 순서다.

1. `harness` workflow를 시작한다.
2. `AGENTS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/RESULTS_POLICY.md`를 읽는다.
3. 사용자와 구현 범위, 제약, validation 계획을 구체화한다.
4. 계획을 `steps/index.json`, `steps/stepN.md` 형태의 step 파일로 만든다.
5. `python3 scripts/execute.py`를 실행한다.
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

## raw 폴더

`raw/`는 harness가 참고할 코드, 논문, 노트, 샘플 입력 같은 reference-only 자료를 두는 곳이다.

- harness step은 필요하면 `raw/` 안의 파일을 읽어도 된다.
- `raw/` 안의 일반 참고자료는 기본적으로 Git 추적 대상이 아니다.
- 다만 `raw/README.md` 같은 tracked 파일 수정은 dirty worktree 차단에 걸린다.
- `raw/` 내용은 step auto-commit에 포함되지 않는다.
- `raw/` 안의 코드 조각은 TDD guard 대상이 아니다.
- `raw/` 안의 PDF 논문은 로컬 `pdf` 스킬로 읽고, 필요한 식/표/파라미터/검증 기준은 `steps/artifacts/reference/` 아래에 추출 산출물로 남긴다.
- 한 번 추출한 뒤에는 후속 step이 해당 정보가 필요할 때 `raw/` 원본을 반복해서 읽기보다 `steps/artifacts/reference/`의 산출물을 우선 재사용한다.

## 최소 명령어

### framework self-check

```bash
python3 -m pytest -q scripts
bash scripts/codex_repo_checks.sh
```

### harness 실행

```bash
python3 scripts/execute.py
python3 scripts/execute.py --push
```

## docs 변경 후 재구동

`steps/`만 삭제한다고 harness가 깨끗한 상태로 다시 시작되는 것은 아니다.

`scripts/execute.py`는 실행 전에 현재 Git worktree를 검사하고, 현재 `steps/` 관련 파일 밖의 변경이 남아 있으면 자동 실행을 중단한다. 이는 step auto-commit에 unrelated 변경이 섞이는 것을 막기 위한 안전장치다.

따라서 docs를 바꾼 뒤 하네스를 다시 돌릴 때의 기본 원칙은 다음과 같다.

1. docs 변경을 먼저 정리한다.
2. 기존 source/build/results/tmp 등 step 외 변경을 commit, stash, 또는 정리한다.
3. `steps/index.json`, `steps/stepN.md`를 현재 docs 기준으로 다시 만든다.
4. 그 다음 `python3 scripts/execute.py`를 실행한다.

주의:

- `steps/` 삭제만으로는 현재 세션 문맥이나 Git worktree 변경이 초기화되지 않는다.
- 같은 Codex 세션에서는 이전에 읽은 step 내용이 대화 문맥에 남아 있을 수 있다.
- 가장 깨끗한 재실행이 필요하면 새 step 계획 + 새 Codex 세션이 가장 안전하다.
- 단, untracked `build/`, `results/`, `cmake-build-*` 같은 generated output은 dirty worktree 차단에서 제외된다.

## step 구조

```text
steps/
├── index.json
├── step0.md
├── step1.md
└── ...
```

## step 완료 조건

- step 상태는 `steps/index.json`에서 관리한다.
- `steps/index.json` 최상위에 `validation_scope`를 선언한다.
- 허용되는 validation scope:
  - `framework`
  - `external-target`
- `validation_scope`가 `external-target`이면 최상위에 `target_root`도 선언해야 한다.
- 모든 step은 `type`을 선언해야 한다.
- 허용되는 step type:
  - `reference`
  - `implementation`
  - `validation`
- `raw/`의 논문이나 참고 자료를 읽는 step은 `reference_contract`를 step 항목에 선언해야 한다.
- `reference_contract` 최소 필드:
  - `source_files`
  - `output_paths`
  - `required_items`
- `validation` step은 `validation_commands`를 step 항목에 선언해야 한다.
- `validation_commands`는 비어 있으면 안 된다.
- `validation` step은 `results_contract`를 step 항목에 선언해야 한다.
- `results_contract` 최소 필드:
  - `summary_path`
  - `output_paths`
  - `comparison_artifacts`
  - `comparison_basis`
  - `validation_log_paths`

scope별 규칙:

- `framework`: framework self-check만 수행한다. `cmake`, `ctest`, `./build/...` 같은 external target 명령은 validation command로 넣지 않는다.
- `external-target`: executor가 step 실행 전 `target_root`와 `CMakeLists.txt` 존재를 선검사한다.
- repo root에 `src/`가 있으면 기본값은 `validation_scope: "external-target"`, `target_root: "."` 이다.
- repo root에 `src/`가 있고 루트 `CMakeLists.txt`가 없으면 harness는 framework-only 분석으로 축소하지 않는다. 먼저 루트 `CMakeLists.txt`를 생성하는 implementation step을 두고, 그 뒤 실제 `cmake`/`build`/`ctest` 검증으로 이어간다.
- bootstrap window가 끝난 뒤에도 `CMakeLists.txt`가 없으면 step은 `completed`가 아니라 `blocked` 또는 `error`여야 한다.

executor는 완료 직전 아래를 검사한다.

- 실행될 Acceptance Criteria / validation command에 대한 위험 명령 preflight
- circuit breaker
- repo self-check
- step type/schema 유효성
- `validation_scope`와 repo capability의 일치 여부
- `reference_contract`에 선언한 source/reference artifact와 required items 존재 여부
- `results_contract`에 선언한 결과 파일, validation log, 요약 섹션, 실행 명령 증빙 존재 여부

PDF reference step 기본 원칙:

- 텍스트 추출은 `pypdf`, `pdfplumber` 같은 Python 경로를 먼저 시도한다.
- Poppler CLI(`pdfinfo`, `pdftotext`, `pdftoppm`)는 있으면 사용하되 필수는 아니다.
- Poppler가 없다는 이유만으로 reference step을 바로 `blocked` 처리하지 않는다.

## 참고

- 실행 규칙: `AGENTS.md`
- setup / 운영 설명: `docs/CODEX_SETUP.md`
- 결과 산출물 계약: `docs/RESULTS_POLICY.md`
- 로컬 스킬: `.codex/skills/harness`, `.codex/skills/review`, `.codex/skills/pdf`

target CUDA/C++ 프로젝트 쪽 validation 패턴은 보통 아래 명령을 포함한다.

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

`src/`가 있는 external-target validation step은 최소한 위 세 명령을 포함해야 한다. `tests/CMakeLists.txt`가 있으면 bootstrap된 루트 `CMakeLists.txt`는 `enable_testing()`과 테스트 연결을 포함해야 하고, 실행 가능한 시뮬레이터/케이스가 있으면 대표 실행과 결과 비교 명령도 추가한다.

논문 figure 비교 산출물은 가능하면 외부 Python plotting 패키지 없이도 재현 가능한 형식을 우선한다.

- raw/sample data: `.csv`
- 비교 요약: `.md`
- overlay figure: `.svg`
- `.png`는 선택적 산출물로 둔다.
