# 证据等级与质量检查

1. **规则支持**：结构环境、路径和守恒均通过程序检查，可参与固定覆盖评分。
2. **守恒但路径未验证**：可描述为假说，不能计分。
3. **仅质量相符**：同整数质量不是断键机制，不能升级为确定性证据。
4. **未解释**：保持可见，不以猜测填满报告。

EI 母体常由中性分子失去一个电子形成奇电子阳离子。碎片电荷、自由基电子和丢失中性物种需要分别记账。单原子元素质量求和后按电荷计算离子质量，不能忽略离子类型。
对低分辨率峰只做名义质量匹配，不写出仪器未提供的 ppm 精度。同位素峰单独处理，尤其含 Cl/Br 的候选；M+1/M+2 不能直接当作比母体更重的碎片。

方法参考（用于科学背景，系统不会自动访问）：
- Allen et al., Computational Prediction of Electron Ionization Mass Spectra to Assist in GC/MS Compound Identification, DOI: 10.1021/acs.analchem.6b01622。
- Zhu and Jonas, Rapid Approximate Subset-Based Spectra Prediction for Electron Ionization–Mass Spectrometry, DOI: 10.1021/acs.analchem.2c02093。

这些工作区分可能片段和生成倾向。本项目首版是规则支持覆盖基线，不具有已训练的碎片强度模型。
