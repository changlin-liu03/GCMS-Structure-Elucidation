---
name: molecode-graph-reasoning
description: Read GC–MS candidate MoleCode as explicit atom–bond graphs; locate branches, rings and functional groups by atom ID, interpret invalid states, and propose validated edits within the gcms-molecode-1 subset.
---

# MoleCode 分子图理解

本任务的 MoleCode 是分子原子图，使用 `gcms-molecode-1` 小分子子集。读取节点和边来理解结构，再按原子 ID 编辑。以下核心语法已在上下文中；普通结构无需额外读文件。复杂情形按文末路由调用 `read_reference`。

## 必须掌握的语法

| 表达 | 应如何读取 |
|---|---|
| `Mol_C_2[CH2]` | 一个碳原子，稳定 ID 为 `Mol_C_2`，附带 2 个氢；ID 的 2 不是碳数量、环号或 IUPAC 位次 |
| `[C]` / `[CH]` / `[CH3]` | 分别是 0 / 1 / 3 个附着氢；`[C]` 不自动补氢，也不必是季碳 |
| `[OH]` / `[O]` | 同一个元素的不同氢状态；单靠 `[O]` 不能区分羰基氧与醚氧，要读相邻的键 |
| `[N(+)]` / `[O(-)]` | 形式电荷 +1 / −1；单个原子带电仍可组成总电荷为零的母体 |
| `A --- B` / `A === B` / `A -.- B` | A 与 B 间的单 / 双 / 三键；边是双向的分子连接，不是反应箭头 |
| `A <--> B` | 芳香键表示；输入声明不等于芳香性已验证。通常合法输入显示 Kekulé 单双键 |
| `[13CH3]` / `[C;H=?]` | 前者为碳-13 且有 3 个氢；后者氢数未知，不能当作 0 或自行忽略 |
| `graph TB/LR`、`subgraph`、`%%` | 排版、分组、普通注释，不决定空间构型、额外连接或化学真实性 |
| `%%@atom` / `%%@bond` | 本项目保真元数据，保存芳香标记、自由基、立体参考等；不得当普通注释丢弃 |

## 从图读到可核对的结构事实

1. 通读全部节点和边：按唯一节点数原子，按边确定连通分量、支链与环闭合。ID 连号、相邻文本行和图上位置均不表示成键；稠环共享原子只计一次。未知氢或验证错误应保留为待解决状态。
2. 定位目标及其邻接：记清“原子 ID → 邻居 ID、键级、氢数、电荷”。例如 `[CH]` 若连三个单键重原子，只有一个附着氢；不能仅凭标签猜作芳香碳。环必须有真实闭合边，取代位置沿环上的边判断。
3. 从局部连接识别官能团：先找 C=O、C≡N 等实际键，再读其邻居区分醛/酮/酸/酯/酰胺。将官能团名称绑定具体原子 ID；一条 C–O 单键不足以断言是醇。涉及芳香体系、稠环或杂原子时按需读结构参考。
4. 检查编辑位置的氢与价态：对普通中性闭壳层非芳香碳，可用“相邻显式键级之和 + 标签氢数 = 4”自检。该等式不能无条件推广到芳香原子、带电/自由基原子或所有杂原子；最终由工具校验。

说明编辑时只汇报与提案有关的结构事实、原子 ID 和前后变化，不必重抄全图或输出逐步思维过程。MoleCode 由工具从完整图生成；紧凑批审只展示 MoleCode，需要核对时用当前会话的 `get_candidate`（单候选模式为 `get_graph`）读取图。不能靠分子名字或注释覆盖真实连接。不同候选的同名原子 ID 不代表同一原子，每次编辑同时指定候选和版本。

## 从结构事实到工具编辑

以当前 `graph_version` 为 `base_version`。`set_bond` 建立或修改两个已有原子的键，`remove_bond` 删除现有键，`set_atom` 只修改氢数、电荷、自由基数或芳香标记；禁止重原子添加、删除、换元素与重命名。每个提案的操作项数不限。

把连接/键级变化与配套氢、电荷、芳香修正一起提交分析工具，中间状态可暂时不合法，最终结构必须通过验证。批审的 `analyze_proposals` 同时完成化学验证与谱图分析；旧单候选工具为 `validate_proposal` / `analyze_proposal`。失败后定位具体原子或键再修改，不反复试验同一结构。化学合法与谱图支持分开判断。批审通过 `submit_reviews` 引用试验 ID 正式提交并逐项解释，单候选模式使用 `submit_review`；试验工具不提交结构。

同位素及立体信息必须保留。上游的 `_R/_S` ID 后缀在本适配器中不支持；带 E/Z 的图需要配套元数据。MCP 不能重新指定立体构型，改变已标记手性中心的邻接或氢数会被拒绝，遇到此类结构先读语法参考。修复电荷/自由基必须作为显式、有理由的操作，不通过删除元数据绕过验证。SMILES 由工具生成。

## 按需参考

调用 `read_reference` 时传以下完整 `reference_id`，不要请求文件系统或 shell：

| 遇到的问题 | reference_id |
|---|---|
| 未知氢、同位素、芳香标记、立体或格式边界 | `molecode-graph-reasoning/references/syntax.md` |
| 支链、环/稠环、官能团和碎片原子定位 | `molecode-graph-reasoning/references/structure-reading.md` |
| 需要完整读图示例 | `molecode-graph-reasoning/examples/topology.md` |
| 键级与氢数协同修改、未知氢与过价修复 | `molecode-graph-reasoning/examples/transaction.md` |
| 芳香错误、环重连、立体编辑拒绝 | `molecode-graph-reasoning/examples/repair.md` |

这些是合成教学示例，不提供当前 case 的谱图证据。来源与适配说明见 `molecode-graph-reasoning/references/provenance.md`；上游许可保留于 `UPSTREAM_LICENSE.txt`。
