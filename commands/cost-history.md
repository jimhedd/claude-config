---
allowed-tools: Bash(npx ccusage@latest)
description: Gets cost history
---

# Cost History

Runs npx ccusage@latest and summarizes for the user.

## Purpose

This command fetches the latest Claude usage costs using the ccusage tool and provides a summary of usage patterns and costs to help users understand their Claude consumption.

## Instructions

1. Run `npx ccusage@latest` to fetch the latest usage data
2. Parse the output to extract key metrics
3. Provide a clear summary including:
   - Total costs for recent periods
   - Usage trends
   - Any notable patterns or spikes
4. Present the information in an easy-to-understand format

## Parameters

No parameters required - the command runs with default settings.

## Examples

### Example 1: Basic Usage
When the user says "/cost-history" you should:

1. Execute `npx ccusage@latest`
2. Analyze the returned data
3. Summarize key findings like total spend, daily/weekly trends, and any usage patterns
4. Present the summary in a readable format with highlights of important information

## Notes

- Usage data availability depends on the ccusage tool's capabilities
- Summary should focus on actionable insights rather than raw data dumps