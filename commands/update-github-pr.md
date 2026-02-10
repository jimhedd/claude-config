---
allowed-tools: Bash(git push:*)
description: Force push the current branch to update an existing GitHub PR.
---

# Update GitHub PR

Force push the current branch to update an existing GitHub PR.

## Purpose

This command updates an existing pull request by force pushing your current local branch to `origin` using a safer force option.

## Instructions

1. Ensure the user is on the branch associated with the PR they want to update.
2. Run `git push --force-with-lease`.
3. Report success or the git error output back to the user.

## Parameters

- None

## Examples

### Example 1: Basic Usage

When the user says "/update-github-pr" you should run `git push --force-with-lease` from the current branch.

## Notes

- Uses `--force-with-lease` instead of `--force` to reduce the risk of overwriting remote updates made by others.
- Requires the current branch to have an upstream branch on `origin`.
