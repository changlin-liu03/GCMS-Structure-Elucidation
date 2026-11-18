# DiffGCMS 实验复现说明

[English](reproducibility.md) | 简体中文

本文说明实验记录、运行配置、预测输出和历史权重的使用方式。训练与测试命令见 [README](../README.zh-CN.md)，输入字段见 [数据格式](data.zh-CN.md)。

## 保存实验记录

为使代码、权重和结果能够对应，每次实验应保存以下信息：

| 信息 | 建议记录内容 |
|---|---|
| 代码版本 | Git commit 或对应的代码快照 |
| 软件环境 | Python、PyTorch、CUDA、PyG、Lightning、Transformers、RDKit 等版本 |
| 运行硬件 | GPU 型号与数量、显存及操作系统 |
| 数据与预处理 | 数据版本、划分依据、样本数、去重与谱图处理规则 |
| 统计量 | 四个统计文件及其来源训练集 |
| 运行配置 | Hydra 保存的完整配置和命令行覆盖参数 |
| 模型权重 | 编码器来源、阶段初始化权重、最终测试 checkpoint |
| 评估设置 | 测试集合、候选数量、扩散步数及指标定义 |

依赖版本应从实际训练环境导出。例如，在该环境中运行：

```bash
python --version
python -m pip freeze > environment-freeze.txt
```

使用 GPU 时，可另外保存 `nvidia-smi` 显示的设备与驱动信息。Hydra 将本次运行配置保存到输出目录的 `.hydra/` 中，CSV 日志位于 `logs/` 下。

## 训练阶段与 checkpoint

冻结编码器训练和最后四层微调是两个独立阶段：

- 冻结阶段使用 `config_train`，训练投影层和图解码器。
- 微调阶段使用 `config_train_last4`，通过 `general.load_weights` 加载冻结阶段选定的权重，再训练编码器最后四层、投影层和图解码器。
- 同阶段续训使用 `general.resume`，保持原阶段的配置。
- 测试使用 `config_test` 和 `general.test_only` 指定实际 checkpoint。

默认按 `val/NLL` 选择训练 checkpoint。实验记录中应写明最终测试权重的路径和选择依据。训练和测试均需准备三个数据划分、训练集统计量及独立的 SpecTUS 编码器目录。

## 候选输出与指标口径

本说明采用默认单 GPU 测试流程。测试 JSON 位于运行目录的 `preds/<运行名称>_predictions.json`，每条记录包含：

- `input_ids`、`position_ids`：原始谱图 token。
- `true_smiles`：数据中的真实 SMILES，或从真实分子补得的 SMILES。
- `generated`：按采样顺序排列的候选，每个包含 `smiles`、`inchi` 和 `valid`。

每个 batch 还输出包含 RDKit 分子的 pickle 文件。默认每个输入生成 10 个候选，每个候选经过 500 步去噪。

准确率和相似度指标计算先过滤无效候选，再按 InChI 出现频次排序并去重。因此，JSON 中的前 k 项不一定等于 `acc_at_k` 使用的前 k 个候选；频次排序不表示校准概率。具体准确率、相似度和有效率定义见 [模型架构](../model.zh-CN.md#7-验证与测试指标)。

对比不同实验时，应保持数据集合、候选数量、结构标准化及指标计算方式一致，并分别记录完整测试集与子集结果。

## 历史权重与运行名称

项目展示名称、安装包名称和 W&B 项目名称统一为 `DiffGCMS`，默认运行名称为 `DiffGCMS_medium`、`DiffGCMS_last4` 和 `DiffGCMS_test`。

名称整理不改变模型张量结构，也不会移动已有文件。已有 checkpoint 通过其实际路径指定，并使用与训练时一致的模型配置。保留原始实验代码、权重和日志，有助于后续对照不同版本的结果。
