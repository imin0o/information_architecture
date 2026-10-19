"""終了報告（P3-4）。実行記録から、成果物と版、仮定、検証の区別、人の判断が必要な事項を report.md にまとめる。

報告は記録から導く派生物で、成果物として登録しない。模擬評価（simulated）と構造の測定は実測ではないため、
実測（tested_*）と別の欄に分ける。機密区分は現行の成果物と入力のうち最も厳しいものを継承する。
"""

import re
from pathlib import Path

from . import judgment, validation_plan, yamlio
from .decisions import max_classification
from .run_store import load

NAME = "report.md"
QUESTION = re.compile(r"(?<![0-9A-Za-z_-])(Q-[0-9]{3,})(?![0-9])")  # 回答の説明に挙がった質問ID
MEASURED = ("tested_supported", "tested_refuted", "tested_inconclusive")


def _table(head: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["なし", ""]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |" for r in rows]
    return lines + [""]


def _optional(directory: Path, name: str, key: str) -> list[dict]:
    return yamlio.load(directory / name)[key] if (directory / name).is_file() else []


def _human_rows(directory: Path, run: dict) -> list[str]:
    plan = next((a for a in run["artifacts"] if a["kind"] == "validation_plan" and a["status"] == "current"), None)
    if plan is None:
        return []
    rows = validation_plan._table((directory / plan["path"]).read_text(encoding="utf-8").splitlines())
    if not rows or "実行者" not in rows[0]:
        return []
    who, state = rows[0].index("実行者"), rows[0].index("実行状況")
    return [f"検証計画（{plan['artifact_id']} v{plan['version']}）: " + " / ".join(r)
            for r in rows[2:] if len(r) > max(who, state) and r[who] == "人" and not r[state].startswith("実行済み")]


def _judgment(directory: Path, run: dict) -> tuple[list[str], list[str]]:
    """独立判定の要約と、人に示す事項。"""
    notes = [h["note"] for h in run["history"] if h["event"] == "judged"]
    if not (directory / judgment.FILE).is_file():
        if not notes or notes[-1].startswith("not_run"):
            reason = notes[-1] if notes else "判定を依頼していない"
            return [f"未実施（{reason}）"], [f"独立判定が未実施: {reason}"]
        # 判定を依頼したが、記録がないか検査で採らなかった。前回までの記録は退避してある
        old = sorted(p.name for p in directory.glob("rubric-judgment.*.yaml"))
        text = f"判定を依頼したが有効な記録がない（{notes[-1]}）" + (f"。退避した記録: {', '.join(old)}" if old else "")
        return [text], [text]
    j = yamlio.load(directory / judgment.FILE)
    lines = [f"- 判定者: {j['judge']['agent']}（{j['judge']['host']} / {j['judge']['model']}）、{j['judged_at']}",
             f"- ルーブリック {j['rubric_version']}、判定: **{j['verdict']}**（{j['reason']}）"]
    lines += [f"- 問題（{i['severity']}、{i['item']}）: {i['problem']}。残す理由と影響: {i['residual']}" for i in j["issues"]]
    asks = [f"入力の不足（{g['item']}）: {g['gap']}（{g['handling']}）" for g in j.get("input_gaps", [])]
    return lines, asks


def build(directory: Path) -> str:
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")["inputs"]
    decisions = [d for d in load(directory, "decisions.yaml")["decisions"] if d["status"] == "active"]
    findings = _optional(directory, "findings.yaml", "findings")
    sims = _optional(directory, "simulations.yaml", "simulations")
    current = [a for a in run["artifacts"] if a["status"] == "current"]
    level = max_classification([i["classification"] for i in inputs] + [a["classification"] for a in current])
    env = run["environment"]
    out = [f"# 実行の終了報告 {run['run_id']}", "", f"- 機密区分: {level}",
           f"- 状態: {run['status']}（Refine {run['refine']['rounds']}巡）",
           f"- ホスト・モデル: {env['host']} / {env['model']}", f"- 課題の種類: {run['task_type']}", ""]
    out += ["## 成果物と版", ""] + _table(["ID", "種類", "版", "状態", "区分", "パス"], [
        [a["artifact_id"], a["kind"], f"v{a['version']}", a["status"], a["classification"], a["path"]]
        for a in run["artifacts"]])
    out += ["## 仮定", ""] + _table(["判断", "仮定", "重要"], [
        [f"{d['decision_id']} r{d['decision_revision']}", a["statement"], "critical" if a["critical"] else ""]
        for d in decisions for a in d["assumptions"]])
    measured = [d for d in decisions if d["validation_status"] in MEASURED]
    simulated = [d for d in decisions if d["validation_status"] == "simulated"]
    untested = [d for d in decisions if d["validation_status"] in ("not_tested", "planned")]
    out += ["## 検証の区別", "", "模擬評価と構造の測定は利用者の行動ではなく、実測に数えない。", "",
            f"- 実測（tested_*）: {', '.join(f'{d['decision_id']} r{d['decision_revision']}（{d['validation_status']}）' for d in measured) or 'なし'}",
            f"- 模擬評価（simulated）: {', '.join(f'{d['decision_id']} r{d['decision_revision']}' for d in simulated) or 'なし'}"
            + (f"。記録 {', '.join(s['simulation_id'] for s in sims)}" if sims else ""),
            f"- 未検証（not_tested・planned）: {', '.join(f'{d['decision_id']} r{d['decision_revision']}' for d in untested) or 'なし'}",
            f"- 構造の測定: {'measurements/structure.yaml' if (directory / 'measurements/structure.yaml').is_file() else 'なし'}", ""]
    left = [f for f in findings if f["status"] != "resolved"]
    out += ["## 残る指摘", ""] + _table(["ID", "重大度", "状態", "対象", "問題"], [
        [f["finding_id"], f["severity"], f["status"], f"{f['target']['artifact_id']} v{f['target']['version']}", f["problem"]]
        for f in left])
    judged, asks = _judgment(directory, run)
    out += ["## 独立判定", ""] + judged + [""]
    answered = {q for i in inputs if i["kind"] == "hearing_answer" for q in QUESTION.findall(i["description"])}
    asks = [f"質問 {p.stem} に未回答（{p.relative_to(directory).as_posix()}）。仮定で進めた"
            for p in sorted((directory / "questions").glob("Q-*.md")) if p.stem not in answered] + asks
    asks += [f"送信を止めた: {t['destination']}（{t['data_kind']}）" for t in run["transmissions"] if t["outcome"] == "blocked"]
    asks += [f"未解決の重大な指摘 {f['finding_id']}: {f['problem']}" for f in left if f["severity"] == "critical"]
    asks += [f"中断: {h['note']}" for h in run["history"][-1:] if h["event"] == "interrupted"]
    asks += _human_rows(directory, run)
    out += ["## 人の判断が必要な事項", ""] + ([f"- {a}" for a in asks] or ["なし"]) + [""]
    return "\n".join(out)


def write(directory: Path) -> dict:
    text = build(directory)
    (directory / NAME).write_text(text, encoding="utf-8", newline="\n")
    return {"report": (directory / NAME).as_posix(), "lines": text.count("\n") + 1}
