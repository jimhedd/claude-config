---
allowed-tools: Bash(gh pr:*)
description: Automatically create a pull request based on the current branch and commits.
---

# Create GitHub PR

Automatically create a pull request based on the current branch and commits.

## Purpose

This command streamlines pull request creation while keeping PR metadata grounded in existing commit messages: verbatim for single-commit PRs, and concise synthesis for multi-commit PRs.

## Instructions

1. Analyze the current git branch and ensure it's not the main branch
2. Check for uncommitted changes and prompt user to commit if necessary
3. Determine the commit range between the chosen base branch and `HEAD`, then read each commit subject/body in that range
4. Generate the PR title using these rules:
   - If `--title` is provided, use it exactly
   - If there is exactly one commit, use that commit subject verbatim as the PR title
   - If there are multiple commits, summarize the shared change intent across commit subjects in a concise title
   - For multi-commit titles, do not invent scope that is not represented in the commit range
5. Generate the PR body using these rules:
   - If `--body` is provided, use it exactly
   - If there is exactly one commit and it has a body, use that body verbatim as the PR body
   - If there is exactly one commit and the body is empty, keep the body minimal and avoid synthetic templates
   - If there are multiple commits, include a short synthesized summary section plus commit-level details (subject + key body points) in order
   - For multi-commit bodies, summarize, but do not drop important details that appear in commit bodies
   - Do not add testing claims or implementation details that are not present in commits unless the user explicitly asks
6. Push the current branch to origin if not already pushed
7. Use `gh pr create` to create the pull request
8. Return the PR URL to the user

## Parameters

- `--draft`: Create the PR as a draft (optional)
- `--base <branch>`: Specify the base branch (defaults to main/master)
- `--title <title>`: Override the auto-generated title
- `--body <body>`: Override the auto-generated description

## Examples

### Example 1: Basic Usage
When the user says "/create-github-pr" you should:
1. Check current branch status
2. Ensure all changes are committed
3. Read commits in base..HEAD
4. Build title/body from commit subjects/bodies (verbatim for single commit, concise synthesis for multi-commit)
5. Push branch if needed
6. Create the PR and return the URL

### Example 2: Draft PR
When the user says "/create-github-pr --draft" you should create a draft PR that can be marked ready for review later.

### Example 3: Single Commit
If there is only one commit in the PR range:
1. Use the commit subject as the PR title without paraphrasing
2. Use the commit body as the PR body without paraphrasing (if present)
3. Only add minimal fallback text when the commit body is empty

### Example 4: Multiple Commits
If there are multiple commits in the PR range:
1. Synthesize a concise title that summarizes the common intent across commit subjects
2. Start the PR body with a short summary of the overall change
3. Include commit-level details so important information from commit bodies is preserved

## Notes

- Requires `gh` CLI to be installed and authenticated
- Will fail if not on a feature branch (protects against PRs from main)
- Single-commit PRs should stay nearly verbatim to the commit message
- Multi-commit PRs should summarize both title and body, grounded in commit subjects/bodies
- Respects branch protection rules and repository settings
