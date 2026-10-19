"""構造の測定（P2-1、P2-5）。iakit measure structure。

サイトマップ・統制語彙・主要タスクから導線の指標を測り、ビルド済みサイトがあればHTMLでも同じ指標を測って
変更前後を比べる。利用者の行動ではないため、判断の validation_status は変えない。
基準を下回った項目は iakit finding add にそのまま渡せる指摘の候補として出す。
"""

from pathlib import Path

from . import manifest, measure_site, yamlio
from .run_store import load, now

OUT = Path("measurements") / "structure.yaml"
BASIS = "iakit measure structure"


def sitemap_metrics(site: dict, vocab: dict | None) -> dict:
    nodes = {n["node_id"]: n for n in site["nodes"]}
    concepts = {c["concept_id"]: c for c in vocab["concepts"]} if vocab else {}
    labels: dict[str, list[str]] = {}
    for n in site["nodes"]:
        labels.setdefault(n["label"], []).append(n["node_id"])
    match = {}
    for n in site["nodes"]:
        c = concepts.get(n["concept_id"])
        if c:
            terms = [c["pref_label"]["ja"], *c["alt_labels"]]
            # 「地区から探す」のように語を含む入口のラベルは contains として区別し、指摘の候補にしない
            match[n["node_id"]] = ("pref_label" if n["label"] == terms[0] else "alt_label" if n["label"] in terms else
                                   "contains" if any(term in n["label"] for term in terms) else "mismatch")
    root = nodes[site["root"]]
    return {
        "tasks": [{"task_id": t["task_id"], "target": t["path"][-1], "label": nodes[t["path"][-1]]["label"],
                   "clicks": len(t["path"]) - 1} for t in site["tasks"]],
        "one_click": sorted({n["node_id"] for n in site["nodes"] if n["parent"] == site["root"]} | set(root["links"])),
        "label_match": dict(sorted(match.items())),
        "duplicate_labels": {k: v for k, v in sorted(labels.items()) if len(v) > 1},
    }


def site_task_clicks(site: dict, html: dict) -> dict:
    """ノードのラベルとページの名称（h1、なければtitle）が一致するページを対応付ける。"""
    by_name: dict[str, list[str]] = {}
    for page, info in html["pages"].items():
        by_name.setdefault(info["name"], []).append(page)
    nodes = {n["node_id"]: n for n in site["nodes"]}
    out = []
    for t in site["tasks"]:
        label = nodes[t["path"][-1]]["label"]
        pages = sorted(by_name.get(label, []))
        reach = [html["metrics"]["clicks"][p] for p in pages if p in html["metrics"]["clicks"]]
        out.append({"task_id": t["task_id"], "label": label, "pages": pages, "clicks": min(reach, default=None)})
    return {"tasks": out, "unmapped_nodes": sorted(n["node_id"] for n in site["nodes"]
                                                   if n["node_id"] != site["root"] and n["label"] not in by_name)}


def _site(root: Path) -> dict:
    pages = measure_site.crawl(root)
    return {"pages": pages, "metrics": measure_site.metrics(pages)}


def _shown(path: Path) -> str:
    return path.as_posix() if not path.is_absolute() else path.name


def _candidate(target: dict, severity: str, locator: str, problem: str, recommendation: str, condition: str) -> dict:
    return {"severity": severity, "target": target, "locator": locator, "problem": problem,
            "basis": [f"{BASIS}（{OUT.as_posix()}）"], "recommendation": recommendation,
            "resolution_condition": condition, "return_to": "structure"}


def candidates(target: dict, sm: dict, site: dict | None, max_clicks: int) -> list[dict]:
    out = [_candidate(target, "major", t["task_id"], f"到達先「{t['label']}」まで {t['clicks']} クリックで、基準 {max_clicks} を超える",
                      "経路を短くするか、上位の階層からのリンクを加える", f"サイトマップ上で {max_clicks} クリック以内になる")
           for t in sm["tasks"] if t["clicks"] > max_clicks]
    out += [_candidate(target, "minor", nid, "ノードのラベルが統制語彙の優先ラベル・同義語のどれとも一致せず、含みもしない",
                       "優先ラベルに合わせるか、語彙に同義語として加える", "ラベルが優先ラベルか同義語に一致する")
            for nid, m in sm["label_match"].items() if m == "mismatch"]
    out += [_candidate(target, "major", ", ".join(ids), f"同じラベル「{label}」のノードが複数ある",
                       "区別できるラベルにするか、1つにまとめる", "同名のノードがない")
            for label, ids in sm["duplicate_labels"].items()]
    if site is None:
        return out
    m = site["metrics"]
    for t in site["tasks"]:
        if not t["pages"]:
            out.append(_candidate(target, "major", t["task_id"], f"到達先「{t['label']}」と同じ名称のページがサイトにない",
                                  "ページの見出しをサイトマップのラベルに合わせる", "到達先のページを名称で特定できる"))
        elif t["clicks"] is None:
            out.append(_candidate(target, "critical", t["task_id"], f"到達先「{t['label']}」のページにトップからたどれない",
                                  "トップからの導線を加える", "トップからリンクをたどって到達できる"))
        elif t["clicks"] > max_clicks:
            out.append(_candidate(target, "major", t["task_id"], f"サイトで到達先まで {t['clicks']} クリックかかる",
                                  "導線を短くする", f"サイトで {max_clicks} クリック以内になる"))
    out += [_candidate(target, "major", f"サイト: {p}", "どのページからもリンクされていない",
                       "ナビゲーションか関連ページからリンクする", "被リンクが1件以上ある") for p in m["orphans"]]
    out += [_candidate(target, "major", f"サイト: 「{t}」", f"同じ文言のリンクが別のページ {d} を指す",
                       "リンク文言を行き先ごとに区別する", "同じ文言のリンクが1つのページだけを指す")
            for t, d in m["same_text_different_dest"].items()]
    out += [_candidate(target, "minor", f"サイト: {x['dest']}", f"リンク文言「{x['text']}」が着地先の名称「{x['name']}」と異なる",
                       "リンク文言か着地先の見出しをそろえる", "リンク文言と着地先の名称が一致する")
            for x in m["landing_name_mismatch"]]
    return out


def _summary(site: dict) -> dict:
    m = site["metrics"]
    return {"pages": m["pages"], "one_click": len(m["one_click"]), "nav_listed": len(m["nav_listed"]),
            "landing_name_mismatch": len(m["landing_name_mismatch"]), "link_text_variants": len(m["link_text_variants"]),
            "same_text_different_dest": len(m["same_text_different_dest"]), "orphans": len(m["orphans"])}


def structure(directory: Path, site_dir: Path | None = None, baseline: Path | None = None, max_clicks: int = 3) -> dict:
    if baseline and not site_dir:
        raise ValueError("--baseline は --site と一緒に指定する")
    run = load(directory, "run.yaml")
    reg = next((a for a in run["artifacts"] if a["kind"] == "sitemap" and a["status"] == "current"), None)
    if reg is None:
        raise ValueError("現行のサイトマップが登録されていない")
    if manifest.sha256_file(directory / reg["path"]) != reg["sha256"]:
        raise ValueError("サイトマップの内容が登録時から変わっている。先に登録し直す")
    vreg = next((a for a in run["artifacts"] if a["kind"] == "controlled_vocabulary"), None)
    site = yamlio.load(directory / reg["path"])
    vocab = yamlio.load(directory / vreg["path"]) if vreg else None
    target = {"artifact_id": reg["artifact_id"], "version": reg["version"]}
    sm = sitemap_metrics(site, vocab)
    doc = {"measurement": "structure", "run_id": run["run_id"], "measured_at": now(), "target": target,
           "source_sha256": reg["sha256"],
           "thresholds": {"max_clicks": max_clicks}, "sitemap": sm, "site": None, "baseline": None, "comparison": None}
    after = None
    if site_dir:
        after = _site(site_dir)
        after.update(site_task_clicks(site, after))
        doc["site"] = {"dir": _shown(site_dir), "summary": _summary(after), "metrics": after["metrics"],
                       "tasks": after["tasks"], "unmapped_nodes": after["unmapped_nodes"]}
    if baseline:
        before = _site(baseline)
        before.update(site_task_clicks(site, before))
        doc["baseline"] = {"dir": _shown(baseline), "summary": _summary(before), "tasks": before["tasks"]}
        doc["comparison"] = {
            "summary": {k: {"before": v, "after": doc["site"]["summary"][k]} for k, v in _summary(before).items()},
            "tasks": [{"task_id": b["task_id"], "before": b["clicks"], "after": a["clicks"]}
                      for b, a in zip(before["tasks"], after["tasks"])]}
    doc["finding_candidates"] = candidates(target, sm, after, max_clicks)
    out = directory / OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
    return {"result": "pass", "written": OUT.as_posix(), "target": target,
            "finding_candidates": len(doc["finding_candidates"]), "comparison": doc["comparison"],
            "next": "候補を確かめ、採るものを iakit finding add で指摘にする。測定は validation_status を変えない"}
