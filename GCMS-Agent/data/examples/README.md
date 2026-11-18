# 人工构造的离线示例

[English](README.en.md) | **简体中文**

本目录包含两条独立人工编写的演示输入。峰位置、线性强度、候选顺序及参考标签均为演示设定，没有抽取、变换或复制私有研究数据，也不是仪器实测谱或外部谱库记录。候选 SMILES 使用常见小分子结构；这里的峰强不代表这些分子的真实 EI 丰度。

| 文件 | 用途 |
|---|---|
| [inference.json](inference.json) | 两条无标签输入，每条有 10 个候选槽位和显式条件 |
| [labels.json](labels.json) | 按样本 ID 对应的人工参考结构，仅供演示独立评估 |

`synthetic_ethanol_demo` 使用乙醇/二甲醚候选，包含重复字符串和等价 SMILES，用于展示来源槽位与去重；`synthetic_acetone_demo` 使用丙酮及其他 C3H6O 异构体候选。参考结构分别人为指定为乙醇和丙酮，并非从所造谱峰实验证实的身份。

`energy_ev: 70` 是演示条件，`energy_is_assumed: true` 表示假定值。文件仅使用运行时允许的字段，人工来源说明放在本文中。

## 离线运行与评估

先安装项目依赖，在项目根目录执行。以下流程不调用模型，不需要 API 密钥或私有数据；输出目录必须是新的，重复体验时换一个目录名。

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

./.venv/Scripts/python.exe -B -m gcms_agent.cli run data/examples/inference.json --mode baseline --output runs/public_example
./.venv/Scripts/python.exe -B -m gcms_agent.cli evaluate runs/public_example --labels data/examples/labels.json --input data/examples/inference.json
```

第一步输出确定性候选评分、排名与解释报告；不会调用 Agent 修改结构。第二步读取独立演示标签并生成 `runs/public_example/evaluation.json`。显式指定 `--labels`，避免使用私有数据的默认标签路径。

预期流程处理两条输入，每条保留十个来源槽位；合并等价结构后的排名条目可能少于十条。分数和准确率仅展示输出格式与评估流程，不是模型性能、结构鉴定能力或真实实验效果的证据，也不要求人为参考结构必定排名第一。

新增文件默认被数据忽略规则排除。不要用私有研究数据替换这两个已放行的 JSON 文件。更多格式与本地数据说明见[上级说明](../README.md)。
