---
allowed-tools: Bash(git:*)
description: Squash all commits on the current branch into one with a synthesized message.
requires-argument: false
argument-hint: "[base-branch]"
argument-description: "Optional base branch (defaults to auto-detected main/master)"
---

# Squash Commits

Squash all commits on the current branch into a single commit with a synthesized message that summarizes the work across all original commits.

## Bash Command Rules

**CRITICAL**: The `Bash(git:*)` permission only matches commands that START with `git`. You MUST:
- Run each git command as a standalone `git ...` call — never wrap in shell variable assignments like `VAR=$(git ...)` or chain with `&&`
- Capture outputs by reading the Bash tool result, then reference the value in subsequent commands

## Context

- Current branch: !`git branch --show-current`
- Recent commits (last 30): !`git log --oneline -30`

## Instructions

### Step 1: Preflight

1. Verify inside a git repo: `git rev-parse --is-inside-work-tree`
2. Check the current branch name: `git rev-parse --abbrev-ref HEAD`
   - If the result is `HEAD` (detached HEAD state), stop with error: "Detached HEAD — checkout a branch first."
3. If the branch name is `main` or `master`, stop with error: "Cannot squash main/master — switch to a feature branch first."
4. Check the working tree is clean: `git status --porcelain`
   - If output is non-empty, stop with error: "Dirty working tree — commit or stash changes first."

### Step 2: Resolve Base Branch

1. If `$ARGUMENTS` is non-empty, use it as the base branch name. Verify it exists: `git rev-parse --verify <base-branch>`. If it fails, stop with error: "Base branch '<base-branch>' not found."
2. Otherwise, auto-detect the base branch:
   - Check if `main` exists as a local or remote branch: `git rev-parse --verify main` or `git rev-parse --verify origin/main`
   - If not, check `master`: `git rev-parse --verify master` or `git rev-parse --verify origin/master`
   - If neither exists, stop with error: "Could not detect base branch. Specify one explicitly: /squash-commits <base-branch>"
3. Compute the merge base: `git merge-base <base-branch> HEAD`
4. If merge-base fails, stop with error: "Could not compute merge-base between <base-branch> and HEAD."

### Step 3: Gather Commits

1. Count commits since merge-base: `git rev-list --count <merge-base>..HEAD`
2. If count is 0 or 1, report "Nothing to squash (1 or fewer commits since <base-branch>)." and stop.
3. Check for merge commits in the range: `git log --merges --oneline <merge-base>..HEAD`
4. If merge commits exist, stop and warn: "Merge commits detected in range — squashing will flatten them. Resolve merge commits manually first (e.g., rebase), then rerun."
5. Collect full commit log with messages: `git log --format="--- Commit %h ---%n%B" <merge-base>..HEAD`
6. Collect diff stat: `git diff --stat <merge-base>..HEAD`

### Step 4: Synthesize Commit Message

Analyze all collected commit messages and the diff stat to produce a single high-quality commit message:

- **Subject line**: A conventional-commit subject (max ~72 chars). Infer the type (`feat`, `fix`, `refactor`, `chore`, etc.) from the changes. Summarize the overall intent of the work, not individual commits.
- **Body**: Minimum 3 non-empty lines. Synthesize the what/why/how across all commits — do not just list the original messages. Capture the narrative arc: what problem was being solved, what approach was taken, what the end result is. The message should read as if the work was done in a single coherent pass.
- **Forbidden trailers**: Never include `Co-Authored-By:` or `Generated with Claude Code` in the commit message.

### Step 5: Squash

1. Reset to merge-base while preserving all changes staged: `git reset --soft <merge-base>`
2. Create the squashed commit by passing the synthesized message directly as a string argument:
   `git commit -m "<subject line><newline><newline><body>"`
   The message must be a single string with embedded newlines — no shell subshells (`$(...)`) or heredocs, since those violate the Bash Command Rules above.
3. If the commit fails: inform the user that all changes are staged but the original commits are gone. Suggest running `git commit` to retry with a manual message, or `git reflog` to recover the pre-squash HEAD.

### Step 6: Report

1. Show the final commit: `git log -1 --stat`
2. Report: "Squashed N commits into 1."

## Parameters

- `[base-branch]`: Optional base branch name. Defaults to auto-detected `main` or `master`.

## Examples

### Example 1: Basic Usage

`/squash-commits` — squashes all commits on the current branch since divergence from main/master into a single commit with a synthesized message.

### Example 2: Explicit Base Branch

`/squash-commits develop` — squashes all commits since divergence from `develop`.

## Error Handling

| Case | Action |
|---|---|
| Not in git repo | Stop with error |
| Detached HEAD | Stop: "checkout a branch first" |
| On main/master | Stop: "cannot squash main/master" |
| Dirty working tree | Stop: "commit or stash changes first" |
| Base branch not found | Stop: suggest specifying explicitly |
| merge-base resolution fails | Stop with error |
| 1 or fewer commits since base | Stop: "nothing to squash" |
| Merge commits in range | Stop with warning, suggest manual resolution |
| `git commit` fails after reset | Warn: changes staged but originals gone, suggest `git commit` or `git reflog` |

## Notes

- Uses `git reset --soft` to preserve all changes while removing commit boundaries.
- The synthesized message should read as if the work was done in a single coherent pass.
- Recovery from partial failure: `git reflog` shows the pre-squash HEAD, which can be restored with `git reset --hard <reflog-hash>`.
