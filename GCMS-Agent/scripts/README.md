# 脚本使用说明

[English](README.en.md) | **简体中文**

本目录提供数据准备、离线检查、真实模型验证和历史实验工具。常规批量推理、基线和独立评估使用项目 CLI，见[项目中文首页](../README.zh-CN.md)。脚本不会被默认工作流自动执行。

## 运行前提

先安装项目运行依赖；测试还需要 pytest。以下命令在项目根目录的 PowerShell 中执行，显式指定本仓库源码，避免已有环境导入其他目录的项目副本。

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

先按[根目录安装说明](../README.zh-CN.md#从零安装)建立独立环境并可编辑安装项目。示例使用该 `.venv` 的 Python，也可替换成安装了依赖的解释器。数据文件、历史实验结果和本地凭据需自行准备；仅取得源码不代表这些输入已经存在。

## 离线数据与化学工具

以下脚本不调用模型，但部分会写文件。

| 脚本 | 输入与前提 | 输出与边界 |
|---|---|---|
| [prepare_development_cases.py](prepare_development_cases.py) | 已准备的数据目录及原始源文件；核对源文件哈希。默认数据目录为 `data/raw_linear_v1/`，源文件为项目上级的 `test.json` | 写入单例输入、单例标签、工程子集及选择清单；会覆盖对应子集文件。单例默认索引 0，工程子集为 85、120、294、313，均从 0 开始。读取标签用于导出，选样按固定索引进行 |
| [economy_pilot.py](economy_pilot.py) `prepare` | 隔离后的无标签推理输入；按候选数、峰数、重原子数和合法性分层抽样 | 写指定输出 JSON 和同名 `.manifest.json`；默认 20 条、随机种子 20261103，不执行后续推理 |
| [economy_pilot.py](economy_pilot.py) `report` | 一个或多个运行目录中的 `logs/budget.json`、结果与运行清单；可传抽样清单 | 打印费用报告，可用 `--output` 保存。费用外推有完整性与用量条件；默认总体规模 1490，可用 `--population` 修改；使用代码中的价格快照，不是实时账单 |
| [validate_dataset.py](validate_dataset.py) | 无标签推理输入 | 检查候选合法性和分子图往返，默认写 `runs/dataset_validation.json`；用 `--output` 改路径 |
| [validate_molecode_skill.py](validate_molecode_skill.py) | 仓库内实际技能 Markdown 与 `tests/fixtures/molecode_skill_cases.json` | 打印教学案例验证结果，不写实验结果；验证文档与工具一致性，不衡量模型推理准确率 |
| [validate_silicon_rules.py](validate_silicon_rules.py) | `--input`、`--labels`、`--old-policy`、`--output` 均必填；新旧规则的新增启用项必须恰好是脚本预期的含硅规则 | 在新目录写规则副本、选择清单、逐例比较和汇总；包含历史 case870 开发集约定。标签仅用于离线诊断，结果不是独立外部基准 |

除明确拒绝已有目标目录的脚本外，输出文件可能被覆盖；运行时选用适当的新输出路径。开发子集与历史开发案例不能当作未接触的留出测试集。

```powershell
# 只验证技能教学示例，不依赖完整实验数据。
./.venv/Scripts/python.exe -B scripts/validate_molecode_skill.py

# 查看有参数解析器的入口，不运行数据准备或验证。
./.venv/Scripts/python.exe -B scripts/prepare_development_cases.py --help
./.venv/Scripts/python.exe -B scripts/economy_pilot.py prepare --help
./.venv/Scripts/python.exe -B scripts/economy_pilot.py report --help
./.venv/Scripts/python.exe -B scripts/validate_dataset.py --help
./.venv/Scripts/python.exe -B scripts/validate_silicon_rules.py --help
```

## 本地模拟服务检查

| 脚本 | 检查内容 | 运行条件与输出 |
|---|---|---|
| [validate_thinking_transport.py](validate_thinking_transport.py) | SDK/CLI 实际发出的 thinking、模型、输出限制等白名单字段 | 启动 `127.0.0.1` 模拟 Messages 服务，使用假凭据；需要可运行的 Claude Agent SDK/CLI。打印结果，可用 `--output` 保存 |
| [validate_progress_watchdog.py](validate_progress_watchdog.py) | 输出截断、无进展、会话字符上限等中断场景 | 同样使用本地模拟服务及假凭据；需要 SDK/CLI、子进程和本地监听权限。打印结果，可用 `--output` 保存 |

两者不调用真实模型服务，但与只使用 Python 模拟对象的默认 pytest 不同，会启动实际 CLI 子进程。检查结果反映本机安装版本的传输和取消行为，不是模型质量或实际费用结论。

## 真实模型入口

以下入口会发起真实模型请求，需要 `DEEPSEEK_API_KEY`，可能产生费用。运行前检查[配置说明](../configs/README.md)及本地模型、服务和预算设置。

| 脚本 | 用途与输入 | 输出与配置 |
|---|---|---|
| [probe.py](probe.py) | 使用内置乙醇案例检查真实工具调用链 | 固定写入 `runs/probe/result.json`，重复运行会覆盖；单次回复上限 8192 tokens，最多 8 工具轮次，直接调用 Harness，不经过批量预算与时段调度 |
| [run_single_miss.py](run_single_miss.py) | 读取当前数据目录中的固定单例与选择清单，核对源文件及输入哈希后运行 | `--output` 必填，采用 economy 配置；支持 `--thinking`、`--max-output-tokens`、`--allow-peak`。预测冻结后才加载标签评估；文件名沿用历史命名，当前准备工具不按真值重新筛选 |
| [validate_economy_profile.py](validate_economy_profile.py) | 读取当前数据目录中的 `engineering_pilot.json`，默认由四条开发案例构成 | `--output` 必填，采用 economy 配置；可用 `--budget-cny` 指定正数费用上限。输出运行结果和 `engineering_report.json`，不据此宣称全量准确率或费用达标 |

后两个入口支持 `--data-root` 和 `--ask-key`，并拒绝目标目录中已有 `manifest.json` 的情况；应使用新输出目录。未显式指定费用上限时，以配置为准，不能仅凭脚本名称推定存在固定金额上限。

`probe.py` 没有命令行参数解析器，**不要用 `probe.py --help` 检查帮助**，它仍会执行真实调用。查看其说明应阅读本文或源码。`validate_molecode_skill.py` 也没有参数解析器，直接执行的是离线验证。

## 公开案例导出

[export_case_studies.py](export_case_studies.py)仅处理已获准公开的三个固定案例，从本地冻结记录生成 [case_studies/](../case_studies/README.md)。原始 `runs/` 必须存在；脚本先核对冻结哈希，按字段选择材料，去重科学对象并保留所有已记录试验和未完成状态。不调用模型、不重新评分，也不读取整个私有数据集或会话工作区。

需要项目 Python 环境、RDKit 和 Pillow。首次导出要求目标目录不存在；`--check` 在内存中重建并与现有公开文件逐字节比较，不覆盖文件。跨平台字体/绘图依赖可能改变图片字节，严格比较适用于相同导出环境。

```powershell
./.venv/Scripts/python.exe -B scripts/export_case_studies.py --check
```

公开读者可直接查看案例，无需持有原始运行目录。`provenance.json` 中的原始文件哈希用于来源追溯，不代替历史源码快照或完整模型实验复现。

## 历史归档

- [pilot20_20261103_04](archive/pilot20_20261103_04/README.md)：10 个早期预算、运行、报告和质量审计脚本，部分运行入口可产生费用。
- [legacy_checks](archive/legacy_checks/README.md)：旧回执回放与 case84 固定结果重排，均不调用模型，但依赖当前不存在的历史文件。

归档是用途分类，不表示这些脚本与当前协议兼容，也不表示恢复了历史输入。此次整理只适配移动后的项目路径，并为 case84 重排补充主入口；历史原始源码应以实验冻结快照为准。公开版本另将两份验证脚本的私有案例编号默认值改为显式本地选择参数，详见归档说明。

## 测试依赖

`economy_pilot.py`、`prepare_development_cases.py`、`validate_molecode_skill.py` 以及归档中的 `run_economy_validation.py` 仍被测试调用。测试使用构造数据、临时目录或函数级检查，不会运行归档的付费主入口。对应关系见[测试说明](../tests/README.md)。

```powershell
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
```
