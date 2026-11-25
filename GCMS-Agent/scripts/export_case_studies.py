"""Export two owner-approved historical cases; no inference or rescoring.

Default: create case_studies/ only if absent. --check: regenerate in memory and
compare every byte with the published bundle. Requires the private source runs
locally; readers of the public bundle do not need them. Never reads workspaces,
.env, CLI caches, raw console logs, or other private sources beyond the specified case runs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    {"id": "butyl-butanoate", "name_en": "Butyl butanoate", "name_zh": "丁酸正丁酯", "source": "case1055_oxygen_20261116",
     "stages": (("initial_baseline", "baseline"), ("initial_agent", "agent"),
                ("retry_baseline", "retry/baseline"), ("agent", "retry/agent")),
     "main": "agent", "focus": "c03", "trials": 26,
     "title": "服务失败、重试与参考结构生成"},
    {"id": "trimethylpentylsilane", "name_en": "Trimethylpentylsilane", "name_zh": "三甲基正戊基硅烷", "source": "case1062_silyl_20261116",
     "stages": (("baseline", "baseline"), ("agent", "agent")),
     "main": "agent", "focus": "c01", "trials": 16,
     "title": "含硅结构编辑与并列排名"},
)
FILES = ("README.zh-CN.md", "README.md", "input.json", "reference.json", "trajectory.json",
         "result.json", "provenance.json", "trajectory.png")
VALIDATION_FIELDS = ("valid", "errors", "canonical_smiles", "formula", "exact_mass",
                     "nominal_mass", "composition", "hydrogen_count", "formal_charge",
                     "dbe", "heavy_atom_count", "ring_count")
PROPOSAL_FIELDS = ("base_version", "operations", "reason", "peak_refs", "operation_explanations")
ALLOWED_SAMPLE_IDS = {"s01055_907fc7efff69", "s01062_ba1377eb3e8d"}


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pick(value, fields):
    return {key: deepcopy(value[key]) for key in fields if key in value}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_public(value, path="root"):
    """Fail closed on credentials, local machine paths or other sample IDs."""
    if isinstance(value, dict):
        for key, child in value.items():
            require(not re.search(r"(?i)^(api_key|auth_token|authorization|password|secret|credential|access_token|refresh_token)$", key),
                    "Forbidden field at " + path)
            check_public(child, path + "." + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_public(child, f"{path}[{index}]")
    elif isinstance(value, str):
        require(not re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home|tmp|mnt)/", value), "Local path at " + path)
        require(not re.search(r"(?i)sk-[A-Za-z0-9_-]{16,}|bearer\s+[A-Za-z0-9._-]{12,}", value), "Credential-like text at " + path)
        require(set(re.findall(r"s\d{5}_[a-f0-9]{12}", value)) <= ALLOWED_SAMPLE_IDS,
                "Unapproved sample ID at " + path)


class Source:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.hashes = {}

    def file(self, relative):
        path = (self.root / relative).resolve()
        require(path.is_relative_to(self.root), "Source path escapes case directory")
        require("workspaces" not in path.parts and path.suffix != ".key", "Excluded source file")
        return path

    def read(self, relative):
        path = self.file(relative)
        raw = path.read_bytes()
        self.hashes[path.relative_to(self.root).as_posix()] = digest(raw)
        return json.loads(raw.decode("utf-8-sig"))

    def verify_freeze(self, stage):
        freeze = self.read(f"{stage}/prediction_freeze.json")
        inventory = freeze.get("files")
        require(isinstance(inventory, dict) and bool(inventory), "Missing frozen inventory")
        for name, expected in inventory.items():
            path = self.file(f"{stage}/{name}")
            require(path.is_relative_to(self.file(stage)), "Frozen path escapes stage")
            require(digest(path.read_bytes()) == expected, "Frozen file mismatch: " + str(name))
        return {"all_matched_at_export": True, "file_count": len(inventory), "files": inventory}


class Pool:
    def __init__(self):
        self.data = {"graphs": {}, "scores": {}, "paths": {}}

    def ref(self, kind, value):
        if value is None:
            return None
        key = digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.data[kind][key] = deepcopy(value)
        return key

    def assessment(self, validation, score, evidence=None, graph=None):
        output = {"validation": pick(validation or {}, VALIDATION_FIELDS),
                  "graph_ref": self.ref("graphs", graph if graph is not None else (validation or {}).get("graph")),
                  "score_ref": self.ref("scores", score)}
        if evidence is not None:
            output["evidence"] = pick(evidence, ("complete", "supported_peaks", "diagnostics"))
            output["evidence"]["path_refs"] = [self.ref("paths", row) for row in evidence.get("evidence", [])]
        return output


def accounting(ledger):
    requests = ledger.get("requests", [])
    return {"currency": ledger.get("currency"), "recorded_spend_cny": ledger.get("spent_cny"),
            "known_cost_cny": sum(r.get("cost_cny", 0) for r in requests if r.get("usage", {}).get("known") is True),
            "estimated_cost_cny": sum(r.get("cost_cny", 0) for r in requests if r.get("usage", {}).get("known") is not True),
            "known_requests": sum(r.get("usage", {}).get("known") is True for r in requests),
            "unknown_requests": sum(r.get("usage", {}).get("known") is not True for r in requests),
            "pending_reservation_cny": sum(ledger.get("pending_reservations", {}).values()),
            "usage": ledger.get("usage"),
            "note": "Historical ledger estimates, not a provider invoice; unknown use is not zero cost."}


def candidate_trace(candidate, pool):
    result = pick(candidate, ("candidate_id", "original_smiles", "final_smiles", "result_status",
                              "stop_reason", "rounds_completed", "accepted_edits"))
    result["initial"] = pool.assessment(candidate["initial_validation"], candidate.get("initial_score"), graph=candidate.get("initial_graph"))
    result["final"] = pool.assessment(candidate["validation"], candidate.get("score"), candidate.get("evidence"), candidate.get("graph"))
    exploration = candidate.get("exploration_state")
    if exploration:
        result["last_exploration"] = {"graph_version": exploration.get("graph_version"),
            **pool.assessment(exploration.get("validation", {}), exploration.get("score"), exploration.get("evidence"), exploration.get("graph"))}
    result["rounds"] = []
    for history in candidate.get("history", []):
        row = pick(history, ("round", "summary", "review_receipt", "stop_evidence", "error_category", "error_type", "failure_receipt", "models", "exploration_feedback"))
        row["outcomes"] = []
        for outcome in history.get("outcomes", []):
            row["outcomes"].append({"proposal": pick(outcome.get("proposal", {}), PROPOSAL_FIELDS),
                "base_version": outcome.get("base_version"), "verdict": outcome.get("verdict"),
                "committed": outcome.get("committed", False),
                **pool.assessment(outcome.get("validation", {}), outcome.get("score"), graph=outcome.get("proposed_graph"))})
        result["rounds"].append(row)
    return result


def stage_export(source, relative, pool):
    freeze = source.verify_freeze(relative)
    manifest = source.read(f"{relative}/manifest.json")
    result_files = sorted(source.file(f"{relative}/results").glob("*.json"))
    require(len(result_files) == 1, "Expected one case result")
    raw = source.read(result_files[0].relative_to(source.root).as_posix())
    summary = source.read(f"{relative}/summary.json")
    evaluation = source.read(f"{relative}/evaluation.json")
    ledger = source.read(f"{relative}/logs/budget.json")
    result = pick(raw, ("sample_id", "mode", "status", "ranking_complete", "ranking_status", "ranking_diagnostics", "explanation_quality", "resource_usage"))
    result.update(summary=summary, evaluation=evaluation, accounting=accounting(ledger))
    result["peaks_as_recorded"] = raw["peaks"]
    result["source_slots"] = []
    trace = {"candidates": {}, "sessions": [], "failures": []}
    for candidate in sorted(raw["ranked_candidates"], key=lambda c: c["candidate_id"]):
        computed = candidate.get("computed_candidate_id", candidate["candidate_id"])
        result["source_slots"].append({**pick(candidate, ("candidate_id", "original_smiles", "final_smiles", "rank", "tied", "stop_reason", "result_status", "accepted_edits")),
            "computed_candidate_id": computed, "initial_valid": candidate["initial_validation"]["valid"],
            "final_valid": candidate["validation"]["valid"], "score": candidate.get("score")})
        if computed not in trace["candidates"]:
            representative = next(c for c in raw["ranked_candidates"] if c["candidate_id"] == computed)
            trace["candidates"][computed] = candidate_trace(representative, pool)
    result["ranking"] = raw["unique_ranking"]
    result["top1"] = raw.get("top1")
    result["top1_candidates"] = raw.get("top1_candidates")
    for path in sorted(source.file(f"{relative}/checkpoints").glob("*_review.json")):
        receipt = source.read(path.relative_to(source.root).as_posix())
        review = receipt["review"]
        audit = review.get("audit", {})
        session = {"receipt_id": path.stem, **pick(receipt, ("request_id", "elapsed_seconds", "review_tier")),
            **pick(review, ("usage", "format_errors", "actual_models")),
            "tool_calls": [pick(t, ("tool", "input_chars", "output_chars", "error", "elapsed_seconds")) for t in audit.get("tool_metrics", [])],
            "reviews": [pick(r, ("candidate_id", "decision", "proposals", "summary", "stop_review", "stop_evidence")) for r in review["reviews"]],
            "trials": []}
        for trial in audit.get("trials", []):
            session["trials"].append({**pick(trial, ("sample_id", "candidate_id", "base_version", "graph_hash", "rules_version", "spectrum_hash", "operations", "trial_id", "verdict", "cache_hit", "selected")),
                **pool.assessment(trial.get("result", {}).get("validation", {}), trial.get("result", {}).get("score"), trial.get("result", {}).get("evidence"))})
        trace["sessions"].append(session)
    for path in sorted(source.file(f"{relative}/checkpoints").glob("*_failure.json")):
        failure = source.read(path.relative_to(source.root).as_posix())
        require(not failure.get("audit", {}).get("trials"), "Failure contains trials requiring explicit export")
        trace["failures"].append({"receipt_id": path.stem, **pick(failure, ("request_id", "error_category", "error_type", "usage", "retryable", "format_errors", "elapsed_seconds"))})
    version = {**pick(manifest, ("created_utc", "mode", "backend", "python", "rdkit", "input_hash", "config_hash", "versions")),
        "configuration": {"model": pick(manifest["config"]["model"], ("model", "thinking", "effort", "request_timeout_seconds", "prices", "prices_cny")),
                          **pick(manifest["config"], ("runtime", "scoring", "chemistry"))},
        "original_freeze": freeze}
    return result, trace, version


def focus_steps(trace, focus, pool):
    candidate = trace["candidates"][focus]
    steps = [("Initial", candidate["original_smiles"], candidate["initial"])]
    for row in candidate["rounds"]:
        for outcome in row["outcomes"]:
            if outcome["committed"]:
                steps.append((f"Round {row['round']}: committed", outcome["validation"].get("canonical_smiles"), outcome))
    steps.append(("Final: best retained", candidate["final_smiles"], candidate["final"]))
    return steps


def render_figure(case, steps, pool):
    from PIL import Image, ImageDraw, ImageFont
    from rdkit import Chem, rdBase
    from rdkit.Chem.Draw import rdMolDraw2D
    def font(size):
        for name in ("DejaVuSans.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
        return ImageFont.load_default(size=size)
    width = 350 * len(steps)
    canvas = Image.new("RGB", (width, 420), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((22, 16), f"{case['name_en']} | {case['focus']} | recorded committed structures", font=font(23), fill="#12283b")
    for index, (label, smiles, assessment) in enumerate(steps):
        x = index * 350
        draw.text((x + 15, 62), label, font=font(17), fill="#126c70")
        with rdBase.BlockLogs():
            mol = Chem.MolFromSmiles(smiles) if smiles else None
            if mol is None and smiles:
                mol = Chem.MolFromSmiles(smiles, sanitize=False)
                if mol is not None: mol.UpdatePropertyCache(strict=False)
            require(mol is not None, "Cannot depict recorded focus structure")
            drawer = rdMolDraw2D.MolDraw2DCairo(320, 220)
            rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
            drawer.FinishDrawing()
        canvas.paste(Image.open(io.BytesIO(drawer.GetDrawingText())).convert("RGB"), (x + 10, 88))
        score = pool.data["scores"].get(assessment.get("score_ref"))
        text = f"Coverage: {100 * score['score']:.4f} / 100" if score else "Coverage: not scored"
        draw.text((x + 15, 315), text, font=font(17), fill="#23384b")
        draw.text((x + 15, 342), "Valid" if assessment["validation"].get("valid") else "Invalid initial structure", font=font(16), fill="#526273")
        if index + 1 < len(steps):
            draw.line((x + 322, 195, x + 346, 195), fill="#126c70", width=3)
            draw.polygon([(x+346,195),(x+339,190),(x+339,200)],fill="#126c70")
    draw.text((22, 386), "Historical scores; not identity probabilities. Full trials and atom IDs: trajectory.json", font=font(17), fill="#526273")
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def md(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def model_prose(value):
    # Chemical brackets in free text must not become Markdown links or HTML.
    text = str(value)
    for old, new in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                     ("[", "&#91;"), ("]", "&#93;"), ("`", "&#96;"), ("|", "&#124;")):
        text = text.replace(old, new)
    return text.replace("\n", " ")


def score_text(score, *, english=False):
    return ("Not scored" if english else "未评分") if score is None else f"{100 * score['score']:.6f}"


NOTES = {
 "butyl-butanoate": "初次 Agent 尝试因服务错误中止；重试使用相同输入和参考标签。关键候选 c03 在第一轮正式提交参考结构，但另一结构覆盖分略高，因此参考结构在保存的展示排名中位于第 2。所有来源槽位最终合法，仍因会话额度结束而未完成。初次失败的未知用量估算与重试费用分开保存，不能仅统计重试后宣称一次完整成功。",
 "trimethylpentylsilane": "关键候选 c01 在第一轮正式提交参考含硅结构。保存结果中六个不同结构同分，参考结构位于展示顺序第 6，证据排名仍为并列第 1。当前展示顺序不等于独立谱学区分；生成参考结构并未让它成为 Top1。整体在会话额度耗尽后结束，严格端到端口径仍记未完成。",
}


TITLES_EN = {
    "butyl-butanoate": "Service failure, retry, and generation of the reference structure",
    "trimethylpentylsilane": "Silicon-containing structure edits and tied rankings",
}
NOTES_EN = {
    "butyl-butanoate": "The first Agent attempt stops because of a service error; the retry uses the same input and reference label. Focus candidate c03 commits the reference structure in its first round, but another structure has a slightly higher coverage score, placing the reference second in the saved display order. All source slots end with valid structures, yet the review remains incomplete when its session allowance is exhausted. Estimated costs for unknown usage in the initial failure remain separate from retry costs; counting only the retry does not establish a complete successful run.",
    "trimethylpentylsilane": "Focus candidate c01 commits the silicon-containing reference structure in its first round. Six distinct saved structures have the same score. The reference is sixth in display order but tied for first by evidence rank. Display order does not establish independent spectroscopic discrimination; generating the reference does not make it Top1. The run ends when its session allowance is exhausted and remains incomplete under strict end-to-end evaluation.",
}


def case_markdown(case, inputs, reference, results, trace, pool, *, english=False):
    def t(zh, en):
        return en if english else zh
    main = results[case["main"]]
    main_trace = trace[case["main"]]
    focus = main_trace["candidates"][case["focus"]]
    lines = [f"# {case['name_en']}: {TITLES_EN[case['id']]}" if english else f"# {case['name_zh']}：{case['title']}", "",
      t("[English](README.md) | **简体中文**", "**English** | [简体中文](README.zh-CN.md)"), "",
      t(NOTES[case["id"]], NOTES_EN[case["id"]]), "",
      t("这是获准公开的历史开发案例，不是随机测试集或当前版本的新实验。模型文字按原记录保留，为模型提出的解释，不代表实验确认的机理。未重新运行模型、验证化学路径或重算分数。", "This is an authorized historical development case, not a random test set or a new experiment with the current version. Model summaries, reasons, and operation explanations below remain in their original language (mostly Chinese), with only Markdown escaping applied. They are model-proposed explanations, not experimentally confirmed mechanisms. No model was rerun, chemical pathway revalidated, or score recalculated."), "",
      t("## 文件与阅读顺序", "## Files and reading order"), "",
      t("1. [输入](input.json)：观测谱与十个原始候选；[参考答案](reference.json)独立保存，仅用于事后说明与评估。", "1. [Input](input.json): observed spectrum and ten original candidates. The [reference answer](reference.json) is stored separately for retrospective explanation and evaluation only."),
      t("2. 本文：候选来源、关键轨迹、全部试验索引及最终状态。", "2. This document: candidate sources, the focus trajectory, all trial entries, and the final status."),
      t("3. [完整结构化轨迹](trajectory.json)：所有独立候选的初始图、正式提交历史、会话工具顺序和全部试验。", "3. [Full structured trajectory](trajectory.json): initial graphs for every independently computed candidate, committed edit history, tool-call order within sessions, and all trials."),
      t("4. [结果与对照](result.json)：全部来源槽位、原始排名、各阶段评估及已知/未知费用。", "4. [Results and comparisons](result.json): every source slot, original rankings, per-stage evaluations, and costs with known or unknown usage."),
      t("5. [来源与校验](provenance.json)：历史配置/源码指纹、原冻结清单验证与公开文件 SHA-256。", "5. [Provenance and checksums](provenance.json): historical configuration/source fingerprints, verification of the original frozen inventories, and SHA-256 checksums of public files."), "",
      t("## 输入与版本", "## Input and version"), "",
      t(f"样本 `{inputs[0]['sample_id']}`，{len(inputs[0]['input_ids'])} 个谱峰，10 个候选来源槽位。参考结构：`{reference[0]['true_smiles']}`。", f"Sample `{inputs[0]['sample_id']}`, {len(inputs[0]['input_ids'])} spectral peaks, and 10 candidate source slots. Reference structure: `{reference[0]['true_smiles']}`."),
      t("输入为 `raw_linear`；当前加载器可校验其格式，但历史代码/技能版本与当前不同，重跑结果不保证一致。", "The input uses `raw_linear`. The current loader can validate its format, but historical code and skill versions differ from the current ones; reruns are not guaranteed to produce the same results."),
      t("本材料展示已保存的历史过程，不是可恢复检查点或完整会话逐字记录。", "These materials present the saved historical process. They are neither a resumable checkpoint nor a complete verbatim session transcript."), "",
      t("## 十个来源槽位", "## Ten source slots"), "", t("| 来源 | 独立计算候选 | 初始 SMILES | 最终 SMILES | 结束原因 |", "| Source | Independently computed candidate | Initial SMILES | Final SMILES | Stop reason |"), "|---|---|---|---|---|"]
    for row in main["source_slots"]:
        lines.append(f"| {row['candidate_id']} | {row['computed_candidate_id']} | `{md(row['original_smiles'])}` | `{md(row['final_smiles'])}` | {row['stop_reason']} |")
    lines += ["", t("同一独立计算候选的图与历史只存一份，十个来源槽位及其最终状态全部保留。", "Graphs and history are stored once per independently computed candidate. All ten source slots and their final states are retained."), "", t("## 关键结构轨迹", "## Focus structure trajectory"), "",
              t("![已正式提交结构与历史最佳恢复](trajectory.png)", "![Committed structures and restoration of the historical best](trajectory.png)"), "", t(f"图中只突出 `{case['focus']}`；初始状态、每次正式提交与最终输出均来自原记录。图上数值是覆盖分 × 100。", f"The figure focuses on `{case['focus']}`. The initial state, each committed edit, and final output come from the original records. All coverage scores displayed in this document and figure are multiplied by 100.")]
    for history in focus["rounds"]:
        lines += ["", t(f"### 第 {history['round']} 轮", f"### Round {history['round']}"), "", t(f"模型提交摘要（未经机理确证）：{model_prose(history.get('summary', '无结构化摘要。'))}", f"Original model summary (not a confirmed mechanism): {model_prose(history.get('summary', 'No structured summary recorded.'))}")]
        for outcome in history["outcomes"]:
            p = outcome["proposal"]
            score = pool.data["scores"].get(outcome["score_ref"])
            lines += ["", t(f"正式提交：**{'是' if outcome['committed'] else '否'}**；工具/宿主结论：`{(outcome.get('verdict') or {}).get('reason')}`；覆盖分：{score_text(score)}。", f"Committed: **{'Yes' if outcome['committed'] else 'No'}**; tool/host verdict: `{(outcome.get('verdict') or {}).get('reason')}`; coverage score: {score_text(score, english=True)}."),
                      t(f"结果结构：`{outcome['validation'].get('canonical_smiles')}`。", f"Resulting structure: `{outcome['validation'].get('canonical_smiles')}`."),
                      t(f"模型理由：{model_prose(p.get('reason', '未记录'))}。", f"Original model reason: {model_prose(p.get('reason', 'Not recorded'))}"), "", t("| 操作 | 模型逐项说明 |", "| Operation | Original model explanation |"), "|---|---|"]
            for index, operation in enumerate(p.get("operations", [])):
                explanations = p.get("operation_explanations", [])
                explanation = explanations[index] if index < len(explanations) else t("未记录", "Not recorded")
                lines.append(f"| `{md(json.dumps(operation, ensure_ascii=False))}` | {model_prose(explanation)} |")
    lines += ["", t("## 全部工具试验", "## All tool trials"), "", t("按回执文件顺序、回执内记录顺序列出；并发会话之间不据此推断精确时间顺序。工具选中引用不等于宿主最终提交，正式提交以候选 rounds 中的 committed 为准。", "Entries follow receipt-file order and the order within each receipt; this does not establish an exact chronology across concurrent sessions. A selected tool reference is not necessarily a host commit. Committed state changes are identified by `committed` in candidate `rounds`."), "",
              t("| 会话 | 候选 / 图版本 | 试验 ID | 合法 | 工具结论 | 引用选中 | 覆盖分 |", "| Session | Candidate / graph version | Trial ID | Valid | Tool verdict | Reference selected | Coverage score |"), "|---|---|---|---|---|---|---|"]
    for index, session in enumerate(main_trace["sessions"], 1):
        for trial in session["trials"]:
            score = pool.data["scores"].get(trial.get("score_ref"))
            lines.append(f"| {index} | {trial['candidate_id']} / {trial['base_version']} | `{trial['trial_id']}` | {trial['validation'].get('valid')} | {(trial.get('verdict') or {}).get('reason')} | {trial.get('selected')} | {score_text(score, english=english)} |")
    lines += ["", t("## 各阶段结果", "## Results by stage"), "", t("| 阶段 | 完成 | 保存排名 Acc@1 / Acc@10 | 严格端到端 Acc@1 / Acc@10 | 已知费用 / 估算费用（元） |", "| Stage | Complete | Saved ranking Acc@1 / Acc@10 | Strict end-to-end Acc@1 / Acc@10 | Known / estimated cost (CNY) |"), "|---|---|---|---|---|"]
    for stage, result in results.items():
        ev = result["evaluation"]; regular=ev["requested_set"]; strict=ev["strict_end_to_end_requested_set"]; cost=result["accounting"]
        lines.append(f"| {stage} | {result['ranking_complete']} | {regular['acc_at_1']} / {regular['acc_at_10']} | {strict['acc_at_1']} / {strict['acc_at_10']} | {cost['known_cost_cny']:.8f} / {cost['estimated_cost_cny']:.8f} |")
    lines += ["", t("### 最终保存的唯一结构顺序", "### Final saved order of unique structures"), "", t("| 展示位置 | 证据排名 | SMILES | 覆盖分 | 参考结构 |", "| Display position | Evidence rank | SMILES | Coverage score | Reference structure |"), "|---|---|---|---|---|"]
    for index, row in enumerate(main["ranking"], 1):
        lines.append(f"| {index} | {row['rank']} | `{row['final_smiles']}` | {score_text(row['score'], english=english)} | {t('是', 'Yes') if row['final_smiles']==reference[0]['true_smiles'] else t('否', 'No')} |")
    lines += ["", t("## 如何理解这些材料", "## How to interpret these materials"), "",
      t("全部主要 Agent 运行均为 provisional/incomplete。生成参考结构、选择 Top1 和完成整个审阅是三个不同结果。严格端到端口径把未完成案例记为未命中；单个开发案例不能证明总体识别率或费用。", "All main Agent runs are provisional/incomplete. Generating the reference structure, selecting Top1, and completing the entire review are distinct outcomes. Strict end-to-end evaluation counts incomplete cases as misses; individual development cases do not establish overall identification accuracy or cost."),
      t("轨迹中的 graphs、scores、paths 为按内容 SHA-256 去重的对象池；graph_ref、score_ref、path_refs 在同一 trajectory.json 内解析。原子 ID 和图版本保留，不用 canonical SMILES 重建原子对应关系。", "The trajectory uses content-addressed object pools (`graphs`, `scores`, and `paths`), deduplicated by SHA-256. Resolve `graph_ref`, `score_ref`, and `path_refs` within the same `trajectory.json`. Atom IDs and graph versions are preserved; atom correspondence is not reconstructed from canonical SMILES."),
      t("原始输入和标签与源文件语义一致。公开轨迹是字段筛选和对象去重后的派生材料，provenance.json 单独记录公开文件哈希；原冻结清单仅说明导出时本地原文件核验通过，不表示公开文件与原始整份结果逐字节相同。", "Input and labels retain the semantics of their source files. Public trajectories are derived materials with selected fields and deduplicated objects. `provenance.json` records public-file hashes separately. The original frozen inventories show that local source files passed verification at export time; public files are not byte-for-byte copies of the complete original results."),
      t("只包含已记录的工具调用元数据、试验与正式说明，不包含隐藏思考正文、原始会话流、凭据、个人路径或 CLI 缓存。无法从这些摘要补出未记录的推理过程。", "Only recorded tool-call metadata, trials, and submitted explanations are included. Hidden reasoning text, raw session streams, credentials, personal paths, and CLI caches are excluded. Unrecorded reasoning cannot be reconstructed from these summaries."),
      t("完整历史源码不在本包中，只有原记录指纹，不能声称具备逐字节重现旧模型实验的全部条件。", "The bundle contains recorded fingerprints, not the complete historical source code. It does not provide all conditions needed to reproduce historical model experiments byte for byte."), "",
      t("返回[案例导航](../README.zh-CN.md)。", "Return to the [case-study index](../README.md)."), ""]
    return "\n".join(lines).encode("utf-8")


def export_case(root, case):
    from rdkit import rdBase
    import PIL
    source = Source(Path(root) / "runs" / case["source"])
    inputs = source.read("input.json")
    require(len(inputs) == 1 and inputs[0]["sample_id"] in ALLOWED_SAMPLE_IDS, "Unexpected source sample")
    reference = source.read("labels.json")
    if case["id"] == "butyl-butanoate":
        require(source.read("retry/input.json") == inputs and source.read("retry/labels.json") == reference, "Retry inputs differ")
    pool = Pool()
    results, traces, versions = {}, {}, {}
    for name, relative in case["stages"]:
        results[name], traces[name], versions[name] = stage_export(source, relative, pool)
        require(results[name]["sample_id"] == inputs[0]["sample_id"], "Mixed case source")
    require(len(reference) == 1 and reference[0]["sample_id"] == inputs[0]["sample_id"], "Reference identity differs")
    require("true_smiles" not in inputs[0], "Reference leaked into inference input")
    main_trace = traces[case["main"]]
    require(sum(len(s["trials"]) for s in main_trace["sessions"]) == case["trials"], "Trial inventory changed")
    require(len(results[case["main"]]["source_slots"]) == 10, "Source slots lost")
    require(results[case["main"]]["ranking_complete"] is False, "Historical completeness changed")
    trajectory = {"schema_version": "case_study_v1", "sample_id": inputs[0]["sample_id"],
                  "main_stage": case["main"], "stages": traces, "objects": pool.data,
                  "notes": "All recorded trials; selected is a tool reference, committed is a host state change. Model explanations are not verified mechanisms."}
    result = {"schema_version": "case_study_v1", "main_stage": case["main"], "stages": results}
    contents = {"input.json": encode(inputs), "reference.json": encode(reference),
                "trajectory.json": encode(trajectory), "result.json": encode(result)}
    contents["README.zh-CN.md"] = case_markdown(case, inputs, reference, results, traces, pool)
    contents["README.md"] = case_markdown(case, inputs, reference, results, traces, pool, english=True)
    contents["trajectory.png"] = render_figure(case, focus_steps(main_trace, case["focus"], pool), pool)
    provenance = {"schema_version": "case_study_v1", "source_directory": "runs/" + case["source"],
        "publication_scope": "Owner-authorized exception for this case only; other research data remains private.",
        "historical_stages": versions, "source_files_sha256": source.hashes,
        "exporter": {"path": "scripts/export_case_studies.py", "sha256": digest(Path(__file__).read_bytes()),
                     "python": sys.version.split()[0], "rdkit": rdBase.rdkitVersion, "pillow": PIL.__version__},
        "transforms": ["Field projection; exclude local paths, endpoints, workspace/cache/credential contents and raw session streams.",
                       "Intern graphs, full score objects and individual evidence paths by canonical-JSON SHA-256.",
                       "Preserve old encoding, original scores, ordering, trial decisions and incomplete status; no inference or rescoring.",
                       "Render focus structures from recorded SMILES; no new experimental claim.",
                       "Provide Chinese and English editorial narratives; retain model-authored text in its original language."],
        "exported_files_sha256": {name: digest(raw) for name, raw in sorted(contents.items())},
        "checksum_scope": "All other files in this case directory; provenance.json excludes itself to avoid recursive hashing.",
        "reproduction_limit": "A source fingerprint is not a source snapshot. Historical model results are not promised reproducible with current code."}
    contents["provenance.json"] = encode(provenance)
    for name, raw in contents.items():
        if name.endswith(".json"): check_public(json.loads(raw))
        elif name.endswith(".md"): check_public(raw.decode("utf-8"))
    return contents


INDEX = """# 公开案例展示

[English](README.md) | **简体中文**

| 分子 | 最终结果 |
|---|---|
| [丁酸正丁酯](butyl-butanoate/README.zh-CN.md) | 参考结构排名第 2。 |
| [三甲基正戊基硅烷](trimethylpentylsilane/README.zh-CN.md) | 参考结构与另外 5 个候选按证据评分并列第 1，在结果列表中列第 6。 |
"""


INDEX_EN = """# Case studies

**English** | [简体中文](README.zh-CN.md)

| Molecule | Final result |
|---|---|
| [Butyl butanoate](butyl-butanoate/README.md) | The reference structure ranked second. |
| [Trimethylpentylsilane](trimethylpentylsilane/README.md) | The reference structure tied with five other candidates for first by evidence score and appeared sixth in the results list. |
"""


def build(root):
    bundle = {"README.zh-CN.md": INDEX.encode("utf-8"), "README.md": INDEX_EN.encode("utf-8")}
    for case in CASES:
        for name, raw in export_case(root, case).items():
            bundle[f"{case['id']}/{name}"] = raw
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Rebuild in memory and verify existing public files; no writes")
    args = parser.parse_args()
    destination = ROOT / "case_studies"
    require(args.check or not destination.exists(), "case_studies already exists; use --check (no overwrite)")
    bundle = build(ROOT)
    if args.check:
        actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
        require(actual == set(bundle), "Public file inventory differs")
        for name, raw in bundle.items():
            require((destination / name).read_bytes() == raw, "Export differs: " + name)
    else:
        destination.mkdir()
        for name, raw in bundle.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print(json.dumps({"mode": "checked" if args.check else "created", "cases": len(CASES),
                      "files": len(bundle), "bytes": sum(map(len, bundle.values())), "model_calls": 0}))


if __name__ == "__main__":
    main()
