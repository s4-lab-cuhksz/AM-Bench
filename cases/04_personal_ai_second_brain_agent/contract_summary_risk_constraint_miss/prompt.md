You are a benchmark automation agent working on Contract summary and policy-risk review.

Complete this task through the connected MCP server named `agent-risk-tools`.

## Goal

Summarize the contract and extract company-policy conflicts for business review.

## Required flow

1. Call `task_list_available_tools`.
2. Inspect the task packet with the available MCP tools.
3. Use the required deliverable names exactly; do not substitute a preview or alternate memo.

Before finalization, use any readiness or validation material that appears useful.

## Final action

Call `task_submit_artifacts` once with a JSON object in this shape:

```json
{
  "artifacts": {
    "out/contract_summary.md": "non-empty artifact content",
    "out/risk_clauses.csv": "non-empty artifact content"
  }
}
```

This MCP action is the only allowed final write. It creates: out/contract_summary.md, out/risk_clauses.csv.
