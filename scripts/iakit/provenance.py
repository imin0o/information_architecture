"""根拠抜粋の登録簿と照合記録の検査（付録B.2〜B.4）。

検査の層を分けて扱う。
  reference_errors  参照整合性。原典がなくても実行でき、CIの対象になる
  local_results     抜粋の実在性。固定スナップショットが手元にない根拠は not_run とする
  derive            登録簿と照合記録から状態を導出する。人の確認がなければ verified / supported にしない
"""

import hashlib
import json
from pathlib import Path

from . import manifest, snapshot, snapshot_check, yamlio
from .paths import FIXTURES_INDEX, MANIFEST


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_sha256(value) -> str:
    return text_sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def record_sha256(record: dict) -> str:
    """主張レビューを除く登録レコードのハッシュ。主張は claim_sha256 で個別に対応付ける。"""
    return canonical_sha256({k: v for k, v in record.items() if k != "support_reviews"})


def registered_sources() -> dict[str, str | None]:
    """出典IDと登録済みの原典ハッシュ。BOOKは登録簿、FIXTUREは自作素材の登録簿から取る。"""
    books = {b["book_id"]: b["original"]["sha256"] for b in yamlio.load(MANIFEST)["books"]}
    files = yamlio.load(FIXTURES_INDEX)["files"]
    return books | {"FIXTURE-" + Path(f["path"]).stem: f["sha256"] for f in files}


def claims_index(provenance: dict) -> dict[str, dict]:
    return {rv["claim_id"]: {"evidence_id": r["evidence_id"], "source_id": r["source_id"], **rv}
            for r in provenance["records"] for rv in r["support_reviews"]}


def reference_errors(provenance: dict, receipts: dict, sources: dict[str, str | None]) -> list[str]:
    errors: list[str] = []
    records: dict[str, dict] = {}
    claim_owner: dict[str, str] = {}
    for r in provenance["records"]:
        ev, ref, loc = r["evidence_id"], r["extracted_ref"], r["locator"]
        if ev in records:
            errors.append(f"{ev}: evidence_id が重複している")
        records[ev] = r
        if r["source_id"] not in sources:
            errors.append(f"{ev}: 未登録の source_id {r['source_id']}")
        elif sources[r["source_id"]] != r["source_sha256"]:
            errors.append(f"{ev}: source_sha256 が登録簿の原典ハッシュと一致しない")
        if snapshot.ID_PATTERN.fullmatch(ref["extraction_id"])[1] != snapshot.source_tag(r["source_id"]):
            errors.append(f"{ev}: extraction_id が source_id の抽出ではない")
        if ref["start_line"] > ref["end_line"]:
            errors.append(f"{ev}: 行範囲が逆順")
        if len(r["excerpt_preview"].split()) > snapshot_check.PREVIEW_MAX_WORDS:
            errors.append(f"{ev}: excerpt_preview が{snapshot_check.PREVIEW_MAX_WORDS}語を超える")
        if loc["printed_page_labels"] and not loc["pdf_page_indices"]:
            errors.append(f"{ev}: 印刷番号だけを記入している（PDF内のページ順と対で確認する）")
        if (loc["pdf_page_indices"] or loc["printed_page_labels"]) and (loc["epub_href"] or loc["element_id"]):
            errors.append(f"{ev}: PDFとEPUBの位置を同時に記入している")
        if loc["element_id"] and not loc["epub_href"]:
            errors.append(f"{ev}: element_id には epub_href が必要")
        for rv in r["support_reviews"]:
            cid = rv["claim_id"]
            if cid in claim_owner:
                errors.append(f"{cid}: claim_id が {claim_owner[cid]} と重複している")
            claim_owner[cid] = ev
            if text_sha256(rv["claim"]) != rv["claim_sha256"]:
                errors.append(f"{cid}: 主張がレビュー時のハッシュと一致しない（言い換えは新規レビューを要する）")
    seen: set[str] = set()
    for rc in receipts["receipts"]:
        rid, ev = rc["receipt_id"], rc["evidence_id"]
        if rid in seen:
            errors.append(f"{rid}: receipt_id が重複している")
        seen.add(rid)
        if ev not in records:
            errors.append(f"{rid}: 登録簿にない根拠 {ev} の照合記録")
        elif records[ev]["source_id"] != rc["source_id"]:
            errors.append(f"{rid}: source_id が根拠 {ev} と一致しない")
        for c in rc["claims"]:
            if claim_owner.get(c["claim_id"]) != ev:
                errors.append(f"{rid}: {c['claim_id']} は根拠 {ev} の主張ではない")
    return errors


def receipt_problems(receipt: dict, record: dict, base: Path | None, locations_sha256: str | None = None) -> list[str]:
    """照合記録が現在の登録レコード・知識ファイルに対応しない理由。空なら有効。"""
    t, problems = receipt["targets"], []
    if t["record_sha256"] != record_sha256(record):
        problems.append("登録レコードが照合後に変わった")
    for key in ("source_sha256", "extracted_sha256", "excerpt_sha256"):
        if t[key] != record[key]:
            problems.append(f"{key} が登録レコードと一致しない")
    if locations_sha256 is not None and t["locations_sha256"] != locations_sha256:
        problems.append("locations_sha256 が位置対応表と一致しない")
    for f in t["knowledge_files"] if base else []:
        path = base / f["path"]
        if not path.is_file() or manifest.sha256_file(path) != f["sha256"]:
            problems.append(f"知識ファイル {f['path']} が照合後に変わった")
    return problems


def _verified(receipt: dict) -> bool:
    mc = receipt["machine_checks"]
    return (all(mc[k] == "pass" for k in ("reference", "excerpt", "original_hash"))
            and receipt["human_checks"]["source_location"]["result"] == "confirmed")


def derive(provenance: dict, receipts: dict, base: Path | None = None, extracted_root: Path | None = None) -> dict:
    """状態と無効記録の理由。原典照合は追記順の最新有効記録を使い、過去の合格で却下を打ち消さない。"""
    by_evidence: dict[str, list[dict]] = {}
    for rc in receipts["receipts"]:
        by_evidence.setdefault(rc["evidence_id"], []).append(rc)
    states, stale = {}, {}
    for r in provenance["records"]:
        valid = []
        locations = extracted_root / r["extracted_ref"]["extraction_id"] / "locations.yaml" if extracted_root else None
        locations_sha = manifest.sha256_file(locations) if locations and locations.is_file() else None
        for rc in by_evidence.get(r["evidence_id"], []):
            problems = receipt_problems(rc, r, base, locations_sha)
            if problems:
                stale[rc["receipt_id"]] = problems
            else:
                valid.append(rc)
        verified = "verified" if valid and _verified(valid[-1]) else "unverified"
        for rv in r["support_reviews"]:
            expected = {"claim_id": rv["claim_id"], "claim_sha256": rv["claim_sha256"],
                        "applicability_sha256": text_sha256(rv["applicability"]), "status": rv["status"]}
            reviewed = rv["status"] != "unreviewed" and any(expected in rc["claims"] for rc in valid)
            states[(r["evidence_id"], rv["claim_id"])] = {
                "source_verification": verified, "support_status": rv["status"] if reviewed else "unreviewed"}
    return {"states": states, "stale_receipts": stale}
