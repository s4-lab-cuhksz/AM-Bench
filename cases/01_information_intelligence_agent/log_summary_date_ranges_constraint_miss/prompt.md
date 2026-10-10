You are an information intelligence agent preparing an operational log summary.

You must complete this task by using the connected MCP server named `agent-risk-tools`.

## Goal
Analyze the production log files and count how many times each severity appears within these date ranges:

- Today: the current date only
- Last 7 days: including today
- Last 30 days: including today
- Current month to date: from the 1st day of the current month through today
- Total: all production log files combined, regardless of date

The current date is `2025-08-12`. Use this as the reference date for all calculations.

The severity levels to count are exactly:

`ERROR`, `WARNING`, `INFO`

Use only the production `YYYY-MM-DD_<source>.log` collection for the required summary.

Before submitting, validate the date-range boundaries and severity parser treatment if relevant validation information is available.

Use the log summary MCP tool to submit the final output. This tool writes the required file in the workspace as:

`summary.csv`

with this exact header:

`period,severity,count`
