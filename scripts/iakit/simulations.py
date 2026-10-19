"""模擬評価の記録の導出と整合性（P2-3、FR-06、AC-07）。

到達率・最初の選択は試行の記録から導き、記録に書かれた値が導出値と異なれば拒否する。
模擬評価は判断を simulated にする根拠にだけ使い、tested_* の根拠にはしない（decisions.decision_errors）。
"""

from collections import Counter

NONE = "(none)"


def expected(method: str, item_id: str, site: dict, categories: list[str]) -> tuple[list[str], str | None]:
    """サイトマップから正解のノードと、ルートの次に選ぶべきノードを導く。"""
    nodes = {n["node_id"]: n for n in site["nodes"]}
    if method == "tree_test":
        task = next((t for t in site["tasks"] if t["task_id"] == item_id), None)
        if task is None:
            raise ValueError(f"{item_id}: サイトマップの主要タスクにない")
        return [task["path"][-1]], (task["path"][1] if len(task["path"]) > 1 else None)
    found = []
    for n in site["nodes"]:
        if item_id not in n["content_ids"]:
            continue
        cur = n["node_id"]
        while cur in nodes:
            if cur in categories and cur not in found:
                found.append(cur)
            cur = nodes[cur]["parent"]
    if not found:
        raise ValueError(f"{item_id}: 分類先 {categories} のどこにも配置されていない")
    return found, None


def derive_results(sim: dict) -> list[dict]:
    items = {i["item_id"]: i for i in sim["items"]}
    out = []
    for item_id, item in items.items():
        trials = [t for t in sim["trials"] if t["item_id"] == item_id]
        if not trials:
            continue
        ok = sum(t["answer"] in item["expected"] for t in trials) / len(trials)
        direct = (sum(t["first_choice"] == item["expected_first"] for t in trials) / len(trials)
                  if item["expected_first"] else None)
        firsts = Counter(t["first_choice"] or NONE for t in trials)
        out.append({"item_id": item_id, "attempts": len(trials), "success_rate": round(ok, 3),
                    "direct_first_rate": None if direct is None else round(direct, 3),
                    "first_choices": dict(sorted(firsts.items())),
                    "below_threshold": ok < sim["threshold"]["success_rate"]})
    return out


def record_errors(doc: dict, artifacts: dict[str, dict]) -> list[str]:
    """artifacts は実行記録の成果物（ID→登録内容）。"""
    errors, seen = [], set()
    for sim in doc["simulations"]:
        sid, found = sim["simulation_id"], []
        if sid in seen:
            found.append(f"{sid}: simulation_id が重複している")
        seen.add(sid)
        for key, kind in (("target", "sitemap"), ("persona", "problem_definition")):
            ref = sim[key] if key == "target" else sim[key]["source"]
            a = artifacts.get(ref["artifact_id"])
            if a is None or a["kind"] != kind or ref["version"] > a["version"]:
                found.append(f"{sid}: {key} {ref['artifact_id']} v{ref['version']} は登録済みの {kind} ではない")
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
