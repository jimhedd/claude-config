---
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Task, AskUserQuestion
description: "Implement a plan file with a simple 4-agent review loop"
requires-argument: false
argument-hint: "plan-name.md [--squash|--no-squash]"
argument-description: "Optional plan file. If omitted, choose from recent files in ~/.claude/plans"
---

# Implement with Review Loop

Implement a plan, code normally, and use a tight 4-agent review loop until all reviewers approve.

## Phase 0: Preflight

1. Confirm git repo: `git rev-parse --is-inside-work-tree`.
2. Capture `repo_root`: `git rev-parse --show-toplevel`.
3. Require clean tree: `git -C {repo_root} status --porcelain` must be empty.
4. Capture `branch`: `git -C {repo_root} rev-parse --abbrev-ref HEAD`.
5. Extract optional `jira_ticket` from branch via `[A-Z][A-Z0-9]+-[0-9]+`.

If any step fails, stop and report the error.

## Phase 1: Resolve Plan and Squash Mode

1. Parse optional flags from `$ARGUMENTS`:
   - `--squash` => `squash_mode=on`
   - `--no-squash` => `squash_mode=off`
   - if both are present, stop with error
2. Resolve plan path from remaining argument:
   - absolute path => use as-is
   - relative path => resolve from current directory
   - bare `*.md` => also try `~/.claude/plans/<filename>`
3. If no plan argument:
   - list 5 most recent `~/.claude/plans/*.md`
   - ask user to choose via `AskUserQuestion`
   - if none selected, stop
4. Read selected plan into `{plan_contents}`.
5. If CLI did not set squash mode, read optional plan-level override:
   - `squash: true` => `on`
   - `squash: false` => `off`
   - otherwise default `auto`
6. Capture `base_hash`: `git -C {repo_root} rev-parse HEAD`.

## Phase 2: Implement and Initial Commit

1. Implement requested changes from `{plan_contents}`.
2. Confirm changes exist: `git -C {repo_root} status --porcelain` is non-empty.
3. Sanity-check your own diff and remove accidental edits.
4. Stage specific files only:
   - use changed tracked files and untracked files from git output
   - never use `git add -A` or `git add .`
5. Ensure staged diff is non-empty.
6. Create commit with substantive subject + body:
   - subject:
     - with ticket: `{jira_ticket} <conventional-commit-subject>`
     - without ticket: `<conventional-commit-subject>`
   - body: minimum 3 non-empty lines describing what/why/how
7. Reject forbidden trailers:
   - `Co-Authored-By:`
   - `Generated with Claude Code`

## Phase 3: Review Loop (4 Agents, Parallel)

Set:

- `iteration = 0`
- `max_iterations = 5`

Repeat until all reviewers approve or max iterations reached.

### 3.1 Run all reviewers in parallel

Run these 4 subagents every iteration:

- `code-quality-reviewer`
- `architecture-reviewer`
- `test-reviewer`
- `bug-reviewer`

Each reviewer prompt must include:

- compact plan summary from `{plan_contents}`
- commit context: `git -C {repo_root} log {base_hash}..HEAD`
- review range: `git -C {repo_root} diff {base_hash}..HEAD`
- instruction to follow that reviewer's required output format exactly

### 3.2 Parse verdicts

For each reviewer, parse exactly one verdict header:

- `### Verdict: APPROVE`
- `### Verdict: REQUEST_CHANGES`

If output is unparseable, rerun that reviewer once. If still unparseable, stop for manual intervention.

### 3.3 Decision

- If all 4 are `APPROVE`: exit loop and finalize.
- If any reviewer is `REQUEST_CHANGES`:
  - merge and prioritize blocking issues
  - implement fixes
  - add/update regression tests when fixing bug/correctness findings (when feasible)
  - stage specific files only
  - if staged diff is non-empty, create a remediation commit with substantive body
  - continue to next review iteration

If `iteration > max_iterations`, stop and report unresolved issues.

## Phase 4: Optional Squash and Finalize

1. Count commits since `base_hash`.
2. Squash policy:
   - `off`: keep commits
   - `auto`: squash only when commit count > 1
   - `on`: squash when commit count > 1
3. If squashing:
   - `git -C {repo_root} reset --soft {base_hash}`
   - create one clean final commit describing final implementation (not review process)
4. Show final commit: `git -C {repo_root} log -1 --pretty=full`.
5. Report final commit hash and subject.

## Error Handling

| Case | Action |
|---|---|
| Not in git repo | Stop and report error |
| Working tree not clean at start | Stop and ask user to commit/stash first |
| Plan path missing/invalid | Stop and report path |
| No plan selected | Stop |
| No changes after implementation | Stop with `No changes produced` |
| Staged diff empty | Stop with `Nothing staged` |
| Reviewer unparseable twice | Stop and report reviewer name |
| Max review iterations reached | Stop and report unresolved issues |
| Git commit fails | Stop and report git output |

## Important Rules

- Never use `git add -A` or `git add .`.
- Always run git commands as `git -C {repo_root} ...` after preflight.
- Always run all 4 reviewers each iteration.
- Keep review prompts compact and focused on `{base_hash}..HEAD`.
- Keep commits descriptive with real bodies.
- Never include forbidden trailers.
- Do not rewrite history unless squash mode resolves to `auto` or `on`.
