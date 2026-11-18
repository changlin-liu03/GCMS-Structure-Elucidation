---
name: gcms-input-audit
description: Audit a GC–EI–MS candidate review input, raw linear peak intensities and missing metadata before chemical interpretation.
---

# GC–EI–MS 输入与证据边界

仅使用工具提供的谱峰与候选图。`true_smiles` 和评估答案不属于推理输入。
谱表每行提供 m/z 与原始峰强，直接使用已给出的数值读谱，避免重复查询。
`spectrum_weights` 与谱表逐行对应，为线性强度除以全谱强度之和，直接用于覆盖评分。零强度项不作为观测峰；正强度弱峰保留。负数、非有限值、重复m/z及全零谱由宿主拒绝。
未提供测量误差模型，兼容字段 `score_lower` 和 `score_upper` 均等于点分，不是量化区间或统计置信区间；这不意味着实验无误差，也不证明最高强度峰就是分子离子。
缺少 EI 能量和 RI 时只采用标注的工作假设，不编造元数据。整数 m/z 不支持精确质量唯一分子式归属。
重复候选不增加证据。对同一结构的处理与分数应与输入次序无关。
疑似噪声、共洗脱及同位素混淆只作为局限记录，不自行删除不利峰。

