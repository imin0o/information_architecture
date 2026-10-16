"""自作素材（tests/fixtures/）の生成と一致確認。"""

import hashlib

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
    return {"check": "fixtures", "result": "fail" if errors else "pass", "files": sorted(files), "errors": errors}
