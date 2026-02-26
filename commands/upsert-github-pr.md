---
allowed-tools: Bash(git push:*), Bash(gh pr:*)
description: Push the current branch and create or update a GitHub PR.
---

# Upsert GitHub PR

Push the current branch and create or update a GitHub PR.

## Purpose

This command pushes your current branch to `origin` and either updates an existing pull request or creates a new one if none exists for the branch.

## Instructions

1. Check the current branch name. If it is `main` or `master`, stop with an error telling the user to switch to a feature branch first.
2. Force push the current branch to origin with `git push --force-with-lease`. If the push fails, report the error and stop.
3. Check if a PR already exists for the current branch using `gh pr view --json url`.
4. **If a PR exists**: report success and include the PR URL from the output.
5. **If no PR exists**: create a new PR:
   - Get the latest commit subject: `git log -1 --format=%s`
   - Get the latest commit body: `git log -1 --format=%b` (may be empty, that is fine)
   - Run `gh pr create --title <title> --body <body>` using the values above.
   - If creation fails, report the error and stop.
   - Report success with the new PR URL.

## Notes

- Uses `--force-with-lease` instead of `--force` to reduce the risk of overwriting remote updates made by others.
- Requires the current branch to have an upstream branch on `origin`.
- Requires the `gh` CLI to be installed and authenticated.
- Will create a new PR if one does not exist for the current branch.
- Protects against accidentally running on main/master.
