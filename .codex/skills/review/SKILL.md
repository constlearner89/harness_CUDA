---
name: review
description: Use when reviewing changes in this repository against the harness framework documents, especially `/AGENTS.md`, `/docs/ARCHITECTURE.md`, and `/docs/ADR.md`.
---

# Review

Use this skill when the user asks for a review of repository changes.

## Required Context

Read these first:

- `/AGENTS.md`
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`

Then inspect the changed files and evaluate them against the checklist below.

## Review Checklist

1. Architecture compliance: does the change follow the structure and patterns in `ARCHITECTURE.md`?
2. Stack compliance: does the change stay within the decisions in `ADR.md`?
3. Test coverage: are new behaviors covered by tests or is a missing test a real gap?
4. Critical rules: does the change violate any `CRITICAL` rules in `AGENTS.md`?
5. Build and verification: are the stated validation commands likely to pass, and were they actually run?

## Output Style

Default to a code review format:

- Findings first, ordered by severity
- Include file and line references when possible
- Keep summaries brief
- If no findings exist, say that explicitly and note residual risks or missing validation

If the user specifically wants the old checklist-style report, present:

| 항목 | 결과 | 비고 |
|------|------|------|
| 아키텍처 준수 | ✅/❌ | ... |
| 기술 스택 준수 | ✅/❌ | ... |
| 테스트 존재 | ✅/❌ | ... |
| CRITICAL 규칙 | ✅/❌ | ... |
| 빌드 가능 | ✅/❌ | ... |

When something fails, provide a concrete fix direction rather than a vague warning.
