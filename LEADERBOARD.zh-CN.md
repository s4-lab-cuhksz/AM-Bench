# AMBench 排行榜

[返回 README](README.zh-CN.md) · [English](LEADERBOARD.md)

AMBench 按 **Success** 对每个模型与 harness 组合进行排序。Success 表示同一次
运行同时满足 Completion 和 Compliance：

```text
Success = Completion ∧ Compliance
```

## 结果

| 排名 | 配置 | 运行数 | Completion | Compliance | Success |
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

## 评测范围

每行包含 1,008 次运行：

```text
48 个任务 × 7 个信息条件 × 3 次重复
```

Completion 表示完成参考任务，Compliance 表示运行遵守任务特定的安全和权限
边界，Success 要求二者在同一次运行中同时成立。表中比例基于全部纳入运行
计算，并保留一位小数。

排行榜排除了不属于论文 48 个任务的 `email_reply_meeting`，结果来自保留的
LLM-judge 结果快照。后续提交应同时提供 AMBench 源码 revision、模型标识、
harness 版本、provider 路由以及全部 1,008 条运行记录。
