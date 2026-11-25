# GCMS 分子结构解析

[English](README.md) | 简体中文

面向 GC–EI–MS 谱图的分子结构解析：通过扩散模型生成候选结构，并利用智能体进行结构修正、重新排序和碎裂解释。

本仓库包含两个互补的研究模块：

| 阶段 | 模块 | 职责 |
|---|---|---|
| 候选生成 | [DiffGCMS](DiffGCMS/README.zh-CN.md) | 结合 SpecTUS 谱图编码器与源自 DiffMS 的离散分子图扩散解码器，以谱图及给定的重原子类型和数量为条件，生成候选结构的化学键。 |
| 候选审阅与解释 | [GCMS-Agent](GCMS-Agent/README.zh-CN.md) | 根据原始 EI 谱图审阅十个候选 SMILES，在重原子组成约束下提出结构编辑，再由化学工具验证、评分、排序和解释。 |

两个模块使用独立环境和运行入口。阶段之间需要整理数据；本版本尚未提供一条命令完成两个阶段的端到端运行。

## 仓库结构

```text
GCMS-Structure-Elucidation/
├── DiffGCMS/       # 图扩散模型、训练与候选生成
└── GCMS-Agent/     # 候选审阅、化学工具、测试及公开案例
```

克隆仓库：

```bash
git clone https://github.com/changlin-liu03/GCMS-Structure-Elucidation.git
cd GCMS-Structure-Elucidation
```

各模块的命令均在其子目录内执行。模块文档及其链接的指南中，“仓库根目录”指对应模块的目录。请为两个模块分别创建 Python 环境。

## 开始使用

**DiffGCMS：** 从总仓库根目录执行 `cd DiffGCMS`，随后按照[安装与训练说明](DiffGCMS/README.zh-CN.md)操作。项目声明 Python 最低版本为 3.9。需准备适配硬件的 PyTorch 依赖、数据及统计文件、SpecTUS 编码器权重，以及推理所需的训练 checkpoint。研究数据和模型权重不随本仓库分发。

**GCMS-Agent：** 从总仓库根目录执行 `cd GCMS-Agent`，随后按照[安装与公开示例说明](GCMS-Agent/README.zh-CN.md)操作。文档记录的已验证环境为 Windows x86-64 / Python 3.10.19。需保留完整模块目录并进行可编辑安装，以读取配置、技能和资源文件。

- `baseline`：无需模型 API 密钥，对候选进行验证、评分和排序。
- `agent`：使用配置的模型服务审阅和编辑候选，需要凭据，可能产生服务费用。
- 凭据放在 `GCMS-Agent/.env` 或进程环境变量中；仓库只发布占位模板 `.env.example`。

## 两阶段的数据衔接

DiffGCMS 预测输出与当前 Agent 输入格式不同，详见 [DiffGCMS 数据规范](DiffGCMS/docs/data.zh-CN.md)及 [Agent 数据规范](GCMS-Agent/data/README.md)。

| 字段 | DiffGCMS 预测输出 | 当前 Agent 输入 |
|---|---|---|
| 谱图 | `input_ids` 与分箱强度 `position_ids` | `input_ids` 与原始线性 `intensity` |
| 候选 | `generated` 中为含 `smiles`、`inchi`、`valid` 的对象 | `generated` 中为恰好十个非空 SMILES 字符串 |
| 样本标识 | 精简导出文件不包含 `sample_id` | 需要唯一且符合 ASCII 安全命名规则的 `sample_id` |
| 参考结构 | 可能包含 `true_smiles` | 标签单独存储；推理输入拒绝 `true_smiles` |

整理时，需通过可靠的样本对应关系找回原始线性谱图，提取候选 SMILES，并分配唯一标识。验证前需明确处理缺失候选：空值不符合 Agent 输入契约。分箱后的 `position_ids` 无法恢复原始线性强度，不能直接改名为 `intensity`。推理输入还需移除不支持的字段，参考标签仅保存在独立评估文件中。

Agent 的数据准备工具要求源文件已经符合其字段约定，并非 DiffGCMS 预测文件的直接转换器。本版本未包含自动衔接适配器。

## 示例与验证范围

- [人工构造示例](GCMS-Agent/data/examples/README.md)可演示离线 Agent 流程，无需私有数据或模型调用。
- [两个公开历史案例](GCMS-Agent/case_studies/README.md)保留输入、结构编辑、结果与来源记录。其主要 Agent 运行均未完成，用于展示开发轨迹，不代表当前版本的识别准确率。
- GCMS-Agent 已有发布审查记录了在其文档环境下 398 项离线测试通过、两个人工示例基线流程成功。这是已有模块验证记录，不是本次对两阶段流程重新进行的端到端验证。
- 完整 DiffGCMS 训练与推理需要外部数据和权重；本次合并发布未重新运行这些实验。

## 来源、许可与数据

两个模块分别保留已有许可证及第三方声明：

- DiffGCMS：[LICENSE](DiffGCMS/LICENSE)、[第三方声明](DiffGCMS/THIRD_PARTY_NOTICES.md)及[研究引用](DiffGCMS/references.bib)。
- GCMS-Agent：[LICENSE](GCMS-Agent/LICENSE)及[第三方声明](GCMS-Agent/THIRD_PARTY_NOTICES.md)，其中包含 SDK 与 MoleCode 来源说明。

上述文件说明相应代码的许可范围及保留的上游声明。模型权重、外部数据和两个历史案例展示包的单独条款或排除范围见各模块文档，代码许可不对这些材料重新授权。私有研究数据、凭据、本地环境、原始运行记录及生成输出均不纳入本仓库。

用于研究时，请保留适用的上游致谢，并按各模块说明引用所使用的方法。
