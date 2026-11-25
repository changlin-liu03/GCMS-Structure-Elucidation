# 数据说明

[English](README.md) | **简体中文**

完整研究数据为私有数据，不随 GitHub 仓库发布。除明确获准公开的两个历史案例外，此限制同样适用于从中派生的试跑子集、单例、标签、选择清单和数据副本。公开运行示例位于 [examples/](examples/README.zh-CN.md)，由人工独立构造，不摘取研究数据。

## 两个真实案例的公开例外

数据持有者明确允许公开 [丁酸正丁酯、三甲基正戊基硅烷](../case_studies/README.zh-CN.md) 的谱峰、候选结构、参考答案和修改轨迹。经过字段筛选的材料单独放在 `case_studies/`，不解除对 `data/raw_linear_v1/`、原始 `runs/` 或其余数据的忽略规则。此例外不代表允许分发完整数据集。

两个案例的展示输入均保留原始线性强度。两例主要 Agent 运行均未完成，展示包保留该状态及失败/重试。人工 `data/examples/` 与这些真实历史案例用途不同。

## 目录职责

| 路径 | 用途 | 是否随仓库发布 |
|---|---|---|
| `raw_linear_v1/inference/all.json` | 完整无标签输入 | 否，仅本地 |
| `raw_linear_v1/inference/pilot.json` | 20 条按候选状态分层选出的开发样本 | 否，仅本地 |
| `raw_linear_v1/inference/engineering_pilot.json` | 4 条固定开发案例 | 否，仅本地 |
| `raw_linear_v1/inference/single_top10_miss.json` | 固定开发单例，名称沿用历史约定 | 否，仅本地 |
| `raw_linear_v1/evaluation/` | 完整及单例评估标签 | 否，仅本地 |
| `raw_linear_v1/manifests/` | 源哈希、输入哈希、选择索引和本地源路径 | 否，仅本地 |
| [examples/](examples/README.zh-CN.md) | 人工构造的输入、演示参考标签和说明 | 是，仅明确列出的文件 |

根目录 `.gitignore` 默认忽略 `data/` 下的数据，仅放行 `README.md`、`README.zh-CN.md`、`examples/README.md`、`examples/README.zh-CN.md`、`examples/inference.json`、`examples/labels.json`。新数据目录、示例旁的备份和其他文件不会自动成为可上传内容。忽略规则不控制手动上传或压缩整个文件夹；发布时使用经核对的文件清单。当前说明不是对私有数据的公开许可。

## 推理输入格式

输入为 UTF-8 JSON 数组，每项包含下列字段；实际校验以 [loader.py](../src/gcms_agent/data/loader.py) 和 [intensity.py](../src/gcms_agent/data/intensity.py) 为准。

| 字段 | 约定 |
|---|---|
| `sample_id` | 1–100 个 ASCII 字母、数字、下划线或连字符；文件内唯一 |
| `input_ids` | 非空、不重复的正整数名义 m/z 数组 |
| `intensity` | 与 `input_ids` 等长的有限、非负线性强度，至少一个正值；允许小数；零强度不作为观测峰 |
| `generated` | 恰好 10 个非空候选 SMILES 字符串；允许重复，保留来源槽位。语法和化学合法性由后续工具处理 |
| `conditions` | 可选对象，只允许 `ionization`、`energy_ev`、`energy_is_assumed`、`resolution`、`intensity_encoding` |

当前条件只支持 `EI`、`nominal`、`raw_linear`；提供能量时须为大于 0 且小于 1000 的数值，假定标记须为布尔值。旧 `position_ids` 分箱格式不再支持。输入强度保留原始尺度，计分权重为 `I/sum(I)`；不要求最大值为 999。

推理输入不允许 `true_smiles` 或其他未知字段。标签另存为 JSON 数组，每项为 `sample_id` 和 `true_smiles`，在运行结束后的独立评估中使用。示例标签只是人工指定的演示参考结构。

## 本地数据准备

已有的私有 `raw_linear_v1/` 数据继续本地使用，无需重新生成或修改清单。清单中的绝对源路径用于本机核验，不是可移植的下载地址。当前准备工具默认查找项目上级的 `test.json`，该源文件不随仓库提供；如果源文件位于别处，显式指定实际路径。

有权使用自己的原始数据时，可使用可信准备程序拆分输入与标签。源 JSON 每项需要 `input_ids`、`intensity`、`generated`，可附 `conditions` 和 `true_smiles`；源标签在准备阶段分离，不传入推理。

在项目根目录、已安装运行依赖的环境中执行；下面的路径是占位示例，应替换为自己的本地路径。

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

./.venv/Scripts/python.exe -B -m gcms_agent.cli audit 'C:/local-data/source.json' --output data/local_prepared
```

准备程序生成 `inference/all.json`、最多 20 条的 `inference/pilot.json`、`evaluation/labels.json` 和 `manifests/data.json`。已有这些输出时拒绝覆盖，应使用新目录。固定开发子集可由 [prepare_development_cases.py](../scripts/prepare_development_cases.py) 另行生成；默认索引适用于原开发数据，其他数据应显式指定有效索引。

自定义数据目录评估时必须使用 `--labels` 指定相应标签；CLI 的默认标签路径仍是本地 `raw_linear_v1/evaluation/labels.json`。完整安装说明见[项目中文首页](../README.zh-CN.md)，不需要私有数据的体验流程见[人工示例说明](examples/README.zh-CN.md)。
