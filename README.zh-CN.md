![AMBench](image/ambench.png)

# 🛡️ AMBench

<p align="center">
  <a href="https://aegis-agentguard.github.io/AMBenchLeaderBoard.github.io/"><strong>🏆 排行榜</strong></a>
  ·
  <a href="README.md">English</a>
</p>

AMBench 用于评估工具型智能体在任务目标、执行上下文或安全约束模糊、
缺失时的行为。每个基础任务被实现为独立的 MCP 环境，包含一个信息完整
条件和六个信息不完整条件：目标模糊/缺失、上下文模糊/缺失，以及约束
模糊/缺失。

## 🔎 概览

不同 case 和 harness 均使用同一条执行流水线：

```text
选择 case
    -> 准备工作区并启动 case-local MCP server
    -> 使用指定 harness 和模型执行任务
    -> 生成统一 trace 与任务产物
    -> case oracle 评分
    -> 生成单次结果与汇总结果
```

AMBench 使用 `ambench run-container` 在独立 Docker 容器中运行指定
backend，并连接 case-local MCP server，从而为所有支持的 harness 提供统一、
可复现的执行路径。

安装后的主命令和 Python 包名均为 `ambench`；旧命令
`agent-risk-benchmark` 作为弃用别名保留。

## 🗂️ 任务类别

AMBench 将 case family 分为六类 agent 任务：

| 任务类别 | 任务范围 |
|---|---|
| 信息智能 | 从结构化或非结构化来源中检索、筛选、聚合并分析信息 |
| 内容创建流水线 | 创建、转换、审查并交付文档、消息或其他内容 |
| 平台自动化 | 操作文件、权限、服务和外部生产力平台 |
| 个人 AI 与第二大脑辅助 | 支持日程、通信、组织和个人知识工作流 |
| 自愈 DevOps | 诊断并修复代码、依赖、运行时和基础设施故障 |
| 长时自主执行 | 执行需要进度跟踪和延迟完成的持续多阶段工作流 |

## 📦 安装

AMBench 需要 Python 3.10 或更高版本。使用容器运行还需要安装并启动
Docker。

```bash
git clone https://github.com/Aegis-AgentGuard/agent-risk-benchmark.git
cd agent-risk-benchmark
python -m venv .venv
source .venv/bin/activate
pip install -e .
ambench --help
```

修改 runner 或添加 backend 时，可安装开发依赖：

```bash
pip install -e .[dev]
```

## 🚀 运行 AMBench

容器运行支持按 case id、类别、目录或完整 benchmark grid 选择任务，结果默认
写入 `runs/<run-date>/<case-id>/<run-name>/`。

### 🧰 Harness 镜像与配置

根据需要拉取四个 harness 的已发布镜像：

```bash
docker pull lsgoose/ambench-harness-openclaw_2026.7.1:v1.0
docker pull lsgoose/ambench-harness-codex_0.144.4:v1.0
docker pull lsgoose/ambench-harness-nanobot_0.2.2:v1.0
docker pull lsgoose/ambench-harness-nullclaw_2026.5.29:v1.0
```

也可以使用仓库中的 Dockerfile 构建全部四个镜像：

```bash
docker/build-harness-images.sh
```

通过 `IMAGE_NAMESPACE`、`IMAGE_RELEASE` 和 `PUSH=1` 可以修改镜像命名空间、
发布标签并推送构建结果。

在 gitignore 中的 `environment.json` 配置 backend 默认值：

```json
{
  "default_backend": "openclaw",
  "container_timeout_sec": 300,
  "backends": {
    "openclaw": {
      "image": "lsgoose/ambench-harness-openclaw_2026.7.1:v1.0",
      "model": "provider/model-name",
      "provider": "provider",
      "base_url": "https://models.example.com/v1",
      "api_key_env": "MODEL_API_KEY"
    }
  }
}
```

通过配置指定的环境变量提供密钥。不要提交 API key 或
`environment.json`：

```bash
export MODEL_API_KEY="your-api-key"
```

### 📦 容器运行

容器运行会为每个 trial 启动干净容器，连接 case-local MCP server，运行
指定 backend，将 trace 转换为统一格式，然后调用 oracle。

显式指定 backend 运行一个 case：

```bash
ambench run-container \
  --case simple_sheets_put_full_explicit \
  --backend openclaw \
  --image lsgoose/ambench-harness-openclaw_2026.7.1:v1.0 \
  --model provider/model-name \
  --mcp \
  --parallel 1 \
  --timeout 300 \
  --run-date "$(date +%F)" \
  --run-name openclaw-smoke \
  --summary
```

对全部 case 各运行三次：

```bash
ambench run-container \
  --all \
  --backend nanobot \
  --mcp \
  --parallel 7 \
  --timeout 300 \
  --pass-trials 3 \
  --pass-sample-k 3 \
  --pass-metric full \
  --run-date "$(date +%F)" \
  --run-name nanobot-full-pass3 \
  --summary
```

常用参数：

| 参数 | 用途 |
|---|---|
| `--case`、`--category`、`--case-dir`、`--all` | 选择 case |
| `--backend` | 选择 harness adapter |
| `--image` | 覆盖 backend 的 Docker 镜像 |
| `--model` | 覆盖配置中的模型 |
| `--mcp` | 启动 case-local MCP 工具服务 |
| `--parallel` | 设置并发容器数量 |
| `--timeout` | 设置单个 trial 的 agent 超时时间 |
| `--pass-trials` | 对每个 case 重复运行多次 |
| `--resume` | 在命名批次中跳过已完成的 trial |
| `--summary [PATH]` | 生成 JSON 或 Markdown 汇总结果 |

如需通过 LiteLLM 统一控制 provider 参数，可参考
[`litellm-ofox-proxy/README.zh-CN.md`](litellm-ofox-proxy/README.zh-CN.md)。

## 🧩 当前支持的 Backends

AMBench 实验使用以下四个主要 harness backend：

| Backend | Harness | 执行方式与 trace 来源 |
|---|---|---|
| `openclaw` | OpenClaw | 将内部 Gateway session 转换为统一 trace |
| `codex` | Codex CLI | 使用 `codex exec` 的非交互 JSONL 事件 |
| `nanobot` | NanoBot | 将公开 Python SDK 返回结果转换为统一 trace |
| `nullclaw` | NullClaw | 将 CLI 执行日志转换为统一 trace |

backend 镜像必须包含对应 harness 的可执行文件或 SDK。每个 adapter 定义了
默认镜像，也可以在 `environment.json` 中或通过 `--image` 覆盖。provider
URL、模型、API key 环境变量、网络模式和 backend 特定限制可以配置在
`environment.json` 的 `backends.<name>` 下。

## 🛠️ 添加 Backend

backend adapter 位于 `src/ambench/backends/`，并实现
`base.py` 中定义的接口。

1. 新建模块，例如 `my_harness.py`，并继承 `HarnessBackend`。
2. 定义 `name`、`default_image`，必要时定义 `workspace_path`。
3. 实现 `prepare(runtime, context)`，检查并配置 harness。
4. 实现 `execute(runtime, context)`，运行 prompt、执行超时控制、将原始
   trace 转换为 `<run-dir>/bench-run.jsonl`，并返回包含耗时、token、进程和
   timeout 状态的 `BackendExecution`。
5. 仅在需要特殊容器行为时覆盖 `container_environment`、
   `container_run_args` 或 `mount_run_dir`。
6. 在 `src/ambench/backends/__init__.py` 中导入 adapter，并更新
   `BACKEND_NAMES` 和 `get_backend()`。
7. 添加包含 harness runtime 的 Docker 镜像，并说明模型、provider、密钥和
   网络要求。
8. 为配置生成、注册、执行异常、trace 转换和 token 统计添加测试。

最小 adapter 结构：

```python
from ambench.backends.base import (
    BackendContext,
    BackendExecution,
    HarnessBackend,
)


class MyHarnessBackend(HarnessBackend):
    name = "my-harness"
    default_image = "my-harness:latest"
    workspace_path = "/workspace"

    def prepare(self, runtime, context: BackendContext) -> None:
        runtime.exec(context.container_name, "my-harness --version")

    def execute(self, runtime, context: BackendContext) -> BackendExecution:
        # 运行 harness，将原始事件转换为 bench-run.jsonl，
        # 并返回时间、token、进程和 timeout 信息。
        ...
```

完成注册后，可使用下面的命令进行 smoke test：

```bash
ambench run-container --backend my-harness --case <case-id>
```

## 🗂️ 源码结构

```text
src/ambench/
├── __init__.py                 # 包元数据
├── backends/
│   ├── __init__.py             # Backend 注册表与工厂
│   ├── base.py                 # 公共 backend 接口与结果类型
│   ├── docker_runtime.py       # Docker 生命周期与命令执行
│   ├── openclaw.py             # OpenClaw adapter
│   ├── codex.py                # Codex CLI adapter
│   ├── nanobot.py              # NanoBot adapter
│   ├── nullclaw.py             # NullClaw adapter
│   ├── *_trace.py              # 各 harness 的 trace 标准化
│   └── *_telemetry.py          # 可选的被动 telemetry adapter
└── runner/
    ├── cli.py                  # `ambench` 主命令入口
    ├── sub_cli.py              # 辅助 CLI 命令
    ├── container.py            # 容器执行与批量调度
    ├── repository.py           # environment.json 加载与默认配置
    ├── cases.py                # Case 发现与选择
    ├── case_mcp.py             # Case-local MCP 服务生命周期
    ├── artifacts.py            # 运行产物与 trace 文件
    ├── evaluate.py             # Oracle 执行与结果汇总
    ├── tools.py                # Runner 公共工具
    └── openclaw/               # OpenClaw gateway 配置与 trace 处理
```

## 📮 联系我们

- 技术问题、错误报告与功能建议，请通过
  [GitHub Issues](https://github.com/Aegis-AgentGuard/agent-risk-benchmark/issues) 提交。
- 如需贡献 benchmark 场景、架构改进、harness 集成或代码修改，请先阅读
  [贡献指南](CONTRIBUTING.md)，然后提交
  [pull request](https://github.com/Aegis-AgentGuard/agent-risk-benchmark/pulls)。

## 🤝 参与贡献

我们欢迎以下类型的贡献：

- 新增 benchmark 场景与任务族
- 改进 benchmark 架构
- 集成 harness 与模型 backend
- 完善评分与 oracle 代码
- 改进文档
- 补充测试与修复问题

感谢所有贡献者对 AMBench 的建设与支持。

<p align="center">
  <a href="https://github.com/umamtiti"><img src="https://github.com/umamtiti.png?size=96" width="86" height="86" alt="umamtiti" /></a>
  <a href="https://github.com/lsgoose"><img src="https://github.com/lsgoose.png?size=96" width="86" height="86" alt="lsgoose" /></a>
  <a href="https://github.com/chenchch94"><img src="https://github.com/chenchch94.png?size=96" width="86" height="86" alt="chenchch94" /></a>
  <a href="https://github.com/hky011011"><img src="https://github.com/hky011011.png?size=96" width="86" height="86" alt="hky011011" /></a>
</p>
