# 第三方来源与许可说明

本项目原创代码与说明文档采用根目录 [MIT License](LICENSE)，版权署名为刘昌霖。本文说明第三方来源及授权范围例外，不改变第三方的原有条款。

## MoleCode 语法与技能适配

本项目的 MoleCode 语法与技能资料参考 AtomFlow-AI/MoleCode，并进行了面向 GC–EI–MS 候选修复的本地适配。

- 本地保留的完整上游许可：[UPSTREAM_LICENSE.txt](skills/molecode-graph-reasoning/UPSTREAM_LICENSE.txt)，MIT，版权声明为 `Copyright (c) 2026 AtomFlow-AI`。
- 历史来源、借鉴范围与本地修改：[provenance.md](skills/molecode-graph-reasoning/references/provenance.md)。
- 现有记录未注明所参考的上游 commit/tag；不推测补填版本，也不将本地历史说明当作上游当前状态。

发布相关适配资料时应一并保留已有版权声明与完整许可。根目录的刘昌霖版权声明不替代 AtomFlow-AI 的原有版权声明。

## Claude Agent SDK for Python

本项目依赖并参考 [Anthropic Claude Agent SDK for Python](https://github.com/anthropics/claude-agent-sdk-python)。2026-11-18 核对的本地参考目录在 `pyproject.toml` 中声明版本 `0.2.176`，与本项目锁定的 SDK 版本一致。该目录是 Python SDK 源码；SDK 调用的 Claude Code CLI 是单独组件。

- SDK 版权声明：`Copyright (c) 2025 Anthropic, PBC`。
- 完整 MIT 许可原样保留于 [claude-agent-sdk-python.LICENSE](licenses/claude-agent-sdk-python.LICENSE)，与核对的本地 SDK `LICENSE` 字节一致。
- 许可文件 SHA-256：`cebdde8a8fb9ee59e5eaaed19578bf8085aa7047562259c31f38f225c26f6812`。
- 本地参考目录不含 Git 元数据，未确认上游 commit/tag；版本字段不用于推断精确提交。

### 接入与参考范围

| 本项目位置 | 使用的 SDK 接口及参考模式 |
|---|---|
| [harness/client.py](src/gcms_agent/harness/client.py) | `ClaudeAgentOptions`、`ClaudeSDKClient`、流式消息、会话中断与使用量读取 |
| [harness/hooks.py](src/gcms_agent/harness/hooks.py) | `PreToolUse` 回调及 `hookSpecificOutput` 权限响应协议 |
| [tools/server.py](src/gcms_agent/tools/server.py)、[tools/batch_server.py](src/gcms_agent/tools/batch_server.py) | `tool` 与 `create_sdk_mcp_server`，注册宿主管理的化学工具 |
| [validate_thinking_transport.py](scripts/validate_thinking_transport.py)、[validate_progress_watchdog.py](scripts/validate_progress_watchdog.py) | SDK 接入和本地模拟服务验证 |

本次源码比对及对应接口的人工检查未识别出整份 SDK 实现文件的复制；项目通过安装依赖调用 SDK，没有随仓库放入 `claude_agent_sdk` 实现目录。该检查不能排除经过改写的历史借鉴，因此保留明确来源与完整上游许可；本项目的版权署名不替代 Anthropic 的版权声明。

### SDK 与 CLI 的条款边界

参考 SDK 的 `LICENSE` 为 MIT；其 README 的 “License and terms” 另说明 SDK 使用受 Anthropic 商业服务条款约束，并对具有独立许可的组件作出例外说明。核对使用条件时应同时阅读所用版本的 LICENSE、README 及其指向的条款。SDK 的 MIT 许可不应被理解为对 Claude Code CLI 本体或在线服务的统一 MIT 授权。

本仓库只提供自身源码和许可说明，不附带 SDK 实现、Claude Code 可执行文件或安装环境。SDK 会话接口的使用与项目自身科研逻辑的许可范围分别说明，不将 Claude Code CLI 标记为本项目原创或 MIT 代码。

## 安装依赖

Python 依赖通过 [pyproject.toml](pyproject.toml) 和 [requirements.lock](requirements.lock) 声明，由使用者安装；本仓库不附带 `.venv`、依赖安装包或 Claude 可执行文件。

各依赖及其附带组件遵循各自的许可或使用条款。SDK 的 Python 代码与其附带的可执行组件不应自动视为具有相同条款。若另行分发包含依赖的安装包或容器，需要按实际分发内容检查其许可与声明；本文不是完整依赖许可证清单。

## 数据与案例

私有研究数据不随仓库分发。`case_studies/` 中两个真实案例的整个展示包（包括谱图、候选、参考标签、轨迹、结果、说明及图片）不纳入根目录代码与文档的 MIT 授权。其已获准公开的范围见[案例说明](case_studies/README.zh-CN.md)和[数据说明](data/README.zh-CN.md)，不据此授予额外再利用许可或完整数据集的分发许可。独立人工构造的 `data/examples/` 示例按本项目 MIT 许可提供。
