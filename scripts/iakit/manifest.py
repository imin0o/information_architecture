"""原典登録簿の検査（BR-01、AC-01）。"""

import hashlib
from pathlib import Path

from . import yamlio
from .paths import MANIFEST, ORIGINALS

EXPECTED_IDS = [f"BOOK-{n:03d}" for n in range(1, 19)]
CONFIRMED_STATUSES = {"registered", "extracted", "integrated"}
ORIGINAL_SUFFIXES = {".pdf": "pdf", ".epub": "epub"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def known_hashes(data) -> set[str]:
    entries = [b["original"] for b in data["books"]] + [u["original"] for u in data["unassigned_originals"]]
    return {o["sha256"] for o in entries if o["sha256"]}


def consistency_errors(data) -> list[str]:
    """スキーマで表せない整合性。値を推測で埋めていないかを中心に見る。"""
    errors: list[str] = []
    ids = [b["book_id"] for b in data["books"]]
    if ids != EXPECTED_IDS:
        errors.append(f"books: BOOK-001〜BOOK-018を順に1件ずつ登録する（実際: {ids}）")
    seen: dict[str, str] = {}
    entries = [(b["book_id"], b["original"]) for b in data["books"]]
    entries += [(f"unassigned[{i}]", u["original"]) for i, u in enumerate(data["unassigned_originals"])]
    for owner, original in entries:
        values = [original["format"], original["sha256"], original["size_bytes"]]
        if original["received"] and None in values:
            errors.append(f"{owner}: 受領済みなら format・sha256・size_bytes を実ファイルから記録する")
        if not original["received"] and any(v is not None for v in values):
            errors.append(f"{owner}: 未受領なのに format・sha256・size_bytes が埋まっている")
        sha = original["sha256"]
        if sha and sha in seen:
            errors.append(f"{owner}: sha256 が {seen[sha]} と重複している")
        elif sha:
            seen[sha] = owner
    for book in data["books"]:
        bid, bib = book["book_id"], book["bibliography"]
        received = book["original"]["received"]
        if book["status"] == "not_received" and received:
            errors.append(f"{bid}: 受領済みなのに status が not_received")
        if book["status"] != "not_received" and book["status"] != "on_hold" and not received:
            errors.append(f"{bid}: 未受領なのに status が {book['status']}")
        filled = [k for k, v in bib.items() if k not in ("confirmed_by", "confirmed_at") and v is not None]
        if bool(bib["confirmed_by"]) != bool(bib["confirmed_at"]):
            errors.append(f"{bid}: 書誌の確認者・確認日時は組で記録する")
        if filled and not (bib["confirmed_by"] and bib["confirmed_at"]):
            errors.append(f"{bid}: 確認者・確認日時のない書誌値がある: {filled}")
        if book["status"] in CONFIRMED_STATUSES:
            if not (bib["confirmed_by"] and bib["confirmed_at"]):
                errors.append(f"{bid}: status {book['status']} には原典での書誌確認が必要")
            if not bib["title"] or not bib["title"].strip() or not bib["authors"]:
                errors.append(f"{bid}: 確認済み状態には確認した題名・著者が必要")
        if book["license"]["terms"] and not book["license"]["confirmed_by"]:
            errors.append(f"{bid}: 確認者のない利用条件がある")
        if book["mvp"]["selected"] is not None and not book["mvp"]["reason"]:
            errors.append(f"{bid}: MVP採否には選定理由を記録する")
    return errors


def local_errors(data, originals: Path = ORIGINALS) -> tuple[list[str], str]:
    """手元の原典と照合する。原典がない環境（CI等）では not_run を返す。"""
    files = [p for p in originals.rglob("*") if p.is_file()] if originals.is_dir() else []
    if not files:
        return [], "not_run"
    errors: list[str] = []
    actual = {}
    for path in files:
        actual[sha256_file(path)] = path
    expected = {}
    for book in data["books"]:
        if book["original"]["sha256"]:
            expected[book["original"]["sha256"]] = (book["book_id"], book["original"])
    for i, u in enumerate(data["unassigned_originals"]):
        expected[u["original"]["sha256"]] = (f"unassigned[{i}]", u["original"])
    for sha, (owner, original) in expected.items():
        path = actual.get(sha)
        if path is None:
            errors.append(f"{owner}: sha256 に一致する原典が sources/originals/ にない")
            continue
        if ORIGINAL_SUFFIXES.get(path.suffix.lower()) != original["format"]:
            errors.append(f"{owner}: format {original['format']} が実ファイルの拡張子と一致しない")
        if path.stat().st_size != original["size_bytes"]:
            errors.append(f"{owner}: size_bytes が実ファイルと一致しない")
    for sha, path in actual.items():
        if sha not in expected:
            errors.append(f"未登録の原典がある: sha256={sha} ({path.suffix})")
    return errors, "run"


def check(local: bool) -> dict:
    data = yamlio.load(MANIFEST)
    errors = yamlio.schema_errors(data, "manifest.schema.json")
    if not errors:
        errors = consistency_errors(data)
    local_status = "skipped"
    if local and not errors:
        local_errs, local_status = local_errors(data)
        errors += local_errs
    statuses: dict[str, int] = {}
    for book in data.get("books", []):
        statuses[book["status"]] = statuses.get(book["status"], 0) + 1
    return {
        "check": "manifest",
        "result": "fail" if errors else "pass",
        "local_originals": local_status,
        "status_counts": statuses,
        "unassigned_originals": len(data.get("unassigned_originals", [])),
        "errors": errors,
    }
