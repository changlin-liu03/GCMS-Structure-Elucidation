# 待修复 MoleCode 与合法输出契约

[English](molecode_contract.en.md) | **简体中文**

转换、编辑与验证分别由 [smiles_io.py](../src/gcms_agent/chemistry/smiles_io.py)、[graph_edit.py](../src/gcms_agent/chemistry/graph_edit.py) 和 [validation.py](../src/gcms_agent/chemistry/validation.py) 实现。MoleCode 适配器见 [molecode_adapter.py](../src/gcms_agent/chemistry/molecode_adapter.py)。

## 输入分层

1. 默认 RDKit 解析通过：生成图并继续执行本项目的中性、连通、闭壳层与保真检查。
2. SMILES 语法可解析但化学检查失败：使用 `sanitize=False` 和宽松属性缓存提取原子与连接，生成待修复图。该步骤不保证化学合法性，氢数和芳香标记标为待确认。
3. 语法无法形成完整图：仅自动清除明确的外层空白和完整单条 SMILES 代码围栏；不猜测缺失环号、括号或原子。失败记录为 `syntax_unparseable`，`final_smiles=null`。

这使“能转为 MoleCode”“分子合法”“谱图支持”成为独立状态。文件中存在图文本不代表已经修复成功。

## 稳定结构数据

图 JSON 是机器状态：

```text
atoms: id, element, isotope, charge, hydrogens, radicals,
       aromatic, chiral_tag, atom_map
bonds: a, b, order, stereo, stereo_atoms
```

MoleCode 用节点与边呈现这些结构：同位素写在原子标签中（如 `[13CH3]`），额外 `%%@atom` / `%%@bond` 元数据保持自由基、映射及立体参考。不能将这些机器注释与普通解释注释混同，也不能任意删去。未知氢数不能在导出时自动补成合法结构。

常规单/双/三/芳香键可读取。非法输入可保留芳香键 `<-->`，合法结构可显示 Kekulé 形式。编辑使用稳定原子 ID，canonical SMILES 的重排不用于重建历史身份。

## 立体化学兼容范围

本项目使用 `gcms-molecode-1` 小分子子集，并不完整实现上游所有 MoleCode 语法。

- 从带立体信息的 SMILES 转入时，四面体构型保存在 `%%@atom` 的 `chiral_tag` 中，并与原子及边的顺序一起维护。它是 RDKit 的相对顺序标签，不是绝对 R/S 标签；不得直接把 R 或 S 映射为某个整数。
- 上游直接写在原子 ID 尾部的 `_R` / `_S` 绝对 CIP 标记，当前不支持恢复，解析和图验证均明确拒绝，即使同时提供了 `chiral_tag`。不会把它们当作普通 ID 而导出丢失构型的 SMILES。应从含 `@` / `@@` 的 SMILES 经本项目转换器得到完整图及元数据。
- 双键 `===|E|` / `===|Z|` 必须附有一致的 `%%@bond` 元数据，含 `stereo` 和两个有序的 `stereo_atoms` 参考 ID。裸 E/Z 标签、标签与元数据矛盾、参考原子缺失都会拒绝。由本项目 SMILES 转换器生成的 E/Z 图可保真往返；手写元数据仍须通过严格结构验证。
- 当前编辑 API 不能重新指定四面体构型；影响已标记中心邻接关系或氢数的编辑会拒绝。仅删除后重新添加同一邻接键时，会根据邻接顺序调整相对标签以保留原构型。
- 不支持的立体类型、聚合物与 Markush 语法不能作为本项目最终分子图输入。未指定立体化学的分子保持未指定状态，不通过谱图评分自动补定构型。

## 编辑与验证

允许 `set_bond`、`remove_bond`、`set_atom`，后者只修改氢、电荷、自由基与芳香标记。重原子添加、删除和元素替换不允许；操作数量没有上限。完整操作组在副本上执行，最后统一检查。

严格输出验证要求：

- 所有节点/边语法合法，ID 唯一，无悬空边或自环。
- 重原子组成与初始图一致；母体中性、单连通、闭壳层。
- 完整 RDKit sanitization 通过，氢数与自由基状态没有被悄悄修改。
- 显式同位素在当前 RDKit 同位素质量表中有正质量值；未知同位素保留为待修复图，但拒绝最终输出和质量计算，不能回退为普通元素质量。
- 导出 SMILES 可再次严格解析，结构和分子式一致。
- MoleCode 往返保留原子/键属性和分子身份。

碎片自由基不采用母体闭壳层限制，而在独立碎片证据结构中记账。当前不支持需要重新指定四面体立体中心连接的编辑，工具会明确拒绝；不会通过删除立体标记来放行。

## 输出不变量

非空 `final_smiles` 必须通过所有最终检查。未修复成功的输入仍保留在十条来源记录中，但不输出非法字符串作为最终分子。输出合法率与修复成功率分别报告：预算内未修复成功不等于允许非法结果通过。

## 独立离子与产物验证

[ion_validation.py](../src/gcms_agent/fragmentation/ion_validation.py)为碎片假说提供独立检查，不改变母体中性、连通、闭壳层的要求。

- `validate_fragment_ion` 检查离子图的连接、价态、氢/自由基保真、电荷和电子奇偶性，以及 SMILES/MoleCode 往返。
- `validate_fragment_products` 检查单电离后的离子与中性产物图、母体原子 ID 分配、元素/同位素及氢守恒、申报键变化与实际图变化。

稳定 ID 表示申报的图间对应关系，不证明真实反应的原子轨迹；隐式氢变化只表示净变化。同位素氢须作为显式原子保存。验证器不会为缺失产物创造原子映射，也不把化学计量可行性升级为实验机制确认。

验证结果本身不自动注册为正式计分规则。当前计分通道及限制见[评分说明](scoring.md)和[规则条件](../resources/ei_rules/CONDITIONS.md)。

## 可重复检查

[教学案例](../skills/molecode-graph-reasoning/examples/)使用独立构造的结构检查图语法、结构事实和编辑结果；[验证脚本](../scripts/validate_molecode_skill.py)读取实际 Markdown 与 fixture，默认 pytest 已包含该检查。运行方式和验证边界见[测试说明](../tests/README.md)。这些检查不测量真实模型的结构识别准确率。
