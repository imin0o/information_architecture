"""検証計画の表の検査（P2-4）。実行者がエージェントの行は評価の段階で実行し、結果の記録を示す。

人の行は計画のまま残してよい。実行済みとする場合は実ユーザー調査の記録（STUDY-nnn）を示す。
"""

import re

SECTION = "## 未検証の判断と検証方法"
EXECUTORS = ("エージェント", "人")
AGENT_EVIDENCE = re.compile(r"SIM-[0-9]{3}|measurements/")
STUDY = re.compile(r"STUDY-[0-9]{3}")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(lines: list[str]) -> list[list[str]]:
    start = next((i for i, ln in enumerate(lines) if ln.strip() == SECTION), None)
    if start is None:
        return []
    rows = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if ln.strip().startswith("|"):
            rows.append(_cells(ln))
    return rows


def errors(lines: list[str], tag: str) -> list[str]:
    rows = _table(lines)
    if not rows:
        return [f"{tag}: 「{SECTION}」に表がない"]
    header = rows[0]
    if "実行者" not in header or "実行状況" not in header:
        return [f"{tag}: 検証方法の表に「実行者」「実行状況」の列がない"]
    who, state = header.index("実行者"), header.index("実行状況")
    found = []
    for n, row in enumerate(rows[2:], start=1):
        if len(row) <= max(who, state):
            found.append(f"{tag}: 検証方法の表の{n}行目の列が足りない")
            continue
        executor, status = row[who], row[state]
        if executor not in EXECUTORS:
            found.append(f"{tag}: {n}行目の実行者「{executor}」は {'・'.join(EXECUTORS)} のどれかにする")
        elif executor == "エージェント" and not (status.startswith("実行済み") and AGENT_EVIDENCE.search(status)):
            found.append(f"{tag}: {n}行目はエージェントの行で、実行済みと結果の記録（SIM-nnn または measurements/）を示していない")
        elif executor == "人" and status.startswith("実行済み") and not STUDY.search(status):
            found.append(f"{tag}: {n}行目は人の行で、実行済みとするなら調査記録（STUDY-nnn）を示す")
    return found
