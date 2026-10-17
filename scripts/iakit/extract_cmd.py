"""iakit extract / excerpt の処理。出力には本文を含めず、ID・ハッシュ・件数・位置だけを返す。"""

from pathlib import Path

from . import fixtures, manifest, snapshot, snapshot_check, yamlio
from .paths import EXTRACTED, FIXTURES, MANIFEST, ORIGINALS


def _original_for(book_id: str) -> tuple[Path, str]:
    data = yamlio.load(MANIFEST)
    book = next((b for b in data["books"] if b["book_id"] == book_id), None)
    if book is None:
        raise ValueError(f"{book_id} は登録簿にない")
    expected = book["original"]["sha256"]
    if not expected:
        raise ValueError(f"{book_id} は原典未受領（sha256がnull）")
    for path in (p for p in ORIGINALS.rglob("*") if p.is_file()) if ORIGINALS.is_dir() else ():
        if manifest.sha256_file(path) == expected:
            state = "confirmed" if book["status"] in manifest.CONFIRMED_STATUSES else "trial"
            return path, state
    raise ValueError(f"{book_id} のsha256に一致する原典が sources/originals/ にない")


def run(book: str | None, fixture: str | None, root: Path | None) -> dict:
    if fixture:
        if fixture not in fixtures.BUILDERS:
            raise ValueError(f"自作素材 {fixture} は登録簿にない")
        source, source_id, state = FIXTURES / fixture, "FIXTURE-" + Path(fixture).stem, "trial"
    else:
        source, state = _original_for(book)
        source_id = book
    directory = snapshot.create(source, source_id, root or EXTRACTED, state)
    meta = yamlio.load(directory / "extraction.yaml")
    errors = snapshot_check.verify(directory, source)
    return {
        "check": "extract", "result": "fail" if errors else "pass", "extraction_id": meta["extraction_id"],
        "source_id": source_id, "bibliography_state": state, "text_sha256": meta["text"]["sha256"],
        "lines": meta["text"]["lines"], "quality": {k: v for k, v in meta["quality"].items() if k != "warnings"},
        "errors": errors,
    }


def _originals_by_hash() -> dict[str, Path]:
    found = {}
    for base in (ORIGINALS, FIXTURES):
        if base.is_dir():
            for path in base.rglob("*"):
                if path.is_file() and path.suffix.lower() in (".pdf", ".epub"):
                    found[manifest.sha256_file(path)] = path
    return found


def check(dirs: list[Path] | None, local: bool) -> dict:
    targets = dirs or (sorted(p for p in EXTRACTED.iterdir() if p.is_dir() and not p.name.startswith("."))
                       if EXTRACTED.is_dir() else [])
    originals = _originals_by_hash() if local else {}
    errors, source_checks = [], {"run": 0, "not_run": 0}
    for directory in targets:
        original = None
        if local:
            try:
                original = originals.get(yamlio.load(directory / "extraction.yaml")["source"]["sha256"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        source_checks["run" if original else "not_run"] += 1
        errors += [f"{directory.name}: {e}" for e in snapshot_check.verify(directory, original)]
    return {
        "check": "extracted", "result": "fail" if errors else "pass", "snapshots": len(targets),
        "original_hash_check": source_checks if local else "skipped", "errors": errors,
    }


def ref(directory: Path, lines: str) -> dict:
    start, _, end = lines.partition("-")
    start_line, end_line = int(start), int(end or start)
    errors = snapshot_check.verify(directory)
    if errors:
        return {"check": "excerpt-ref", "result": "fail", "errors": errors}
    meta = yamlio.load(directory / "extraction.yaml")
    excerpt = snapshot_check.excerpt_bytes((directory / "text.txt").read_bytes(), start_line, end_line)
    return {
        "check": "excerpt-ref", "result": "fail" if errors else "pass",
        "source_id": meta["source"]["source_id"], "source_sha256": meta["source"]["sha256"],
        "extracted_sha256": meta["text"]["sha256"],
        "extracted_ref": {"extraction_id": meta["extraction_id"], "start_line": start_line, "end_line": end_line},
        "excerpt_sha256": snapshot.sha256(excerpt),
        "locator_candidate": snapshot_check.locate(yamlio.load(directory / "locations.yaml"), start_line, end_line),
        "errors": errors,
    }
