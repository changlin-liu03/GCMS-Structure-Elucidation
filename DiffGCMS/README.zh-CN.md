# DiffGCMS

> **单仓库目录说明：** 本文中的“仓库根目录”均指 `DiffGCMS/` 子目录。克隆总仓库后，先执行 `cd DiffGCMS`，再运行本文命令；本地环境、配置及 `.env`（如使用）也位于此子目录。参见[项目总览](../README.zh-CN.md)。

[English](README.md) | 简体中文

**GC-EI-MS-conditioned discrete molecular graph diffusion with a SpecTUS encoder.**

DiffGCMS 使用 GC-EI-MS 谱图和给定的重原子类型、数量作为条件，通过离散图扩散生成分子的化学键。默认先冻结 SpecTUS 编码器，训练投影层和图 Transformer；随后可解冻编码器最后四层进行微调。

本项目整合 [SpecTUS](https://github.com/hejjack/SpecTUS) 的预训练谱图编码器与 [DiffMS](https://github.com/coleygroup/DiffMS) 的离散图扩散解码器，用于 GC–EI–MS 分子结构推断。本仓库提供该组合的实现、配置及训练和测试流程，编码器与解码器的原始方法和代码归属各自上游项目。

> **输入要求：** 模型使用谱图和给定的重原子组成作为条件。训练和测试通过数据记录的 `node_types` 提供原子类型及节点数，只对边进行扩散。运行所需的数据和模型权重需另行准备。

## 模型概览

```text
谱图 m/z token + 强度 log-bin
              │
      SpecTUS 编码器（12 层，1024 维）
              │
      有效峰均值池化
              │
      投影层（1024 → 4096 → 1024）
              │
              └─────────────┐
                            ▼
给定原子类型和节点数 → 图 Transformer → 离散边去噪 → 候选分子
```

默认图解码器为 4 层，扩散步数为 500。支持 10 种重原子以及无键、单键、双键、三键、芳香键 5 种边状态。详细数据流见 [模型架构](model.zh-CN.md)。

## 安装

所有命令均从仓库根目录执行。项目声明的 Python 版本要求为 ≥ 3.9。复现实验时，请使用与原训练运行一致的依赖环境。

1. 创建并激活独立 Python 环境。
2. 根据机器的 CPU / CUDA 环境安装适配的 PyTorch。
3. 安装项目及其声明的依赖：

```bash
python -m pip install -e .
```

主要依赖包括 PyTorch、PyTorch Geometric、PyTorch Lightning、Transformers、Hydra 和 RDKit。请先安装适配硬件的 PyTorch，再安装项目依赖。

下文 `.sh` 脚本需要 Bash。Windows 用户可使用 WSL，或准备好环境、权重和统计文件后使用文中的 `python -m src.gcms_main` 命令。

默认配置使用单 GPU、`bf16-mixed`、训练 batch size 128，请按硬件调整。CPU 调试可覆盖 `general.gpus=0 train.precision=32-true train.num_workers=0 train.batch_size=2 train.eval_batch_size=2`；完整模型的 CPU 运行成本仍较高。

## 数据准备

默认把 `DiffGCMS/` 和 `dataset/` 放在同一父目录：

```text
parent/
├── DiffGCMS/
└── dataset/
    └── split_90_5_5/
        ├── train.jsonl
        ├── valid.jsonl
        ├── test.jsonl
        └── stats/
            ├── atom_types.txt
            ├── edge_types.txt
            ├── n_counts.txt
            └── valencies.txt
```

目录名 `split_90_5_5` 是默认约定，程序不会自动划分数据。字段和预处理要求见 [数据格式](docs/data.zh-CN.md)。从训练集生成统计量：

```bash
python -m scripts.compute_stats --jsonl ../dataset/split_90_5_5/train.jsonl --stats-dir ../dataset/split_90_5_5/stats
```

训练和测试均需准备上述三个数据文件和四个统计文件；初始化过程会读取训练 batch 以确定模型输入维度。

## 准备 SpecTUS 编码器

默认脚本从配置的 Hugging Face 模型标识 `MS-ML/SpecTUS_pretrained_only` 获取 `pytorch_model.bin` 并提取编码器张量：

```bash
python scripts/extract_spectus_encoder.py
```

也可以指定本地 PyTorch 权重：

```bash
python scripts/extract_spectus_encoder.py --source /path/to/pytorch_model.bin
```

输出位于 `checkpoints/spectus_encoder/`，包含 `.pt` 张量、对应 `.key` 文件和 `manifest.json`。请为大型权重预留存储和内存。

提取脚本使用 PyTorch 格式权重。`--output` 指定的目录会被删除并重建，请使用专用权重目录，并只加载可信来源的权重。

## 训练、微调与测试

### 冻结编码器训练

```bash
bash scripts/train.sh
```

脚本会在编码器文件缺失时尝试提取权重，在 `atom_types.txt` 缺失时生成统计量。运行前请确认四个统计文件齐全。完成准备工作后，也可直接运行：

```bash
python -m src.gcms_main --config-name=config_train
```

默认训练 30 个 epoch，编码器冻结，投影层和图解码器参与训练。验证计算 NLL 和边分类指标，默认不采样分子；训练结束后不自动测试。

### 最后四层微调

选择冻结训练阶段保存的 checkpoint：

```bash
bash scripts/train_last4.sh /absolute/path/to/selected.ckpt
```

或：

```bash
python -m src.gcms_main --config-name=config_train_last4 "general.load_weights='/absolute/path/to/selected.ckpt'"
```

该阶段训练编码器 `layers[8:12]`、投影层及图解码器，默认 15 个 epoch。`general.load_weights` 仅加载权重，不恢复上一阶段的优化器和 epoch。

### 同阶段断点续训

```bash
python -m src.gcms_main --config-name=config_train "general.resume='/absolute/path/to/last.ckpt'"
```

微调阶段续训使用 `--config-name=config_train_last4`。`resume` 应与原运行阶段及配置匹配，不应用它代替跨阶段的 `load_weights`。

### 测试与候选生成

```bash
bash scripts/test.sh /absolute/path/to/selected.ckpt
```

或：

```bash
python -m src.gcms_main --config-name=config_test "general.test_only='/absolute/path/to/selected.ckpt'"
```

默认每个样本生成 10 个候选，每个候选执行 500 步去噪。本页流程使用单 GPU 测试，需准备独立的 SpecTUS 编码器目录、三个数据划分和统计文件。

脚本末尾可追加 Hydra 参数，例如：

```bash
bash scripts/test.sh /absolute/path/to/selected.ckpt train.eval_batch_size=16 general.test_samples_to_generate=5
```

包含空格或 `=` 的 checkpoint 路径，建议采用上述 Python 命令的引号写法。

## 配置与输出

| 配置组 | 作用 |
|---|---|
| `configs/config_*.yaml` | 组合配置，设置 Hydra 输出目录 |
| `configs/general/` | 运行名称、权重、冻结策略、采样和日志 |
| `configs/train/` | 学习率、batch size、精度和 epoch |
| `configs/model/medium.yaml` | 编码器、解码器和扩散结构 |
| `configs/dataset/gcms_train.yaml` | 数据路径、原子表和样本限制 |

Hydra 将工作目录切换到 `outputs/<日期>/<时间>-<运行名称>/`，产物写入该目录：

```text
outputs/<日期>/<时间>-<运行名称>/
├── .hydra/                    # 本次运行配置
├── gcms_main.log
├── logs/<运行名称>/           # CSV 日志及可选 W&B 日志
├── checkpoints/<运行名称>/    # 训练 checkpoint
└── preds/                     # 测试 JSON 和逐 batch 的 pickle
```

训练 checkpoint 默认按 `val/NLL` 选择保存，与仓库根目录的 `checkpoints/spectus_encoder/` 不同。恢复训练请使用实际运行输出中的 checkpoint 路径。

相对权重路径以仓库根目录为基准，相对数据路径以仓库父目录为基准。自定义数据位置时同时覆盖 `dataset.datadir` 和 `dataset.stats_dir`。Bash 训练脚本的统计预处理仍固定使用默认数据位置；自定义路径请先手动生成统计量，再用 Python 入口。

默认运行名称为 `DiffGCMS_medium`、`DiffGCMS_last4` 和 `DiffGCMS_test`；安装包与 W&B 项目名称统一为 `DiffGCMS`。既有运行目录不会自动重命名。

## 项目结构

```text
DiffGCMS/
├── configs/                 # Hydra 配置
├── scripts/                 # 启动、统计和权重提取
├── src/
│   ├── gcms_main.py          # 运行入口
│   ├── diffusion_model_gcms.py
│   ├── datasets/            # JSONL 读取与分子图构建
│   ├── models/              # 谱图编码器与图 Transformer
│   ├── diffusion/           # 噪声、后验、采样和额外特征
│   ├── metrics/             # 损失与评估指标
│   └── analysis/            # 分子重建、绘图与 RDKit 工具
├── docs/                    # 数据格式与实验复现说明
├── checkpoints/             # 本地编码器权重
├── model.md                 # 模型架构
└── pyproject.toml           # 包元数据与依赖
```

## 实验复现

复现实验时，请保留对应的代码版本、依赖环境、数据划分、运行配置和 checkpoint，并使用一致的候选生成与评估设置。运行记录、预测文件及指标口径见 [实验复现说明](docs/reproducibility.zh-CN.md)。

## 来源与许可

| 组件 | 来源 | 许可说明 |
|---|---|---|
| 谱图编码器 | [SpecTUS](https://github.com/hejjack/SpecTUS) | 上游源码采用 MIT，保留其版权及许可文本 |
| 图扩散解码器及相关组件 | [DiffMS](https://github.com/coleygroup/DiffMS) | 上游源码采用 MIT，其代码进一步源自 DiGress |
| 继承的图扩散基础组件 | [DiGress](https://github.com/cvignac/DiGress) | 保留其 MIT 版权及许可文本 |
| 默认 SpecTUS 预训练权重 | [MS-ML/SpecTUS_pretrained_only](https://huggingface.co/MS-ML/SpecTUS_pretrained_only) | 模型页面标注为 CC BY-NC 4.0，与代码许可分别适用 |

代码的 MIT 条款见 [LICENSE](LICENSE)，各组件的来源与保留声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。原作者署名予以保留，本项目名称不替代上游版权归属。

外部数据及预训练权重不随本代码仓库分发。代码的 MIT 声明不将预训练权重重新授权为 MIT；提取的编码器及包含这些参数的 checkpoint 也不能仅因由本项目生成而视为 MIT 权重。使用或分发时应遵守实际权重的来源条款。默认权重的 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) 包含署名与非商业使用要求。

## 引用

使用本仓库开展研究时，请同时引用所使用的上游方法：

- Adam Hájek, Helge Hecht, Elliott J. Price, Aleš Křenek. *SpecTUS: Spectral Translator for Unknown Structures annotation from EI-MS spectra*. arXiv:2502.05114v1, 2025. [论文](https://arxiv.org/abs/2502.05114v1)
- Montgomery Bohde, Mrunali Manjrekar, Runzhong Wang, Shuiwang Ji, Connor W. Coley. *DiffMS: Diffusion Generation of Molecules Conditioned on Mass Spectra*. ICML, PMLR 267:4737–4756, 2025. [论文](https://proceedings.mlr.press/v267/bohde25a.html)

可复制的 BibTeX 条目见 [references.bib](references.bib)。SpecTUS 条目对应本项目参考的 2025 年预印本 v1；如引用其他版本，应采用该版本的作者与出版信息。学术引用与保留软件许可证分别适用。
