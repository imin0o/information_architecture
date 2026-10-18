# /// script
# requires-python = ">=3.12"
# dependencies = ["jsonschema==4.25.1", "ruamel.yaml==0.18.15"]
# ///
"""分野Skillを単独で使うときの成果物の形式検査（P-M6-01）。

  uv run <このSkillのディレクトリ>/scripts/check_artifacts.py <成果物のディレクトリ>

ディレクトリ直下のYAMLのうち、このSkillにスキーマを同梱したものを検査する。ID参照、依存、判断や入力との
整合は検査しない（ia-orchestrator の contracts check の範囲）。結果は契約検査ではなく形式検査として報告する。
uv がなければ、依存（jsonschema、ruamel.yaml）を入れた Python で python check_artifacts.py ... と実行する。
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from ruamel.yaml import YAML

# iakit.contracts.SCHEMAS と同じ対応（tests/checks/test_check_artifacts.py で一致を確かめる）
FILES = {
    "inputs.yaml": "input.schema.json",
    "decisions.yaml": "decision.schema.json",
    "observations.yaml": "observation.schema.json",
    "run.yaml": "run.schema.json",
    "content-inventory.yaml": "content-inventory.schema.json",
    "controlled-vocabulary.yaml": "controlled-vocabulary.schema.json",
    "taxonomy.yaml": "taxonomy.schema.json",
    "sitemap.yaml": "sitemap.schema.json",
    "findings.yaml": "findings.schema.json",
}
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def _registry() -> Registry:
    return Registry().with_resources(
        (p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8")), default_specification=DRAFT202012))
        for p in sorted(SCHEMAS.glob("*.schema.json")))


def _load(path: Path):
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)  # 日付を文字列のまま読む（iakit の読込みと同じ）
    yaml.allow_duplicate_keys = False
    return yaml.load(path.read_text(encoding="utf-8"))


def check(target: Path) -> dict:
    registry, checked, skipped, errors = _registry(), [], [], []
    for path in sorted(target.glob("*.yaml")):
        schema = FILES.get(path.name)
        if schema is None or not (SCHEMAS / schema).is_file():
            skipped.append(path.name)
            continue
        checked.append(path.name)
        try:
            data = _load(path)
        except Exception as exc:  # ruamel の各種例外をまとめて扱う
            errors.append(f"{path.name}: YAMLとして読めない: {str(exc).splitlines()[0]}")
            continue
        validator = Draft202012Validator(registry.contents(schema), registry=registry)
        errors += [f"{path.name}: {'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
                   for e in sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])]
    if not checked:
        errors.append("検査できるファイルがない（このSkillにスキーマを同梱した成果物を置いたディレクトリを指定する）")
    return {"check": "schema_only", "result": "fail" if errors else "pass", "checked": checked,
            "not_checked": skipped, "contracts_check": "not_run", "errors": errors}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not Path(args[0]).is_dir():
        sys.stdout.write("usage: check_artifacts.py <成果物のディレクトリ>\n")
        return 2
    out = check(Path(args[0]))
    yaml = YAML(typ="safe", pure=True)
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.dump(out, sys.stdout)
    return 0 if out["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
