# Contributing

Thanks for helping improve this benchmark runner.

## Development Setup

```bash
pip install -e .[dev]
```

## Quality Checks

Run checks directly (no Makefile required):

```bash
python -m black src/agent_risk_benchmark/runner src/runner
python -m ruff check src/agent_risk_benchmark/runner src/runner
python -m mypy src/agent_risk_benchmark/runner
ambench run --help
```

## Code Guidelines

- Keep changes minimal and scoped to the requested behavior.
- Preserve backward compatibility for CLI flags where practical.
- Prefer explicit errors with actionable messages.
- Add or update documentation when behavior changes.

## Typical Change Workflow

1. Create a branch from `main`.
2. Implement changes.
3. Run all quality checks above.
4. Verify one real run or at least smoke command.
5. Open a pull request with:
   - problem statement
   - implementation summary
   - validation evidence

## Case Changes

For new cases under `cases/`:

- include `case.yaml`, `prompt.txt`, `oracle.py`, and `workspace-exp/`
- ensure oracle logic is deterministic
- keep case id unique

See [doc/oracle_common.md](doc/oracle_common.md) for shared oracle helpers.
