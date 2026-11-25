# 固定教学案例

[English](README.md) | **简体中文**

[molecode_skill_cases.json](molecode_skill_cases.json) 保存 11 个独立构造的 MoleCode 教学案例，服务于图语法、结构读取及编辑边界的程序验证。文件声明不包含实验谱图、实验评估标签或由 LLM 给出的预期答案。

## 字段含义

| 字段 | 用途 |
|---|---|
| `schema_version`、`purpose` | fixture 格式版本与用途声明 |
| `cases[].id` | 与 Markdown 中 `<!-- case:... -->` 对应的案例 ID |
| `source_smiles` | 初始合法结构的独立结构参照；不是未知样品的实验真值 |
| `document`、`title` | 案例所属教学文档和名称 |
| `molecode` | 含完整元数据的参照分子图 |
| `initial_valid` | 初始结构预期是否合法；部分案例故意含有错误 |
| `facts` | 预期元素组成、邻接、环、官能团、同位素或立体属性等 |
| `trials` | 操作参数及预期合法性、产物结构或拒绝原因 |

三份对应文档位于 [技能示例目录](../../skills/molecode-graph-reasoning/examples/)：

- [topology.md](../../skills/molecode-graph-reasoning/examples/topology.md)：支链、芳香取代、稠环、官能团及同位素/EZ 读取。
- [transaction.md](../../skills/molecode-graph-reasoning/examples/transaction.md)：键级与氢数联动、未知氢和过价修复。
- [repair.md](../../skills/molecode-graph-reasoning/examples/repair.md)：芳香标记错误、环重连及立体编辑拒绝。

[验证脚本](../../scripts/validate_molecode_skill.py) 读取实际 Markdown 中的分子图和 JSON 操作，检查其与 fixture 一致、图往返保真、结构事实正确以及修改的预期结果。默认 pytest 通过 [集成测试](../integration/test_molecode_examples.py) 调用同一验证函数。

该文件需要随仓库保留，但不是 Agent 的推理输入或可检索谱图库。技能示例没有观测谱峰，不能用这些案例证明真实未知样品的结构或碎裂机制，也不能用通过结果衡量模型理解准确率。

修改教学内容时，先核验结构事实和预期结果，再同步案例 ID、文档图、操作和 fixture；不得把程序当前输出未经核查就写成预期答案。
