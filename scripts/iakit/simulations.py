"""模擬評価の記録の導出と整合性（P2-3、FR-06、AC-07）。

到達率・最初の選択は試行の記録から導き、記録に書かれた値が導出値と異なれば拒否する。
模擬評価は判断を simulated にする根拠にだけ使い、tested_* の根拠にはしない（decisions.decision_errors）。
"""

from collections import Counter
import hashlib

from . import yamlio
from .decisions import RANK, max_classification

NONE = "(none)"


def expected(method: str, item_id: str, site: dict, categories: list[str]) -> tuple[list[str], str | None]:
    """サイトマップから正解のノードと、ルートの次に選ぶべきノードを導く。"""
    nodes = {n["node_id"]: n for n in site["nodes"]}
    if method == "tree_test":
        task = next((t for t in site["tasks"] if t["task_id"] == item_id), None)
        if task is None:
            raise ValueError(f"{item_id}: サイトマップの主要タスクにない")
        content = task.get("target_content_ids")
        if content:
            found = sorted(n["node_id"] for n in site["nodes"] if set(content) <= set(n["content_ids"]))
            if not found:
                raise ValueError(f"{item_id}: 目的のコンテンツをすべて載せるノードがない")
            firsts = first_choices(found, site)
            return found, firsts[0] if len(firsts) == 1 else None
        return [task["path"][-1]], (task["path"][1] if len(task["path"]) > 1 else None)
    found = []
    for n in site["nodes"]:
        if item_id not in n["content_ids"]:
            continue
        cur = n["node_id"]
        seen = set()
        while cur in nodes and cur not in seen:
            seen.add(cur)
            if cur in categories and cur not in found:
                found.append(cur)
            cur = nodes[cur]["parent"]
    if not found:
        raise ValueError(f"{item_id}: 分類先 {categories} のどこにも配置されていない")
    return found, None


def first_choices(targets: list[str], site: dict) -> list[str]:
    """正解ノードを含む主階層の入口。複数の正解経路を同等に扱う。"""
    nodes = {n["node_id"]: n for n in site["nodes"]}
    out = set()
    for target in targets:
        cur, seen = target, set()
        while cur in nodes and cur not in seen:
            seen.add(cur)
            parent = nodes[cur]["parent"]
            if parent == site["root"]:
                out.add(cur)
                break
            cur = parent
    return sorted(out)


def expected_item(method: str, item_id: str, site: dict, categories: list[str]) -> dict:
    exp, first = expected(method, item_id, site, categories)
    task = next((t for t in site["tasks"] if t["task_id"] == item_id), {})
    firsts = (first_choices(exp, site) if method == "tree_test" and task.get("target_content_ids")
              else [first] if first else [])
    return {"expected": exp, "expected_first": first, "expected_first_choices": firsts}


def derive_results(sim: dict) -> list[dict]:
    items = {i["item_id"]: i for i in sim["items"]}
    out = []
    for item_id, item in items.items():
        trials = [t for t in sim["trials"] if t["item_id"] == item_id]
        if not trials:
            continue
        ok = sum(t["answer"] in item["expected"] for t in trials) / len(trials)
        first_choices = item.get("expected_first_choices", [item["expected_first"]] if item["expected_first"] else [])
        direct = (sum(t["first_choice"] in first_choices for t in trials) / len(trials) if first_choices else None)
        firsts = Counter(t["first_choice"] or NONE for t in trials)
        out.append({"item_id": item_id, "attempts": len(trials), "success_rate": round(ok, 3),
                    "direct_first_rate": None if direct is None else round(direct, 3),
                    "first_choices": dict(sorted(firsts.items())),
                    "below_threshold": ok < sim["threshold"]["success_rate"]})
    return out


def record_errors(doc: dict, artifacts: dict[str, dict], site: dict | None = None) -> list[str]:
    """artifacts は実行記録の成果物（ID→登録内容）。"""
    errors, seen = [], set()
    for sim in doc["simulations"]:
        sid, found = sim["simulation_id"], []
        if sid in seen:
            found.append(f"{sid}: simulation_id が重複している")
        seen.add(sid)
        levels = []
        for key, kind in (("target", "sitemap"), ("persona", "problem_definition")):
            ref = sim[key] if key == "target" else sim[key]["source"]
            a = artifacts.get(ref["artifact_id"])
            if a is None or a["kind"] != kind or ref["version"] > a["version"]:
                found.append(f"{sid}: {key} {ref['artifact_id']} v{ref['version']} は登録済みの {kind} ではない")
            elif ref["version"] == a["version"]:
                levels.append(a["classification"])
        if RANK[sim["classification"]] < RANK[max_classification(levels)]:
            found.append(f"{sid}: 機密区分が参照した成果物の区分より低い")
        truth = None
        ref = sim["target"]
        if site and (site["artifact_id"], site["version"]) == (ref["artifact_id"], ref["version"]):
            truth = site
        if "sitemap_snapshot" in sim:
            raw = sim["sitemap_snapshot"]
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != sim.get("sitemap_sha256"):
                found.append(f"{sid}: サイトマップのスナップショットのハッシュが一致しない")
            a = artifacts.get(ref["artifact_id"])
            if a and a["version"] == ref["version"] and a["sha256"] != sim.get("sitemap_sha256"):
                found.append(f"{sid}: スナップショットが登録済みのサイトマップと一致しない")
            try:
                snapshot = yamlio.loads(raw)
                invalid = yamlio.schema_errors(snapshot, "sitemap.schema.json")
                if invalid or (snapshot["artifact_id"], snapshot["version"], snapshot["run_id"]) != (
                        ref["artifact_id"], ref["version"], doc["run_id"]):
                    found.append(f"{sid}: スナップショットの対象・版・実行が一致しないか形式が不正")
                elif truth is None:
                    truth = snapshot
            except ValueError:
                found.append(f"{sid}: スナップショットを読み込めない")
        # 旧形式の過去版は履歴として保持する。現行の判断・計画の証跡には使えない。
        if truth:
            if RANK[sim["classification"]] < RANK[truth["classification"]]:
                found.append(f"{sid}: 機密区分が評価対象の区分より低い")
            nodes = {n["node_id"] for n in truth["nodes"]}
            if any(c not in nodes for c in sim["categories"]):
                found.append(f"{sid}: categories にサイトマップにないノードがある")
            if any(v is not None and v not in nodes for t in sim["trials"] for v in (t["answer"], t["first_choice"])):
                found.append(f"{sid}: 試行の選択がサイトマップにない")
            for item in sim["items"]:
                try:
                    derived = expected_item(sim["method"], item["item_id"], truth, sim["categories"])
                    multi = any(t["task_id"] == item["item_id"] and t.get("target_content_ids") for t in truth["tasks"])
                    if any(item.get(k) != v for k, v in derived.items()
                           if multi or k in item or k != "expected_first_choices"):
                        found.append(f"{sid}: {item['item_id']} の正解が対象版のサイトマップからの導出と一致しない")
                except ValueError as exc:
                    found.append(f"{sid}: {exc}")
        if (sim["method"] == "closed_card_sort") != bool(sim["categories"]):
            found.append(f"{sid}: categories は閉じたカードソートだけに書く")
        prefix = "TASK-" if sim["method"] == "tree_test" else "CONTENT-"
        ids = [i["item_id"] for i in sim["items"]]
        found += [f"{sid}: {i} は {sim['method']} の項目ではない" for i in ids if not i.startswith(prefix)]
        found += [f"{sid}: 項目 {i} が重複している" for i, n in Counter(ids).items() if n > 1]
        counts = Counter(t["item_id"] for t in sim["trials"])
        found += [f"{sid}: 試行の項目 {i} が items にない" for i in counts if i not in ids]
        found += [f"{sid}: {i} の試行が {counts.get(i, 0)} 回で repetitions {sim['repetitions']} と一致しない"
                  for i in ids if counts.get(i, 0) != sim["repetitions"]]
        found += [f"{sid}: {i} の repetition {r} が重複している"
                  for (i, r), n in Counter((t["item_id"], t["repetition"]) for t in sim["trials"]).items() if n > 1]
        if not found and sim["results"] != derive_results(sim):
            found.append(f"{sid}: results が試行の記録からの導出と一致しない（自己申告は不可）")
        errors += found
    return errors
