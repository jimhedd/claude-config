#!/bin/bash

# ntfy.sh push notification script for Claude Code hooks
# Sends mobile push notifications with a 30-second debounce cooldown
#
# Usage: ntfy-notify.sh 'Your message here'
#
# Setup:
#   1. Install ntfy app on your phone (iOS App Store / Android Play Store or F-Droid)
#   2. Subscribe to your topic in the app
#   3. Replace the NTFY_TOPIC below with your chosen topic name

NTFY_TOPIC="claude-jim-a8f3x"
LOCKFILE="/tmp/claude-ntfy-last"
COOLDOWN=30

MESSAGE="${1:-Claude Code notification}"

# Check debounce lockfile
if [ -f "$LOCKFILE" ]; then
    LAST=$(cat "$LOCKFILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    ELAPSED=$((NOW - LAST))
    if [ "$ELAPSED" -lt "$COOLDOWN" ]; then
        exit 0
    fi
fi

# Write current timestamp
date +%s > "$LOCKFILE"

# Send notification (silent, never block Claude)
curl --silent --show-error \
    -d "$MESSAGE" \
    "https://ntfy.sh/$NTFY_TOPIC" \
    >/dev/null 2>&1

exit 0
