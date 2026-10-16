"""リポジトリ内の固定パス。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "sources"
MANIFEST = SOURCES / "manifest.yaml"
ORIGINALS = SOURCES / "originals"
EXTRACTED = SOURCES / "extracted"
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "tests" / "fixtures"
FIXTURES_INDEX = FIXTURES / "fixtures.yaml"
CHECK_REPORTS = ROOT / "library" / "build-logs" / "checks"


def require_checkout() -> None:
    """構築用CLIはcheckout + editable installで利用する。"""
    if not (ROOT / "pyproject.toml").is_file() or not (ROOT / "scripts/iakit").is_dir():
        raise ValueError("iakitはcheckout専用です。リポジトリで uv sync --locked を実行してください")
