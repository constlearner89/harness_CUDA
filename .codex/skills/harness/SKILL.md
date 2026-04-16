---
name: harness
description: Use when working in this repository to plan or generate phase-based harness tasks, including `phases/index.json`, `phases/<task>/index.json`, and `stepN.md` files for the Codex-driven execution flow.
---

# Harness

This repository uses a phase-based harness. Use this skill when the user wants to discuss implementation scope, draft steps, generate harness task files, and then run the phase automatically through `scripts/execute.py`.

## Layer Model

Always interpret this repository through four layers:

1. `docs/*.md` define project intent.
2. `/AGENTS.md` defines execution rules that must be enforced in every harness step.
3. Harness execution turns the agreed plan into phases and runs them through `scripts/execute.py`.
4. Hook scripts and repo checks perform automatic validation and safety checks around execution.

## Workflow

1. Read `/AGENTS.md` and relevant `/docs/*.md` files first.
2. If docs are incomplete or implementation details are still open, discuss them with the user before creating steps.
3. Turn the agreed implementation plan into small, self-contained steps.
4. Create or update:
   - `phases/index.json`
   - `phases/<task-name>/index.json`
   - `phases/<task-name>/stepN.md`
5. Unless the user explicitly wants to stop after planning, run:

```bash
python3 scripts/execute.py <task-name>
```

6. Run or rely on the Layer 4 validation flow:
   - `scripts/hooks/tdd-guard.sh`
   - `scripts/hooks/dangerous-cmd-guard.sh`
   - `scripts/hooks/circuit-breaker.sh`
   - `scripts/codex_repo_checks.sh`
7. After execution, summarize the phase result and note whether docs or rules should be refined.

## Step Design Rules

- Keep scope narrow. One step should cover one layer or one coherent module.
- Make each step self-contained. A step file must not rely on prior chat context.
- List the files the agent must read before making changes.
- Specify interfaces and constraints, not line-by-line implementations, unless a low-level requirement is critical.
- Acceptance criteria must be executable commands.
- Write cautions concretely: `Do not do X. Reason: Y.`
- Use kebab-case for `steps[].name`.

## Phase File Contracts

### `phases/index.json`

- Maintain a top-level `phases` array.
- Each entry must contain:
  - `dir`
  - `status`
- `status` is one of: `pending`, `completed`, `error`, `blocked`.
- Timestamp fields are written by `scripts/execute.py`; do not prefill them.

### `phases/<task-name>/index.json`

- Required top-level fields:
  - `project`
  - `phase`
  - `steps`
- Each step must contain:
  - `step` starting at `0`
  - `name`
  - `status` initialized to `pending`
- Target-validation steps must also contain:
  - `results_contract.summary_path`
  - `results_contract.output_paths`
  - `results_contract.comparison_artifacts`
  - `results_contract.comparison_basis`
- `summary`, `error_message`, and `blocked_reason` are written by the Codex run inside each step.
- `created_at` and step-level timestamps are written by `scripts/execute.py`.

### `phases/<task-name>/stepN.md`

Each step file should include:

- A step title
- A `읽어야 할 파일` section
- A `작업` section with implementation intent and constraints
- `Acceptance Criteria` with runnable commands
- `검증 절차` that tells Codex to update the matching step state in `phases/<task-name>/index.json`
- `금지사항`

## Execution Model

The harness is executed with:

```bash
python3 scripts/execute.py <task-name>
python3 scripts/execute.py <task-name> --push
```

`scripts/execute.py` currently:

- creates or checks out `feat-<task-name>`
- loads `/AGENTS.md` and `/docs/*.md` into each step prompt
- accumulates completed-step summaries into later prompts
- retries a failed step up to 3 times with the prior error injected
- expects Codex to update the step status fields in `phases/<task-name>/index.json`
- separates code commits from output metadata commits

When this skill is active, do not stop at generating phase files unless the user explicitly asks to stop before execution.
Also treat hook-driven validation as part of the harness flow, not as an optional afterthought.

## Review Before Writing Files

Before generating step files, verify:

- the step order is implementation-safe
- every step has explicit validation commands
- every step references the docs and prior artifacts it needs
- the plan does not require hidden context from the conversation
