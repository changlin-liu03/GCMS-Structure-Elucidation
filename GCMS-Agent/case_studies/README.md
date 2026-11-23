# 公开案例展示

[English](README.en.md) | **简体中文**

这三个真实历史案例由数据持有者明确授权公开，是私有研究数据的有限例外。其余数据不公开；本目录不授予整个数据集的分发许可。

| 案例 | 看点 | 历史结果边界 |
|---|---|---|
| [2,4-己二烯二腈](hexa-2,4-dienedinitrile/README.md) | 多轮编辑、同分/降分探索、恢复历史最佳 | 历史评分使用分箱强度；保存 Top1 命中，但未完成 |
| [丁酸正丁酯](butyl-butanoate/README.md) | 初次服务失败与重试、生成参考结构 | 参考结构展示第 2；重试也未完成 |
| [三甲基正戊基硅烷](trimethylpentylsilane/README.md) | 含硅结构编辑、同分结构与来源排序 | 参考结构展示第 6、证据并列第 1；未完成 |

先阅读每例中英文 README，再查看结构图和 JSON。英文版翻译编写者的流程说明；模型摘要、理由和逐项操作说明保留原语言，并标为原文记录。三个主要 Agent 阶段合计 24 个有审阅回执的模型会话、62 条工具试验；丁酸正丁酯 初次服务失败另列。全部试验保留，包括拒绝和未选中项，等价来源槽位共用一次计算的历史。

材料是从冻结记录提取的公开展示包，不是可恢复检查点。初始图、操作、逐项说明、正式提交、证据、评分、最终排名、失败和用量都有对应记录。模型说明不是机制确证，不展示或重建隐藏思考正文。当前代码和历史版本可能不同，未为展示重新运行模型或重算分数。

## 文件契约

每例包含 README.md、README.en.md、input.json、reference.json、trajectory.json、result.json、provenance.json 和 trajectory.png。轨迹以 SHA-256 为键共享图、评分和证据路径；所有引用均在同一 JSON 的 objects 中。每例 provenance 包含其他七个公开文件的字节校验和、原来源哈希和冻结核验记录；它不证明未公开原始文件可由读者独立取得。

三个案例的展示输入均使用原始线性强度。2,4-己二烯二腈的展示强度来自 `Agent-Harness/test.json`，其历史轨迹、评分和排名来自分箱强度实验。评估标签独立保存在 reference.json。

## 本地重新导出

导出仅供拥有相应原始 runs 的维护者使用。需要项目 Python 依赖、Pillow 和原始强度文件。脚本默认在项目根目录的 `../../../Agent-Harness/test.json` 查找该文件，也可用 `--raw-source` 指定位置。

```powershell
# 首次生成：目标目录必须不存在。
./.venv/Scripts/python.exe -B scripts/export_case_studies.py
# 已有展示包：在内存中重建，并逐文件比较，不覆盖发布文件。
./.venv/Scripts/python.exe -B scripts/export_case_studies.py --check
```

图片按本机 RDKit/Pillow 与字体生成，跨环境渲染字节可能不同；--check 的逐字节校验适用于相同导出环境。公开读者无需原始 runs 即可查看完整展示材料，读取方法见各案例 README。运行当前软件的无私有数据演示见[人工示例](../data/examples/README.md)，技术契约见[文档导航](../docs/README.md)。
