# 语法来源与本地适配

上游来源：[AtomFlow-AI/MoleCode](https://github.com/AtomFlow-AI/MoleCode)。历史适配记录中的本地参考位置为同级 `MoleCode-main/.claude/skills/molecode`，该位置不是本项目的运行依赖，使用者无需准备此目录。
本地保留的上游许可文件为 [UPSTREAM_LICENSE.txt](../UPSTREAM_LICENSE.txt)（MIT），本次整理保持原文不变。现有记录未注明所参考的上游 commit 或 tag，因此不能据此定位一个确定的上游版本；下述行为描述限于历史核对记录，不代表上游现状。
借鉴内容：Subgraph–Node–Edge 语法、稳定原子 ID、显式氢、图优先编辑、Kekulé 与立体标记。

本项目改变了执行方式：原技能允许文件编辑、增删原子和通用分子任务；本项目只开放受控工具，并按用户要求保存重原子组成。原技能的转换/validate 命令作为参考，其宽松行为不能代替独立的化学与往返验证。

技能及参考文件由宿主计算 SHA-256 并记录到运行结果；升级后需要重新通过回归测试。

## 2026-11-03 阅读与适配

2026-11-03 的适配记录记载核对了上游 `AGENTS.md`、中英文 README、`docs/01-overview.md`、`docs/02-syntax.md`、`docs/05-tasks.md`、`docs/06-why-it-works.md`、小分子双向转换代码、`molecule_system_prompt.py`、内置 skill 及完整语法，以及理解/编辑/推理示例。借鉴其显式节点与边、稳定 ID、图优先编辑及“文法 + 图 + 验证”的教学方式。

以下保留当时的适配差异说明；本次目录整理未重新核验上游现状：

- 上游 `docs/02-syntax.md` 的简化乙醇图使用 `[C]`，不能作为本项目显式氢教学图；这里必须写 `[CH3]` / `[CH2]`。
- 上游 `molecode/prompts/molecule_system_prompt.py` 中仍有 `<-->` 不支持和 R/S 直接映射 CW/CCW 的旧文字；当时核对的上游代码已支持芳香键并按 CIP 恢复。本项目明确接受芳香键，以自身元数据保存立体信息，拒绝直接 `_R/_S` 后缀。
- 上游宽松解析/隐式补氢行为不能代替本项目显式氢、化学 sanitization 与往返检查。局部标签不等于官能团，整体中性也不等于每个原子电荷为零。
- 原仓库的新增甲基、反应产物、Markush 与聚合物示例不属于本项目受控 MCP 操作范围。

新增案例均为独立构造的教学结构，不读取当前测试集或评估标签。`scripts/validate_molecode_skill.py` 核验教学图、邻接/环/官能团事实与编辑；这些离线检查不等于 DeepSeek 读图能力或 GC–MS 识别准确率。上游论文报告的准确率/token 优势也不直接移用于本项目。
