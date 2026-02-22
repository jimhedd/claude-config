---
allowed-tools: Bash(gh pr:*), Bash(git:*), Bash(* /tmp/*), Read, Glob, Grep, Task, Write
description: Review a GitHub pull request using 4 parallel reviewer agents with worktree isolation.
requires-argument: true
argument-description: PR number to review
---

# Review GitHub PR

Perform a comprehensive code review on a GitHub pull request using 4 specialized reviewer agents in parallel. Outputs a CLI summary and an HTML report. No comments are posted to GitHub.

## Bash Command Rules

**CRITICAL**: The `Bash(git:*)` permission only matches commands that START with `git`. You MUST:
- Run each git command as a standalone `git ...` call — never wrap in shell variable assignments like `VAR=$(git ...)` or chain with `&&`
- Capture outputs by reading the Bash tool result, then reference the value in subsequent commands
- Each `gh` command must start with `gh` (matched by `Bash(gh pr:*)`)

## Instructions

### Step 1: Validate Input

The PR number is provided in `$ARGUMENTS`. If empty or non-numeric, stop and print usage:
```
Usage: /review-github-pr <PR_NUMBER>
```

### Step 2: Gather PR Metadata

```bash
gh pr view <PR_NUMBER> --json number,title,body,baseRefName,headRefName,commits,additions,deletions,changedFiles,headRefOid,baseRefOid
```

Extract and store: `pr_number`, `pr_title`, `pr_body`, `base_ref`, `head_ref`, `base_sha`, `head_sha`, `additions`, `deletions`, `changed_files`.

If this command fails, stop and report: "PR #<N> not found or `gh` auth error."

### Step 3: Create Isolated Worktree

Determine the repo root:
```bash
git rev-parse --show-toplevel
```
Remember the output as `repo_root`.

Set `worktree_path=/tmp/pr-review-<PR_NUMBER>-<first 8 chars of head_sha>`.

Clean up any stale worktree from a previous interrupted run:
```bash
git -C <repo_root> worktree remove --force <worktree_path>
```
(Ignore errors if it doesn't exist.)

Fetch the PR head and the base branch (so merge-base has both sides). Use explicit refspecs to avoid FETCH_HEAD race conditions between the two fetches:
```bash
git -C <repo_root> fetch origin pull/<PR_NUMBER>/head:refs/pr-review/<PR_NUMBER>
```
```bash
git -C <repo_root> fetch origin <base_ref>
```

Create a detached worktree at the exact PR head SHA (from `gh pr view`, not FETCH_HEAD):
```bash
git -C <repo_root> worktree add --detach <worktree_path> <head_sha>
```

Compute the merge base (this gives us the same diff range GitHub shows):
```bash
git -C <worktree_path> merge-base <base_sha> HEAD
```
Remember the output as `merge_base`.

Get the diff stat, summary, and commit log (run each as a separate command):
```bash
git -C <worktree_path> diff --no-renames --stat <merge_base>..HEAD
```
```bash
git -C <worktree_path> diff --no-renames --shortstat <merge_base>..HEAD
```
```bash
git -C <worktree_path> log --oneline <merge_base>..HEAD
```

For large PRs (>50 changed files or >3000 lines changed), only include the diff stat in agent prompts and let agents fetch details themselves. For smaller PRs, also capture the full diff:
```bash
git -C <worktree_path> diff --no-renames <merge_base>..HEAD
```

Probe for expected CLAUDE.md files:
1. From `git -C <worktree_path> diff --name-only <merge_base>..HEAD`, extract the set of
   ancestor directories (e.g., for `services/catalog-service/foo.yml`: root, `services/`,
   `services/catalog-service/`). Deduplicate across all changed files.
2. For each directory (deepest first), test existence at merge-base:
   `git -C <worktree_path> show <merge_base>:<dir>/CLAUDE.md` and
   `git -C <worktree_path> show <merge_base>:<dir>/.claude/CLAUDE.md`
   (for root: `git -C <worktree_path> show <merge_base>:CLAUDE.md` and
   `git -C <worktree_path> show <merge_base>:.claude/CLAUDE.md`)
   Record each path that exists as `expected_guidelines`.
3. Do NOT include `expected_guidelines` in reviewer prompts — keep it orchestrator-internal
   for cross-checking only.
4. For each path in `expected_guidelines`, read its content at merge-base:
   `git -C <worktree_path> show <merge_base>:<path>`
   Scan for @ directive candidates using the same rules reviewers use (step 3.3.1):
   - `@` must be the first non-whitespace character on the line
   - The `@<path>` token must NOT be inside a fenced code block (``` or ~~~)
   - The `@<path>` token must NOT be inside an inline code span (backticks)
   - `<path>` must consist only of safe path characters (`A-Za-z0-9._/~-`)
   - Reject paths containing `..` as a path component
   - Reject absolute paths (starting with `/`)
   Lines not matching all criteria are skipped (not recorded as expected directives)
   For each candidate, resolve the path relative to the CLAUDE.md's directory.
   Record as `expected_directives` (set of {parent_path, directive_text, resolved_path}).
   This is orchestrator-internal only — do NOT include in reviewer prompts.

### Step 4: Spawn 4 Reviewer Agents in Parallel

Launch all 4 agents using the Task tool **in a single message** so they run concurrently. Each agent receives the same context block plus its specific focus instructions.

**Context block for all agents** (adapt the git commands to use `-C <worktree_path>`):

```
You are reviewing PR #<pr_number>: <pr_title>

PR Description:
<pr_body>

Commits:
<commit_log>

Diff stat:
<diff_stat>
<diff_summary>

Worktree path: <worktree_path>
Diff range: <merge_base>..HEAD

IMPORTANT instructions for this review:
- All git commands must use: git -C <worktree_path> ...
- All file reads must use absolute paths under <worktree_path>/
- Your review scope is the diff range: <merge_base>..HEAD
- Use `git -C <worktree_path> diff --no-renames <merge_base>..HEAD` to see the full diff
- Use `git -C <worktree_path> diff --no-renames --name-only <merge_base>..HEAD` for changed file list
- Use `git -C <worktree_path> log <merge_base>..HEAD` for commit history
- Read files under <worktree_path>/ to examine surrounding context beyond the diff
- For CLAUDE.md loading (workflow step 3), the merge_base commit is: <merge_base>

<if small PR: include full_diff here>
<if large PR: "This is a large PR. Use the git and Read commands above to examine changes yourself.">

Perform your review now.
```

The 4 agents to spawn (all via `Task` tool with appropriate `subagent_type`):

| Task | subagent_type | Focus |
|---|---|---|
| Bug Review | `bug-reviewer` | Logic errors, null safety, race conditions, resource leaks, security, data integrity |
| Architecture Review | `architecture-reviewer` | Design patterns, coupling, API design, caller-impact contracts, separation of concerns |
| Code Quality Review | `code-quality-reviewer` | Readability, naming, DRY, style consistency, maintainability |
| Test Coverage Review | `test-reviewer` | Test existence, edge cases, assertion quality, test structure |

### Step 5: Collect and Parse Agent Outputs

Each agent returns structured output with:
- A verdict line: `### Verdict: APPROVE` or `### Verdict: REQUEST_CHANGES`
- Zero or more issues, each with: Title, File, Line(s), Severity (high/medium/low/nitpick), Category, Problem, Suggestion

Parse each agent's output to extract:
1. The verdict (APPROVE or REQUEST_CHANGES)
2. All issues with their severity and category

If an agent's output cannot be parsed, warn about that reviewer but continue with the others. If ALL agents fail, stop and report the error.

For each reviewer, extract and cross-check the `#### Guidelines Loaded` section:
- **Guideline entries**: top-level bullets only — lines matching `- <path> (<source>)`
  with no leading indentation before the `-`. These are CLAUDE.md file paths.
- **Directive sub-items**: indented bullets matching `  - @<directive> -> <resolved-path> (<status>)`
  (with 2+ spaces before `-`). These belong to the nearest preceding guideline entry.
Cross-check guideline entries against `expected_guidelines`:
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

For each entry in `expected_directives`, check if the reviewer reported a matching
`@<directive>` sub-item under the corresponding parent path:
- Directive not reported at all: log warning
  "Warning: <reviewer-name> did not report expected @ directive: @<directive> in <parent-path>"
- Directive reported with any status (`resolved`, `truncated`, `not-found`,
  `cycle-skipped`): no warning (reviewer acknowledged the directive)
- Directive reported with status `budget-dropped`: log informational
  "Info: <reviewer-name> budget-dropped @ directive: @<directive> in <parent-path>"
- Reviewer reports @ directives not in `expected_directives`: no warning
  (reviewer may have found directives the orchestrator's simplified heuristic missed,
  or recursive includes that the orchestrator does not track)
These are warnings only — do not trigger parse failure, retry, or verdict override.

### Step 6: Classify Issues into Priority Tiers

Map each agent issue to a priority tier using these rules:

**P0 — Must Fix** (correctness bugs, security, data corruption, fundamental violations):
- Bug reviewer: severity=high (any category)
- Bug reviewer: severity=medium AND category in {security, data-integrity, race-condition}
- Architecture reviewer: severity=high

**P1 — Should Fix** (moderate bugs, significant arch issues, missing critical tests):
- Bug reviewer: severity=medium (remaining, not already P0)
- Bug reviewer: severity=low AND category in {security, data-integrity}
- Architecture reviewer: severity=medium
- Test reviewer: severity=high
- Code quality reviewer: severity=high

**P2 — Consider Fixing** (minor bugs, small inconsistencies, missing edge-case tests):
- Bug reviewer: severity=low (remaining, not already P1)
- Architecture reviewer: severity=low
- Test reviewer: severity=medium OR severity=low
- Code quality reviewer: severity=medium OR severity=low

**Nitpick — Optional** (subjective preferences, equally-valid alternatives):
- Any issue with severity=nitpick from any reviewer

**Overall verdict**: `REQUEST_CHANGES` if any P0 or P1 issue exists, otherwise `APPROVE`.

### Step 7: Render CLI Report

Output the final report in this exact format:

```
========================================
PR Review: #<number> - <title>
========================================
Base: <base_ref> (<base_sha short>)  Head: <head_ref> (<head_sha short>)
Files: <changed_files>  (+<additions> / -<deletions>)

Reviewers: bug=<verdict>  arch=<verdict>  quality=<verdict>  tests=<verdict>
Overall: <APPROVE|REQUEST_CHANGES>  (<count> P0, <count> P1, <count> P2, <count> nitpick)

── P0 — Must Fix ──────────────────────

[P0-1] <Issue title>
  Source:  <Reviewer Name> (<severity> / <category>)
  File:    <file_path>:<line_range>
  Problem: <problem description>
  Fix:     <suggestion>

── P1 — Should Fix ─────────────────────

[P1-1] <Issue title>
  Source:  <Reviewer Name> (<severity> / <category>)
  File:    <file_path>:<line_range>
  Problem: <problem description>
  Fix:     <suggestion>

── P2 — Consider Fixing ────────────────

[P2-1] <Issue title>
  Source:  <Reviewer Name> (<severity> / <category>)
  File:    <file_path>:<line_range>
  Problem: <problem description>
  Fix:     <suggestion>

── Nitpick ─────────────────────────────

[N-1] <Issue title>
  Source:  <Reviewer Name> (<category>)
  File:    <file_path>:<line_range>
  Comment: <description>

========================================
```

Omit any tier section that has zero issues (e.g., if no P0 issues, skip that entire section).

If the overall verdict is APPROVE and there are zero issues at any level, output:
```
Reviewers: bug=APPROVE  arch=APPROVE  quality=APPROVE  tests=APPROVE
Overall: APPROVE  (0 P0, 0 P1, 0 P2, 0 nitpick)

No issues found. All reviewers approved.
```

### Step 8: Generate HTML Report

Spawn the `html-report-writer` agent via the Task tool. Pass it:

- PR metadata: number, title, base_ref, head_ref, base_sha (first 8 chars), head_sha (first 8 chars), additions, deletions, changed_files
- Per-reviewer verdicts (bug, arch, quality, tests)
- Overall verdict
- All classified issues grouped by tier (P0/P1/P2/nitpick), each with: id, title, source reviewer, severity, category, file, line range, problem, suggestion
- Worktree path and merge_base (so the agent can fetch per-file diffs)
- Output path: `/tmp/pr-review-<PR_NUMBER>.html`

After the agent returns, print:
```
HTML report: /tmp/pr-review-<PR_NUMBER>.html
```

### Step 9: Cleanup Worktree

After the report is rendered, clean up (run each as a separate command, ignore errors):
```bash
git -C <repo_root> worktree remove --force <worktree_path>
```
```bash
git -C <repo_root> worktree prune
```

If cleanup fails, warn: "Warning: Could not remove worktree at <path>. Run `git worktree prune` manually."

## Error Handling

| Condition | Action |
|---|---|
| No PR number provided | Stop with usage message |
| `gh pr view` fails | Stop: "PR not found or auth error" |
| Worktree path already exists | Remove and recreate |
| Agent output unparseable | Warn, skip that reviewer, continue with others |
| All 4 agents fail | Stop and report error |
| Worktree cleanup fails | Warn, suggest `git worktree prune` |

## Important Notes

- This command produces a CLI summary and an HTML report. It does NOT post comments to GitHub.
- Requires `gh` CLI to be installed and authenticated.
- The worktree is created in `/tmp` to avoid disrupting the current working directory.
- Using `--detach` avoids branch conflicts if the PR branch is already checked out locally.
