"""UI/UX原則カードとの知識対応表の検査（iakit library check の一部）。

対応表 library/mappings/uiux-principles.yaml は、出典の対応と主張の対応の2層を持つ。
IA側（索引・主張・根拠・原典ハッシュ）は常に検査する。UI/UX側（原則カード・出典登録簿）は
リポジトリが見つかる場合だけ検査し、見つからなければ ux_check を not_run にする（CIはIAだけを取得する）。
主張、該当欄、カードの適用条件・例外・根拠が変わると、対応を確認し直すまで失敗する。
"""

import os
import re
from collections import Counter
from pathlib import Path

from . import provenance, yamlio

FILE = ("library/mappings/uiux-principles.yaml", "uiux-mapping.schema.json")
UIUX_ENV = "IAKIT_UIUX_ROOT"
FIELD = re.compile(r"^(recommendation|(conditions|exceptions|tradeoffs)\[([0-9]+)\])$")
CONTEXT_FIELDS = ("claim_type", "recommendation", "conditions", "exceptions", "tradeoffs", "source_statement", "evidence")


def context_sha256(card: dict) -> str:
    """該当欄の解釈に必要な文脈と根拠を固定する。表示名や確認者の更新は含めない。"""
    return provenance.canonical_sha256({k: card.get(k) for k in CONTEXT_FIELDS})


def uiux_root(root: Path) -> tuple[Path | None, list[str]]:
    """環境変数で指定されたUI/UXリポジトリ、なければIAリポジトリと並ぶ ui_ux を使う。"""
    explicit = os.environ.get(UIUX_ENV)
    path = Path(explicit) if explicit else root.parent / "ui_ux"
    if (path / "knowledge/principles").is_dir():
        return path, []
    return None, [f"{UIUX_ENV}: UI/UXリポジトリの knowledge/principles がない"] if explicit else []


def _in_scope(pid: str, scope: dict) -> bool:
    return scope["from"] <= pid <= scope["to"]


def _card(ux: Path, pid: str) -> dict | None:
    path = ux / "knowledge/principles" / f"{pid}.md"
    if not path.is_file():
        return None
    parts = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("---\n", 2)
    if len(parts) != 3 or parts[0] != "":
        return None
    card = yamlio.loads(parts[1])
    # 外部カードのうち、この検査が読む構造を検証する。壊れた値は検査エラーとして返す。
    if not isinstance(card, dict):
        raise ValueError("frontmatter はオブジェクトでなければならない")
    for key in ("id", "recommendation"):
        if not isinstance(card.get(key), str):
            raise ValueError(f"{key} は文字列でなければならない")
    for key in ("conditions", "exceptions", "tradeoffs", "evidence"):
        items = card.get(key, [])
        if not isinstance(items, list):
            raise ValueError(f"{key} は配列でなければならない")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{key} の各項目はオブジェクトでなければならない")
            if key == "evidence":
                if not isinstance(item.get("source_id"), str):
                    raise ValueError("evidence の source_id は文字列でなければならない")
            elif not isinstance(item.get("text"), str) or item.get("basis") not in ("source", "interpretation"):
                raise ValueError(f"{key} の各項目には文字列の text と source/interpretation の basis が必要")
    return card


def _field_text(card: dict, field: str) -> str | None:
    m = FIELD.fullmatch(field)
    if m[1] == "recommendation":
        return card.get("recommendation")
    items = card.get(m[2]) or []
    return items[int(m[3])].get("text") if int(m[3]) < len(items) else None


def ia_errors(doc: dict, index: dict, prov: dict, sources: dict) -> list[str]:
    """出典の登録と原典ハッシュ、範囲、主張の存在・所属・根拠・版、関係の向きと重複。"""
    errors: list[str] = []
    claims = provenance.claims_index(prov)
    entries = {e["knowledge_id"]: set(e["claims"]) for e in index["entries"]}
    scope = doc["scope"]
    errors += [f"scope: 未登録の知識 {k}" for k in scope["ia_knowledge"] if k not in entries]
    if scope["ux_principles"]["from"] > scope["ux_principles"]["to"]:
        errors.append("scope: ux_principles の from と to が逆順")
    paired: set[str] = set()
    for s in doc["sources"]:
        sid = s["ia_source_id"]
        if sid in paired:
            errors.append(f"sources: {sid} が重複している")
        paired.add(sid)
        if sid not in sources:
            errors.append(f"sources: 未登録の出典 {sid}")
        elif sources[sid] != s["original_sha256"]:
            errors.append(f"sources: {sid} の原典ハッシュが出典登録簿と一致しない")
    seen: set[tuple[str, str]] = set()
    for m in doc["claims"]:
        cid, kid, pid = m["claim_id"], m["knowledge_id"], m["principle_id"]
        where = f"{cid}→{pid}"
        # 向きは IA の主張 → 原則カード に固定する。同じ組を二度書くと関係が食い違うので拒否する
        if (cid, pid) in seen:
            errors.append(f"{where}: 同じ主張と原則カードの対応が重複している")
        seen.add((cid, pid))
        if kid not in scope["ia_knowledge"]:
            errors.append(f"{where}: {kid} は対応表の範囲外")
        elif cid not in entries.get(kid, ()):
            errors.append(f"{where}: {cid} は {kid} が引く主張ではない")
        if not _in_scope(pid, scope["ux_principles"]):
            errors.append(f"{where}: {pid} は対応表の範囲外")
        c = claims.get(cid)
        if c is None:
            errors.append(f"{where}: 未登録の主張 {cid}")
            continue
        if c["source_id"] not in paired:
            errors.append(f"{where}: 主張の出典 {c['source_id']} が sources にない（出典の異なる主張は対応させない）")
        if c["evidence_id"] != m["ia_evidence_id"]:
            errors.append(f"{where}: ia_evidence_id が主張の根拠 {c['evidence_id']} と一致しない")
        if c["claim_sha256"] != m["ia_claim_sha256"]:
            errors.append(f"{where}: 主張の版が変わった（対応を確認し直し、ia_claim_sha256 を更新する）")
        if "independent_review" in m:
            errors += [f"{where}: {e}" for e in _review_errors(m, m["independent_review"])]
    return errors


def _review_errors(m: dict, r: dict) -> list[str]:
    """独立照合の記録が、別の提供元による、現在の版と relation についての判定であるか。"""
    errors = []
    if r["generator"]["provider"] == r["reviewer"]["provider"]:
        errors.append(f"生成者と照合者の提供元がともに {r['reviewer']['provider']}（自己確認は独立照合にならない）")
    if any(r[k] != m[k] for k in ("ia_claim_sha256", "ux_text_sha256", "ux_context_sha256")):
        errors.append("照合した後に対象が変わった（照合し直し、independent_review の版を更新する）")
    if r["verdict"] != m["relation"]:
        errors.append(f"照合の判定 {r['verdict']} が relation {m['relation']} と一致しない")
    return errors


def ux_errors(doc: dict, prov: dict, ux: Path) -> list[str]:
    """UI/UX側の原典、該当欄と文脈の版、該当欄の出典を確かめる。"""
    errors: list[str] = []
    local = yamlio.load(ux / "sources/local-index.yaml")
    pairs = {s["ia_source_id"]: s["ux_source_id"] for s in doc["sources"]}
    for s in doc["sources"]:
        if s["original_sha256"] not in {f["sha256"] for f in local.get(s["ux_source_id"]) or []}:
            errors.append(f"sources: {s['ux_source_id']} のUI/UX側の原典ハッシュが original_sha256 と一致しない")
    claims = provenance.claims_index(prov)
    for m in doc["claims"]:
        where = f"{m['claim_id']}→{m['principle_id']}"
        try:
            card = _card(ux, m["principle_id"])
        except (OSError, ValueError) as exc:
            errors.append(f"{where}: 原則カードを読めない: {exc}")
            continue
        if card is None or card.get("id") != m["principle_id"]:
            errors.append(f"{where}: 原則カード {m['principle_id']} がない")
            continue
        text = _field_text(card, m["ux_field"])
        if text is None:
            errors.append(f"{where}: 原則カードに {m['ux_field']} がない")
        elif provenance.text_sha256(text) != m["ux_text_sha256"]:
            errors.append(f"{where}: 原則カードの {m['ux_field']} が変わった（対応を確認し直し、ux_text_sha256 を更新する）")
        if context_sha256(card) != m["ux_context_sha256"]:
            errors.append(f"{where}: 原則カードの文脈が変わった（条件・例外・根拠を含む対応を確認し直し、ux_context_sha256 を更新する）")
        expected = pairs.get(claims.get(m["claim_id"], {}).get("source_id"))
        if expected not in {e.get("source_id") for e in card.get("evidence") or []}:
            errors.append(f"{where}: 原則カードの根拠に、主張と対応する出典 {expected} がない")
        field = FIELD.fullmatch(m["ux_field"])
        if text is not None and field[1] != "recommendation":
            qualified = card[field[2]][int(field[3])]
            if qualified.get("basis") != "source" or qualified.get("source_id") != expected:
                errors.append(f"{where}: 該当欄の出典が主張と対応する出典 {expected} ではない")
    return errors


def check(doc: dict, index: dict, prov: dict, sources: dict, root: Path) -> tuple[list[str], dict]:
    errors = ia_errors(doc, index, prov, sources)
    ux, root_errors = uiux_root(root)
    errors += root_errors
    if ux is not None:
        try:
            errors += ux_errors(doc, prov, ux)
        except (OSError, ValueError) as exc:
            errors.append(f"UI/UX側の読込み失敗: {exc}")
    owner = {cid: v["source_id"] for cid, v in provenance.claims_index(prov).items()}
    paired = {s["ia_source_id"] for s in doc["sources"]}
    in_scope = {c for e in index["entries"] if e["knowledge_id"] in doc["scope"]["ia_knowledge"]
                for c in e["claims"] if owner.get(c) in paired}
    mapped = {m["claim_id"] for m in doc["claims"]}
    report = {
        "claims": len(doc["claims"]),
        "relations": dict(sorted(Counter(m["relation"] for m in doc["claims"]).items())),
        # 同じ出典を引くが原則カードに対応がない主張。対応がないことも確認の結果として見えるようにする
        "unmapped_claims": sorted(in_scope - mapped),
        "ux_check": "checked" if ux is not None else "not_run",
    }
    return errors, report
