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

Resolve project guidelines via the CLAUDE.md resolution script:
```bash
python3 ~/.claude/scripts/resolve-claude-md.py \
  --git-dir {repo_root} \
  --merge-base {base_hash} \
  --ref-range {base_hash}..HEAD \
  --depth 5 \
  --check-head
```

Parse the JSON output and store:
- `ancestor_dirs_list` — for reference in reviewer prompts
- `expected_guidelines` — for cross-checking
- `expected_directives` — for cross-checking (filter to depth<=2 for orchestrator expectations)
- `pr_added_guidelines` — for CLI report
- `warnings` — print each as `⚠ <warning text>`
- `resolved_content` — to include in reviewer prompts
- `guidelines_loaded_section` — to include in reviewer prompts

If the script exits non-zero, stop and report the error.
If `warnings` is non-empty, print all warnings but do not treat them as fatal.

Run these 4 subagents every iteration:

- `code-quality-reviewer`
- `architecture-reviewer`
- `test-reviewer`
- `bug-reviewer`

Each reviewer prompt must include:

- compact plan summary from `{plan_contents}`
- commit context: `git -C {repo_root} log {base_hash}..HEAD`
- instruction to follow that reviewer's required output format exactly
- the following file-read, git instructions, and pre-resolved guidelines block:

```
IMPORTANT instructions for this review:
- All file reads must use absolute paths under {repo_root}/
- Your review scope is the diff range: {base_hash}..HEAD
- Use `git -C {repo_root} diff --no-renames {base_hash}..HEAD` to see the full diff
- Use `git -C {repo_root} diff --no-renames {base_hash}..HEAD -- <file>` for a single file's diff
- Use `git -C {repo_root} diff --no-renames --name-only {base_hash}..HEAD` for changed file list
- Use `git -C {repo_root} log {base_hash}..HEAD` for commit history
- Read files under {repo_root}/ to examine surrounding context beyond the diff

Project Guidelines (pre-resolved from CLAUDE.md files at merge-base {base_hash}):
---BEGIN GUIDELINES---
{resolved_content}
---END GUIDELINES---

For your #### Guidelines Loaded output section, use this pre-computed block:
---BEGIN GUIDELINES_LOADED---
{guidelines_loaded_section}
---END GUIDELINES_LOADED---
```

### 3.2 Parse, retry, and classify all issues

1. For each reviewer, attempt to parse:
   - Exactly one verdict: `### Verdict: APPROVE` or `### Verdict: REQUEST_CHANGES`
   - All issues from `REQUEST_CHANGES` verdicts, each tagged with **source reviewer identity** (bug-reviewer, architecture-reviewer, test-reviewer, code-quality-reviewer), title, file, lines, severity, category, problem, suggestion
   - Non-blocking blocks from `APPROVE` verdicts: `#### Nitpick N:` (architecture-reviewer, code-quality-reviewer) and `#### Recommendation N:` (test-reviewer), both with `**Comment**:` instead of `**Problem**:`/`**Suggestion**:`. Infer severity=nitpick from the header and include in classification. These do not need severity/category fields to parse successfully. Note: bug-reviewer does not emit nitpick blocks in APPROVE verdicts.
   - `#### Guidelines Loaded` section: extract guideline entries and directive sub-items separately.
     - **Guideline entries**: top-level bullets only — lines matching `- <path> (<source>)`
       with no leading indentation before the `-`. These are CLAUDE.md file paths.
     - **Directive sub-items**: indented bullets matching `  - @<directive> -> <resolved-path> (<status>)`
       (with 2+ spaces before `-`). These belong to the nearest preceding guideline entry.
     Cross-check guideline entries against `expected_guidelines` from script output:
     - Section missing entirely: log warning
       "Warning: <reviewer-name> did not report Guidelines Loaded — CLAUDE.md context unverifiable"
     - `expected_guidelines` is non-empty but reviewer reports "None found": log warning
       "Warning: <reviewer-name> reported no guidelines but expected: <expected list>"
     - Reviewer reports paths not in `expected_guidelines`: log warning
       "Warning: <reviewer-name> reported unexpected guideline path: <path>"
     - Expected path missing from reported set: log warning
       "Warning: <reviewer-name> did not report expected guideline: <path>"
     These are warnings only — do not trigger parse failure, retry, or verdict override.
   - For each entry in `expected_directives` (depth<=2 from script output), check if the reviewer
     reported a matching `@<directive>` sub-item under the corresponding parent path:
     - Directive not reported at all: log warning
       "Warning: <reviewer-name> did not report expected @ directive: @<directive> in <parent-path>"
     - Directive reported with any status (`resolved`, `truncated`, `not-found`,
       `cycle-skipped`): no warning (reviewer acknowledged the directive)
     - Directive reported with status `budget-dropped`: log informational
       "Info: <reviewer-name> budget-dropped @ directive: @<directive> in <parent-path>"
     - Reviewer reports @ directives not in `expected_directives`: no warning
   These are warnings only — do not trigger parse failure, retry, or verdict override.
   After completing all cross-checks, emit a compact guidelines summary to the CLI.
   Note: The implement command has no HTML report phase — this transient CLI summary is the
   sole guidelines visibility point, deliberately compact to avoid noise in the review loop.
   - If `expected_guidelines` is empty: `Guidelines: No CLAUDE.md files found in ancestor directories.`
   - Otherwise: `Guidelines: <N> CLAUDE.md files, <M> @ directives` (counts from `expected_guidelines` and `expected_directives`)
     - If all reviewers matched expectations: `  Reviewers: <N>/4 matched` (where N = number of reviewers with parseable output)
     - For any warnings: emit one `  ⚠ <reviewer-name>: <warning summary>` line per warning
   - If `pr_added_guidelines` is non-empty, append: `  PR-added (skipped): <path>, ...`
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
