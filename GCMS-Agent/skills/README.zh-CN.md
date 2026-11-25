# Agent 技能目录

[English](README.md) | **简体中文**

本目录是 GC–EI–MS Agent 的运行时提示资料，由 Python 宿主显式加载。它不是需要用户另行安装到个人助手目录的插件包。

## 技能职责

| 技能 | 职责 | 配套资料 |
|---|---|---|
| [gcms-input-audit](gcms-input-audit/SKILL.md) | 输入、原始峰强与元数据边界 | 无独立参考文件 |
| [molecode-graph-reasoning](molecode-graph-reasoning/SKILL.md) | 分子图语法、邻接与结构读取、编辑边界 | 3 份参考、3 份教学示例、上游许可 |
| [ei-fragment-evidence](ei-fragment-evidence/SKILL.md) | 规则支持证据、守恒与未解释机制 | 证据边界、硅规则边界 |
| [constrained-structure-edit](constrained-structure-edit/SKILL.md) | 试验、正式提交、验收和停止 | 批审协议、验收与停止 |
| [rerank-and-explain](rerank-and-explain/SKILL.md) | 分数、并列 Top1 和解释边界 | 无独立参考文件 |

## 加载与调用

[SkillLibrary](../src/gcms_agent/harness/skill_loader.py) 固定加载上述五份 `SKILL.md`，正文经 [prompts.py](../src/gcms_agent/harness/prompts.py) 拼入系统提示词。YAML front matter 也随文件全文进入提示词，并非自动发现或路由机制。

每个技能的 `references/` 与 `examples/` 下直接包含的 Markdown 文件注册为 `read_reference` 可用资料。当前共 10 份，调用使用相对 `skills/` 的完整 `reference_id`，例如 `molecode-graph-reasoning/references/syntax.md`。参考正文不会全部预先放入提示词；模型可根据已公布的 ID 按需读取。保持既有目录与文件名可避免破坏引用。

本 README 和 `UPSTREAM_LICENSE.txt` 不会作为技能正文或 `read_reference` 资料加载。宿主的运行指纹仍覆盖 `skills/` 中的文件，因此维护文档也会改变版本指纹。

## 工具与科学边界

- 默认批审使用 `analyze_proposals` 试验副本、`submit_reviews` 正式提交。一次分析调用每候选最多三个替代提案；一次会话可在额度内多次分析；最终每候选最多提交三个试验引用。单个提案的原子编辑操作项数不限。
- 旧单候选模式使用 `validate_proposal` / `analyze_proposal` / `submit_review`。教学 JSON 展示操作参数，批审调用需按示例开头说明补全外层结构；工具 schema 以当前会话为准。
- 分数是规则支持的原始强度覆盖率，不是身份正确概率。最高分并列时，来源候选数量参与展示 Top1 的选择，不增加证据分，也不取消并列关系。
- 接受新的合法探索结构不要求分数提高；宿主分别保存当前探索图和历史最佳结果。实际验收、排序与停止由程序决定，技能文字不替代程序检查。
- 教学结构不含实验谱峰，不提供当前案例的质谱证据；不得将实验答案或评估标签写入技能。

## 来源与维护

MoleCode 的历史来源与本地适配见 [provenance.md](molecode-graph-reasoning/references/provenance.md)，保留的上游许可见 [UPSTREAM_LICENSE.txt](molecode-graph-reasoning/UPSTREAM_LICENSE.txt)。历史本机参考目录不是运行依赖；未记录的上游 commit/tag 不推测补填。

维护提示词时应同步核对工具字段、分数语义、提案额度和停止条件。不要仅为文档语言统一而直接翻译运行时技能；正文或按需参考的文字变化可能影响模型行为，应与普通项目说明的翻译分开验证。

## 离线验证

在项目根目录执行，明确使用当前仓库源码，避免已有环境导入其他项目副本：

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
./.venv/Scripts/python.exe -B scripts/validate_molecode_skill.py
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/integration/test_harness.py tests/integration/test_batch_tools.py tests/unit/test_ranking_interpretation.py tests/unit/test_top1_report.py tests/unit/test_nitrile_rules_and_exploration.py tests/end_to_end/test_case_batch.py
```

教学验证程序读取三份示例中的分子图与 JSON，核验 11 个案例的结构事实、往返保真及预期接受/拒绝结果，不调用模型、不读取实验标签。通过这些检查说明教学资料与工具兼容，不能据此断言模型理解能力或识别准确率提升。

修改后应使用新运行目录，并保留旧实验的原始结果及版本校验。已有输出提示词快照不会自动更新，运行时以源码和本目录实际拼接内容为准。
