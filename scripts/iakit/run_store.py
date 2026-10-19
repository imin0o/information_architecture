"""実行記録（run.yaml・inputs.yaml・decisions.yaml）の読み書き。書き込む前にスキーマで検査する。"""

from datetime import datetime, timezone
from pathlib import Path

from . import yamlio

SCHEMAS = {"run.yaml": "run.schema.json", "inputs.yaml": "input.schema.json", "decisions.yaml": "decision.schema.json"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load(directory: Path, name: str) -> dict:
    path = directory / name
    if not path.is_file():
        raise ValueError(f"{path} がない。先に iakit run init を実行する")
    return yamlio.load(path)


def save(directory: Path, name: str, doc: dict) -> None:
    errors = yamlio.schema_errors(doc, SCHEMAS[name])
    if errors:
        raise ValueError(f"{name} がスキーマに合わない: {errors[:3]}")
    (directory / name).write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
