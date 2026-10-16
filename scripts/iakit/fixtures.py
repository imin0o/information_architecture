"""自作素材（tests/fixtures/）の生成と一致確認。"""

import hashlib
import json

from . import fixture_epub, fixture_pdf, yamlio
from .paths import FIXTURES, FIXTURES_INDEX

BUILDERS = {
    "pdf/page-labels.pdf": (fixture_pdf.build, fixture_pdf.expected, "pdf"),
    "epub/reflowable.epub": (fixture_epub.build, fixture_epub.expected, "epub"),
}
HEADER = (
    "# 自作素材の登録簿。iakit fixtures build で生成し、手で編集しない。\n"
    "# ここに sha256 が一致する書籍形式ファイルだけ、Gitへの追加を許可する（iakit leaks check）。\n"
)
# 確認済みの生成結果だけを登録する。Git履歴の登録簿から自動追加しない。
# M0: PDFページ番号・EPUB位置参照の初版。生成器更新前に旧版の値を保存する。
HISTORICAL_CATALOGUE_HASHES = frozenset({
    "613e0d2949b0304264d333ab12fb12710f3eca4185de97f7656a11b25abcefe0",
})


def catalogue_sha256(index) -> str:
    canonical = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render() -> tuple[dict[str, bytes], str]:
    files: dict[str, bytes] = {}
    entries = []
    for rel, (build, expected, fmt) in BUILDERS.items():
        content = build()
        files[rel] = content
        entries.append({
            "path": rel,
            "format": fmt,
            "origin": "self-made",
            "sha256": hashlib.sha256(content).hexdigest(),
            "expected": expected(),
        })
    index = HEADER + yamlio.dumps({"schema_version": "0.1.0", "files": entries})
    return files, index


def build() -> list[str]:
    files, index = render()
    for rel, content in files.items():
        path = FIXTURES / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    FIXTURES_INDEX.write_text(index, encoding="utf-8", newline="\n")
    return sorted(files) + [FIXTURES_INDEX.name]


def check() -> dict:
    files, index = render()
    errors = []
    for rel, content in files.items():
        path = FIXTURES / rel
        if not path.is_file() or path.read_bytes() != content:
            errors.append(f"{rel}: 生成結果と一致しない（iakit fixtures build で再生成）")
    if not FIXTURES_INDEX.is_file() or FIXTURES_INDEX.read_text(encoding="utf-8") != index:
        errors.append("fixtures.yaml: 生成結果と一致しない")
    return {
        "check": "fixtures", "result": "fail" if errors else "pass", "files": sorted(files),
        "expected_catalogue_sha256": catalogue_sha256(yamlio.loads(index)), "errors": errors,
    }
