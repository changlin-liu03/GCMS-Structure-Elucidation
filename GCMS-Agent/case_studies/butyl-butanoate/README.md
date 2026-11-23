# 丁酸正丁酯：服务失败、重试与参考结构生成

[English](README.en.md) | **简体中文**

初次 Agent 尝试因服务错误中止；重试使用相同输入和参考标签。关键候选 c03 在第一轮正式提交参考结构，但另一结构覆盖分略高，因此参考结构在保存的展示排名中位于第 2。所有来源槽位最终合法，仍因会话额度结束而未完成。初次失败的未知用量估算与重试费用分开保存，不能仅统计重试后宣称一次完整成功。

这是获准公开的历史开发案例，不是随机测试集或当前版本的新实验。模型文字按原记录保留，为模型提出的解释，不代表实验确认的机理。未重新运行模型、验证化学路径或重算分数。

## 文件与阅读顺序

1. [输入](input.json)：观测谱与十个原始候选；[参考答案](reference.json)独立保存，仅用于事后说明与评估。
2. 本文：候选来源、关键轨迹、全部试验索引及最终状态。
3. [完整结构化轨迹](trajectory.json)：所有独立候选的初始图、正式提交历史、会话工具顺序和全部试验。
4. [结果与对照](result.json)：全部来源槽位、原始排名、各阶段评估及已知/未知费用。
5. [来源与校验](provenance.json)：历史配置/源码指纹、原冻结清单验证与公开文件 SHA-256。

## 输入与版本

样本 `s01055_907fc7efff69`，46 个谱峰，10 个候选来源槽位。参考结构：`CCCCOC(=O)CCC`。
输入为 `raw_linear`；当前加载器可校验其格式，但历史代码/技能版本与当前不同，重跑结果不保证一致。
本材料展示已保存的历史过程，不是可恢复检查点或完整会话逐字记录。

## 十个来源槽位

| 来源 | 独立计算候选 | 初始 SMILES | 最终 SMILES | 结束原因 |
|---|---|---|---|---|
| c01 | c01 | `CCC(=O)OCC(C)CC` | `CCCC(=O)OC(C)CC` | case_session_limit |
| c02 | c02 | `CCC(=O)OC(CC)CC` | `CCCC(=O)OC(C)CC` | case_session_limit |
| c03 | c03 | `CCCCC(=O)OCCC` | `CCCCOC(=O)CCC` | case_session_limit |
| c04 | c04 | `CCCCC.O=C1CCO1` | `CCCCOC(=O)CCC` | case_session_limit |
| c05 | c05 | `CC1CCCCOC(=O)C1` | `CCCCOC(=O)CCC` | case_session_limit |
| c06 | c06 | `CCCCCOC(=O)CC` | `CCCC(=O)OC(C)CC` | case_session_limit |
| c07 | c03 | `CCCCC(=O)OCCC` | `CCCCOC(=O)CCC` | case_session_limit |
| c08 | c01 | `CCC(=O)OCC(C)CC` | `CCCC(=O)OC(C)CC` | case_session_limit |
| c09 | c09 | `CCCC(C)OC(=O)CC` | `CCCC(=O)OC(C)CC` | case_session_limit |
| c10 | c04 | `CCCCC.O=C1CCO1` | `CCCCOC(=O)CCC` | case_session_limit |

同一独立计算候选的图与历史只存一份，十个来源槽位及其最终状态全部保留。

## 关键结构轨迹

![已正式提交结构与历史最佳恢复](trajectory.png)

图中只突出 `c03`；初始状态、每次正式提交与最终输出均来自原记录。图上数值是覆盖分 × 100。

### 第 1 轮

模型提交摘要（未经机理确证）：重排为丁酸丁酯CCCCOC(=O)CCC，合法C8H16O2；工具支持116(McLafferty)与101，分数0.001565→0.013892，为当前最优；71/89/56等强峰与缺失的M+144仍为规则未覆盖的局限。

正式提交：**是**；工具/宿主结论：`valid_exploration`；覆盖分：1.389161。
结果结构：`CCCCOC(=O)CCC`。
模型理由：把丙基戊酸酯重排为丁酸丁酯（CCCCOC(=O)CCC）：丁酰基给出 m/z 116 的 McLafferty 离子（C6H12O2）及 101（C5H9O2），二者被工具路径支持；同时丁酰基（酸侧4碳直链，含γ-H）与丁氧基（醇侧4碳直链，有γ-H）分别对应144→116(C2H4)与→88(C4H8)的中性丢失，可解释观测到的116(12)、88(73)、101(59)，并与丁酰阳离子71(999)、丁烯56(484)、43(637)的直链特征一致。原丙基戊酸酯需要5碳戊酰基（预期85阳离子/102 McLafferty），与观测不符。分子离子144未出现、88/89/71/56/43/41/60等强峰仍无规则路径，属当前规则覆盖局限。。

| 操作 | 模型逐项说明 |
|---|---|
| `{"op": "remove_bond", "a": "Mol_C_4", "b": "Mol_C_5"}` | 断开 Mol_C_4–Mol_C_5（C5为羰基碳），把 C4 从酸侧戊酰链移出 |
| `{"op": "remove_bond", "a": "Mol_C_3", "b": "Mol_C_4"}` | 断开 Mol_C_3–Mol_C_4，为 C4 改接到醇侧末端 C8 腾出价位 |
| `{"op": "set_bond", "a": "Mol_C_3", "b": "Mol_C_5", "order": 1}` | 新成 Mol_C_3–Mol_C_5 单键，使酸侧变为 C5–C3–C2–C1 的丁酰基（4碳） |
| `{"op": "set_bond", "a": "Mol_C_4", "b": "Mol_C_8", "order": 1}` | 新成 Mol_C_4–Mol_C_8 单键，使醇侧变为 O2–C6–C7–C8–C4 的丁氧基（4碳） |
| `{"op": "set_atom", "id": "Mol_C_4", "hydrogens": 3}` | Mol_C_4 现只连 C8，氢数 2→3（CH2→CH3） |
| `{"op": "set_atom", "id": "Mol_C_8", "hydrogens": 2}` | Mol_C_8 现连 C7 与 C4，氢数 3→2（CH3→CH2）；总氢16、C8H16O2 |

### 第 2 轮

模型提交摘要（未经机理确证）：保留正丁酸丁酯 CCCCOC(=O)CCC（C8H16O2，M=144）。谱图与丁酸酯骨架一致但规则覆盖率低：M+ 144 未观测，116（M-28）、101（M-43）由 mclafferty/carbonyl_alpha/heteroatom_alpha 支持，而最强峰 71、43、89、56、41 无对应规则路径（71 权重 0.195、43 0.125、89 0.112、56 0.095、41 0.091，约 98.6% 强度未分配），属规则集缺少酯 α-断裂/酰基离子通道的覆盖局限。已试两个有依据的替代：异丁酸丁酯（同分 0.01389，无新增支持峰）与仲丁酸丁酯（0.01487，仅新增 m/z 115，强度 5）。仲丁基的独特通道 115 极弱，而共享的 101（强度 59）与强峰 56（484）&gt; 57（167）更符合直链正丁基而非仲丁基/异丁基，且分数提升仅来自强度 5 的弱峰，不构成可核验的结构改进，故不采纳编辑。

## 全部工具试验

按回执文件顺序、回执内记录顺序列出；并发会话之间不据此推断精确时间顺序。工具选中引用不等于宿主最终提交，正式提交以候选 rounds 中的 committed 为准。

| 会话 | 候选 / 图版本 | 试验 ID | 合法 | 工具结论 | 引用选中 | 覆盖分 |
|---|---|---|---|---|---|---|
| 1 | c01 / 0 | `t_c809c7ea08e3555066ae2be7` | True | valid_exploration | True | 1.389161 |
| 1 | c02 / 0 | `t_59742937fb0f4cae70fe6d5f` | True | valid_exploration | True | 1.389161 |
| 1 | c01 / 0 | `t_23e55a0c4515edbd718ba993` | True | valid_exploration | False | 1.154373 |
| 2 | c03 / 0 | `t_cd7ff702157e93d3f4fa5e29` | True | valid_exploration | True | 1.389161 |
| 2 | c03 / 0 | `t_4a09f180f38f0708b324455b` | True | valid_exploration | False | 1.389161 |
| 2 | c04 / 0 | `t_4561c19f397cdb61ba516a79` | True | first_valid_repair | True | 1.389161 |
| 2 | c04 / 0 | `t_9d7ab6f6468837b8fedd6cc6` | True | first_valid_repair | False | 1.389161 |
| 3 | c05 / 0 | `t_c47b4429542db045e9d78191` | True | valid_exploration | True | 1.389161 |
| 3 | c05 / 0 | `t_3523536148afef3c659d49db` | True | valid_exploration | False | 2.054392 |
| 3 | c06 / 0 | `t_8f926245421967d6a26bf361` | True | valid_exploration | True | 1.389161 |
| 3 | c06 / 0 | `t_c5b28d7cce4db1f6dd1d17c8` | True | valid_exploration | True | 1.486989 |
| 4 | c09 / 0 | `t_39df86e864a45b4b54af00f5` | True | valid_exploration | False | 1.389161 |
| 4 | c09 / 0 | `t_8603bcd9ee4cc7f8006f2477` | True | valid_exploration | True | 1.486989 |
| 5 | c01 / 1 | `t_6faf4222c6ef3cbc4fdd28d3` | True | valid_exploration | False | 1.154373 |
| 5 | c01 / 1 | `t_fad7a998fa233b82d0f0f52f` | True | valid_exploration | False | 1.389161 |
| 5 | c01 / 1 | `t_094f80f329db7b420fd2bf24` | True | valid_exploration | True | 1.486989 |
| 6 | c02 / 1 | `t_c8142684172a123c5af54ea7` | True | valid_exploration | False | 1.389161 |
| 6 | c02 / 1 | `t_5177b5cb360ab95df01f4b81` | True | valid_exploration | False | 0.097828 |
| 6 | c02 / 1 | `t_c4008b8f8e2397da6b8eb126` | True | valid_exploration | False | 1.252201 |
| 6 | c02 / 1 | `t_3af7898fa411e48bc014e638` | True | valid_exploration | True | 1.486989 |
| 6 | c02 / 1 | `t_d4338d6b618f64fb96a6c337` | False | chemical_validation_failed | False | 未评分 |
| 6 | c02 / 1 | `t_b4e0012670182b85e6b31605` | True | valid_exploration | False | 0.097828 |
| 6 | c02 / 1 | `t_a01052a2946822dc5ceeb2ca` | True | valid_exploration | False | 1.389161 |
| 6 | c02 / 1 | `t_f67df3955fc4ed06b924157f` | True | valid_exploration | False | 0.097828 |
| 7 | c03 / 1 | `t_373b5ea08290ac50c6ebd430` | True | valid_exploration | False | 1.389161 |
| 7 | c03 / 1 | `t_80b6b351e7265175f0733597` | True | valid_exploration | False | 1.486989 |

## 各阶段结果

| 阶段 | 完成 | 保存排名 Acc@1 / Acc@10 | 严格端到端 Acc@1 / Acc@10 | 已知费用 / 估算费用（元） |
|---|---|---|---|---|
| initial_baseline | True | 0.0 / 0.0 | 0.0 / 0.0 | 0.00000000 / 0.00000000 |
| initial_agent | False | 0.0 / 0.0 | 0.0 / 0.0 | 0.00000000 / 0.14553600 |
| retry_baseline | True | 0.0 / 0.0 | 0.0 / 0.0 | 0.00000000 / 0.00000000 |
| agent | False | 0.0 / 1.0 | 0.0 / 0.0 | 0.81609360 / 0.00000000 |

### 最终保存的唯一结构顺序

| 展示位置 | 证据排名 | SMILES | 覆盖分 | 参考结构 |
|---|---|---|---|---|
| 1 | 1 | `CCCC(=O)OC(C)CC` | 1.486989 | 否 |
| 2 | 2 | `CCCCOC(=O)CCC` | 1.389161 | 是 |

## 如何理解这些材料

全部主要 Agent 运行均为 provisional/incomplete。生成参考结构、选择 Top1 和完成整个审阅是三个不同结果。严格端到端口径把未完成案例记为未命中；单个开发案例不能证明总体识别率或费用。
轨迹中的 graphs、scores、paths 为按内容 SHA-256 去重的对象池；graph_ref、score_ref、path_refs 在同一 trajectory.json 内解析。原子 ID 和图版本保留，不用 canonical SMILES 重建原子对应关系。
原始输入和标签与源文件语义一致。公开轨迹是字段筛选和对象去重后的派生材料，provenance.json 单独记录公开文件哈希；原冻结清单仅说明导出时本地原文件核验通过，不表示公开文件与原始整份结果逐字节相同。
只包含已记录的工具调用元数据、试验与正式说明，不包含隐藏思考正文、原始会话流、凭据、个人路径或 CLI 缓存。无法从这些摘要补出未记录的推理过程。
完整历史源码不在本包中，只有原记录指纹，不能声称具备逐字节重现旧模型实验的全部条件。

返回[案例导航](../README.md)。
