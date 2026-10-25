---
name: spec-driven-development
description: Creates specs before coding. Use when starting a new project, feature, or significant change and no specification exists yet. Use when drafting a PRD or requirements document with objectives and scope, or when requirements are unclear, ambiguous, or only exist as a vague idea. Use when a single requirement spans several independently testable capabilities and needs decomposing into a capability map of modules before specifying.
---

# Spec-Driven Development

## Overview

Write a structured specification before writing any code. The spec is the shared source of truth between you and the human engineer — it defines what we're building, why, and how we'll know it's done. Code without a spec is guessing.

## When to Use

- Starting a new project or feature
- Requirements are ambiguous or incomplete
- The change touches multiple files or modules
- You're about to make an architectural decision
- The task would take more than 30 minutes to implement

**When NOT to use:** Single-line fixes, typo corrections, or changes where requirements are unambiguous and self-contained.

## The Gated Workflow

Spec-driven development has four phases, preceded by a scope check (Phase 0) that activates only when one request bundles several independently testable capabilities. Do not advance to the next phase until the current one is validated.

```
SPECIFY ──→ PLAN ──→ TASKS ──→ IMPLEMENT
   │          │        │          │
   ▼          ▼        ▼          ▼
 Human      Human    Human      Human
 reviews    reviews  reviews    reviews
```

### Review Readiness Hooks

The evidence-based review tool reads the spec, the task files, and the test reports exactly. A document that looks right can still stop the review at setup or during the run. The checks below run the review tool's own parser and setup code (`scripts/review-readiness.py`, run in the tool's environment).

**Commit gate.** A phase that creates the spec or the task file ends only when that file is committed. The project's Git pre-commit hook (`scripts/pre-commit`) runs `review-readiness.py staged` on every commit and refuses the commit when:
- a staged spec does not parse as review setup parses it, or a `Verification:` line cannot be read;
- any task file in the commit breaks a review setup rule (the `plan` rules below), including after a spec change removes a requirement a task lists;
- a staged `.py` file adds a `parametrize`, a `class Test`, a skip, or an xfail.

A refused commit means the phase has not ended. Fix the file and commit again.

Before Phase 1 writes anything, check that the hook is installed:

```
ls -l "$(git rev-parse --git-common-dir)/hooks/pre-commit"
```

It must be a link to `/home/kyleschuetz/.agents/skills/spec-driven-development/scripts/pre-commit`. If it is missing, install it:

```
ln -s /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/pre-commit "$(git rev-parse --git-common-dir)/hooks/pre-commit"
```

If another pre-commit hook is already there, stop and ask the human. Never commit with `--no-verify`, and never remove, replace, or edit the hook.

The commands below run the same checks by hand, plus the named-test collection that a commit does not run:

| Gate | When | Command, run from the project checkout |
|---|---|---|
| Specify | After the spec is written, before asking for approval | `uv run --project /home/kyleschuetz/evidence-based-review python /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py spec <spec file>` |
| Plan and Tasks | After the spec, plan, and task file are committed, from a clean checkout | `uv run --project /home/kyleschuetz/evidence-based-review python /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py plan --task-file <task file>...` |
| Implement | After each task's work is committed | `uv run --project /home/kyleschuetz/evidence-based-review python /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py review task "<task file>#Task <n>" --candidate HEAD --task-file <task file>...` |
| Handoff to review | Before saying the work is ready for review, with the arguments the review will get | `uv run --project /home/kyleschuetz/evidence-based-review python /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py review <level> [<target>] --candidate <ref> --task-file <task file>...` |

Add `--worktree <path>` when running from another directory. `review` takes the same arguments as `evidence-based-review review`.

**Expected output:** one `PASS` line per check, then `READY`, exit code 0. Any `FAIL` line ends with `NOT READY` and exit code 1; the lines under it are the review tool's own message.

**Rules:**
- Do not finish a gate, ask for approval, mark a task done, or say anything is ready for review without running its hook and pasting its full output in your message.
- Only `READY` passes a gate. Fix the document or code and run the hook again. Never edit the review tool or this script to get `READY`.
- The hook does not run the tests. The named tests must also pass with the project's test command.

What each stage checks:
- `spec`: the spec parses as review setup parses it, and every `Verification:` line names its tests and one test file the project has a test command for. It prints the requirement count and IDs.
- `plan`: at `HEAD` of a clean checkout, every task file and its spec are committed and parse; every phase has tasks; every task lists requirements that exist in the spec, with readable `Verification:` lines; every recorded starting commit is a bare 40-character commit ID in `HEAD`'s history, in task order. A task that has not started may have an empty starting commit.
- `staged`: what the pre-commit hook runs on the commit being made.
- `review`: the review tool's setup (`scope.review_scope`) with no exceptions, including a starting commit for every task in scope. Then, for each requirement in scope, `uv run pytest --collect-only -q` on its test file collects each named test exactly once, as a bare `<test file>::<test name>`, with no other node of that name (`name[param]`, `Class::name`). Then `git grep` at the candidate finds no `parametrize`, `^class Test`, skip, or xfail in the folders of the named test files. Then the checkout is still clean.

### Phase 0: Scope Check

Most requests describe one capability. If this one does, skip this phase and go straight to Specify — Phase 0 exists for the exception, not the rule, and it puts no hierarchy on single-capability features.

**Detection.** Decompose before specifying when a single requirement bundles several independently testable capabilities:

- The requirement names distinct capabilities with their own consumers or data (e.g. identity, billing, notifications, reporting)
- Acceptance criteria cluster into groups that could ship and be verified separately
- One capability could be cut or replaced without rewriting the others' requirements

**Propose a capability map before writing any spec.** Small and reviewable — a module table plus a build order, not a project plan:

```markdown
# Capability Map: [Initiative Name]

| Module id | Responsibility | Depends on |
|---|---|---|
| identity | Accounts, sessions, SSO | — |
| billing | Plans, invoices, payments | identity |
| notifications | Email and webhook fan-out | identity |
| reporting | Usage dashboards | billing, notifications |

Build order: identity → billing, notifications → reporting
```

- **Stable module ids.** Kebab-case, chosen once, never renamed mid-initiative. Specs, plans, and downstream commands select work by these ids instead of guessing which spec is active.
- **Dependency direction, no cycles.** Arrows point one way. If two modules each need the other, they are one module.
- **Interfaces live at the boundary.** The map records that `billing` depends on `identity`; the contract between them belongs in the provider module's spec (see `api-and-interface-design` for designing it).

**The map is gated like every phase.** The human reviews module boundaries, dependency direction, and build order before any module spec is written. Getting the map wrong is expensive; reviewing ten lines is not.

**Then recurse per module.** Run Specify → Plan → Tasks → Implement for each module in dependency order. Each module gets its own spec, scoped to that module's objective, boundaries, and success criteria. Save the approved map at the project root and each module's spec alongside it, named by module id (`SPEC-identity.md`, `SPEC-billing.md`) — the map, not filename guessing, is the index of what exists.

### Phase 1: Specify

Start with a high-level vision. Ask the human clarifying questions until requirements are concrete.

**Surface assumptions immediately.** Before writing any spec content, list what you're assuming:

```
ASSUMPTIONS I'M MAKING:
1. This is a web application (not native mobile)
2. Authentication uses session-based cookies (not JWT)
3. The database is PostgreSQL (based on existing Prisma schema)
4. We're targeting modern browsers only (no IE11)
→ Correct me now or I'll proceed with these.
```

Don't silently fill in ambiguous requirements. The spec's entire purpose is to surface misunderstandings *before* code gets written — assumptions are the most dangerous form of misunderstanding.

**Write a spec document covering these six core areas:**

1. **Objective** — What are we building and why? Who is the user? What does success look like?

2. **Commands** — Full executable commands with flags, not just tool names.
   ```
   Build: npm run build
   Test: npm test -- --coverage
   Lint: npm run lint --fix
   Dev: npm run dev
   ```

3. **Project Structure** — Where source code lives, where tests go, where docs belong.
   ```
   src/           → Application source code
   src/components → React components
   src/lib        → Shared utilities
   tests/         → Unit and integration tests
   e2e/           → End-to-end tests
   docs/          → Documentation
   ```

4. **Code Style** — One real code snippet showing your style beats three paragraphs describing it. Include naming conventions, formatting rules, and examples of good output.

5. **Testing Strategy** — What framework, where tests live, coverage expectations, which test levels for which concerns.

6. **Boundaries** — Three-tier system:
   - **Always do:** Run tests before commits, follow naming conventions, validate inputs
   - **Ask first:** Database schema changes, adding dependencies, changing CI config
   - **Never do:** Commit secrets, edit vendor directories, remove failing tests without approval

**Spec template:**

```markdown
# Spec: [Project/Feature Name]

## Objective
[What we're building and why. User stories or acceptance criteria.]

## Requirements
- **R1:** [What must be true. One testable statement.]
  Verification: [The test in this project that proves it, e.g. `test_name` in `tests/test_file.py`.]

## Tech Stack
[Framework, language, key dependencies with versions]

## Commands
[Build, test, lint, dev — full commands]

## Project Structure
[Directory layout with descriptions]

## Code Style
[Example snippet + key conventions]

## Testing Strategy
[Framework, test locations, coverage requirements, test levels]

## Boundaries
- Always: [...]
- Ask first: [...]
- Never: [...]

## Success Criteria
[How we'll know this is done — specific, testable conditions]

## Open Questions
[Anything unresolved that needs human input]
```

**Requirements format.** List every requirement under one `## Requirements` heading, with how it is verified. Tasks and reviews refer to requirements by ID.

```markdown
## Requirements

- **R1:** The review level is `task`, `phase`, or `full_work`.
  Verification: `test_unknown_level_stops` in `tests/test_scope.py`.
```

- Requirements are the `- **<ID>:**` items under the `## Requirements` heading. An ID is a letter followed by letters, digits, dots, or hyphens.
- The spec has exactly one `## Requirements` heading. Every non-blank line under it is either an item line or an indented continuation line of the item above. Introductory prose, notes, and tables go in another section. Any heading, including a `###` subheading, ends the section, so items under it are not read.
- The text runs to the `Verification:` line, an indented continuation line. The verification runs to the next item or heading. Both must be present.
- Every requirement is tested inside the project under test. The `Verification:` line names one test as a backticked `test...` identifier and one test file as a backticked path with its suffix, relative to the repository root. It contains no other backticked span with a dot in it, because the review reads every such span as a test file. A command, `diff`, manual check, or check outside the repository is not a verification.
- A named test is one plain module-level function in that file, run once. Never parametrize it, put it in a test class, give it a parametrized fixture, or skip or xfail it. The review runs the test file and matches the name to exactly one JUnit test case by exact name. `name[param]` never matches. `Class::name` is not read as a test name, and one method name in two classes matches several cases, which stops the review. A skipped test also stops it. Kyle's rule for his projects: no parametrized tests and no test classes at all.
- A real run is acceptance evidence for a task, not a requirement. Put its expected result in the task's acceptance criteria.
- An install is a release step, not a requirement.
- IDs are unique within the spec and are never renumbered.
- Format examples inside code fences are not read as requirements.
- Every spec uses this format, in every project. Do not substitute another spec tool's format.

**Reframe instructions as success criteria.** When receiving vague requirements, translate them into concrete conditions:

```
REQUIREMENT: "Make the dashboard faster"

REFRAMED SUCCESS CRITERIA:
- Dashboard LCP < 2.5s on 4G connection
- Initial data load completes in < 500ms
- No layout shift during load (CLS < 0.1)
→ Are these the right targets?
```

This lets you loop, retry, and problem-solve toward a clear goal rather than guessing what "faster" means.

**Specify hook.** Commit the spec; the pre-commit hook checks it. Before asking the human to approve the spec, also run `uv run --project /home/kyleschuetz/evidence-based-review python /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py spec <spec file>` and paste its output. It must end with `READY`.

### Phase 2: Plan

With the validated spec, generate a technical implementation plan:

1. Identify the major components and their dependencies
2. Determine the implementation order (what must be built first)
3. Note risks and mitigation strategies
4. Identify what can be built in parallel vs. what must be sequential
5. Define verification checkpoints between phases

> Follow `planning-and-task-breakdown` for the dependency-graph mapping and vertical-slicing mechanics behind these steps; it is the canonical source. The bullets above are a lightweight summary; if they ever diverge, `planning-and-task-breakdown` takes precedence.
>
> **Output convention:** Save the plan to `tasks/plan.md` and record the task list in the task list target defined by `planning-and-task-breakdown`: a markdown task file committed to the repository (default `tasks/todo.md`). A tracker may mirror it but does not replace it. Create `tasks/` if it does not exist. Downstream commands (`/build`, etc.) expect these defaults.

The plan should be reviewable: the human should be able to read it and say "yes, that's the right approach" or "no, change X."

### Phase 3: Tasks

Break the plan into discrete, implementable tasks:

- Each task should be completable in a single focused session
- Each task has explicit acceptance criteria
- Each task includes a verification step (test, build, manual check)
- Tasks are ordered by dependency, not by perceived importance
- No task should require changing more than ~5 files

> Follow `planning-and-task-breakdown` for the full task-sizing and dependency-ordering mechanics; it is the canonical source. The template below is a lightweight inline form; if they ever diverge, `planning-and-task-breakdown` takes precedence.

**Task template:**
```markdown
**Spec:** [SPEC-[name].md](SPEC-[name].md)

## Phase [N]: [Title]

### Task [N]: [Description]

**Requirements:** [Spec requirement IDs, e.g. R1, R3]
**Starting commit:** [Full ID of HEAD when the task starts. Empty until then.]

**Acceptance criteria:**
- [ ] [What must be true when done]

**Verification:**
- [ ] [How to confirm: test command, build, manual check]

**Files likely touched:**
- [Which files will be touched]
```

The task file rules are in `planning-and-task-breakdown` (Step 4). The one that fails most often: `**Starting commit:**` is the bare 40-character ID, with no backticks or other text.

**Plan and Tasks hook.** Commit the spec, plan, and task file. From a clean checkout, run `uv run --project /home/kyleschuetz/evidence-based-review python /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py plan --task-file <task file>...` and paste its output. It must end with `READY` before you ask the human to approve the tasks.

### Phase 4: Implement

Execute tasks one at a time following `skills/incremental-implementation/SKILL.md` (`incremental-implementation`) and `skills/test-driven-development/SKILL.md` (`test-driven-development`). Use `skills/context-engineering/SKILL.md` (`context-engineering`) to load the right spec sections and source files at each step rather than flooding the agent with the entire spec.

Write each named verification test as one plain function whose single run proves its requirement (see Requirements format).

**Implement hook, per task.** Record the task's starting commit before its work, commit the work, then run `uv run --project /home/kyleschuetz/evidence-based-review python /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py review task "<task file>#Task <n>" --candidate HEAD --task-file <task file>...` and paste its output. The task is not done until it ends with `READY` and the named tests pass.

**Handoff hook.** Before saying work is ready for review, or starting `evidence-based-review review`, run the `review` stage with the exact level, target, candidate, and task files the review will get, and paste its output. It must end with `READY`.

## Keeping the Spec Alive

The spec is a living document, not a one-time artifact:

- **Update when decisions change** — If you discover the data model needs to change, update the spec first, then implement.
- **Update when scope changes** — Features added or cut should be reflected in the spec.
- **Commit the spec** — The spec belongs in version control alongside the code.
- **Reference the spec in PRs** — Link back to the spec section that each PR implements.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "This is simple, I don't need a spec" | Simple tasks don't need *long* specs, but they still need acceptance criteria. A two-line spec is fine. |
| "I'll write the spec after I code it" | That's documentation, not specification. The spec's value is in forcing clarity *before* code. |
| "The spec will slow us down" | A 15-minute spec prevents hours of rework. Waterfall in 15 minutes beats debugging in 15 hours. |
| "Requirements will change anyway" | That's why the spec is a living document. An outdated spec is still better than no spec. |
| "The user knows what they want" | Even clear requests have implicit assumptions. The spec surfaces those assumptions. |
| "It's one big feature; splitting it is overhead" | If acceptance criteria cluster into independently testable groups, a monolithic spec forces every downstream task to reason over the whole contract. A ten-line capability map is the cheap alternative. |
| "I read the spec and task file; the format is right" | Reading is not checking. Three reviews failed at setup or mid-run on formats that looked right. Run the hook and paste its output. |
| "The test is parametrized, but the base name is the one in the spec" | The review matches the exact name to one test case. `name[param]` never matches. Write one plain function. |
| "I'll decompose during planning" | Planning slices tasks within a spec. By then the oversized artifact already exists — module boundaries and dependency direction must be decided before the spec is written, not after. |

## Red Flags

- Starting to write code without any written requirements
- Asking "should I just start building?" before clarifying what "done" means
- Implementing features not mentioned in any spec or task list
- Making architectural decisions without documenting them
- Saying a spec, task, or piece of work is done or ready for review without pasted hook output that ends with `READY`
- A commit made with `--no-verify`, or a project with no readiness pre-commit hook
- A named verification test that is parametrized, inside a test class, or skipped
- Skipping the spec because "it's obvious what to build"
- One spec whose requirements span several independently testable capabilities
- Module boundaries or build order decided implicitly during implementation because no capability map was approved up front

## Verification

Before proceeding to implementation, confirm:

- [ ] The spec covers all six core areas
- [ ] The human has reviewed and approved the spec
- [ ] Success criteria are specific and testable
- [ ] Every requirement has a stable ID and a `Verification:` line under `## Requirements` that names one test and its `.py` file in this project
- [ ] The project's pre-commit hook is the link to `scripts/pre-commit`, and the spec and task file were committed through it
- [ ] The Specify hook output is pasted and ends with `READY`
- [ ] The Plan and Tasks hook output is pasted and ends with `READY`
- [ ] Boundaries (Always/Ask First/Never) are defined
- [ ] The spec is saved to a file in the repository
- [ ] If the request bundles several independently testable capabilities, a capability map (module ids, dependency direction, build order) was approved before any module spec was written
- [ ] Every module spec traces to a module id in the approved map
