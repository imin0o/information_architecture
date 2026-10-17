"""YAML 1.2（JSON互換の値に限定）の読み書きとJSON Schema検証（4.4）。"""

import functools
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from ruamel.yaml import YAML

from .paths import SCHEMAS

FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _date_time(value) -> bool:
    # jsonschemaの日時検証用extrasに依存せず、時差付きRFC3339表記を検査する。
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return False
    return datetime.fromisoformat(value.upper()).tzinfo is not None


class DataError(ValueError):
    """JSON互換でない値、重複キー、独自タグなどの読込み失敗。"""


def _loader() -> YAML:
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    yaml.allow_duplicate_keys = False
    return yaml


def _check_json_compatible(value, where: str = "$", active: set[int] | None = None) -> None:
    active = set() if active is None else active
    if isinstance(value, (dict, list)):
        if id(value) in active:
            raise DataError(f"{where}: 循環参照はJSON互換でない")
        active.add(id(value))
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DataError(f"{where}: キーは文字列に限る: {key!r}")
            _check_json_compatible(item, f"{where}.{key}", active)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_compatible(item, f"{where}[{i}]", active)
    elif isinstance(value, float) and not math.isfinite(value):
        raise DataError(f"{where}: 非有限数はJSON互換でない")
    elif not (value is None or isinstance(value, (str, bool, int, float))):
        raise DataError(f"{where}: JSON互換でない値（日時は文字列で書く）: {type(value).__name__}")
    if isinstance(value, (dict, list)):
        active.remove(id(value))


def loads(text: str):
    try:
        data = _loader().load(text)
    except Exception as exc:  # ruamel の各種例外をまとめて扱う
        raise DataError(str(exc)) from exc
    _check_json_compatible(data)
    return data


def load(path: Path):
    return loads(path.read_text(encoding="utf-8"))


def dumps(data) -> str:
    _check_json_compatible(data)
    yaml = YAML(typ="safe", pure=True)
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.sort_base_mapping_type_on_output = False
    buf = io.StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


@functools.cache
def _registry() -> Registry:
    """schemas/ の全スキーマを $id で登録し、ファイル間の相対 $ref を配布物内で解決する。"""
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if schema.get("$id") != path.name:
            raise DataError(f"{path.name}: $id はファイル名と一致させる")
        resources.append((path.name, Resource.from_contents(schema, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


def schema_errors(data, schema_name: str) -> list[str]:
    schema = _registry().contents(schema_name)
    validator = Draft202012Validator(schema, registry=_registry(), format_checker=FORMAT_CHECKER)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '$'}: {err.message}"
        for err in sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    ]
