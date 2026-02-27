---
allowed-tools: Bash(git:*)
description: "Reset worktree to latest default branch and create a new feature branch."
requires-argument: true
argument-description: "Name of the new feature branch to create"
argument-hint: "<branch-name>"
---

# Worktree Reset

Reset a persistent worktree to the latest default branch and create a new feature branch.

## Bash Command Rules

**CRITICAL**: The `Bash(git:*)` permission only matches commands that START with `git`. You MUST:
- Run each git command as a standalone `git ...` call — never wrap in shell variable assignments like `VAR=$(git ...)` or chain with `&&`
- Capture outputs by reading the Bash tool result, then reference the value in subsequent commands

## Context

- Current branch: !`git rev-parse --abbrev-ref HEAD`
- Working tree status: !`git status --short`

## Purpose

Recycle a long-lived worktree between features. Fetches the latest default branch (main/master) from origin and creates a new feature branch from it — without needing to checkout master (which would fail in a worktree where master is checked out by the main repo).

## Instructions

### Step 1: Validate environment and argument

1. Verify inside a git repo: `git rev-parse --is-inside-work-tree`. If it fails, stop with error: "Not inside a git repository."
2. Check current branch: `git rev-parse --abbrev-ref HEAD`. If the result is `HEAD` (detached HEAD state), stop with error: "Detached HEAD — checkout a branch first."
3. Read the branch name from `$ARGUMENTS`.
4. Validate the branch name: `git check-ref-format refs/heads/<branch-name>`. If invalid, stop with error: "Invalid branch name '<branch-name>'." (This rejects shorthand refs like `@{-1}`, preventing shorthand expansion in subsequent git commands. Names starting with `-` are separately rejected by `git checkout -b` in Step 5.)
5. Check if a local branch already exists: `git rev-parse --verify --quiet refs/heads/<branch-name>`. If it succeeds (exit 0), stop with error: "Branch '<branch-name>' already exists. Choose a different name."

### Step 2: Detect default branch

1. Check if `main` exists on remote: `git rev-parse --verify --quiet origin/main`. If it succeeds, use `main`.
2. Otherwise check `master`: `git rev-parse --verify --quiet origin/master`. If it succeeds, use `master`.
3. If neither exists, stop with error: "Could not detect default branch (neither origin/main nor origin/master found)."

### Step 3: Check for dirty state

1. Check for uncommitted changes: `git status --porcelain`. If output is non-empty, print the dirty files and stop: "Uncommitted changes detected. Commit, stash, or discard them before resetting."
2. Check if current branch has an upstream: `git rev-parse --abbrev-ref @{u}`.
   - If upstream exists: check for unpushed commits with `git log @{u}..HEAD --oneline`. If non-empty, print the unpushed commits and stop: "Unpushed commits on current branch. Push or resolve before resetting."
   - If upstream does NOT exist (command fails): check for commits ahead of the default branch with `git log origin/<default-branch>..HEAD --oneline`. If non-empty, print them and stop: "Unpushed commits on current branch (no upstream set). Push or resolve before resetting."

### Step 4: Fetch latest

1. Fetch the default branch: `git fetch origin <default-branch>`
2. If fetch fails, stop with the git error output.

### Step 5: Create feature branch

1. Create the new branch from the fetched default branch: `git checkout -b <branch-name> origin/<default-branch>`
2. If this fails, stop with the git error output.

### Step 6: Confirm

Print: "Worktree reset. Created branch `<branch-name>` from latest `origin/<default-branch>`."

## Parameters

- `<branch-name>`: Name of the new feature branch to create (required).

## Examples

### Example 1: Basic usage

`/worktree-reset CATCP-1234-add-auth` — fetches latest default branch, creates `CATCP-1234-add-auth` from it.

### Example 2: Feature branch

`/worktree-reset feature/dark-mode` — fetches latest default branch, creates `feature/dark-mode` from it.

## Error Handling

| Case | Action |
|---|---|
| Not in git repo | Stop with error |
| Detached HEAD | Stop: "checkout a branch first" |
| Invalid branch name | Stop: "Invalid branch name" |
| Branch already exists | Stop: "Branch already exists" |
| Uncommitted changes | Print dirty files, stop |
| Unpushed commits (with upstream) | Print unpushed commits, stop |
| Unpushed commits (no upstream) | Print commits ahead of default branch, stop |
| Default branch not found | Stop: "Could not detect default branch" |
| `git fetch` fails | Stop with git error |
| `git checkout -b` fails | Stop with git error |

## Notes

- This command is designed for persistent worktrees where the main repo has master/main checked out. It never checks out the default branch directly — it uses `origin/<default-branch>` as the base. It also works fine outside worktrees as a general "start fresh feature branch from latest remote" command.
- Each git command must be run as a standalone call per the Bash Command Rules above.
- In shallow clones, `git log origin/<default-branch>..HEAD` (Step 3) may produce unexpected results. The command does not explicitly detect shallow clones.
- If Step 5 (`git checkout -b`) fails after a successful fetch, remote tracking refs have been updated but the working tree is unchanged. No rollback is needed.
- If `git fetch` fails after default branch detection, the remote-tracking ref may be stale (e.g., remote renamed `master` to `main`). Suggest running `git remote update --prune origin` to clean stale refs.
