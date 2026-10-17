"""抜粋の実在性の手元照合（付録B.2の第2層）。

固定スナップショットが手元にある根拠だけを照合し、ない根拠は not_run と数える。
not_run を pass や verified に置き換えない。人による原典・位置の照合はここでは行わない。
"""

from pathlib import Path

from . import snapshot, snapshot_check, yamlio


def _locator_errors(ev: str, locator: dict, locations: dict, ref: dict) -> list[str]:
    cand = snapshot_check.locate(locations, ref["start_line"], ref["end_line"])
    errors = []
    if locations["format"] == "pdf":
        if locator["epub_href"] or locator["element_id"]:
            errors.append(f"{ev}: PDFの根拠にEPUBの位置がある")
        if locator["pdf_page_indices"] and locator["pdf_page_indices"] != cand["pdf_page_indices"]:
            errors.append(f"{ev}: pdf_page_indices が対応表の範囲 {cand['pdf_page_indices']} と一致しない")
        if locator["printed_page_labels"]:
            if cand["printed_page_labels_source"] is None:
                errors.append(f"{ev}: 対応表に印刷番号がないのに printed_page_labels を記入している")
            elif locator["printed_page_labels"] != cand["printed_page_labels"]:
                errors.append(f"{ev}: printed_page_labels が対応表と一致しない（PDF内のページ順との混同を含む）")
    else:
        if locator["pdf_page_indices"] or locator["printed_page_labels"]:
            errors.append(f"{ev}: EPUBの根拠にページ番号を記入している（リフローのページ番号は作らない）")
        if locator["epub_href"] and locator["epub_href"] != cand["epub_href"]:
            errors.append(f"{ev}: epub_href が対応表と一致しない")
        if locator["element_id"] and locator["element_id"] != cand["element_id_candidate"]:
            errors.append(f"{ev}: element_id が範囲を含む要素と一致しない")
    return errors


def local_results(provenance: dict, extracted_root: Path, originals: dict[str, Path]) -> tuple[list[str], dict]:
    """抜粋の照合。originals は原典ハッシュから実ファイルへの対応（手元で見つかったものだけ）。"""
    errors: list[str] = []
    counts = {"excerpt": {"run": 0, "not_run": 0}, "original_hash": {"run": 0, "not_run": 0}}
    for r in provenance["records"]:
        ev, ref = r["evidence_id"], r["extracted_ref"]
        directory = extracted_root / ref["extraction_id"]
        if not (directory / "extraction.yaml").is_file():
            counts["excerpt"]["not_run"] += 1
            counts["original_hash"]["not_run"] += 1
            continue
        counts["excerpt"]["run"] += 1
        original = originals.get(r["source_sha256"])
        counts["original_hash"]["run" if original else "not_run"] += 1
        snap_errors = snapshot_check.verify(directory, original)
        if snap_errors:
            errors += [f"{ev}: {ref['extraction_id']}: {e}" for e in snap_errors]
            continue
        meta = yamlio.load(directory / "extraction.yaml")
        if meta["source"]["sha256"] != r["source_sha256"]:
            errors.append(f"{ev}: source_sha256 が抽出メタデータの原典ハッシュと一致しない")
        if meta["text"]["sha256"] != r["extracted_sha256"]:
            errors.append(f"{ev}: extracted_sha256 が固定テキスト全体のハッシュと一致しない")
        try:
            excerpt = snapshot_check.excerpt_bytes((directory / "text.txt").read_bytes(),
                                                   ref["start_line"], ref["end_line"])
        except ValueError as exc:
            errors.append(f"{ev}: {exc}")
            continue
        if snapshot.sha256(excerpt) != r["excerpt_sha256"]:
            errors.append(f"{ev}: excerpt_sha256 が範囲のバイト列と一致しない")
        errors += [f"{ev}: {e}" for e in snapshot_check.preview_errors(excerpt, r["excerpt_preview"])]
        errors += _locator_errors(ev, r["locator"], yamlio.load(directory / "locations.yaml"), ref)
    return errors, counts
