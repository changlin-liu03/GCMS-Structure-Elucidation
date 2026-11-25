# 测试说明

[English](README.en.md) | **简体中文**

本目录验证 GC–EI–MS Agent 的化学处理、评分、工具接口及运行流程。默认测试使用构造样本、临时目录、模拟 SDK 或注入的模拟客户端，不需要真实 API 密钥、模型调用或完整实验数据集。

## 目录职责

| 目录 | 覆盖范围 |
|---|---|
| [unit/](unit/) | 分子图与化学约束、EI 规则、原始强度和评分、配置、预算、记忆及报告等局部行为 |
| [integration/](integration/) | 工具协议、Harness/SDK 交互、批审提交、流式监测及教学案例一致性 |
| [end_to_end/](end_to_end/) | 使用模拟模型的工作流、批次并发、资源终止、检查点恢复和独立评估 |
| [fixtures/](fixtures/README.md) | 固定教学结构和预期结果，不是运行时推理输入 |

部分 unit 测试会组合多个模块检查回归行为；目录名称不表示已经测量或达到某种代码覆盖率。`pytest` 函数与 `unittest.TestCase` / `IsolatedAsyncioTestCase` 共存，由 pytest 统一收集。

## 运行方式

先按[根目录安装说明](../README.zh-CN.md#从零安装)建立独立环境，并可编辑安装 `.[test]`。在项目根目录执行；现有 `.venv` 必须具备这些依赖。`pyproject.toml` 已配置 `pythonpath = ["src"]` 和 `testpaths = ["tests"]`，以下同时明确当前进程的源码路径，便于独立脚本也使用同一仓库。

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

# 完整离线回归。
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider

# 只收集测试；这不等于执行通过。
./.venv/Scripts/python.exe -B -m pytest --collect-only -q -p no:cacheprovider

# 单独检查教学文档和工具是否一致。
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/integration/test_molecode_examples.py
```

也可以将最后一个路径替换为某个测试目录或文件。完整依赖安装方式与平台支持范围由项目安装文档维护；当前本机测试通过不代表所有操作系统均已验证。

## 关键测试与保留原因

| 测试 | 作用 |
|---|---|
| [test_chemistry.py](unit/test_chemistry.py) | 分子图、SMILES/MoleCode 往返、重原子约束和立体信息 |
| [test_evidence.py](unit/test_evidence.py)、[test_allylic_scope.py](unit/test_allylic_scope.py)、[test_silicon_rules.py](unit/test_silicon_rules.py) | 碎裂规则适用范围、守恒、策略与枚举上限 |
| [test_n_aromatic_diagnostics.py](unit/test_n_aromatic_diagnostics.py) | 防止已移除诊断通道重新参与评分，并验证离子/产物检查；不是可删除的废弃功能测试 |
| [test_nitrile_rules_and_exploration.py](unit/test_nitrile_rules_and_exploration.py) | 腈规则、同分/降分探索与历史最佳结果保留 |
| [test_compact_views_v2.py](unit/test_compact_views_v2.py) | 当前紧凑模型上下文的信息保真；文件名的 v2 不表示已停用 |
| [test_ranking_interpretation.py](unit/test_ranking_interpretation.py)、[test_top1_report.py](unit/test_top1_report.py) | 并列选择、来源数量、证据局限与报告 |
| [test_config.py](unit/test_config.py)、[test_credentials.py](unit/test_credentials.py) | 自定义配置目录和本地凭据读取边界 |
| [test_batch_tools.py](integration/test_batch_tools.py) | 试验引用、候选/版本隔离、提交完整性与停止验收 |
| [test_molecode_examples.py](integration/test_molecode_examples.py) | 将真实教学 Markdown 和 JSON 操作的验证接入默认 pytest |
| [test_recovery_and_evaluation.py](end_to_end/test_recovery_and_evaluation.py) | 版本/输入恢复约束、预算恢复、持久化与评估 |

旧单候选模式仍受支持，对应测试应保留。测试通过说明被检查的程序行为符合断言，不意味着模型正确识别了真实未知样品或实验确证了碎裂机制。

## 对脚本与资料的依赖

测试集不是只复制 `tests/` 就能独立运行的文件包，需要相应的项目源码、配置、技能和规则资源。

| 使用位置 | 依赖 | 测试用途 |
|---|---|---|
| [test_economy_budget.py](unit/test_economy_budget.py) | [economy_pilot.py](../scripts/economy_pilot.py) | 无标签试跑样本选择与费用报告 |
| [test_validation_budget_report.py](unit/test_validation_budget_report.py) | [run_economy_validation.py](../scripts/archive/pilot20_20261103_04/run_economy_validation.py)、[economy_pilot.py](../scripts/economy_pilot.py) | 历史阶段共享预算、OS 文件锁及费用报告 |
| [test_raw_intensity.py](unit/test_raw_intensity.py) | [prepare_development_cases.py](../scripts/prepare_development_cases.py) | 使用临时构造数据核验开发子集准备 |
| [test_molecode_examples.py](integration/test_molecode_examples.py) | [validate_molecode_skill.py](../scripts/validate_molecode_skill.py)、[教学 fixture](fixtures/molecode_skill_cases.json)、[技能示例](../skills/molecode-graph-reasoning/examples/) | 文档图、结构事实、工具操作和预期接受/拒绝结果 |

历史预算测试只调用预算及锁函数，不执行归档脚本的付费运行入口。整理 `scripts/archive/` 时需要同步处理这项依赖，不能直接删除脚本或通过删除测试掩盖问题。

模型集成测试将 SDK 替换为模拟实现，工作流测试注入模拟客户端。测试中出现的 API key 字符串是测试占位值；临时生成的标签和结果只用于验证隔离及评估逻辑，不作为真实模型效果数据。

## 教学案例与验证范围

[教学 fixture 说明](fixtures/README.md) 记录数据用途。当前包含 11 个案例，通过一个集成测试调用现有验证函数检查三份教学文档；因此它是一个 pytest 测试项，不是 11 个独立 pytest 项。

独立脚本入口仍可使用：

```powershell
./.venv/Scripts/python.exe -B scripts/validate_molecode_skill.py
```

新增教学案例时，应独立确定结构事实及预期结果，再同步文档与 fixture。不要把运行输出直接当作正确答案，也不要为了让回归通过而放宽断言。

真实 API 的可用性、模型识别效果、真实用量及费用需要独立实验评估；不能用模拟调用测试的通过数量代替这些结论。

## 公开案例导出检查

[test_case_study_export.py](unit/test_case_study_export.py)使用临时构造文件验证冻结哈希、路径边界、敏感字段、未知费用与原子编号去重。另检查已公开 [case_studies/](../case_studies/README.md) 的文件哈希、对象引用、全部试验数量、来源槽位与未完成状态；不读取私有 `runs/` 或完整研究数据。

维护者持有原始两例运行目录时，还可执行 `scripts/export_case_studies.py --check`，从原始冻结记录重建公开包并逐字节比较。这项源记录复核不加入默认 pytest，因为公开仓库不包含原始运行目录。

## 缓存与维护

`__pycache__/`、`.pyc` 和 pytest 缓存不是测试源码，可重新生成。本目录不保存实验日志或测试输出；测试产生的文件使用临时目录。不要为消除失败而删除用例、无理由跳过测试或取消原有断言。
