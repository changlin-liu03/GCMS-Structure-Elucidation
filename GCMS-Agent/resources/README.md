# 运行资源与历史来源记录

[English](README.en.md) | **简体中文**

本目录同时保存实际读取的 EI 规则策略、规则适用条件说明，以及历史来源与版本记录。它们的作用不同，修改前应先确认相应的加载方式。

## 文件用途

| 文件 | 用途 |
|---|---|
| [ei_rules/rules.json](ei_rules/rules.json) | 运行时规则策略，选择启用模板和枚举资源上限 |
| [ei_rules/CONDITIONS.md](ei_rules/CONDITIONS.md) | 16 类规则的索引、详细条件、来源和能力边界 |
| [versions.json](versions.json) | 历史上游文件路径、SHA-256 和人工记录的依赖/实现修订标签 |

## 实际规则策略

[load_rule_policy](../src/gcms_agent/fragmentation/rule_engine.py) 默认读取本目录的 `ei_rules/rules.json`。规则实现位于 [rule_engine.py](../src/gcms_agent/fragmentation/rule_engine.py) 和 [silicon.py](../src/gcms_agent/fragmentation/silicon.py)；修改描述文字不会改变代码中的化学适用条件。

| 字段 | 当前值或用途 | 修改边界 |
|---|---|---|
| `version` | `ei-conservative-1.3`，记录到证据结果 | 是版本标签；真正的内容差异还需依赖文件哈希 |
| `enabled` | 16 个启用规则 ID | 只能选择代码已注册规则；新增字符串不会实现新机制 |
| `max_steps` | 2 | 加载器允许 1 或 2；不代表任意模板可递归组合 |
| `max_paths` | 10000 | 正整数；限制去重和匹配前生成的路径数，不是峰数 |
| `scope` | 适用范围概述 | 说明性内容，不替代程序约束 |
| `not_implemented` | 未实现机制列表 | 说明性内容，不是另一组启停开关 |
| `references` | 科学背景、局部模板来源与局限 | 不会联网下载文献，也不会动态生成规则 |

超过路径上限时，枚举报告 `evidence_enumeration_limit`；[证据接口](../src/gcms_agent/fragmentation/enumeration.py) 返回 `complete=false`。不应把部分枚举或未完成结果解释成完整零分。规则路径的存在、守恒和名义质量匹配不等于实验确认机制，也不预测碎片强度。

[configs/scoring.yaml](../configs/scoring.yaml) 中的 `fragmentation_depth` 是当前配置加载器的兼容性声明，不会覆盖这里的 `max_steps`。规则变更应同步核对配置约束、实现和验证结果。

默认规则文件是工作流实际读取的策略。`--configs` 选择模型与运行配置目录，不会自动切换到该目录下的规则文件；底层证据函数另有显式规则路径参数。进行规则消融时应明确传入或记录实际规则策略，不能只修改说明文字。

## 历史记录 versions.json

该文件保留 10 条 `source_materials` 记录及人工版本标签，当前没有发现程序读取这些字段来安装依赖或选择运行行为。它不是依赖锁文件，也不是自动生成的当前环境清单。

- `source_materials.*.path` 是历史适配时的本地参考位置。`../MoleCode-main/...` 不是运行依赖，使用者无需在上级目录准备该项目。
- `source_materials.*.sha256` 是原始来源记录，应随历史材料保留。本次整理只检查其格式，未重新取得对应上游文件核验内容，不以当前本地适配文件覆盖这些哈希。
- `rdkit`、`claude_agent_sdk`、`claude_cli` 等值属于该记录，不保证等于使用者当前安装版本。安装依赖定义见 [pyproject.toml](../pyproject.toml) 和 [requirements.lock](../requirements.lock)。
- `score_version`、`molecode_skill_revision`、`review_protocol`、`ranking_reporting_revision`、`thinking_transport_revision`、`execution_revision` 是人工记录的修订标签，不是运行开关。例如标签中的 `explicit-disabled` 不能用来判断当前是否关闭思考，实际设置应查看 [模型配置](../configs/model.yaml) 和 [economy 覆盖配置](../configs/profiles/economy.yaml)。
- 现有来源记录未提供上游 commit/tag，不推测补填。MoleCode 的历史适配说明见 [provenance.md](../skills/molecode-graph-reasoning/references/provenance.md)。

本次仅整理文档，`versions.json` 和 `ei_rules/rules.json` 原样保留；没有将历史标签改写成新的发布版本。

## 版本与恢复

规则加载器按 JSON 文件原始字节计算 `policy_sha256`，证据诊断中记录为 `rule_policy_sha256`。即使只修改空白和换行，也可能改变规则文件哈希。

宿主还通过 [source_versions](../src/gcms_agent/workflow/runner.py) 为 `src/`、`skills/`、`resources/`、`configs/` 中的文件生成运行指纹并写入实验 manifest。这与 `versions.json` 内的历史来源哈希是不同记录；规则原始字节哈希和宿主文件指纹的计算方式也不同，不应直接当作相同字段比较。

本目录说明文档也参与宿主指纹。因此，本次虽然规则策略保持原样，整理后仍应使用新运行目录，保留旧实验结果和恢复版本校验。

## 离线验证

在项目根目录执行，明确导入当前仓库源码：

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/unit/test_evidence.py tests/unit/test_allylic_scope.py tests/unit/test_nitrile_rules_and_exploration.py tests/unit/test_silicon_rules.py
```

这些测试检查已实现规则的结构条件、守恒与边界、策略切换和路径上限等行为，不调用模型。文献链接保留为来源记录；本次目录整理未重新核验文献结论或外部链接可用性。程序测试通过不能证明某条碎裂机制已被实验确证。
