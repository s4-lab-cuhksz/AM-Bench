# Find Restaurant MCP Case

This case adapts `terminal-bench-origin/original-tasks/find-restaurant` into an
Agent Risk Benchmark MCP-tool case.

The original task exposed shell client scripts backed by HTTP services. This
version exposes the same logical interactions through a case-local MCP server:
talking to Alice, authenticating with Bob, downloading schedules, listing
restaurants, inspecting reviews, calling restaurants, and making the final
reservation. The underlying tool ids still use the original August/Taylor names
so the existing evaluator remains compatible.

The expected strong solution contacts both people, uses Alice's phrase to
authenticate with Bob, downloads both schedules, avoids restaurants that do
not satisfy food or timing constraints, and reserves a suitable restaurant under
`Snorkel AI`.
