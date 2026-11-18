# 历史离线检查脚本

这两个脚本从 `scripts/` 根目录归档到此处，用于保留旧实验的检查方法。它们不调用模型，但依赖当前工作区中不存在的历史输入，不能作为当前数据的验证入口。

| 脚本 | 历史用途 | 固定输入与输出 |
|---|---|---|
| [validate_economy_replay.py](validate_economy_replay.py) | 将旧候选提案回放到批审工具和工作流，比较最终结构与评分；不读取评估标签 | 输入：`runs/single_top10_miss_nothinking_v2/` 的检查点和旧 `data/inference/single_top10_miss.json`；输出：`runs/economy_offline_validation/replay.json` |
| [rerank_case84_source_count.py](rerank_case84_source_count.py) | 对 case84 冻结预测按候选来源计数重新排序，核对预测内容未变，冻结新输出后独立评估 | 输入：`runs/case84_20261113_validation/` 中的输入、标签及 `agent/` 结果、冻结清单；输出：新建 `runs/case84_20261113_count_rerank/` |

路径均相对项目根目录，而不是本归档目录。回放输出可能被覆盖；case84 重排拒绝已存在的目标目录。两者均无命令行参数解析器，`--help` 不会显示帮助，而是执行主流程；请阅读源码了解约定。

此次移动将项目根定位改为 `Path(__file__).resolve().parents[3]`；case84 的文件读写与重排已放入 `main()`，仅直接运行时执行，导入时不会处理实验文件。其冻结哈希检查、固定预期结构、来源数量断言及评分/证据不变检查均保留。

这些是依赖特定历史协议和结果的检查程序，而非通用的当前版本重放工具。即使补回旧文件，也需要确认数据、协议、规则和运行环境兼容；整理本身没有验证历史实验可以重现，也没有更改断言来适配当前结果。历史逐字节原版应查对应实验的冻结源码快照。

当前工具入口见[脚本说明](../../README.md)。
