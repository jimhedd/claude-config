---
allowed-tools: Bash(example to be replaced)
description: Advanced command description
---
# Advanced Command

You are an AI assistant specialized in [domain]. When this command is invoked, follow these guidelines:

### Frontmatter

Command files support frontmatter, useful for specifying metadata about the command:

| Frontmatter                | Purpose                                                                                                                                                                               | Default                             |
| :------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :---------------------------------- |
| `allowed-tools`            | List of tools the command can use                                                                                                                                                     | Inherits from the conversation      |
| `argument-hint`            | The arguments expected for the slash command. Example: `argument-hint: add [tagId] \| remove [tagId] \| list`. This hint is shown to the user when auto-completing the slash command. | None                                |
| `description`              | Brief description of the command                                                                                                                                                      | Uses the first line from the prompt |
| `model`                    | Specific model string (see [Models overview](/en/docs/about-claude/models/overview))                                                                                                  | Inherits from the conversation      |
| `disable-model-invocation` | Whether to prevent `SlashCommand` tool from calling this command                                                                                                                      | false                               |

For example:

```markdown  theme={null}
---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*)
argument-hint: [message]
description: Create a git commit
model: claude-3-5-haiku-20241022
---

Create a git commit with message: $ARGUMENTS
```

Example using positional arguments:

```markdown  theme={null}
---
argument-hint: [pr-number] [priority] [assignee]
description: Review pull request
---

Review PR #$1 with priority $2 and assign to $3.
Focus on security, performance, and code style.
```

## Core Responsibilities

1. **Analysis Phase**
   - Examine the project structure
   - Identify relevant files
   - Understand the context

2. **Planning Phase**
   - Create a plan of action
   - Consider edge cases
   - Validate assumptions

3. **Execution Phase**
   - Implement the solution
   - Provide clear feedback
   - Handle errors gracefully

## Context Understanding

You have access to:

- File system operations
- Code analysis capabilities
- Pattern matching

### JavaScript/TypeScript Runtime

If the user mentions needing a JavaScript runtime for their command, note that devbox packages a bun executable at `~/.devbox/ai/claude/bun` which can be used with `Bash(~/.devbox/ai/claude/bun x --bun)`. Bun provides a fast, lightweight runtime for JavaScript/TypeScript code and package management.

## Decision Framework

When deciding how to proceed:

```flowchart
Start -> Analyze Request -> Is it valid?
  |                           |
  Yes                         No -> Request clarification
  |
  V
Plan approach -> Execute -> Verify -> Complete


## IMPORTANT (TO BE REMOVED IN FINAL FILE AFTER COPYING THIS TEMPLATE)

If this command is called directly with /advanced-command-template, instead prompt the user to run `/create-slash-command command-name --advanced` instead.
