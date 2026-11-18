import csv
from copy import deepcopy
from pathlib import Path
from gcms_agent.config import write_json
from gcms_agent.reporting.quality import explanation_quality, model_text_block

def export_sample(root, result):
    root, sid = Path(root), result["sample_id"]
    result = deepcopy(result)
    result["explanation_quality"] = explanation_quality(result)
    write_json(root / "results" / f"{sid}.json", result)
    candidates = result["ranked_candidates"]
    from gcms_agent.scoring.ranking import evidence_profile
    table_path = root / "reports" / f"{sid}_candidates.csv"
    table_path.parent.mkdir(parents=True, exist_ok=True)
    with table_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["candidate_id", "original_smiles", "final_smiles", "rank", "result_status", "confidence_score", "stop_reason"])
        for c in candidates:
            writer.writerow([c["candidate_id"], c["original_smiles"], c.get("final_smiles"), c.get("rank"), c["result_status"], (c.get("score") or {}).get("confidence_score"), c["stop_reason"]])
        
    text = [f"# GC–EI–MS 分析：{sid}", "", f"处理状态：{result['ranking_status']}。", "", "证据分为固定规则支持的加权谱峰覆盖，未经概率校准。化学合法不等于结构正确。", "", "## 十候选处理结果", "", "|来源|最终 SMILES|排名|处理|证据分|停止原因|", "|---|---|---:|---|---:|---|"]
    overview = ["", "## 证据充分性与执行状态", "",
                "review_complete 表示已完成本次审阅协议，不表示结构已确认；规则枚举 complete 也不表示覆盖全部真实碎裂机制。"]
    usage = result.get("resource_usage", {})
    if "effective_sessions" in usage:
        overview.append(f"请求尝试 {usage['sessions']} 次；有效计数会话 {usage['effective_sessions']} 次；已确认零用量且未执行工具的连接失败 {usage['excluded_transport_failures']} 次。")
    top = result.get("top1")
    if top:
        selected = next(c for c in candidates if c.get("final_smiles") == top["final_smiles"])
        profile = evidence_profile(selected)
        overview += [f"当前展示 Top1：`{top['final_smiles']}`；证据状态：`{profile['evidence_status']}`；未解释强度 {100*profile['unassigned_intensity_fraction']:.2f}%。"]
        if profile["molecular_ion_only"]:
            overview.append("支持仅来自分子离子，缺少异构体区分证据。")
        if profile["strongest_unassigned_peaks"]:
            lookup = {p['mz']: p for p in result.get('peaks', [])}
            overview += ["", "最强未解释峰（最多5个，权重分母为全谱总强度）：", "",
                         "|m/z|原始强度|占全谱强度|", "|---:|---:|---:|"]
            for p in profile["strongest_unassigned_peaks"]:
                raw = lookup.get(p['mz'], {})
                overview.append(f"|{p['mz']}|{raw.get('raw_intensity', raw.get('intensity', '未记录'))}|{100*p['weight']:.2f}%|")
            overview += ["", f"合计占全谱强度 {100*profile['strongest_unassigned_intensity_fraction']:.2f}%；这些峰未获规则支持，不等于实验已否定该结构。"]
    overview += ["", "|来源|审阅停止原因|证据状态|", "|---|---|---|"]
    reported_overview = set()
    for c in candidates:
        origin = c.get("computed_candidate_id", c["candidate_id"])
        if origin not in reported_overview:
            reported_overview.add(origin)
            overview.append(f"|{origin}|{c['stop_reason']}|{evidence_profile(c)['evidence_status']}|")
    text[5:5] = overview + [""]
    if result.get("validation_note"):
        text[4:4] = [f"验证说明：{result['validation_note']}", ""]
    for c in candidates:
        score = (c.get("score") or {}).get("confidence_score")
        text.append(f"|{c['candidate_id']}|`{c.get('final_smiles') or '未获得合法结构'}`|{c.get('rank') or '—'}|{c['result_status']}|{score if score is not None else '—'}|{c['stop_reason']}|")
        write_json(root / "candidates" / sid / f"{c['candidate_id']}.json", c)
    text += ["", "## 编辑审计", "", "已提交与证据分由宿主程序记录。模型的总结、命名和逐项理由仅保留为未验证叙述；通过图编辑验收不表示这些文字中的每个化学判断都已验证。", ""]
    reported = set()
    for c in candidates:
        origin = c.get("computed_candidate_id", c["candidate_id"])
        if origin in reported:
            text.append(f"{c['candidate_id']} 与 {origin} 共享同一结构计算及编辑历史。")
            continue
        reported.add(origin)
        text += ["", f"### {origin}", "", f"原始 SMILES：`{c['original_smiles']}`。"]
        exploration = c.get("exploration_state") or {}
        exploration_smiles = (exploration.get("validation") or {}).get("canonical_smiles")
        if exploration_smiles and exploration_smiles != c.get("final_smiles"):
            text += [f"最后探索结构：`{exploration_smiles}`；最终输出保留历史最高点分结构 `{c.get('final_smiles')}`。同分保留较早结构，获准探索不表示分数改善。"]
        for turn in c.get("history", []):
            if turn.get("summary"):
                text.append(f"第 {turn['round']} 轮：")
                text += model_text_block(turn["summary"])
            for proposal_index, outcome in enumerate(turn.get("outcomes", []), 1):
                proposal = outcome["proposal"]
                text += ["", f"提案 {proposal_index}：{outcome.get('verdict', {}).get('reason')}；已提交：{outcome.get('committed', False)}。", f"模型引用峰（出现于谱图不代表对应机制已验证）：{proposal.get('peak_refs', [])}", ""]
                if proposal.get("reason"):
                    text += model_text_block(proposal["reason"])
                for op, explanation in zip(proposal.get("operations", []), proposal.get("operation_explanations", [])):
                    text.append(f"操作：`{op}`")
                    text += model_text_block(explanation)
                if outcome.get("score"):
                    text.append(f"验证后证据分：{outcome['score']['confidence_score']:.2f}/100。")
        if not c.get("history"):
            text.append(f"没有已提交的模型审查记录；停止原因：{c.get('stop_reason', '未记录')}。未提交的试算不算已完成编辑。")
    text += ["", "## 展示 Top1 的工具证据", "", "本节只展示宿主验证路径。编辑审计中的模型文字没有被提升为本节结论；结构身份和真实碎裂机制仍未得到实验确认。", ""]
    top = result.get("top1")
    if top is None:
        text.append("没有足够规则支持形成 Top1 结构结论。合法但零支持的候选仍保留在结果中。")
    else:
        selected = next(c for c in candidates if c.get("final_smiles") == top["final_smiles"])
        score = top["score"]
        quality = result["explanation_quality"]
        profile = quality["evidence_profile"]
        text += [f"展示候选：`{top['final_smiles']}`；证据分 {score['confidence_score']:.2f}/100。", "评分使用原始线性峰强；未输入测量误差，兼容上下界均等于点分，不是身份置信区间。", f"并列：{top.get('tied', False)}。"]
        if quality["provisional"]:
            text.append("本case审查未完成，下列证据与排名均为暂存结果。")
        if "multiple_structures_share_top_score" in quality["limitations"]:
            text += ["最高分候选集合：" + "、".join(f"`{s}`" for s in quality["top_score_candidate_smiles"]) + "。",
                     "最高证据分并列时，按原始来源候选个数降序选择Top1；个数仍相同时按规范SMILES字典序选择。表中排名保留证据分并列关系。票数不增加证据分，不能据此宣称唯一鉴定。",
                     "", "|最高分结构|来源候选个数|独立计算候选数|来源槽位|", "|---|---:|---:|---|"]
            for group in result.get("top1_candidates", []):
                text.append(f"|`{group['final_smiles']}`|{group.get('source_candidate_count', len(group.get('source_candidate_ids', [])))}|{group.get('independent_candidate_count', '未记录')}|{', '.join(group.get('source_candidate_ids', []))}|")
            text += ["", f"最终选择：`{top['final_smiles']}`；选择依据：{top.get('selection_basis', '未记录')}。",
                     "重复来源槽位计票，但共享同一计算历史，不代表独立实验或独立审阅证据。"]
        if quality["same_supported_peak_set_competitors"]:
            text.append("与展示候选支持相同峰集合的竞争结构：" + "、".join(f"`{s}`" for s in quality["same_supported_peak_set_competitors"]) + "。")
        if profile["majority_intensity_unassigned_at_point_estimate"]:
            text.append(f"按输入线性强度计算，大部分观测强度未解释：未指派强度占比 {100*profile['unassigned_intensity_fraction']:.2f}%。这是规则覆盖局限，不代表这些峰与候选必然矛盾。")
        if profile["molecular_ion_only"]:
            text.append("当前计分支持仅来自分子离子假说，不能据此区分同名义质量异构体；没有已支持的碎片形成路径。")
        if not profile["bond_cleavage_peaks"]:
            text.append("没有工具支持的断键碎片峰；不会为未指派峰补写确定的断键机制。")
        text += ["同位素注释没有完成峰强度比例拟合；规则库不预测碎片丰度，不能从存在一条路径推断它是主导通道。",
                 "", "```mermaid", selected["validation"].get("molecode", ""), "```", "", "|路径|观测峰|规则|断键|离子式|电荷|自由基电子数|理论 m/z|", "|---:|---:|---|---|---|---:|---:|---:|"]
        verified = [e for e in selected.get("evidence", {}).get("evidence", []) if e.get("verified")]
        for index, e in enumerate(verified, 1):
            text.append(f"|{index}|{e.get('peak_mz', '')}|{e.get('rule_id', '')}|{e.get('broken_bonds', [])}|{e.get('ion_formula', '')}|{e.get('charge', '未标注')}|{e.get('radicals', '未标注')}|{e.get('exact_mz', '')}|")
        text += ["", "### 路径中的中性丢失、氢变化与键级变化", "",
                 "下列修改属于碎片形成假说，不是对候选母体的再次编辑；空断键列表也不表示没有脱氢或重排。氢数变化与显式中性产物应一起阅读。", ""]
        for index, e in enumerate(verified, 1):
            neutrals = []
            for neutral in e.get("neutral_losses", []):
                neutrals.append(f"{neutral.get('formula', '未标注')}（原子 {neutral.get('atom_ids', [])}；电荷 {neutral.get('charge', '未标注')}；自由基电子数 {neutral.get('radicals', '未标注')}）")
            hydrogen = [f"{atom_id}: {change:+d}" for atom_id, change in sorted(e.get("hydrogen_changes", {}).items())]
            bonds = [f"{change.get('a')}-{change.get('b')}: {change.get('from')} → {change.get('to')}" for change in e.get("bond_changes", [])]
            text += [f"**路径 {index}：m/z {e.get('peak_mz', '')}，{e.get('rule_id', '')}**", "",
                     f"- 离子保留原子：{e.get('atom_ids', [])}。",
                     f"- 中性产物：{'；'.join(neutrals) if neutrals else '无显式中性产物（例如分子离子假说）'}。",
                     f"- 氢数变化：{'；'.join(hydrogen) if hydrogen else '无显式氢迁移或脱氢'}。",
                     f"- 键级变化：{'；'.join(bonds) if bonds else '无额外键级变化记录'}。",
                     f"- 验证范围：{e.get('verification_scope', '规则适用条件与化学计量检查')}；不表示已实验证实机制。", ""]
            if e.get("reference"):
                text += [f"- 模板来源：{e['reference']}；原子映射状态：{e.get('atom_mapping_status', '未标注')}。",
                         "- 适用局限：" + "；".join(e.get("limitations", [])), ""]
        text += ["", "### 逐峰强度与计分贡献", "",
                 "峰强直接使用输入原始值，不做读谱归一化；全谱权重仅用于覆盖评分。分数贡献不预测碎片丰度。", "",
                 "|m/z|原始强度|全谱权重|规则支持|分数贡献|", "|---:|---:|---:|---|---:|"]
        peak_by_mz = {p["mz"]: p for p in result.get("peaks", [])}
        for contribution in score.get("peak_contributions", []):
            peak = peak_by_mz.get(contribution["mz"], {})
            text.append(f"|{contribution['mz']}|{peak.get('raw_intensity', peak.get('intensity', '未记录'))}|{contribution['weight']:.10g}|{contribution['supported']}|{contribution['contribution']:.10g}|")
        text += ["", f"未解释峰：{score.get('unassigned_peaks', [])}", "", "上述为规则支持的碎片假说。名义质量匹配和守恒不证明唯一机制；规则库不预测碎片强度，未覆盖的机制不自动视为不存在。"]
        write_json(root / "evidence" / f"{sid}_top1.json", selected.get("evidence", {}))
        try:
            from gcms_agent.reporting.figures import render_top1
            render_top1(root / "reports" / f"{sid}_top1.png", selected, result["peaks"])
            text += ["", f"![Top1 原子标号与谱图]({sid}_top1.png)"]
        except ImportError:
            text += ["", "可选绘图依赖未安装，结构与峰证据见 JSON。"]
    (root / "reports" / f"{sid}.md").write_text("\n".join(text)+"\n", encoding="utf-8")
