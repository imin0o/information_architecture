"""模擬評価の材料の作成と記録（P2-2、P2-3）。

kit は評価者に渡す材料（ラベルの階層、カード、課題のID）だけを出し、設計意図・判断・概念の説明を含めない。
add は試行の記録から正解・到達率・最初の選択を導いて simulations.yaml に書く。導出値は自己申告させない。
"""

from pathlib import Path

from . import simulations, yamlio
from .decisions import max_classification
from .run_cmd import load, now

NAME = "simulations.yaml"
AUTO = ("simulation_id", "results", "recorded_at", "sitemap_snapshot", "sitemap_sha256")
DEFAULT_THRESHOLD = 0.8


def _current(run: dict, kind: str) -> dict:
    found = next((a for a in run["artifacts"] if a["kind"] == kind and a["status"] == "current"), None)
    if found is None:
        raise ValueError(f"現行の {kind} が登録されていない。先に登録する")
    return found


def _site(directory: Path, run: dict) -> tuple[dict, dict]:
    reg = _current(run, "sitemap")
    return reg, yamlio.load(directory / reg["path"])


def kit(directory: Path, method: str) -> dict:
    """評価者に渡す材料。ノードIDは回答の記録に使うだけで、意味を持たせない。"""
    run = load(directory, "run.yaml")
    reg, site = _site(directory, run)
    nodes = {n["node_id"]: n for n in site["nodes"]}

    def tree(nid: str, depth: int) -> list[str]:
        lines = [f"{'  ' * depth}- {nodes[nid]['label']} [{nid}]"]
        for n in site["nodes"]:
            if n["parent"] == nid:
                lines += tree(n["node_id"], depth + 1)
        return lines

    out = {"method": method, "target": {"artifact_id": reg["artifact_id"], "version": reg["version"]},
           "evaluator_context": "labels_and_prompts_only"}
    if method == "tree_test":
        out["tree"] = tree(site["root"], 0)
        out["items"] = [t["task_id"] for t in site["tasks"]]
        out["next"] = "問題定義の主要タスクから各TASKの課題文を書き、利用者像と tree だけを別のエージェントに渡す"
    else:
        inv = yamlio.load(directory / _current(run, "content_inventory")["path"])
        categories = [n["node_id"] for n in site["nodes"] if n["parent"] == site["root"]]
        out["categories"] = [f"{nodes[c]['label']} [{c}]" for c in categories]
        out["cards"] = [f"{i['title']} [{i['content_id']}]" for i in inv["items"] if i["status"] == "placed"]
        out["next"] = "利用者像と categories・cards だけを別のエージェントに渡し、各カードの分類先を記録する"
    return out


def add(directory: Path, data: dict) -> dict:
    run = load(directory, "run.yaml")
    fixed = sorted(set(AUTO) & set(data))
    if fixed:
        raise ValueError(f"{fixed} はコマンドが決める")
    reg, site = _site(directory, run)
    problem = _current(run, "problem_definition")
    method = data.get("method")
    if method not in ("tree_test", "closed_card_sort"):
        raise ValueError("method は tree_test か closed_card_sort にする")
    target = data.get("target") or {"artifact_id": reg["artifact_id"], "version": reg["version"]}
    if target != {"artifact_id": reg["artifact_id"], "version": reg["version"]}:
        raise ValueError(f"target は現行のサイトマップ {reg['artifact_id']} v{reg['version']} にする")
    persona = dict(data.get("persona") or {})
    persona.setdefault("source", {"artifact_id": problem["artifact_id"], "version": problem["version"]})
    categories = data.get("categories", [])
    items = []
    for item in data.get("items", []):
        items.append({**item, **simulations.expected_item(method, item.get("item_id"), site, categories)})
    nodes = {n["node_id"] for n in site["nodes"]}
    unknown = sorted({v for t in data.get("trials", []) for v in (t.get("first_choice"), t.get("answer"))
                      if v is not None and v not in nodes})
    if unknown:
        raise ValueError(f"試行の選択 {unknown} がサイトマップにない")
    path = directory / NAME
    doc = yamlio.load(path) if path.is_file() else {"schema_version": "1.0", "run_id": run["run_id"], "simulations": []}
    numbers = [int(s["simulation_id"].split("-")[1]) for s in doc["simulations"]]
    sim = {"simulation_id": f"SIM-{max(numbers, default=0) + 1:03d}", "method": method, "target": target,
           "persona": persona, "evaluator": {**data.get("evaluator", {}), "context": "labels_and_prompts_only"},
           "repetitions": data.get("repetitions"), "categories": categories, "items": items,
           "trials": data.get("trials", []), "threshold": data.get("threshold", {"success_rate": DEFAULT_THRESHOLD}),
           "results": [], "classification": data.get("classification") or max_classification(
               [reg["classification"], problem["classification"]]), "recorded_at": now(),
           "sitemap_snapshot": (directory / reg["path"]).read_bytes().decode("utf-8"),
           "sitemap_sha256": reg["sha256"]}
    doc["simulations"].append(sim)
    errors = yamlio.schema_errors(doc, "simulation.schema.json")
    if not errors:
        sim["results"] = simulations.derive_results(sim)
        errors = simulations.record_errors(doc, {a["artifact_id"]: a for a in run["artifacts"]}, site)
    if errors:
        raise ValueError(f"{NAME} に書き込まない: " + " / ".join(errors[:5]))
    path.write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
    below = [r["item_id"] for r in sim["results"] if r["below_threshold"]]
    return {"simulation_id": sim["simulation_id"], "results": sim["results"], "below_threshold": below,
            "next": "基準を下回った項目は iakit finding add で指摘にする（模擬評価だけを根拠に critical にしない）。"
                    f"判断を simulated にする場合は validation_record_ids に {sim['simulation_id']} を入れて改訂する"}
