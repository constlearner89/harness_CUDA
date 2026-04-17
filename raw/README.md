# raw

Reference-only input area for this harness framework.

이 폴더에는 현재 harness가 참고할 코드, 논문, 실험 노트, 예제 입력 등을 넣는다.

운영 규칙:

- `raw/`는 읽기 전용 reference 영역으로 취급한다.
- 이 폴더의 파일은 기본적으로 Git 추적 대상이 아니다.
- 다만 tracked 상태인 파일 수정은 harness executor의 dirty-worktree 차단 대상이 된다.
- step auto-commit에 포함되지 않는다.
- `raw/` 아래의 코드/문서는 TDD guard 대상이 아니다.
- `raw/` 아래의 PDF 논문은 harness step에서 로컬 `pdf` 스킬로 읽을 수 있다.
- 논문이나 참고 자료를 읽는 step은 `reference_contract`를 선언하고, 추출한 식, 표, 파라미터, 검증 기준을 `steps/artifacts/reference/` 아래 재사용 가능한 텍스트 또는 markdown으로 남기는 것을 기본 원칙으로 한다.
- reference artifact가 한 번 만들어지면, 후속 step은 가능하면 `raw/` 원본 대신 그 추출 산출물을 우선 사용한다.

필요하면 하위 폴더를 자유롭게 추가해도 된다.
