"""人による原典照合・主張レビュー・抽出品質確認を、最小の操作で記録する（iakit review）。

  sheet   Git管理外の sources/review/ に確認シートを作る。抽出テキスト・主張・ページ番号を並べ、
          人はPDFの該当ページと見比べるだけにする。標準出力には本文を出さない。
  record  人の判定を受け取り、provenance.yaml の support_reviews、verification/receipts.yaml、
          sample-reviews.yaml を書き込む。位置・ハッシュ・機械検査はツールが求める。
判定（confirmed・supported・ok）は --all-ok か個別の指定からだけ作る。指定のない箇所は書き換えない。
機械検査が1件でも失敗した場合は、どのファイルも書き換えない。
"""

import copy
import datetime
import itertools
from pathlib import Path

from . import __version__, extract_cmd, manifest, provenance, provenance_local, snapshot_check, yamlio
from .paths import EXTRACTED, ROOT

PROV, RECEIPTS = "library/provenance.yaml", "verification/receipts.yaml"
SAMPLES, INDEX = "library/build-logs/sample-reviews.yaml", "library/index.yaml"
SHEET = "sources/review/review-sheet.md"
RECEIPTS_HEADER = "# 本文を含まない照合記録（付録B.3）。iakit review record が人の判定から書き込む。\n"


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _header(path: Path, default: str) -> str:
    if not path.is_file():
        return default
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(itertools.takewhile(lambda line: line.startswith("#"), lines))


def _load(root: Path) -> tuple[dict, dict, dict]:
    prov = yamlio.load(root / PROV)
    path = root / RECEIPTS
    receipts = yamlio.load(path) if path.is_file() else {"schema_version": "1.0", "receipts": []}
    return prov, receipts, yamlio.load(root / SAMPLES)


def _pending(prov: dict, receipts: dict, root: Path, extracted_root: Path) -> list[dict]:
    """原典照合済みでない、または未レビューの主張が残る根拠。"""
    states = provenance.derive(prov, receipts, root, extracted_root)["states"]
    return [r for r in prov["records"] if any(
        states[(r["evidence_id"], rv["claim_id"])]["source_verification"] != "verified"
        or states[(r["evidence_id"], rv["claim_id"])]["support_status"] == "unreviewed" for rv in r["support_reviews"])]


def _knowledge_files(root: Path, record: dict) -> list[dict]:
    claims = {rv["claim_id"] for rv in record["support_reviews"]}
    paths = sorted({e["path"] for e in yamlio.load(root / INDEX)["entries"] if claims & set(e["claims"])})
    return [{"path": p, "sha256": manifest.sha256_file(root / p)} for p in paths]


def _pages(cand: dict) -> str:
    if "pdf_page_indices" not in cand:
        return f"EPUB {cand['epub_href']}"
    labels = "、".join(cand["printed_page_labels"]) or "なし"
    return f"PDFのページ順 {'、'.join(map(str, cand['pdf_page_indices']))}（印刷番号 {labels}）"


def sheet(root: Path = ROOT, extracted_root: Path = EXTRACTED) -> dict:
    prov, receipts, samples = _load(root)
    out = ["# 確認シート", "", "Git管理外の作業用ファイルで、原典の本文を含む。共有・コミットしない。", "",
           "1. 各根拠について、PDFビューアで示したページを開き、抽出テキストと同じ文章があるかを見る。",
           "2. 主張が、その文章の内容と適用条件に合っているかを判断する。",
           "3. 品質確認の表のページを開き、観点ごとに問題がないかを見る。",
           "4. 問題がなければ `uv run --locked iakit review record --by <名前> --all-ok` を1回実行する。",
           "   問題がある箇所だけ `--reject EV-nnnn`、`--claim CLAIM-nnnn=not_supported`（または unclear）、",
           "   `--sample-issue EXTRACT-ID:kind:メモ` を付ける。メモに原典の本文を書かない。", ""]
    pending = _pending(prov, receipts, root, extracted_root)
    for r in pending:
        ref = r["extracted_ref"]
        directory = extracted_root / ref["extraction_id"]
        lines = (directory / "text.txt").read_text(encoding="utf-8").splitlines()[ref["start_line"] - 1:ref["end_line"]]
        cand = snapshot_check.locate(yamlio.load(directory / "locations.yaml"), ref["start_line"], ref["end_line"])
        out += [f"## {r['evidence_id']}（{r['source_id']}）", "", f"- 原典の位置: {_pages(cand)}",
                f"- 照合後に読む本文: {'、'.join(f['path'] for f in _knowledge_files(root, r))}", "",
                "```text", *lines, "```", ""]
        out += [f"- {rv['claim_id']}: {rv['claim']}\n  - 適用条件: {rv['applicability']}" for rv in r["support_reviews"]]
        out.append("")
    items = [(rv["extraction_id"], i) for rv in samples["reviews"] for i in rv["items"] if i["result"] is None]
    if items:
        out += ["## 抽出品質の確認", "", "| 抽出 | 観点 | ページ | 見ること |", "|---|---|---|---|"]
        out += [f"| {e} | {i['kind']} | {'、'.join(map(str, i['pdf_page_indices'])) or i['epub_href']} | {i['look_for']} |"
                for e, i in items]
    path = root / SHEET
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return {"check": "review-sheet", "result": "pass", "sheet": SHEET, "evidence": len(pending),
            "sample_items": len(items), "errors": []}


def _review_one(r: dict, by: str, now: str, all_ok: bool, rejected: bool, claims: dict, extracted_root: Path) -> bool:
    """1件の根拠へ人の判定を反映する。反映する判定がなければ False。"""
    ids = [rv["claim_id"] for rv in r["support_reviews"]]
    if not (all_ok or rejected or set(ids) & set(claims)):
        return False
    if not rejected:
        ref = r["extracted_ref"]
        cand = snapshot_check.locate(yamlio.load(extracted_root / ref["extraction_id"] / "locations.yaml"),
                                     ref["start_line"], ref["end_line"])
        if "pdf_page_indices" in cand:
            r["locator"]["pdf_page_indices"] = cand["pdf_page_indices"]
            r["locator"]["printed_page_labels"] = cand["printed_page_labels"] if cand["printed_page_labels_source"] else []
        else:
            r["locator"]["epub_href"] = cand["epub_href"]
    for rv in r["support_reviews"]:
        status = claims.get(rv["claim_id"], "supported" if all_ok and not rejected else None)
        if status:
            rv.update(status=status, reviewed_by=by, reviewed_at=now)
    return True


def record(by: str, all_ok: bool = False, rejects: list[str] = (), claim_args: list[str] = (),
           issues: list[str] = (), root: Path = ROOT, extracted_root: Path = EXTRACTED) -> dict:
    result = {"check": "review-record", "result": "fail", "receipts_added": [], "sample_items": 0, "errors": []}
    errors = result["errors"]
    claims = dict(a.partition("=")[::2] for a in claim_args)
    bad = [s for s in claims.values() if s not in ("supported", "not_supported", "unclear")]
    if not by.strip() or bad:
        errors.append("--by が空、または --claim の判定が supported・not_supported・unclear でない")
        return result
    prov, receipts, samples = (copy.deepcopy(d) for d in _load(root))
    now, originals, sources = _now(), extract_cmd._originals_by_hash(), provenance.registered_sources()
    next_id = max((int(rc["receipt_id"][3:]) for rc in receipts["receipts"]), default=0) + 1
    pending = {r["evidence_id"] for r in _pending(prov, receipts, root, extracted_root)}
    for r in (r for r in prov["records"] if r["evidence_id"] in pending):
        ev, rejected = r["evidence_id"], r["evidence_id"] in rejects
        if not _review_one(r, by, now, all_ok, rejected, claims, extracted_root):
            continue
        local_errors, counts = provenance_local.local_results({"records": [r]}, extracted_root, originals)
        ref_errors = [e for e in provenance.reference_errors(prov, receipts, sources) if e.startswith(ev)]
        if local_errors or ref_errors:
            errors += local_errors + ref_errors
            continue
        ref = r["extracted_ref"]
        receipts["receipts"].append({
            "receipt_id": f"VR-{next_id:04d}", "evidence_id": ev, "source_id": r["source_id"],
            "targets": {"record_sha256": provenance.record_sha256(r), "source_sha256": r["source_sha256"],
                        "extracted_sha256": r["extracted_sha256"], "excerpt_sha256": r["excerpt_sha256"],
                        "locations_sha256": manifest.sha256_file(extracted_root / ref["extraction_id"] / "locations.yaml"),
                        "bundle_manifest_sha256": None, "knowledge_files": _knowledge_files(root, r)},
            "claims": [{"claim_id": rv["claim_id"], "claim_sha256": rv["claim_sha256"], "status": rv["status"],
                        "applicability_sha256": provenance.text_sha256(rv["applicability"])}
                       for rv in r["support_reviews"] if rv["status"] != "unreviewed"],
            "machine_checks": {"reference": "pass", **{k: "pass" if counts[k]["run"] else "not_run"
                                                       for k in ("excerpt", "original_hash")}},
            "human_checks": {"source_location": {"result": "rejected" if rejected else "confirmed",
                                                 "checked_by": by, "checked_at": now}},
            "tool": {"name": "iakit", "version": __version__}, "recorded_at": now})
        result["receipts_added"].append(f"VR-{next_id:04d}")
        next_id += 1
    flagged = {tuple(i.split(":", 2)[:2]): i.split(":", 2)[2] if i.count(":") >= 2 else "" for i in issues}
    for rv in samples["reviews"]:
        todo = [i for i in rv["items"] if i["result"] is None]
        if not todo or not (all_ok or any(e == rv["extraction_id"] for e, _ in flagged)):
            continue
        for i in todo:
            key = (rv["extraction_id"], i["kind"])
            i.update(result="issue" if key in flagged else "ok", note=flagged.get(key) or None)
            result["sample_items"] += 1
        rv.update(checked_by=by, checked_at=now)
    for data, schema in ((prov, "provenance.schema.json"), (receipts, "verification-receipt.schema.json"),
                         (samples, "sample-review.schema.json")):
        errors += yamlio.schema_errors(data, schema)
    if errors:
        result["receipts_added"], result["sample_items"] = [], 0
        return result
    for rel, data, default in ((PROV, prov, ""), (RECEIPTS, receipts, RECEIPTS_HEADER), (SAMPLES, samples, "")):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _header(path, default) + yamlio.dumps(data)
        path.write_text(text, encoding="utf-8", newline="\n")
    result["result"] = "pass"
    return result
