---
name: ei-fragment-evidence
description: Interpret GC–EI–MS peaks through verified atom-mapped fragmentation hypotheses and distinguish mass matches, isotopes and unresolved mechanisms.
---

# EI 谱峰证据

最高强度峰不必是分子离子峰，分子离子可以弱或缺失。用母体质量提出假设，再检查真实碎片路径，不凭一个峰直接断言分子式或结构。
每项计分解释必须返回规则 ID、母体保留原子、断键、离子式、电荷/自由基、理论 m/z、观测峰及守恒检查结果。操作必须来自实际图。
先从 MoleCode 的边定位断键和保留原子，再读取工具证据。环内一条键断裂可能只开环而不产生两个分离片段；官能团存在也不等于对应碎裂通道必然发生。
氢迁移必须有供体、受体及允许的路径；中性丢失必须有对应原子和氢，不能只减一个质量差。未通过规则适用性与守恒验证的解释不得计分。
使用当前会话的分析工具（批审 `analyze_proposals`，单候选 `analyze_proposal`）查看规则和覆盖。紧凑返回用证据 ID 引用完整路径，需要核对断键/守恒细节时按 ID 查询；已有证据不必反复重抄。工具未实现的重排可以作为待验证假说说明，不得自报 verified 或加分。
批审 `evidence.paths` 的每行按 `path_columns` 解释，含证据ID、观测峰、规则、离子式、断键和步骤数。编辑说明引用具体行的真实断键与峰，不把同一分子中另一条键的碎片证据移过来。
同名义质量可能有多种离子式。通过守恒只说明假说符合约束，不等于实验确认形成机制或峰强预测。
当前新增 `vinyl_nitrile_hcn_loss`（非环烯基腈、有 beta-H，脱 HCN 并将局部双键变三键）与 `aryl_nitrile_cn_loss`（芳基上末端腈基脱 CN 自由基）。这些是文献支持的母体通道向局部结构模板的扩展；具体取代物和原子映射仍为假说，完整路径含来源与局限。不适用于任意含氮结构或任意连续 HCN 丢失。
`isotope_hypothesis` 仅提示可能的同位素位置；没有数值拟合的观测/预测比例、误差及验证结果，不能说“M+1比例支持此结构”。`used_for_scoring=false` 的注释不得计为新增证据。未指派峰即使出现在 `peak_refs` 里，也不能称为工具已验证的环断裂或重排峰。
“主要通道”“主导碎片”需要丰度模型支持；规则路径存在本身不提供这类结论。需要命名或共轭判断时先核对最终图上的原子与键，无法验证就使用规范SMILES及明确的键操作描述，避免附加确定命名。
解释前按需读取 `ei-fragment-evidence/references/evidence-boundaries.md`。

当前硅规则 `tms_methyl_radical_loss` 与 `tms_cation_cleavage` 只适用于工具识别的完整 TMS 局部环境，分别从真实 Si–CH3 断键和 Si–X 断键生成路径。产物图、原子映射和守恒已检查，但 N/S/饱和C 环境是文献启发的局部扩展，不代表具体机制已被实验确证。不得把 73 当作所有含 Si 结构的自动支持，也不得直接用 M−15 代替断键验证。59/75/147 的重排尚未实现；相同支持峰不区分异构体，不重复计分。详见 `ei-fragment-evidence/references/silicon-boundaries.md`。
