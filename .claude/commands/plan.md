---
description: Break work into small verifiable tasks with acceptance criteria and dependency ordering
---

Invoke the agent-skills:planning-and-task-breakdown skill.

Read the existing spec (docs/specs/[work]/SPEC-[name].md) and the relevant codebase sections. Then:

1. Enter plan mode — read only, no code changes
2. Identify the dependency graph between components
3. Slice work vertically (one complete path per task, not horizontal layers)
4. Write tasks with acceptance criteria and verification steps
5. Add checkpoints between phases
6. Present the plan for human review

Save the plan to docs/specs/[work]/PLAN-[name].md and the task list to docs/specs/[work]/TASKS-[name].md, beside the spec.

If the plan or task file already exists with unchecked tasks for different work, stop and ask before writing — never silently overwrite an incomplete plan.
