"""成果物本文の版・機密区分・参照版の書き換えと、実行ディレクトリ基準のパス（P1-1、P1-4）。

YAMLを読み書きし直すとコメントと書式が失われるため、該当する行だけを正規表現で置き換える。
"""

import os
import re
from pathlib import Path

ART = r"(?<![0-9A-Za-z_-])(ART-[0-9]{3})"
_SELF = re.compile(r"^(artifact_id|version|classification):[ \t]*[^\s#]+", re.M)
_FLOW_REF = re.compile(r"(artifact_id:[ \t]*)" + ART + r"([ \t]*,[ \t]*version:[ \t]*)([0-9]+)")
_BLOCK_REF = re.compile(r"(^[ \t]+(?:-[ \t]+)?artifact_id:[ \t]*)" + ART + r"([ \t]*\n[ \t]+version:[ \t]*)([0-9]+)", re.M)
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
_DECISION_YAML = re.compile(r"(decision_id:[ \t]*)(IA-[0-9]{3})([ \t]*,?[ \t]*\n?[ \t]*decision_revision:[ \t]*)([0-9]+)")


def rewrite_decisions(text: str, decisions: list[dict]) -> str:
    """置き換えられた（superseded）判断の参照を、同じIDの現行版へ書き換える。撤回した判断は書き換えない。"""
    active = {d["decision_id"]: d["decision_revision"] for d in decisions if d["status"] == "active"}
    superseded = {(d["decision_id"], d["decision_revision"]) for d in decisions if d["status"] == "superseded"}

    def latest(did: str, rev: str) -> str:
        return str(active[did]) if (did, int(rev)) in superseded and did in active else rev
    text = _DECISION_REF.sub(lambda m: f"{m[1]} r{latest(m[1], m[2])}", text)
    return _DECISION_YAML.sub(lambda m: f"{m[1]}{m[2]}{m[3]}{latest(m[2], m[4])}", text)


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
    if own:
        text = _SELF.sub(lambda m: f"{m[1]}: {own[m[1]]}", text)

    def ref(m):
        return f"{m[1]}{m[2]}{m[3]}{versions.get(m[2], int(m[4]))}"
    return _BLOCK_REF.sub(ref, _FLOW_REF.sub(ref, text))
