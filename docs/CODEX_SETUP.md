# Codex Setup

이 레포는 더 이상 Claude Code의 `.claude/settings.json`을 사용하지 않는다.

Codex에서 현재 확인된 설정 계층은 다음 두 가지다.

- 전역 설정: `~/.codex/config.toml`
- 전역 규칙: `~/.codex/rules/*.rules`

즉, Claude처럼 레포 안의 `settings.json`으로 hook를 자동 등록하는 방식은 현재 이 레포 기준으로 사용하지 않는다.

## 이 레포에서 남긴 Codex 자산

- 프로젝트 규칙: `/AGENTS.md`
- 레포 로컬 스킬:
  - `/.codex/skills/harness/SKILL.md`
  - `/.codex/skills/review/SKILL.md`
- Codex 실행 진입점: `/scripts/codex_run.sh`
- 레포 검증 스크립트: `/scripts/codex_repo_checks.sh`
- 레포 로컬 guard scripts:
  - `/scripts/hooks/tdd-guard.sh`
  - `/scripts/hooks/dangerous-cmd-guard.sh`
  - `/scripts/hooks/circuit-breaker.sh`

## 권장 사용법

대화형 Codex:

```bash
./scripts/codex_run.sh
```

비대화식 Codex exec:

```bash
./scripts/codex_run.sh exec "요청 프롬프트"
```

실행 후 레포 검증까지 한 번에:

```bash
./scripts/codex_run.sh --run-checks exec "요청 프롬프트"
```

검증만 따로 실행:

```bash
./scripts/codex_repo_checks.sh
```

## 이 레포에서 기대하는 개발 흐름

이 레포는 아래 4-layer 흐름을 기대한다.

1. Layer 1: `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/RESULTS_POLICY.md`를 먼저 채워 프로젝트 의도를 명확히 한다.
2. Layer 2: `/AGENTS.md`에서 Codex의 실행 규칙을 정의한다.
3. Layer 3: `harness` workflow로 사용자와 논의해 계획을 구체화하고, 이를 phase로 쪼갠 뒤 `scripts/execute.py`로 순차 자동 실행한다.
4. Layer 4: hooks / repo checks가 자동 검증과 안전 장치를 담당한다.

즉 `harness` workflow의 목표는 “phase 파일만 생성”이 아니라 “논의 → phase 설계 → execute.py 실행 → hook 기반 자동 검증”까지 이어지는 것이다.

## 전역 Codex 설정 예시

이 레포를 trusted project로 쓰려면 `~/.codex/config.toml`에 아래 항목이 있어야 한다.

```toml
[projects."/absolute/path/to/harness_framework"]
trust_level = "trusted"
```

예:

```toml
[projects."/home/hjpark/Research/harness_framework"]
trust_level = "trusted"
```

## 운영 원칙

- 위험 명령 차단은 `/scripts/hooks/dangerous-cmd-guard.sh`로 대체한다.
- 하네스 step 실행 시 안전 규칙은 `scripts/execute.py` 프롬프트에 직접 주입된다.
- step 프롬프트에는 `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/RESULTS_POLICY.md`를 핵심 guardrail 문서로 주입한다.
- target-project validation이 필요한 step은 `phases/<task>/index.json`에 `results_contract`를 선언해야 하며, `scripts/execute.py`는 완료 직전 해당 산출물과 요약 파일 필수 항목을 검증한다.
- step이 `completed`로 표시되면 `scripts/execute.py`가 `/scripts/codex_repo_checks.sh`를 자동 실행한다.
- 단, `/scripts/codex_repo_checks.sh`는 framework self-check이고 target CUDA/C++ 프로젝트의 `cmake`/`ctest`/case-run/result-compare 검증을 대체하지 않는다.
- 구현 파일 변경 시 테스트 매핑 검사는 `/scripts/hooks/tdd-guard.sh`가 담당하며, 이는 `/scripts/codex_repo_checks.sh` 경유로 post-step validation에 포함된다.
- 반복 에러 경고는 `/scripts/hooks/circuit-breaker.sh`가 담당한다.
- 이 레포에서 Codex를 쓸 때는 가능하면 `/scripts/codex_run.sh`를 진입점으로 사용한다.

## 규칙 우선순위

1. `/AGENTS.md`: 실행 규칙의 최종 우선순위 문서
2. `/docs/PRD.md`, `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/RESULTS_POLICY.md`: 프로젝트 목적, 검증 계약, 결과 산출물 계약의 source 문서

둘이 충돌하면 실행 시에는 `/AGENTS.md`를 따르되, 문서 드리프트로 간주하고 docs를 보강해야 한다.

## CUDA/C++ SPH 작업 요청 작성 가이드

Codex에게 작업을 요청할 때는 가능하면 아래 정보를 포함한다.

- 작업 유형: 기능 추가 / 논문 구현 / 회귀 수정 / 성능 개선
- 대상 모듈 또는 의심 위치
- 참고 자료: 논문, 노트, 기존 커밋, 실패 로그
- 필수 검증: 어떤 케이스나 수치가 통과 기준인지
- 금지사항: 건드리면 안 되는 모듈, 유지해야 할 결과, 허용되지 않는 리팩터링 범위

예시:

```text
기존 SPH pressure 계산에 X 논문의 correction term을 추가하라.
대상은 pressure force와 관련 CUDA kernel이다.
논문 식 (12)~(15)를 기준으로 구현하라.
dam-break 기준 케이스에서 baseline 대비 결과를 비교하고,
기존 단일상 케이스 회귀가 없어야 한다.
neighbor search 구조는 변경하지 마라.
```
