# 配置说明

[English](README.en.md) | **简体中文**

本目录包含运行所需的五份 YAML。参数说明以当前源码行为为准；本目录的整理不调整模型、科学规则或有效运行限额。

## 文件与加载顺序

| 文件 | 用途 |
|---|---|
| [model.yaml](model.yaml) | 模型连接、思考设置、历史估算价格 |
| [chemistry.yaml](chemistry.yaml) | 固定化学约束和实验条件描述 |
| [scoring.yaml](scoring.yaml) | 当前评分版本的兼容性声明 |
| [runtime.yaml](runtime.yaml) | 基础运行参数 |
| [profiles/economy.yaml](profiles/economy.yaml) | 默认 Agent 配置的覆盖项 |

加载顺序：四份基础 YAML → profile 覆盖项 → CLI 显式参数。

- `--profile standard` 直接使用基础配置，不读取 `profiles/standard.yaml`，无需创建该文件。
- Agent 模式的 CLI `run` / `resume` 默认使用 `economy`；`load_config()` 函数本身默认只加载基础配置。
- `--mode baseline` 使用基础配置，不调用模型，也不应用 economy 覆盖项。
- profile 按节执行字段更新，不是递归合并。若覆盖 `model.prices_cny` 等嵌套字段，需要提供该字段完整内容。
- `--configs <目录>` 指定完整配置目录，其中应包含四份基础 YAML；使用 economy 时还必须包含 `profiles/economy.yaml`。缺失时明确报错，不回退到项目自身的 profile。
- 命令行只覆盖有对应选项的字段，例如 `--budget-cny`、`--strategy`；并非每个 YAML 字段都有 CLI 选项。

## standard 与 economy

下表包含继承值和代码默认值，不能只看 economy 文件判断最终配置。

| 项目 | standard | economy |
|---|---|---|
| 审阅方式 | `case_batch` | `case_batch` |
| 思考 | 开启，`low` | 开启，`low` |
| 每批唯一候选上限 | 4 | 4 |
| 同 case 并发批数 | 1（代码默认） | 2 |
| 每候选最多轮数 / 累计时间 | 3 / 900 秒 | 3 / 900 秒 |
| 每会话 SDK turn / 提交预留 | 8 / 2 | 6 / 1 |
| 每会话分析工具调用上限 | 2 | 4 |
| 会话超时 | 300 秒 | 0，关闭 |
| 每 case 有效会话上限 | 未设置 | 8 |
| 仅低峰启动会话 | 否 | 是 |
| 总金额 / 单 case 金额上限 | 未设置 | 未设置 |
| 输入 / 输出 token 预留估算 | 80000 / 65536 | 40000 / 8192 |
| 显式输出 token 上限 | `null` | `null` |

`standard` 不等于旧单候选模式。旧模式需要同时指定 `--profile standard --strategy legacy`。

## 调度、预算与特殊值

- **并发**：`concurrency` 用于旧单候选流程和基线的候选处理；默认批审使用 `batch_concurrency`。不同谱图当前依次处理。
- **分批**：`batch_size` 限制每批唯一候选数，`max_batch_chars` 按紧凑候选 JSON 字符合计拆批，不是 token 限制或完整请求大小上限；单个超长候选不会被截断。
- **时间**：`candidate_timeout_seconds` 限制候选累计处理时间；`session_timeout_seconds` 限制会话时间，并覆盖 `model.request_timeout_seconds`。关闭会话超时不会关闭候选限制。模型文件中的超时不是单独的 HTTP 连接超时。
- **轮数与提案**：`max_rounds` 和 `stagnation_rounds` 参与停止判断；`proposals_per_round` 当前固定为 3，批审的每候选提案上限也由工具协议约束。SDK turn、分析工具调用、替代提案和原子编辑操作数量不是同一概念。
- **输出**：`max_output_tokens: null` 使用 CLI/服务端默认值，不表示无限输出。
- **预留**：`reservation_input_tokens`、`reservation_output_tokens` 用于会话启动前的费用估算，不是生成长度限制。若显式设置 `max_output_tokens`，输出预留计算改用 `max_tool_turns × max_output_tokens`。
- **诊断目标**：`case_output_token_target` 只用于报告标记，不会截断或停止生成。
- **金额**：`budget_currency` 选择人民币或美元记账；对应 `budget_cny` / `budget_usd` 为 `null` 时不设总金额上限。`case_budget_cny` 为 `null` 时不设单 case 金额上限，仍保留用量与费用记录。
- **会话与失败**：economy 每 case 最多 8 次有效会话。已知零用量且无工具执行的明确连接失败可不占此额度，但另受 `max_transport_failures_per_case` 约束；不能把网络重试次数当作无限免费重试。
- **监测**：economy 启用流式监测，但首次工具前的时间、字符、响应数以及整会话字符阈值均以 0 关闭。`pre_tool_interrupt_grace_seconds` 是触发中断后的等待时间，必须大于 0；不能把所有字段的 0 都理解为关闭。

### 低峰与价格

当前程序使用北京时间（UTC+8），将周一至周五的 09:00–12:00、14:00–18:00 判为高峰，其余为低峰；不查询法定节假日。这是 [budget.py](../src/gcms_agent/workflow/budget.py) 中的本地调度与估算规则，不是对供应商当前政策的保证。

`offpeak_only: true` 时，高峰不启动新会话，记录 `offpeak_wait`，之后需显式 `resume`；不会自动等待到低峰并重启。CLI 的 `--offpeak-only` 只能开启此限制，关闭应使用独立配置目录中的 `offpeak_only: false`，并为改变后的配置选择新运行目录。

`model.yaml` 中美元和人民币价格分别保留 2026-11-02、2026-11-03 的快照，单位均为每百万 token。本次整理未联网核验现价或模型别名的在线可用性；记录的估算费用不替代供应商账单。

## 固定约束与实验条件

`chemistry.yaml` 前四个布尔字段在本版本必须为 `true`，不能通过设置 `false` 放宽重原子组成、连通性、中性和闭壳层要求。

该文件的 `ionization`、`energy_ev`、`energy_is_assumed`、`resolution` 是描述字段，当前不会被读取后注入或覆盖样本。实际条件来自输入的 `conditions`；数据准备在源数据未提供相应条件时使用 EI、70 eV（假定）、名义质量分辨率默认值。修改这里的 70 不会修改数据或模型收到的条件。

`scoring.yaml` 的 `raw_linear` 和深度 2 是加载器要求的兼容性声明。实际枚举读取 [rules.json](../resources/ei_rules/rules.json) 的 `enabled`、`max_steps` 等字段，`fragmentation_depth` 不会覆盖该文件。规则适用范围见 [CONDITIONS.md](../resources/ei_rules/CONDITIONS.md)，计分定义见 [评分说明](../docs/scoring.md)。

已移除的 `pilot_size` 没有被代码使用。当前数据准备程序仍固定生成最多 20 条 pilot 样本；运行时可用 `--limit` 限制本次读取的样本数量，这不会重新生成数据集。

## 使用自定义配置

在项目根目录复制整个配置目录后，修改副本中的参数。凭据仍通过进程环境变量或项目根目录 `.env` 提供，不放入 YAML。

```powershell
# 目标目录尚不存在时执行；自定义配置按本地实验需求保存。
Copy-Item -LiteralPath ./configs -Destination ./runs/my_config -Recurse

# 明确使用本仓库源码，避免已有环境的可编辑安装指向其他项目副本。
$env:PYTHONPATH = (Resolve-Path ./src).Path

# 修改 runs/my_config 后，运行无需 API 的基线检查。
./.venv/Scripts/python.exe -B -m gcms_agent.cli run data/raw_linear_v1/inference/pilot.json --mode baseline --configs runs/my_config --limit 1 --output runs/my_config_baseline
```

上例通过当前进程的 `PYTHONPATH` 明确指定本仓库源码，避免复用环境时导入其他项目副本。上例使用现有项目环境与数据；全新环境的安装步骤统一在项目安装文档中维护。自定义目录使用 economy 时，必须同时检查其 `profiles/economy.yaml`，该文件中的值会覆盖基础文件。

## 修改与恢复

加载器会检查部分字段的范围和固定约束，但不是完整 YAML Schema 校验器。按上述用途修改字段，不要假定未报错就一定生效。

恢复运行会核对输入、配置以及源码和资源指纹。项目 `configs/` 下的说明文件和 YAML 注释也参与文件指纹；即使有效参数相同，整理后也可能无法恢复旧运行。使用新的输出目录，保留原始结果和版本检查。总预算与输入费用预留的允许上调规则见 [恢复约定](../docs/editing_and_stopping.md#resume)。
