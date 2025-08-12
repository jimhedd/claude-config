---
allowed-tools: Bash(gh pr:*), Bash(gh api:*), Bash(jq:*)
description: Checkout and review a GitHub pull request by PR number.
requires-argument: true
argument-description: PR number to review
---

# Review GitHub PR

Perform a comprehensive code review on a GitHub pull request by PR number.

## Purpose

This command conducts a thorough code review following professional standards with specific, actionable feedback tied to exact code locations.

## Instructions

1. **Argument Validation**: Verify PR number argument was provided
2. **Context Gathering**: Use `gh pr view <PR_NUMBER>` to understand PR context, description, and purpose
3. **Branch Checkout**: Use `gh pr checkout <PR_NUMBER>` to checkout the PR branch locally
4. **File Analysis**: Use `gh pr diff <PR_NUMBER>` and examine actual changed files using Read tool
5. **Comprehensive Review**: Follow detailed review methodology below
6. **Submit Line Comments**: Use GitHub API to create inline comments on specific code lines

## Review Process - Line Comments Only

**CRITICAL**: Use GitHub API via `gh api` for ALL inline feedback.

### Review Analysis Steps
1. **Read all changed files completely** using Read tool for full context
2. **Identify specific issues** on exact lines that need feedback
3. **Submit inline comments** using GitHub API with proper JSON structure
4. **Final action only** - use `gh pr review --approve` or `gh pr review --request-changes`

### What to Review (Via Inline Comments Only)
- **Type inconsistencies**: Point to exact lines where types don't match
- **Logic errors**: Comment on specific lines with incorrect business logic  
- **Security issues**: Flag lines with potential vulnerabilities
- **Performance problems**: Identify inefficient code on specific lines
- **Missing error handling**: Comment where error handling is absent
- **Code style violations**: Point to lines not following conventions
- **Documentation gaps**: Comment where code needs explanation

## Inline Comment Standards

### Required Comment Format
- **Specific issue**: Clearly state what's wrong
- **Concrete suggestion**: Provide exact replacement code or solution
- **Reasoning**: Explain why the change improves the code

❌ **Avoid**: Vague comments like "Consider improving error handling" or "Type inconsistency found"

## GitHub CLI Review Commands

**GitHub CLI Limitation**: `gh pr comment` and `gh pr review` don't support true inline comments on specific lines.

**Creating Multiple Inline Comments with GitHub API**

```bash
# Define multi-line comment messages as variables:
comment1="Missing null check will cause NullPointerException

Current: user.email.length > 0
Suggested: user.email?.let { it.length > 0 } ?: false

This prevents crashes when user.email is null."

comment2="Logic error in validation

Current: if (result.isEmpty())
Previous: if (result.size != 1)

This changes behavior for multiple matches - document if intentional."

# Submit all inline comments using jq with proper variable substitution:
gh api repos/OWNER/REPO/pulls/PR_NUMBER/reviews \
  --method POST \
  --input <(jq -n \
    --arg c1 "$comment1" \
    --arg c2 "$comment2" \
    '{
      event: "REQUEST_CHANGES",
      body: "Code review feedback",
      comments: [
        {
          path: "src/main/kotlin/File1.kt",
          line: 15,
          body: $c1
        },
        {
          path: "src/main/kotlin/File2.kt",
          line: 42,
          body: $c2
        }
      ]
    }')

# If no issues found, approve instead:
# gh pr review PR_NUMBER --approve
```

**CRITICAL REQUIREMENTS:**
- `"path"`: Must be relative path from repo root (exactly as shown in `gh pr diff`)
- `"line"`: Must be line number from the NEW version of the file
- `"body"`: Your detailed feedback with quotes and suggestions (max 65,536 characters)
- `"event"`: Use `"COMMENT"` for feedback or `"REQUEST_CHANGES"` if blocking
- Always include `"body"` field at review level (even if just "Code review complete")
- **Character Limit**: Each comment body is limited to 65,536 characters by GitHub API

**API Format for Each Issue:**
```json
{
  "path": "relative/path/to/file.kt",
  "line": 35,
  "body": "Specific issue description with code quotes and solution"
}
```

## GitHub CLI Commands Reference

```bash
gh pr view <PR_NUMBER>           # View PR details and description
gh pr checkout <PR_NUMBER>       # Switch to PR branch locally
gh pr diff <PR_NUMBER>           # Show code changes
gh pr checks <PR_NUMBER>         # View CI/CD status
gh pr review <PR_NUMBER>         # Submit review (approve/request changes/comment)
gh pr review <PR_NUMBER> --approve   # Approve the pull request
gh pr status                     # Show PR status for current repo
```

## Notes

- Requires `gh` CLI to be installed and authenticated
- Use `gh auth login` if not already authenticated
- Reference: https://cli.github.com/manual/gh_pr