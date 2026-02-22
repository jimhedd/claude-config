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
- `max_iterations = 7`

Repeat until all reviewers approve or max iterations reached.

### 3.1 Run all reviewers in parallel

Before spawning agents, capture diff statistics:

```bash
git -C {repo_root} diff --no-renames --stat {base_hash}..HEAD
git -C {repo_root} diff --no-renames --shortstat {base_hash}..HEAD
```

Also compute the diff size: count changed files and total changed lines. If the diff is **small** (<50 files AND <3000 lines), capture the full inline diff to include in the prompt. If the diff is **large** (>=50 files OR >=3000 lines), do NOT include the inline diff — agents will fetch it themselves.

Probe for expected CLAUDE.md files:
1. From `git -C {repo_root} diff --name-only {base_hash}..HEAD`, extract the set of ancestor
   directories (e.g., for `services/catalog-service/foo.yml`: root, `services/`,
   `services/catalog-service/`). Deduplicate across all changed files.
2. For each directory (deepest first), test existence at merge-base:
   `git -C {repo_root} show {base_hash}:<dir>/CLAUDE.md` and
   `git -C {repo_root} show {base_hash}:<dir>/.claude/CLAUDE.md`
   (for root: `git -C {repo_root} show {base_hash}:CLAUDE.md` and
   `git -C {repo_root} show {base_hash}:.claude/CLAUDE.md`)
   Record each path that exists as `expected_guidelines`.
3. Do NOT include `expected_guidelines` in reviewer prompts — keep it orchestrator-internal
   for cross-checking only. Reviewers must discover CLAUDE.md files independently via their
   Step 3 workflow.

Run these 4 subagents every iteration:

- `code-quality-reviewer`
- `architecture-reviewer`
- `test-reviewer`
- `bug-reviewer`

Each reviewer prompt must include:

- compact plan summary from `{plan_contents}`
- commit context: `git -C {repo_root} log {base_hash}..HEAD`
- diff stat output (`--stat` and `--shortstat`)
- for small diffs: inline diff from `git -C {repo_root} diff --no-renames {base_hash}..HEAD`
- for large diffs: instruction to fetch the diff themselves
- instruction to follow that reviewer's required output format exactly
- the following file-read and git instructions block:

```
IMPORTANT instructions for this review:
- All file reads must use absolute paths under {repo_root}/
- Your review scope is the diff range: {base_hash}..HEAD
- Use `git -C {repo_root} diff --no-renames {base_hash}..HEAD` to see the full diff
- Use `git -C {repo_root} diff --no-renames --name-only {base_hash}..HEAD` for changed file list
- Use `git -C {repo_root} log {base_hash}..HEAD` for commit history
- Read files under {repo_root}/ to examine surrounding context beyond the diff
- For CLAUDE.md loading (workflow step 3), the merge_base commit is: {base_hash}
```

### 3.2 Parse, retry, and classify all issues

1. For each reviewer, attempt to parse:
   - Exactly one verdict: `### Verdict: APPROVE` or `### Verdict: REQUEST_CHANGES`
   - All issues from `REQUEST_CHANGES` verdicts, each tagged with **source reviewer identity** (bug-reviewer, architecture-reviewer, test-reviewer, code-quality-reviewer), title, file, lines, severity, category, problem, suggestion
   - Non-blocking blocks from `APPROVE` verdicts: `#### Nitpick N:` (architecture-reviewer, code-quality-reviewer) and `#### Recommendation N:` (test-reviewer), both with `**Comment**:` instead of `**Problem**:`/`**Suggestion**:`. Infer severity=nitpick from the header and include in classification. These do not need severity/category fields to parse successfully. Note: bug-reviewer does not emit nitpick blocks in APPROVE verdicts.
   - `#### Guidelines Loaded` section: extract the set of reported paths and their sources.
     Cross-check against `expected_guidelines`:
     - Section missing entirely: log warning
       "Warning: <reviewer-name> did not report Guidelines Loaded — CLAUDE.md context unverifiable"
     - `expected_guidelines` is non-empty but reviewer reports "None found": log warning
       "Warning: <reviewer-name> reported no guidelines but expected: <expected list>"
     - Reviewer reports paths not in `expected_guidelines`: log warning
       "Warning: <reviewer-name> reported unexpected guideline path: <path>"
     - Expected path missing from reported set: log warning
       "Warning: <reviewer-name> did not report expected guideline: <path>"
       (If reviewer included `(budget-limited, ...)` marker, append " (reviewer reported budget-limited)" to the warning for operator context.)
     - Source is not `merge-base` in an orchestrated flow (i.e., merge_base was provided
       in the prompt): log warning
       "Warning: <reviewer-name> loaded <path> from working tree instead of merge-base"
     These are warnings only — do not trigger parse failure, retry, or verdict override.
2. **Parse failure handling**: if a reviewer's output is unparseable, rerun that reviewer once. If still unparseable on retry, stop and report the reviewer name for manual intervention.
3. Classify every successfully parsed issue into P0/P1/P2/nitpick. Classification rules are **scoped by source reviewer** — the reviewer that produced the issue determines which rule applies:
   - **P0**: bug-reviewer severity=high (any category), bug-reviewer severity=medium AND category in {security, data-integrity, race-condition}, architecture-reviewer severity=high
   - **P1**: bug-reviewer severity=medium (remaining), bug-reviewer severity=low AND category in {security, data-integrity}, architecture-reviewer severity=medium, test-reviewer severity=high, code-quality-reviewer severity=high
   - **P2**: bug-reviewer severity=low (remaining), architecture-reviewer severity=low, test-reviewer severity=medium or low, code-quality-reviewer severity=medium or low
   - **Nitpick**: any reviewer severity=nitpick
4. Override the overall verdict: `REQUEST_CHANGES` if any P0 or P1 issue exists, `APPROVE` otherwise — regardless of what the individual agents said.

### 3.3 Decision

- If classified verdict is `APPROVE` (zero P0 and zero P1): exit loop and finalize.
- If classified verdict is `REQUEST_CHANGES`:
  - Before implementing fixes, deduplicate P0/P1/P2 findings across reviewers:
    - If multiple reviewers flag the same file + line(s) range, merge into one fix item
      and note which reviewers flagged it (use `File` + `Line(s)` fields for matching; nitpick-tier items are excluded from deduplication)
  - Prioritize P0 > P1 > P2
  - Address P0 items first, then P1, then P2 if iteration budget remains
  - implement fixes
  - add/update regression tests when fixing bug/correctness findings (when feasible)
  - stage specific files only
  - if staged diff is non-empty, create a remediation commit with substantive body
  - increment `iteration` and continue to next review iteration

If `iteration >= max_iterations` and classified verdict is not `APPROVE`, stop and report unresolved issues.

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
