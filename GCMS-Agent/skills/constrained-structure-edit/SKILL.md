---
name: constrained-structure-edit
description: Propose atom-addressed multi-operation molecular edits under heavy-atom conservation, with validated exploration and separate historical-best retention.
---

# 化学修复与证据优化

保持各元素重原子数量、稳定原子身份；最终母体中性、连通、价态合法。允许键级、连接、环拓扑和配套氢/电荷修改；禁止添加、删除或替换重原子。没有编辑项数上限。
初始非法候选的第一目标是得到严格合法结构；没有强峰支持仍可保存为低分结构。已合法版本允许探索同分、降分及未新增支持峰的不同合法结构；证据计算必须完整。宿主分别保存当前探索图与历史最佳图，最终输出历史最高点分结构，同分保留较早结构。探索获准不代表证据改善；仍须提出有依据的假设并避免重复结构。
批审的一次 `analyze_proposals` 调用中，每候选至多三个替代提案；同一会话可在工具额度允许时多次分析，最终 `submit_reviews` 中每候选至多提交三个试验引用。各次试验都基于该候选在当前会话中的同一图快照；试验数量、正式提交数量与单个提案的操作项数分别计数。旧单候选模式的一次正式审阅提交仍至多包含三个替代提案。先充分比较原结构与有依据的替代假设，检查每个提案最终图的连接、键级、显式氢与电荷；有多个值得区分的假设时可同时试验，不预设只试一个，也不为填满额度编造方案。批审 `analyze_proposals` 同时验证和分析副本，评估反馈后由 `submit_reviews` 引用合格 `trial_id` 正式提交，宿主下一轮继续审查新图。试算本身不提交编辑。
`analyze_proposals` 的原始操作提案带 `candidate_id`、`base_version`、`operations`；`base_version` 精确等于输入 `graph_version`。批审最终 `submit_reviews` 的 `reviews` 数组中，每个候选条目的 `proposals` 是试验引用，只含 `trial_id`、`reason`、`peak_refs`、`operation_explanations`，不要再填 `base_version` 或 `operations`；宿主从试验记录恢复并核验版本。单候选旧接口的原始操作提案仍须携带 `base_version`。
逐项说明绑定真实原子 ID、邻接和前后变化，区分工具路径支持、待验证假说与配套价态修正；每项用短句，不重抄 patch 或给分子命名。未知氢、芳香或立体边界按 MoleCode skill 的参考路由核对，不通过省略氢或元数据使验证通过。
一次失败不否定整个替代假设；在剩余额度内针对工具错误修正。工具反馈 `preferred_action=submit_reviews` 时优先提交，`submit_only=true` 时不再探索或查询，提交后 `end_session` 时结束。未完成的检查如实留空，暂时 keep 不等于审阅完成；由宿主决定续轮或资源终止。完整验收与停滞条件见 `constrained-structure-edit/references/acceptance-and-stop.md`。
批审工具签名以当前会话 schema 为准；遇到试验 ID、批量提交或提前停止问题，读 `constrained-structure-edit/references/batch-review.md`。保留仅代表本次受限搜索的结果，不代表结构已被实验确认。
