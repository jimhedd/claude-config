---
allowed-tools: Bash(gh pr:*), Bash(git:*), Bash(* /tmp/*), Bash(python3:*), Read, Glob, Grep, Task, Write
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

Get the commit log:
```bash
git -C <worktree_path> log --oneline <merge_base>..HEAD
```

Resolve project guidelines via the CLAUDE.md resolution script:
```bash
python3 ~/.claude/scripts/resolve-claude-md.py \
  --git-dir <worktree_path> \
  --merge-base <merge_base> \
  --ref-range <merge_base>..HEAD \
  --depth 5 \
  --check-head
```

Parse the JSON output and store:
- `ancestor_dirs_list` — for reference in reviewer prompts
- `expected_guidelines` — for Step 5 cross-checking
- `expected_directives` — for Step 5 cross-checking (filter to depth<=2 for orchestrator expectations)
- `pr_added_guidelines` — for HTML report JSON
- `warnings` — for HTML report JSON (included in guidelines section)
- `resolved_content` — to include in reviewer prompts
- `guidelines_loaded_section` — to include in reviewer prompts

If the script exits non-zero, stop and report the error.
If `warnings` is non-empty, they will be included in the HTML report via the JSON `guidelines.warnings` array. Do not treat them as fatal.

### Step 4: Spawn 4 Reviewer Agents in Parallel

Launch all 4 agents using the Task tool **in a single message** so they run concurrently. Do NOT use `run_in_background` — foreground parallel calls already run concurrently and return all results in one turn. Each agent receives the same context block plus its specific focus instructions.

**Context block for all agents** (adapt the git commands to use `-C <worktree_path>`):

```
You are reviewing PR #<pr_number>: <pr_title>

PR Description:
<pr_body>

Commits:
<commit_log>

Worktree path: <worktree_path>
Diff range: <merge_base>..HEAD

IMPORTANT instructions for this review:
- All git commands must use: git -C <worktree_path> ...
- All file reads must use absolute paths under <worktree_path>/
- Your review scope is the diff range: <merge_base>..HEAD
- Use `git -C <worktree_path> diff --no-renames <merge_base>..HEAD` to see the full diff
- Use `git -C <worktree_path> diff --no-renames <merge_base>..HEAD -- <file>` for a single file's diff
- Use `git -C <worktree_path> diff --no-renames --name-only <merge_base>..HEAD` for changed file list
- Use `git -C <worktree_path> log <merge_base>..HEAD` for commit history
- Read files under <worktree_path>/ to examine surrounding context beyond the diff

Project Guidelines (pre-resolved from CLAUDE.md files at merge-base <merge_base>):
---BEGIN GUIDELINES---
<resolved_content>
---END GUIDELINES---

For your #### Guidelines Loaded output section, use this pre-computed block:
---BEGIN GUIDELINES_LOADED---
<guidelines_loaded_section>
---END GUIDELINES_LOADED---

Perform your review now.
```

The 4 agents to spawn (all via `Task` tool with appropriate `subagent_type`):

| Task | subagent_type | Focus |
|---|---|---|
| Bug Review | `bug-reviewer` | Logic errors, null safety, race conditions, resource leaks, security, data integrity |
| Architecture Review | `architecture-reviewer` | Design patterns, coupling, API design, caller-impact contracts, separation of concerns, resource representation, concurrency/caching mechanisms |
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

For each reviewer, extract and cross-check the `#### Guidelines Loaded` section against
the script's JSON output (`expected_guidelines` and `expected_directives` filtered to depth<=2):
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

For each entry in `expected_directives` (depth<=2 from script output), check if the reviewer
reported a matching `@<directive>` sub-item under the corresponding parent path:
- Directive not reported at all: log warning
  "Warning: <reviewer-name> did not report expected @ directive: @<directive> in <parent-path>"
- Directive reported with any status (`resolved`, `truncated`, `not-found`,
  `cycle-skipped`): no warning (reviewer acknowledged the directive)
- Directive reported with status `budget-dropped`: log informational
  "Info: <reviewer-name> budget-dropped @ directive: @<directive> in <parent-path>"
- Reviewer reports @ directives not in `expected_directives`: no warning
These are warnings only — do not trigger parse failure, retry, or verdict override.

**Dimensions breadth check** (all reviewers):
- Extract `#### Dimensions Evaluated` from each reviewer.
- Count: `{evaluated}` = OK + Issue count, `{total}` = expected dimensions for that reviewer.
- Expected counts: bug=15, architecture=11, test=10, code-quality=8.
- If `{evaluated}/{total}` < 0.50: log warning.
- If section missing: log warning.
- Warnings are informational only — no retry, no verdict override.
- Emit breadth warnings in the JSON `guidelines.warnings[]` array (same array used for existing guidelines warnings). The CLI summary is intentionally minimal (2-line verdict) and should not include breadth warnings — they surface only in the HTML report via the JSON.

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

### Step 7: Render CLI Verdict Summary

Output a minimal 2-line verdict summary:

```
Reviewers: bug=<verdict>  arch=<verdict>  quality=<verdict>  tests=<verdict>
Overall: <APPROVE|REQUEST_CHANGES>  (<count> P0, <count> P1, <count> P2, <count> nitpick)
```

All issue details are in the HTML report (Step 8). Do not render issue lists, guidelines context, or any other sections here.

### Step 8: Generate HTML Report

**8a.** Write JSON to `/tmp/pr-review-<PR_NUMBER>.json` using the Write tool. Schema:

```json
{
  "pr": {
    "number": 42,
    "title": "Fix auth bug",
    "base_ref": "main",
    "head_ref": "feature/auth-fix",
    "base_sha": "abc12345",
    "head_sha": "def67890",
    "additions": 150,
    "deletions": 30,
    "changed_files": 5
  },
  "verdicts": {
    "bug": "APPROVE",
    "arch": "APPROVE",
    "quality": "REQUEST_CHANGES",
    "tests": "REQUEST_CHANGES",
    "overall": "REQUEST_CHANGES"
  },
  "issues": [
    {
      "id": "P0-1",
      "tier": "p0",
      "title": "Null pointer in handler",
      "reviewer": "bug",
      "severity": "high",
      "category": "null-safety",
      "file": "src/auth/handler.kt",
      "line_range": "42-48",
      "problem": "The `authToken` can...",
      "suggestion": "Add a null check..."
    }
  ],
  "guidelines": {
    "expected_files": ["CLAUDE.md"],
    "expected_directives": [
      {
        "parent_path": "CLAUDE.md",
        "directive_text": "@AGENTS.md",
        "resolved_path": "AGENTS.md",
        "exists_at_merge_base": true
      }
    ],
    "pr_added_files": [],
    "reviewers": {
      "bug":     { "files_count": 1, "directives_count": 0, "matched": true },
      "arch":    { "files_count": 1, "directives_count": 0, "matched": true },
      "quality": { "files_count": 1, "directives_count": 0, "matched": true },
      "tests":   { "files_count": 1, "directives_count": 0, "matched": true }
    },
    "warnings": []
  }
}
```

Field rules:
- `pr.base_sha` and `pr.head_sha`: first 8 characters
- `issues[].id`: unique across all issues (e.g., `P0-1`, `P1-1`, `P2-1`, `N-1`)
- `issues[].tier`: one of `p0`, `p1`, `p2`, `nitpick`
- `issues[].line_range`: `"N-N"` format (single line `12` becomes `"12-12"`), or `null`
- `verdicts.*`: `"APPROVE"` or `"REQUEST_CHANGES"`

**8b.** Render body + pairs:
```bash
python3 ~/.claude/scripts/pr-review/render-report.py /tmp/pr-review-<PR_NUMBER>.json /tmp/pr-review-<PR_NUMBER>-body.html /tmp/pr-review-<PR_NUMBER>-pairs.tsv > /dev/null
```

**8c.** Assemble (unchanged):
```bash
python3 ~/.claude/scripts/pr-review/assemble-report.py /tmp/pr-review-<PR_NUMBER>-body.html /tmp/pr-review-<PR_NUMBER>.html --title "PR Review: #<PR_NUMBER> - <pr_title>"
```

**8d.** Inject diffs (unchanged):
```bash
python3 ~/.claude/scripts/pr-review/inject-diff.py /tmp/pr-review-<PR_NUMBER>.html <worktree_path> <merge_base> --pairs-file /tmp/pr-review-<PR_NUMBER>-pairs.tsv > /dev/null
```

After the pipeline completes, print:
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
