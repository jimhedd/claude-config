---
allowed-tools: Bash(gh pr:*)
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
3. **Status Check**: Use `gh pr checks <PR_NUMBER>` to verify CI/CD status before reviewing
4. **Branch Checkout**: Use `gh pr checkout <PR_NUMBER>` to checkout the PR branch locally
5. **File Analysis**: Use `gh pr diff <PR_NUMBER>` and examine actual changed files using Read tool
6. **Comprehensive Review**: Follow detailed review methodology below
7. **Submit Line Comments**: Use `gh pr comment` for ALL feedback, then `gh pr review` only to approve/request changes

## Review Process - Line Comments Only

**CRITICAL**: Use `gh pr comment` for ALL feedback. Use `gh pr review` ONLY for final approve/request-changes without any body text.

### Review Analysis Steps
1. **Read all changed files completely** using Read tool for full context
2. **Identify specific issues** on exact lines that need feedback
3. **Submit each issue as line comment** using `gh pr comment` with file and line number
4. **Final action only** - use `gh pr review --approve` or `gh pr review --request-changes` with NO body text

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
- **File and line reference**: Always start with `filename:line`
- **Quote the problematic code**: Include the actual code being reviewed
- **Specific issue**: Clearly state what's wrong
- **Concrete suggestion**: Provide exact replacement code or solution
- **Reasoning**: Explain why the change improves the code

### Examples of Good Inline Comments
✅ **Good**: 
```
BuggyValidator.kt:35 - Missing null check will cause NullPointerException
Current: `user.email.length > 0`
Should be: `user.email?.let { it.length > 0 } ?: false`
This prevents crashes when user.email is null.
```

✅ **Good**: 
```
CodeException.kt:19 - Infinite recursion will cause StackOverflowError  
Current: `toString() { return toString() }`
Should be: `toString() { return "CodeException: $message" }`
The current implementation calls itself infinitely.
```

❌ **Avoid**: Vague comments like "Consider improving error handling" or "Type inconsistency found"

## GitHub CLI Review Commands

**GitHub CLI Limitation**: `gh pr comment` and `gh pr review` don't support true inline comments on specific lines.

**Solution: Use GitHub API via gh api**
```bash
# Create a review with inline comments using stdin:
gh api repos/:owner/:repo/pulls/<PR_NUMBER>/reviews \
  --method POST \
  --input <(echo '{
    "event": "COMMENT",
    "body": "Code review feedback",
    "comments": [
      {
        "path": "filename.kt",
        "line": 27,
        "body": "Logic change may affect error handling behavior\n\nCurrent: if (filteredResult.isEmpty())\nPrevious: if (filteredResult.size != 1)\n\nThis changes behavior when multiple valid matches are found."
      }
    ]
  }')

# For multiple inline comments in one review:
gh api repos/:owner/:repo/pulls/<PR_NUMBER>/reviews \
  --method POST \
  --input <(echo '{
    "event": "REQUEST_CHANGES", 
    "body": "Issues found requiring changes",
    "comments": [
      {
        "path": "file1.kt",
        "line": 27,
        "body": "First issue description with specific fix"
      },
      {
        "path": "file2.kt",
        "line": 35,
        "body": "Second issue description with solution"
      }
    ]
  }')

# Or approve if no issues:
gh pr review <PR_NUMBER> --approve
```

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