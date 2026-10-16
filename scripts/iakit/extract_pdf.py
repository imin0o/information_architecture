"""PDFのページ単位抽出（BR-01）。

上流book-to-skillはページごとに抽出した後、ヘッダー・フッター・ページ番号の除去と
行末ハイフンの結合で全ページを1つに整形するため、ページ境界が失われる。ここでは同じpypdfの
`page.extract_text()` をページごとに呼び、整形を行わずにページ順と本文を対応付ける。
"""

import logging
from pathlib import Path

import pypdf

METHOD = "pypdf.page.extract_text"
OPTIONS = {"strict": False, "extraction_mode": "plain", "postprocess": "none"}


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage()[:200])


def _image_count(page) -> int:
    """ページ直下のリソースにある画像XObjectの数。文字として読めない図の目安で、Form内は数えない。"""
    try:
        xobjects = (page.get("/Resources") or {}).get("/XObject") or {}
        return sum(1 for ref in xobjects.values() if ref.get_object().get("/Subtype") == "/Image")
    except Exception:
        return 0


def extract(path: Path) -> dict:
    """ページ順・印刷ページ番号・各ページの本文を返す。

    印刷ページ番号はPageLabelsがある場合だけ記録する。PageLabelsがないときpypdfは
    ページ順を番号として返すが、それは印刷番号ではないためnullにする。
    """
    handler = _Collect()
    logger = logging.getLogger("pypdf")
    logger.addHandler(handler)
    try:
        reader = pypdf.PdfReader(path, strict=OPTIONS["strict"])
        has_labels = "/PageLabels" in reader.trailer["/Root"]
        labels = list(reader.page_labels) if has_labels else []
        units = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text(extraction_mode=OPTIONS["extraction_mode"]) or ""
                error = None
            except Exception as exc:  # 1ページの失敗で全体を止めず、未抽出として記録する
                text, error = "", f"{type(exc).__name__}: {exc}"[:200]
            units.append({
                "pdf_page_index": i + 1,
                "printed_page_label": labels[i] if has_labels else None,
                "images": _image_count(page),
                "text": text,
                "error": error,
            })
    finally:
        logger.removeHandler(handler)
    return {
        "format": "pdf",
        "method": METHOD,
        "libraries": {"pypdf": pypdf.__version__},
        "options": dict(OPTIONS),
        "page_labels": "present" if has_labels else "absent",
        "units": units,
        "warnings": handler.messages,
    }
