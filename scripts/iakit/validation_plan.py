"""検証計画の表の検査（P2-4）。実行者がエージェントの行は評価の段階で実行し、結果の記録を示す。

人の行は計画のまま残してよい。実行済みとする場合は実ユーザー調査の記録（STUDY-nnn）を示す。
"""

import re

from pathlib import Path

from . import manifest, measure, simulations, yamlio

SECTION = "## 未検証の判断と検証方法"
EXECUTORS = ("エージェント", "人")
AGENT_EVIDENCE = re.compile(r"SIM-[0-9]{3}|measurements/")
STUDY = re.compile(r"(?<![0-9A-Za-z_-])STUDY-[0-9]{3}(?![0-9A-Za-z_-])")
SIM = re.compile(r"(?<![0-9A-Za-z_-])SIM-[0-9]{3}(?![0-9A-Za-z_-])")
MEASUREMENT = re.compile(r"measurements/[^\s`）)\]|、,]+\.ya?ml")


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


def evidence_errors(lines: list[str], tag: str, directory: Path, run: dict) -> list[str]:
    """実行済みの証跡を実体・実行ID・対象版と照合する。現行の計画と登録候補に適用する。"""
    rows = _table(lines)
    if len(rows) < 2 or "実行者" not in rows[0] or "実行状況" not in rows[0]:
        return []  # 書式の誤りは errors が返す
    who, state = rows[0].index("実行者"), rows[0].index("実行状況")
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    found = []

    def current(ref):
        a = arts.get(ref.get("artifact_id")) if isinstance(ref, dict) else None
        return bool(a and a["status"] == "current" and a["version"] == ref.get("version"))

    cache = {}

    def records(name, schema, collection, key):
        if name not in cache:
            doc = yamlio.load(directory / name)
            invalid = yamlio.schema_errors(doc, schema)
            if invalid or doc["run_id"] != run["run_id"]:
                raise ValueError(f"{name}: 形式または実行IDが一致しない")
            if name == "simulations.yaml":
                a = next((a for a in arts.values() if a["kind"] == "sitemap"), None)
                site = yamlio.load(directory / a["path"]) if a else None
                if site is not None and yamlio.schema_errors(site, "sitemap.schema.json"):
                    raise ValueError("評価対象のサイトマップの形式が不正")
                invalid = simulations.record_errors(doc, arts, site)
                if invalid:
                    raise ValueError(" / ".join(invalid[:3]))
            cache[name] = {s[key]: s for s in doc[collection]}
        return cache[name]

    for n, row in enumerate(rows[2:], start=1):
        if len(row) <= max(who, state) or not row[state].startswith("実行済み"):
            continue
        status = row[state]
        evidence = re.match(r"実行済み\s*[（(]([^）)]+)[）)]", status)
        status = evidence[1] if evidence else status
        try:
            if row[who] == "エージェント":
                refs, paths = SIM.findall(status), MEASUREMENT.findall(status)
                if not refs and not paths:
                    raise ValueError("結果の記録を特定できない")
                for sid in refs:
                    sim = records("simulations.yaml", "simulation.schema.json", "simulations", "simulation_id").get(sid)
                    if sim is None or not current(sim["target"]) or arts[sim["target"]["artifact_id"]]["kind"] != "sitemap":
                        raise ValueError(f"{sid}: 記録がないか評価対象が現行版ではない")
                    a = arts[sim["target"]["artifact_id"]]
                    if manifest.sha256_file(directory / a["path"]) != a["sha256"]:
                        raise ValueError(f"{sid}: 対象の内容が登録時から変わっている")
                for rel in paths:
                    path = (directory / rel).resolve()
                    if not path.is_relative_to(directory.resolve()):
                        raise ValueError("測定記録が実行ディレクトリの外にある")
                    doc = yamlio.load(path)
                    if not isinstance(doc, dict) or doc.get("measurement") != "structure" or doc.get("run_id") != run["run_id"]:
                        raise ValueError(f"{rel}: 構造の測定記録ではないか実行IDが一致しない")
                    ref = doc.get("target")
                    if not current(ref) or arts[ref["artifact_id"]]["kind"] != "sitemap":
                        raise ValueError(f"{rel}: 測定対象が現行版ではない")
                    if doc.get("source_sha256") != arts[ref["artifact_id"]]["sha256"]:
                        raise ValueError(f"{rel}: 測定元のハッシュが一致しない。構造を測定し直す")
                    if manifest.sha256_file(directory / arts[ref["artifact_id"]]["path"]) != doc["source_sha256"]:
                        raise ValueError(f"{rel}: 対象の内容が登録時から変わっている")
                    site = yamlio.load(directory / arts[ref["artifact_id"]]["path"])
                    vreg = next((a for a in arts.values() if a["kind"] == "controlled_vocabulary"), None)
                    vocab = yamlio.load(directory / vreg["path"]) if vreg else None
                    if doc.get("sitemap") != measure.sitemap_metrics(site, vocab):
                        raise ValueError(f"{rel}: 測定結果がサイトマップからの導出と一致しない")
            elif row[who] == "人":
                refs = STUDY.findall(status)
                if not refs:
                    raise ValueError("実行済みの証跡に STUDY-nnn がない")
                for sid in refs:
                    study = records("observations.yaml", "observation.schema.json", "studies", "study_id").get(sid)
                    if study is None or not current(study["target"]):
                        raise ValueError(f"{sid}: 調査記録がないか対象が現行版ではない")
        except (OSError, ValueError) as exc:
            found.append(f"{tag}: {n}行目の実行済みの証跡が無効: {exc}")
    return found
