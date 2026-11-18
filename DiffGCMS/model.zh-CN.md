# DiffGCMS 模型架构

[English](model.md) | 简体中文

本文说明当前源码与默认 `medium` 配置。运行方法见 [README](README.zh-CN.md)，字段约定见 [数据格式](docs/data.zh-CN.md)。下文按数据流介绍编码器、图解码器、扩散过程及评估方式。

## 1. 任务与条件

DiffGCMS 根据谱图和给定重原子集合生成分子图。默认 `denoise_nodes=false`：节点类型和节点数固定，只有边类别参与训练时的扩散去噪及候选生成。

节点条件来自真实分子记录 `node_types`。模型不预测分子式，也不从谱图独立推断节点数。显式氢、形式电荷和立体化学不是图解码器的独立输出类别；隐式氢由 RDKit 根据生成结构处理。

## 2. 模块与数据流

```text
JSONL 样本
├── input_ids / position_ids → padding + attention_mask
│                                      │
│                              GCMSpectrumEncoder
│                                      │
│                              有效峰均值池化 h0
│                                      │
│                              GCMSConditionProjector → y
└── node_types / edge_index / edge_types → PyG Batch
                                              │
                                    to_dense → X, E, node_mask
                                              │
                                    E 加噪，X 固定
                                              │
                              结构特征 + 化学特征 + 时间
                                              │
                                      GraphTransformer
                                              │
                                边 logits → 损失 / 反向采样
```

`src/gcms_main.py` 负责组装；`src/diffusion_model_gcms.py` 中的 `GCMSDenoisingDiffusion` 负责 Lightning 生命周期、优化器、评估和候选生成。

## 3. 谱图编码器

定义位于 `src/models/gcms_encoder.py`。

| 项目 | 默认值 |
|---|---|
| m/z token 范围 | 0–500 |
| 强度 log-bin 范围 | 0–29 |
| 编码器层数 / 隐藏维度 | 12 / 1024 |
| 注意力头数 / FFN 维度 | 16 / 4096 |
| dropout | 0.2 |

`input_ids` 经过 m/z embedding，`position_ids` 经过带 offset=2 的强度 embedding。二者相加后经过 LayerNorm、dropout 和 BART encoder layers。`position_ids` 表示强度分箱，不是峰在序列中的位置。

池化只使用有效峰，投影层从本模型训练：

```text
h0 = sum(hidden_states × attention_mask) / max(sum(attention_mask), 1)
y  = Linear(4096 → 1024)(SiLU(Linear(1024 → 4096)(h0)))
```

默认冻结整个编码器并保持 eval 模式，关闭其 dropout。最后四层微调训练 `layers[8:12]`，冻结 embedding 和前八层。代码还提供 `full`、`lastN` 和 `last_n` 策略，默认流程只覆盖冻结和最后四层微调。

## 4. 分子图与额外特征

默认原子表：`C, N, O, F, Cl, S, P, Si, Br, I`。

边类别：`0=无键, 1=单键, 2=双键, 3=三键, 4=芳香键`。

| 张量 | 形状 | 含义 |
|---|---|---|
| `X` | `[B, N, 10]` | 原子 one-hot |
| `E` | `[B, N, N, 5]` | 边 one-hot |
| `y` | `[B, 1024]` | 谱图条件 |
| `node_mask` | `[B, N]` | 有效节点 |

`N` 是当前 batch 的最大节点数。边保持对称，稠密边张量的对角线置零，不参与边分类损失。

默认 `extra_features=all` 时，输入维度由源码推导如下：

| 通道 | 组成 | 维度 |
|---|---|---|
| `X` | 原子 10 + 节点环计数 3 + 非最大连通分量指示 1 + 特征向量 2 + 化学特征 2 | 18 |
| `E` | 边类别 5 | 5 |
| `y` | 谱图 1024 + 节点数 1 + 全局环计数 4 + 连通分量数 1 + 特征值 5 + 重量特征 1 + 时间 1 | 1037 |

图数据的初始占位 `y` 被谱图条件替换，不额外贡献一维。维度推断中的 `y_dim + 1` 对应时间条件。

结构特征位于 `src/diffusion/extra_features.py`：节点级 3–5 元环、图级 3–6 元环，以及 Laplacian 特征值和特征向量。环特征除以 10 并截断到上限 1；相关计算关闭混合精度，使用 fp32。

化学辅助特征由 `src/diffusion/extra_features_molecular.py` 中的 `ChargeFeature`、`ValencyFeature` 和 `WeightFeature` 提供。前两者各向节点通道添加一维，后者向全局通道添加一维；具体计算以该模块为准。上述五类边编号在数据构建、扩散和分子重建中保持一致。

## 5. 图 Transformer

默认使用 `src/models/transformer_model.py` 中的 `GraphTransformer`，由 `XEyTransformerLayer` 和 `NodeEdgeBlock` 构成。

| 项目 | 默认值 |
|---|---|
| 层数 | 4 |
| 输入 MLP 隐藏维度 | X:256、E:128、y:1024 |
| Transformer 隐藏维度 | X:256、E:64、y:512 |
| 注意力头数 | 8 |
| FFN 维度 | X:256、E:128、y:1024 |
| 输出维度 | X:10、E:5、y:1024 |

每层同时更新节点、边和全局特征：节点生成 Q/K/V，边通过乘加调制参与注意力，谱图条件通过 FiLM 风格的乘加调制作用于节点和边。节点与边的聚合特征用于更新全局向量。

`Xtoy`、`Etoy` 聚合均值、最小值、最大值和方差，并通过线性层映射到全局特征空间。

解码器对输入的输出通道添加残差，边输出屏蔽对角并对称化。网络虽然输出 X、E、y，默认训练损失只使用边项。

同文件的 `GraphTransformerV2/V3/V4`、`NodeEdgeBlockV2` 和 `FPMultiHeadAttention` 是未接入默认入口的实验实现，不代表已支持的配置选项。

## 6. 离散边扩散与损失

默认使用 500 步 cosine 日程和训练集经验边缘分布。对于边类别分布 `m_E`：

```text
Q_t = (1 − β_t) I + β_t · 1 m_Eᵀ
Q̄_t = ᾱ_t I + (1 − ᾱ_t) · 1 m_Eᵀ
```

训练随机选择 `t ∈ {1, …, T}`，由干净边和累计转移矩阵采样噪声边。采样上三角后对称化，节点 X 和谱图条件 y 固定，解码器预测干净图的类别 logits。

默认 `lambda_train=[0, 1, 0]`，目标为有效边的交叉熵，节点与全局条件对应的损失权重为零。

## 7. 验证与测试指标

代码计算 NLL 风格的变分目标，包含节点数分布项、终态 KL、逐步后验 KL 和重建项，用 `val/NLL` 选择 checkpoint。

候选指标位于 `src/metrics/diffms_metrics.py`：

- `validity`：RDKit sanitize 成功且只有一个连通分量的生成分子比例。
- `acc_at_k`：有效候选转成 InChI，按出现频次排序并去重，检查前 k 个是否包含真实 InChI。
- `tanimoto_at_k` / `cosine_at_k`：候选经 InChI 重建和互变异构规范化后，使用半径 2、2048 位 Morgan 指纹，计算前 k 个中的最高相似度，再对样本平均。

同频候选保留计数器中的出现顺序。频次排序不是经过校准的置信度。无法得到真实分子的样本跳过准确率和相似度更新，其生成分子仍参与有效率统计。

## 8. 反向采样与结果

从边缘分布初始化边，保持原子 X 和条件 y 不变，从 T 迭代至 0：预测干净边类别，结合离散后验得到下一步分布，随机采样并对称化。

每个输入重复此过程生成多个候选，再将图转换为 RDKit 分子。重建不写入真实 `atom_hydrogens`。测试导出逐 batch 的 pickle 和候选 JSON；JSON 保留采样顺序，未应用指标计算中的频次排序。

数据划分、输出说明及历史权重的复现要求见 [实验复现说明](docs/reproducibility.zh-CN.md)。
