"""コンテンツ一覧・語彙・分類・サイトマップの参照整合性（FR-02、FR-03、FR-08、AC-03）。"""


def _dupes(values) -> list:
    seen, dup = set(), []
    for v in values:
        if v in seen:
            dup.append(v)
        seen.add(v)
    return dup


def _ref_errors(name: str, ref: dict, doc: dict | None) -> list[str]:
    if doc is None or (doc["artifact_id"], doc["version"]) != (ref["artifact_id"], ref["version"]):
        return [f"{name}: 参照 {ref['artifact_id']} v{ref['version']} が現行の成果物と一致しない"]
    return []


def inventory_errors(inv: dict, inputs: dict) -> list[str]:
    errors = [f"{c}: content_id が重複している" for c in _dupes(i["content_id"] for i in inv["items"])]
    errors += [f"{i['content_id']}: 入力 {i['input_id']} が登録されていない"
               for i in inv["items"] if i["input_id"] not in inputs]
    return errors


def vocabulary_errors(vocab: dict, inputs: dict) -> list[str]:
    concepts = {c["concept_id"]: c for c in vocab["concepts"]}
    errors = [f"{c}: concept_id が重複している" for c in _dupes(c["concept_id"] for c in vocab["concepts"])]
    labels: dict[str, list[str]] = {}
    for c in vocab["concepts"]:
        cid = c["concept_id"]
        for ref in c["broader"] + c["related"]:
            if ref not in concepts:
                errors.append(f"{cid}: 存在しない概念 {ref} を参照している")
        if cid in c["broader"] or cid in c["related"]:
            errors.append(f"{cid}: 自分自身を参照している")
        errors += [f"{cid}: 入力 {s['input_id']} が登録されていない" for s in c["sources"] if s["input_id"] not in inputs]
        for label in {c["pref_label"]["ja"], *c["alt_labels"]}:
            labels.setdefault(label, []).append(cid)
    for label, ids in labels.items():
        # 同名異義語は許容するが、区別の説明がないものは利用者が判別できない
        if len(ids) > 1 and any(not (concepts[i]["scope_note"] or "").strip() for i in ids):
            errors.append(f"同名異義語「{label}」{ids}: 各概念に scope_note で区別を記す")
    cycle = _cycle({c: [b for b in concepts[c]["broader"] if b in concepts] for c in concepts})
    if cycle:
        errors.append(f"broader が循環している {cycle}")
    return errors


def _cycle(graph: dict[str, list[str]]) -> list[str]:
    """有向グラフの循環を1つ返す。なければ空。"""
    state: dict[str, int] = {}
    for start in graph:
        stack = [(start, iter(graph[start]))]
        path = [start]
        state[start] = state.get(start, 0) or 1
        if state[start] == 2:
            continue
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                path.pop()
            elif state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            elif not state.get(nxt):
                state[nxt] = 1
                stack.append((nxt, iter(graph[nxt])))
                path.append(nxt)
    return []


def _ancestors(cid: str, concepts: dict) -> set[str]:
    seen, todo = set(), [cid]
    while todo:
        cur = todo.pop()
        if cur in seen or cur not in concepts:
            continue
        seen.add(cur)
        todo += concepts[cur]["broader"]
    return seen


def taxonomy_errors(tax: dict, vocab: dict | None, inv: dict | None) -> list[str]:
    errors = _ref_errors("vocabulary", tax["vocabulary"], vocab) + _ref_errors("inventory", tax["inventory"], inv)
    if errors:
        return errors
    concepts = {c["concept_id"]: c for c in vocab["concepts"]}
    items = {i["content_id"]: i for i in inv["items"]}
    tops = {t for s in tax["schemes"] for t in s["top_concepts"]}
    errors += [f"{t}: 体系の最上位概念が語彙にない" for t in tops if t not in concepts]
    errors += [f"{s}: scheme_id が重複している" for s in _dupes(s["scheme_id"] for s in tax["schemes"])]
    assigned = set()
    for a in tax["assignments"]:
        cid = a["content_id"]
        if cid in assigned:
            errors.append(f"{cid}: 分類が重複して記録されている")
        assigned.add(cid)
        item = items.get(cid)
        if item is None:
            errors.append(f"{cid}: コンテンツ一覧にない")
        elif item["status"] != "placed":
            errors.append(f"{cid}: {item['status']} のコンテンツを分類している")
        for concept in a["concept_ids"]:
            if concept not in concepts:
                errors.append(f"{cid}: 存在しない概念 {concept} に分類している")
            elif not _ancestors(concept, concepts) & tops:
                errors.append(f"{cid}: 概念 {concept} がどの体系の最上位概念からもたどれない")
    errors += [f"{c}: 配置済みなのに分類されていない" for c, i in items.items()
               if i["status"] == "placed" and c not in assigned]
    return errors


def sitemap_errors(site: dict, inv: dict | None, vocab: dict | None) -> list[str]:
    errors = _ref_errors("inventory", site["inventory"], inv)
    if errors:
        return errors
    nodes = {n["node_id"]: n for n in site["nodes"]}
    errors += [f"{n}: node_id が重複している" for n in _dupes(n["node_id"] for n in site["nodes"])]
    concepts = {c["concept_id"] for c in vocab["concepts"]} if vocab else None
    items = {i["content_id"]: i for i in inv["items"]}
    root = nodes.get(site["root"])
    if root is None or root["parent"] is not None:
        errors.append(f"root {site['root']} が親のないノードとして存在しない")
    children: dict[str, set[str]] = {n: set() for n in nodes}
    placed: set[str] = set()
    for n in site["nodes"]:
        nid = n["node_id"]
        if n["parent"] is None and nid != site["root"]:
            errors.append(f"{nid}: ルート以外に親のないノードがある")
        for ref in ([n["parent"]] if n["parent"] else []) + n["links"]:
            if ref not in nodes:
                errors.append(f"{nid}: 存在しないノード {ref} を参照している")
        if n["parent"] in nodes:
            children[n["parent"]].add(nid)
        children[nid].update(l for l in n["links"] if l in nodes)
        if concepts is not None and n["concept_id"] and n["concept_id"] not in concepts:
            errors.append(f"{nid}: 存在しない概念 {n['concept_id']} を参照している")
        for c in n["content_ids"]:
            placed.add(c)
            if c not in items:
                errors.append(f"{nid}: コンテンツ一覧にない {c} を配置している")
            elif items[c]["status"] != "placed":
                errors.append(f"{nid}: {items[c]['status']} の {c} を配置している")
    for nid in nodes:
        path, cur = [nid], nodes[nid]["parent"]
        while cur in nodes:
            if cur in path:
                errors.append(f"{nid}: 親が循環している {path + [cur]}")
                break
            path.append(cur)
            cur = nodes[cur]["parent"]
    reached, todo = set(), [site["root"]]
    while todo:
        cur = todo.pop()
        if cur in reached or cur not in nodes:
            continue
        reached.add(cur)
        todo += children[cur]
    errors += [f"{n}: ルートから到達できない" for n in nodes if n not in reached]
    errors += [f"{c}: 配置済みなのにどのノードにも置かれていない" for c, i in items.items()
               if i["status"] == "placed" and c not in placed]
    for t in site["tasks"]:
        path = t["path"]
        if path[0] != site["root"]:
            errors.append(f"{t['task_id']}: 経路がルートから始まらない")
        for a, b in zip(path, path[1:]):
            if b not in children.get(a, set()):
                errors.append(f"{t['task_id']}: {a} から {b} への経路がない")
    return errors
