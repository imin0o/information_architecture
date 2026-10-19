"""成果物本文の版・機密区分・参照版の書き換えと、実行ディレクトリ基準のパス（P1-1、P1-4）。

YAMLは構文木の位置情報を使って値だけを置き換え、コメント・キー順序・書式を保つ。
"""

import os
import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

ART = r"(?<![0-9A-Za-z_-])(ART-[0-9]{3})"
_MD_REF = re.compile(ART + r" v([0-9]+)(?![0-9A-Za-z_])")
_MD_LEVEL = re.compile(r"^(\s*(?:[-*]\s+)?機密区分\s*[:：]\s*`?)(public|internal|confidential)", re.M)


def run_relative(directory: Path, path: str | Path) -> str:
    """成果物のパスを実行ディレクトリからの相対パスにする。絶対パスや作業ディレクトリ基準のパスも受ける。

    割り当てドライブでは resolve() がUNCパスを返すため、正規化だけの比較を先に行い、合わなければ両方を resolve する。
    """
    path = Path(path)
    if not path.is_absolute() and (directory / path).is_file():
        candidate = directory / path
    else:
        candidate = path
    for base, target in ((Path(os.path.abspath(directory)), Path(os.path.abspath(candidate))),
                         (directory.resolve(), candidate.resolve())):
        try:
            rel = target.relative_to(base)
        except ValueError:
            continue
        if ".." not in rel.parts:
            return rel.as_posix()
    raise ValueError(f"{path} は実行ディレクトリ {directory} の外にある")


_DECISION_REF = re.compile(r"(?<![0-9A-Za-z_-])(IA-[0-9]{3}) r([0-9]+)(?![0-9A-Za-z_])")

def _rewrite_yaml(text: str, own: dict | None, versions: dict[str, int],
                  decision_versions: dict[tuple[str, int], int] | None = None) -> str:
    """マッピングの値の範囲だけを書き換える。説明文やコメント内のYAML例には触れない。"""
    root = YAML(typ="safe", pure=True).compose(text)
    edits, seen = {}, set()

    def replace(node: ScalarNode, value) -> None:
        tag = "tag:yaml.org,2002:str" if isinstance(value, str) else "tag:yaml.org,2002:int"
        if node.value == str(value) and node.tag == tag:
            return
        scalar = str(value)
        if isinstance(value, str) and node.style in ("'", '"'):
            scalar = node.style + scalar + node.style
        edits[node.start_mark.index, node.end_mark.index] = scalar

    def visit(node) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, MappingNode):
            fields = {k.value: v for k, v in node.value if isinstance(k, ScalarNode)}
            if node is root and own:
                for key, value in own.items():
                    if isinstance(fields.get(key), ScalarNode):
                        replace(fields[key], value)
            elif all(isinstance(fields.get(k), ScalarNode) for k in ("artifact_id", "version")):
                aid = fields["artifact_id"].value
                if aid in versions:
                    replace(fields["version"], versions[aid])
            if decision_versions and all(isinstance(fields.get(k), ScalarNode)
                                         for k in ("decision_id", "decision_revision")):
                revision = fields["decision_revision"]
                key = (fields["decision_id"].value, int(revision.value))
                if key in decision_versions:
                    replace(revision, decision_versions[key])
            for _, child in node.value:
                visit(child)
        elif isinstance(node, SequenceNode):
            for child in node.value:
                visit(child)

    visit(root)
    for (start, end), value in sorted(edits.items(), reverse=True):
        text = text[:start] + value + text[end:]
    return text


def rewrite_decisions(text: str, decisions: list[dict], markdown: bool = True) -> str:
    """置き換えられた（superseded）判断の参照を、同じIDの現行版へ書き換える。撤回した判断は書き換えない。"""
    active = {d["decision_id"]: d["decision_revision"] for d in decisions if d["status"] == "active"}
    superseded = {(d["decision_id"], d["decision_revision"]) for d in decisions if d["status"] == "superseded"}

    def latest(did: str, rev: str) -> str:
        return str(active[did]) if (did, int(rev)) in superseded and did in active else rev
    text = _DECISION_REF.sub(lambda m: f"{m[1]} r{latest(m[1], m[2])}", text)
    versions = {key: active[key[0]] for key in superseded if key[0] in active}
    return text if markdown else _rewrite_yaml(text, None, {}, versions)


def _header_end(text: str) -> int:
    """Markdownの最初の節の見出しより前の範囲。"""
    m = re.search(r"^## ", text, re.M)
    return m.start() if m else len(text)


def rewrite(text: str, markdown: bool, own: dict | None, versions: dict[str, int]) -> str:
    """own は本体の artifact_id・version・classification、versions は参照先の現行版（書き換えない場合は空）。"""
    if markdown:
        end = _header_end(text)
        head = text[:end]
        if own:
            head = _MD_LEVEL.sub(lambda m: m[1] + own["classification"], head)
        head = _MD_REF.sub(lambda m: f"{m[1]} v{versions.get(m[1], int(m[2]))}", head)
        return head + text[end:]
    return _rewrite_yaml(text, own, versions)
