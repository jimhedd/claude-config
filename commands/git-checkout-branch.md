---
allowed-tools: Bash(git fetch:*), Bash(git checkout:*), Bash(git pull:*)
description: Checkout a git branch by name.
---

# Checkout Branch

Checkout a git branch by name.

## Purpose

Switch to a branch chosen by the user.

## Instructions

1. Read the target branch name from `$ARGUMENTS`.
2. If no branch name is provided, ask the user for the branch name and stop.
3. Run `git fetch origin <branch-name> && git checkout <branch-name> && git pull --ff-only origin <branch-name>` using the provided branch name.
4. Report success or the git error output back to the user.

## Parameters

- `<branch-name>`: The branch to check out (required).

## Examples

### Example 1: Basic Usage

When the user says "/git-checkout-branch feature/my-change" you should run `git fetch origin feature/my-change && git checkout feature/my-change && git pull --ff-only origin feature/my-change`.

### Example 2: Release Branch

When the user says "/git-checkout-branch release/2026-02-07" you should run `git fetch origin release/2026-02-07 && git checkout release/2026-02-07 && git pull --ff-only origin release/2026-02-07`.

## Notes

- The branch name must be provided by the user.
- `--ff-only` ensures local updates to the remote branch without creating merge commits.
