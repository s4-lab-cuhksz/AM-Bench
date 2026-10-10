# AMBench Leaderboard

[Back to README](README.md) · [简体中文](LEADERBOARD.zh-CN.md)

AMBench ranks each model and harness configuration by **Success**, the fraction
of runs that satisfy both Completion and Compliance:

```text
Success = Completion ∧ Compliance
```

## Results

| Rank | Configuration | Runs | Completion | Compliance | Success |
|---:|---|---:|---:|---:|---:|
| 1 | `nanobot_z-ai-glm-5.3` | 1,008 | 55.3% | 76.6% | **37.8%** |
| 2 | `codex_deepseek-v4-flash-0731` | 1,008 | 60.5% | 60.7% | **34.2%** |
| 3 | `openclaw_deepseek-v4-flash-0731` | 1,008 | 56.9% | 63.5% | **34.0%** |
| 4 | `nullclaw_deepseek-v4-pro-0813` | 1,008 | 45.0% | 79.4% | **33.3%** |
| 5 | `openclaw_openai-gpt-5.6-sol` | 1,008 | 53.3% | 75.6% | **32.9%** |
| 6 | `codex_openai-gpt-5.6-luna` | 1,008 | 52.7% | 76.5% | **32.0%** |
| 7 | `nullclaw_deepseek-v4-flash-0731` | 1,008 | 45.1% | 76.2% | **31.9%** |
| 8 | `nanobot_deepseek-v4-flash-0731` | 1,008 | 52.6% | 68.8% | **31.7%** |
| 9 | `codex_z-ai-glm-5.3` | 1,008 | 50.5% | 72.5% | **31.1%** |
| 10 | `codex_openai-gpt-5.6-sol` | 1,008 | 48.5% | 78.5% | **29.5%** |
| 11 | `codex_deepseek-v4-pro-0813` | 1,008 | 43.0% | 69.8% | **26.2%** |
| 12 | `nullclaw_z-ai-glm-5.3` | 1,008 | 37.2% | 80.7% | **26.1%** |
| 13 | `nullclaw_openai-gpt-5.6-sol` | 1,008 | 44.7% | 79.5% | **26.0%** |
| 14 | `openclaw_z-ai-glm-5.3` | 1,008 | 38.8% | 80.9% | **25.8%** |
| 15 | `openclaw_openai-gpt-5.6-luna` | 1,008 | 45.3% | 72.7% | **25.2%** |
| 16 | `nanobot_deepseek-v4-pro-0813` | 1,008 | 40.5% | 72.2% | **24.6%** |
| 17 | `nullclaw_openai-gpt-5.6-luna` | 1,008 | 40.4% | 78.3% | **22.1%** |
| 18 | `openclaw_deepseek-v4-pro-0813` | 1,008 | 31.0% | 76.5% | **20.1%** |
| 19 | `nanobot_openai-gpt-5.6-sol` | 1,008 | 36.0% | 75.6% | **17.9%** |
| 20 | `nanobot_openai-gpt-5.6-luna` | 1,008 | 31.3% | 72.5% | **14.5%** |

## Evaluation Scope

Each row contains 1,008 runs:

```text
48 tasks × 7 information conditions × 3 repetitions
```

Completion indicates reference-task attainment. Compliance indicates that the
run respected the task-specific safety and permission boundaries. Success requires
both outcomes in the same run. Rates are macro results over all included runs and
are rounded to one decimal place.

The leaderboard excludes `email_reply_meeting`, which is not part of the 48-task
paper benchmark. Results are computed from the retained LLM-judge result snapshot.
Future submissions should report the AMBench source revision, model identifier,
harness version, provider route, and all 1,008 run-level records.
