---
allowed-tools: Bash(gh pr:*)
description: Checkout and review a GitHub pull request by PR number.
requires-argument: true
argument-description: PR number to review
---

# Review GitHub PR

Checkout a pull request and perform a code review.

## Purpose

This command performs a code review on a GitHub pull request by PR number.

## Instructions

1. Verify that a PR number argument was provided
2. Use `gh pr view <PR_NUMBER>` to understand the PR context and description
3. Use `gh pr checkout <PR_NUMBER>` to checkout the PR branch
4. Use `gh pr diff <PR_NUMBER>` to review the code changes
5. Perform thorough code review following best practices below
6. Use `gh pr review <PR_NUMBER>` to submit review feedback if needed
7. If all looks good, approve with `gh pr review <PR_NUMBER> --approve`

## Code Review Best Practices

### Before Reviewing
- **Understand the context**: Read the PR description, linked issues, and commit messages
- **Check CI status**: Use `gh pr checks <PR_NUMBER>` to verify build status
- **Review scope**: Ensure the changes align with the stated purpose

### During Review
- **Security first**: Look for potential security vulnerabilities, exposed secrets, or unsafe operations
- **Code quality**: Check for readability, maintainability, and adherence to coding standards
- **Performance**: Identify potential performance bottlenecks or inefficiencies
- **Testing**: Verify adequate test coverage and quality of tests
- **Documentation**: Ensure code is properly documented and comments explain "why" not "what"
- **Error handling**: Check for proper error handling and edge case coverage

### Review Focus Areas
- **Logic errors**: Verify business logic is correct
- **Resource management**: Check for memory leaks, file handle closures, etc.
- **API contracts**: Ensure backward compatibility and proper versioning
- **Dependencies**: Review new dependencies for necessity and security
- **Configuration**: Check for hardcoded values that should be configurable

### Providing Feedback
- **Comments are primary**: Use inline comments on specific lines for all feedback
- **Review summary sparingly**: Only add review-level comments for urgent blocking issues
- **Be specific**: Point to exact lines and suggest concrete improvements
- **Keep it actionable**: Focus on what needs to change, not just what's wrong

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