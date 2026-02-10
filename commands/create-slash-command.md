---
description: Generate a new command-name.md file from templates.
---

# Create New Slash Command

Generate a new command-name.md file from templates.

## Purpose

This command creates a new command documentation file by copying either the basic or advanced template and customizing it with the provided command name.

## Instructions

1. Take the command name as the first parameter
2. Ask the user if they would like to store the command in the project or as a personal one. If project, the final location will be .claude/commands/command-name.md. If personal, the final location will be ~/.claude/commands/command-name.md
3. Check for `--advanced` flag to determine which template to use
4. Copy the appropriate template to the commands directory
5. Rename it to the command name
6. Replace placeholder text with the actual command name

## Frontmatter

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

## Parameters

- `command-name`: The name of the command to create (required)
- `--advanced`: Use the advanced template instead of basic (optional)

## Examples

### Example 1: Basic Usage
When the user says "/create-slash-command command-name" you should:

1. Copy the ~/.claude/commands/basic-command-template.md to `command-name.md`
2. Modify the new file to replace parts that now refere to the `command-name`. See this file for how to fill in the basic template.
3. Finally show the user the location of the completed file and offer that they can distribute this command to all engineers by committing it to internal/pkg/ai/resources/claude/commands in the https://github.com/doordash/devbox-cli repo.
4. Offer to the user if they would like to do that now and if so, clone that repo, add the command, commit it, create a pr and then give the user the URL for that PR.

### Example 2: Advanced Usage
When the user says "/create-slash-command command-name --advanced" you should use the advanced template.

1. Copy the ~/.claude/commands/advanced-command-template.md to `command-name.md`
2. Modify the new file to replace parts that now refere to the `command-name`.
3. Finally show the user the location of the completed file and offer that they can distribute this command to all engineers by committing it to internal/pkg/ai/resources/claude/commands in the https://github.com/doordash/devbox-cli repo.
4. Offer to the user if they would like to do that now and if so, clone that repo, add the command, commit it, create a pr and then give the user the URL for that PR.

## Notes

- The command will overwrite existing files if confirmed
- Template placeholders "Command Name" will be replaced with the actual command name
- The generated file should be manually edited to add specific command details
