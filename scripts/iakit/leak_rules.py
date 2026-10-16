"""漏えい検査の内容ルール。本文は診断へ出さない。"""

import hashlib
import io
import json
import re
import zipfile
from pathlib import PurePosixPath

from . import yamlio

BOOK_SUFFIXES = {".pdf", ".epub", ".mobi", ".azw", ".azw3", ".djvu"}
FORBIDDEN_DIRS = ("runs/", "library/build-logs/raw/")
CONFIDENTIAL = re.compile(rb"^\s*(confidentiality|classification)\s*:\s*[\"']?confidential\b", re.M)


def book_content(content: bytes) -> bool:
    """拡張子を変えたPDF・EPUBも検出する。一般のZIPは書籍扱いしない。"""
    if content.startswith(b"%PDF-"):
        return True
    if not content.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("mimetype")
            return info.file_size == 20 and archive.read(info) == b"application/epub+zip"
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, OSError):
        return False


def _contains_confidential(value) -> bool:
    if isinstance(value, dict):
        return any(
            (key in {"confidentiality", "classification"} and item == "confidential")
            or _contains_confidential(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_confidential(item) for item in value)
    return False


def classification_reasons(path: str, content: bytes) -> list[str]:
    suffix = PurePosixPath(path).suffix.lower()
    metadata = suffix in {".yaml", ".yml", ".json"}
    front_matter = suffix in {".md", ".markdown"} and re.match(rb"^(?:\xef\xbb\xbf)?---(?:\r?\n|$)", content)
    if metadata or front_matter:
        try:
            text = content.decode("utf-8-sig")
            if front_matter:
                lines = text.splitlines()
                end = next(i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."})
                text = "\n".join(lines[1:end])
            if suffix == ".json":
                data = json.loads(text)
                yamlio.dumps(data)  # 非有限数などJSON互換でない値を拒否
            else:
                data = yamlio.loads(text)
        except (ValueError, UnicodeError, StopIteration, RecursionError):
            return ["機密区分のメタデータを解析できない"]
        return ["confidential 区分のデータ"] if _contains_confidential(data) or CONFIDENTIAL.search(content) else []
    return ["confidential 区分のデータ"] if CONFIDENTIAL.search(content) else []


def inspect(path: str, content: bytes, originals: set[str], fixtures: dict[str, str]) -> list[str]:
    reasons = []
    sha = hashlib.sha256(content).hexdigest()
    if sha in originals:
        reasons.append("登録済み原典と同一内容")
    if path.startswith("sources/") and path != "sources/manifest.yaml":
        reasons.append("sources/ は manifest.yaml 以外を管理しない")
    if path.startswith(FORBIDDEN_DIRS):
        reasons.append("実行記録・生ログはGit管理外")
    if (PurePosixPath(path).suffix.lower() in BOOK_SUFFIXES or book_content(content)) and fixtures.get(path) != sha:
        reasons.append("書籍形式のファイルは生成器と一致する登録済み自作素材だけ許可")
    return reasons + classification_reasons(path, content)
