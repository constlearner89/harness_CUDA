---
name: harness
description: Use when working in this repository to plan or generate single-goal step-based harness tasks, including `steps/index.json` and `steps/stepN.md` files for the Codex-driven execution flow.
---

# Harness

This repository uses a single-goal, step-based harness. Use this skill when the user wants to discuss implementation scope, draft steps, generate harness task files, and then run them automatically through `scripts/execute.py`.

## Layer Model

Always interpret this repository through four layers:

1. `docs/*.md` define project intent.
2. `/AGENTS.md` defines execution rules that must be enforced in every harness step.
3. Harness execution turns the agreed plan into ordered steps and runs them through `scripts/execute.py`.
4. Hook scripts and repo checks perform automatic validation and safety checks around execution.

## Workflow

1. Read `/AGENTS.md` and relevant `/docs/*.md` files first.
2. If docs are incomplete or implementation details are still open, discuss them with the user before creating steps.
3. Turn the agreed implementation plan into small, self-contained steps.
4. Create or update:
   - `steps/index.json`
   - `steps/stepN.md`
5. Unless the user explicitly wants to stop after planning, run:

```bash
python3 scripts/execute.py
```

6. Run or rely on the Layer 4 validation flow:
   - `scripts/hooks/tdd-guard.sh`
   - `scripts/hooks/dangerous-cmd-guard.sh`
   - `scripts/hooks/circuit-breaker.sh`
   - `scripts/codex_repo_checks.sh`
7. After execution, summarize the step-run result and note whether docs or rules should be refined.

## Step Design Rules

- Keep scope narrow. One step should cover one layer or one coherent module.
- Make each step self-contained. A step file must not rely on prior chat context.
- List the files the agent must read before making changes.
- If the task depends on papers stored under `raw/`, include the PDF paths explicitly in `읽어야 할 파일` and design an early step that uses the local `pdf` skill to extract the needed equations, parameters, validation targets, or tables into `steps/artifacts/reference/`.
- Once a `reference` step has produced artifacts under `steps/artifacts/reference/`, later steps should read those extracted artifacts first and go back to `raw/` only when the extracted notes are insufficient.
- Specify interfaces and constraints, not line-by-line implementations, unless a low-level requirement is critical.
- Acceptance criteria must be executable commands.
- If a step is `validation`, mirror the same commands in `validation_commands` inside `steps/index.json`.
- Write cautions concretely: `Do not do X. Reason: Y.`
- Use kebab-case for `steps[].name`.

## Step File Contracts

### `steps/index.json`

- Required top-level fields:
  - `project`
  - `goal`
  - `steps`
- Each step must contain:
  - `step` starting at `0`
  - `name`
  - `type` in `reference`, `implementation`, `validation`
  - `status` initialized to `pending`
- Validation steps must also contain:
  - `validation_commands`
  - `results_contract.summary_path`
  - `results_contract.output_paths`
  - `results_contract.comparison_artifacts`
  - `results_contract.comparison_basis`
  - `results_contract.validation_log_paths`
- Reference-extraction steps that read papers or other materials from `raw/` must also contain:
  - `reference_contract.source_files`
  - `reference_contract.output_paths`
  - `reference_contract.required_items`
- `summary`, `error_message`, and `blocked_reason` are written by the Codex run inside each step.
- `created_at` and step-level timestamps are written by `scripts/execute.py`.

### `steps/stepN.md`

Each step file should include:

- A step title
- A `읽어야 할 파일` section
- A `작업` section with implementation intent and constraints
- `Acceptance Criteria` with runnable commands
- `검증 절차` that tells Codex to update the matching step state in `steps/index.json`
- `금지사항`

If a step reads a paper from `raw/`, the step should also require:

- a reference artifact under `steps/artifacts/reference/`
- extracted items that are directly reusable later: equation numbers, constants, algorithm steps, validation targets, or comparison values
- a `reference_contract` entry in `steps/index.json` so the executor can verify the source files, output files, and required extracted items

If later steps depend on that paper-derived information, point them to the generated reference artifact instead of repeating broad reads of the original `raw/` source.

## Execution Model

The harness is executed with:

```bash
python3 scripts/execute.py
python3 scripts/execute.py --push
```

`scripts/execute.py` currently:

- creates or checks out `feat-<goal>`
- loads `/AGENTS.md` plus the core docs `PRD.md`, `ARCHITECTURE.md`, `ADR.md`, and `RESULTS_POLICY.md` into each step prompt
- accumulates completed-step summaries into later prompts
- retries a failed step up to 3 times with the prior error injected
- expects Codex to update the step status fields in `steps/index.json`
- validates step type/schema before execution
- validates that each declared validation command is recorded in both the summary and validation logs
- separates code commits from output metadata commits
- treats any step auto-commit failure as a hard error

When this skill is active, do not stop at generating step files unless the user explicitly asks to stop before execution.
Also treat hook-driven validation as part of the harness flow, not as an optional afterthought.

## Review Before Writing Files

Before generating step files, verify:

- the step order is implementation-safe
- every step has explicit validation commands
- every step references the docs and prior artifacts it needs
- the plan does not require hidden context from the conversation
