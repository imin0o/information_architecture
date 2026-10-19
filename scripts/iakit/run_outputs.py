"""実行の成果物一式の検査: 経路ごとの必須成果物、Markdown成果物、評価の指摘、取込み結果（FR-06〜FR-08、IN-04）。"""

import re
from pathlib import Path

from . import runs
from .decisions import latest_active

STAGE_KINDS = {
    "analyze": ["problem_definition", "information_audit", "content_inventory"],
    "classify": ["taxonomy", "controlled_vocabulary", "classification_options"],
    "structure": ["information_architecture", "sitemap"],
    "validate": ["ia_review", "validation_plan", "review_findings"],
}
MARKDOWN_KINDS = {"problem_definition", "information_audit", "classification_options", "information_architecture",
                  "ia_review", "validation_plan"}
ASSUMPTION_HEADING = "## 仮定に基づく箇所"
# templates/*.md の必須節。対応が変わったらtest_markdown_contract_matches_templatesで検出する。
MARKDOWN_HEADINGS = {
    "problem_definition": ["判定に要る4項目", "解くべき問題", "主要タスク", "範囲と制約", "関連する判断"],
    "information_audit": ["インベントリの概要", "重複", "欠落", "矛盾", "粒度の不一致", "同名異義語・同義語の候補",
                          "情報ニーズと探索行動の仮説", "不明点と追加調査事項", "後の段階への申し送り"],
    "classification_options": ["比較した案", "比較", "採った案と理由", "同名異義語・同義語・矛盾の扱い", "対象外・未分類", "未検証の事項"],
    "information_architecture": ["構造の種類と採用理由", "代替構造", "ナビゲーション", "主要タスクの経路", "コンテンツの配置", "意図的な例外", "未検証の事項"],
    "ia_review": ["指摘の一覧", "観点ごとの結果", "主要タスクの経路の点検", "機械検査", "ルーブリックの観点との対応",
                  "改善の優先度と修正理由", "未解決の指摘", "模擬評価の扱い"],
    "validation_plan": ["未検証の判断と検証方法", "方法の適用可否", "実施の条件", "結果の記録"],
}
# 前後が英数字・ハイフンでないIA-nnnだけを判断IDとみなす。RUN-X-IA-001 のような実行IDの一部には一致させない。
# 日本語の文字に隣接したID（「判断IA-001の」）は判断IDとして扱う
_EDGE = r"(?<![0-9A-Za-z_-])"
DECISION_ID = re.compile(_EDGE + r"IA-[0-9]{3}(?![0-9A-Za-z_-])(?! r[0-9])")
DECISION_REF = re.compile(_EDGE + r"(IA-[0-9]{3}) r([0-9]+)(?![0-9A-Za-z_])")


def _markdown_lines(text: str) -> list[str]:
    """コード例に書かれた見出し・区分を、成果物の必須情報として数えない。"""
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
        elif fence is None:
            lines.append(line)
    return lines


def required_errors(run: dict) -> list[str]:
    """完了・未解決で終えた対応経路は、各段階の成果物をすべて持つ。"""
    if not run["route"]["supported"] or run["status"] not in ("completed", "unresolved"):
        return []
    present = {a["kind"] for a in run["artifacts"]}
    return [f"run.yaml: 段階 {stage} の成果物 {kind} がない" for stage in run["route"]["stages"]
            for kind in STAGE_KINDS.get(stage, []) if kind not in present]


def markdown_errors(directory: Path, run: dict, decisions: list[dict]) -> list[str]:
    errors, known = [], {(d["decision_id"], d["decision_revision"]) for d in decisions}
    active = latest_active(decisions)
    for a in (a for a in run["artifacts"] if a["kind"] in MARKDOWN_KINDS):
        path = directory / a["path"]
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        tag = f"{a['artifact_id']}（{a['path']}）"
        lines = _markdown_lines(text)
        headings = [ln.strip() for ln in lines if ln.startswith("## ")]
        first = next(iter(headings), None)
        if first != ASSUMPTION_HEADING:
            errors.append(f"{tag}: 最初の節を「{ASSUMPTION_HEADING}」にする（仮定がなければ「なし」と書く）")
        errors += [f"{tag}: 必須見出し「## {heading}」がない"
                   for heading in MARKDOWN_HEADINGS[a["kind"]] if f"## {heading}" not in headings]
        header = "\n".join(lines[:next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))])
        levels = re.findall(r"^\s*(?:[-*]\s+)?機密区分\s*[:：]\s*`?(public|internal|confidential)`?\s*$", header, re.M)
        if levels != [a["classification"]]:
            errors.append(f"{tag}: 冒頭に機密区分: {a['classification']} を1件示す（実行記録と一致させる）")
        errors += [f"{tag}: 判断 {m.group(0)} を版なしで参照している（{m.group(0)} rN と書く）"
                   for m in DECISION_ID.finditer(text)]
        for m in DECISION_REF.finditer(text):
            key = (m.group(1), int(m.group(2)))
            if key not in known:
                errors.append(f"{tag}: 判断 {m.group(0)} が判断記録にない")
            elif a["status"] == "current" and key not in active:
                errors.append(f"{tag}: 現行でない判断 {m.group(0)} を参照している")
    plan = next((a for a in run["artifacts"] if a["kind"] == "validation_plan"), None)
    if plan and (directory / plan["path"]).is_file():
        text = (directory / plan["path"]).read_text(encoding="utf-8")
        refs = {(m.group(1), int(m.group(2))) for m in DECISION_REF.finditer(text)}
        errors += [f"validation_plan: planned の判断 {d['decision_id']} r{d['decision_revision']} に検証方法が対応付いていない"
                   for d in decisions if (d["decision_id"], d["decision_revision"]) in active
                   and d["validation_status"] == "planned" and (d["decision_id"], d["decision_revision"]) not in refs]
    return errors


def _header(text: str, markdown: bool) -> str:
    """成果物の冒頭。Markdownは最初の節の本文、YAMLは最初の値より前のコメント行。"""
    lines = text.splitlines()
    if not markdown:
        return "\n".join(ln for ln in lines[:next((i for i, ln in enumerate(lines)
                                                   if ln.strip() and not ln.startswith("#")), len(lines))])
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end])


def assumption_errors(directory: Path, run: dict, decisions: list[dict]) -> list[str]:
    """仮定を持つ判断を参照する現行の成果物は、冒頭にその判断をIDと版で示す（IN-04、R-07）。"""
    with_assumptions = {(d["decision_id"], d["decision_revision"]) for d in decisions if d["assumptions"]}
    errors = []
    for a in run["artifacts"]:
        path = directory / a["path"]
        if a["status"] != "current" or a["kind"] == "review_findings" or not path.is_file():
            continue
        header = _header(path.read_text(encoding="utf-8"), a["kind"] in MARKDOWN_KINDS)
        shown = {(m.group(1), int(m.group(2))) for m in DECISION_REF.finditer(header)}
        place = "最初の節" if a["kind"] in MARKDOWN_KINDS else "先頭のコメント"
        errors += [f"{a['artifact_id']}（{a['path']}）: 仮定を持つ判断 {d} r{r} を{place}に示していない"
                   for d, r in sorted((x["decision_id"], x["decision_revision"]) for x in a["decision_refs"])
                   if (d, r) in with_assumptions and (d, r) not in shown]
    return errors


def finding_item_errors(doc: dict, run: dict) -> list[str]:
    """指摘ごとの検査。実行記録の未解決一覧との同期は登録時に行うため含めない。"""
    errors, seen = [], set()
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    for f in doc["findings"]:
        fid = f["finding_id"]
        if fid in seen:
            errors.append(f"{fid}: finding_id が重複している")
        seen.add(fid)
        target = arts.get(f["target"]["artifact_id"])
        if target is None or f["target"]["version"] > target["version"]:
            errors.append(f"{fid}: 対象 {f['target']['artifact_id']} v{f['target']['version']} が実行記録にない")
        if f["round"] > run["refine"]["rounds"]:
            errors.append(f"{fid}: round {f['round']} が実行記録の巡数 {run['refine']['rounds']} を超える")
        r = f["resolved_in"]
        if r and (r["artifact_id"] != f["target"]["artifact_id"] or r["version"] <= f["target"]["version"]
                  or r["version"] > (target or {}).get("version", 0)):
            errors.append(f"{fid}: resolved_in は対象と同じ成果物の、指摘より新しい登録済みの版にする")
        if f["status"] == "unresolved" and run["refine"]["rounds"] < 2 and f["severity"] == "critical":
            errors.append(f"{fid}: 2巡に達する前の重大な指摘は open のまま修正する")
    return errors


def findings_errors(doc: dict, run: dict) -> list[str]:
    errors = finding_item_errors(doc, run)
    open_critical = runs.unresolved_critical(doc["findings"])
    if open_critical != sorted(run["refine"]["unresolved_critical"]):
        errors.append(f"refine.unresolved_critical {run['refine']['unresolved_critical']} が指摘の状態 {open_critical} と一致しない")
    return errors


def check(directory: Path, docs: dict) -> list[str]:
    run = docs.get("run.yaml")
    found = []
    if not run:
        return found
    decisions = docs.get("decisions.yaml", {"decisions": []})["decisions"]
    found += required_errors(run) + markdown_errors(directory, run, decisions)
    found += assumption_errors(directory, run, decisions)
    reg = next((a for a in run["artifacts"] if a["kind"] == "review_findings"), None)
    doc = docs.get("findings.yaml")
    if doc:
        found += findings_errors(doc, run)
        if reg is None or (reg["artifact_id"], reg["version"], reg["classification"]) != (
                doc["artifact_id"], doc["version"], doc["classification"]):
            found.append("findings.yaml: artifact_id・版・機密区分が実行記録の review_findings と一致しない")
    elif reg:
        found.append("findings.yaml: 登録された成果物のデータがない")
    if reg and reg["path"] != "findings.yaml":
        found.append("review_findings: 指摘は実行ディレクトリ直下の findings.yaml に置く")
    return found
