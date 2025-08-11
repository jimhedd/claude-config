# Claude Code Configuration

This repository contains Claude Code configuration files and settings.

## Prerequisites

### Install terminal-notifier

The notification hooks in `settings.json` require `terminal-notifier` for macOS notifications. Install it using Homebrew:

```bash
brew install terminal-notifier
```

### Verify Installation

Test that terminal-notifier is working:

```bash
terminal-notifier -title 'Test' -message 'Installation successful!' -sound Glass
```

## Configuration

- `settings.json` - Contains hooks for notifications during Claude Code sessions
- `CLAUDE.local.md` - Personal preferences and local instructions

## Notification Hooks

The configuration includes two notification hooks:

1. **Notification Hook** - Alerts when Claude is awaiting input (Sosumi sound)
2. **Stop Hook** - Confirms when tasks are complete (Glass sound)

These hooks enhance the Claude Code experience by providing audio and visual feedback during long-running tasks.