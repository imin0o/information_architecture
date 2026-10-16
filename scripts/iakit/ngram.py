"""原典由来の部分コピーを語n-gramで検出する試作（M1の評価用）。

固定抽出テキストと検査対象の文書を同じ規則で正規化し、対象のn-gramのうち抽出テキストにも
現れる割合と、連続して一致した語数を返す。許可済み抜粋（provenanceの範囲）は例外にできる。
ハッシュ照合用の正規化（付録B）とは別物で、検出のためだけに表記を緩める。
"""

import re
import unicodedata

DEFAULT_N = 8
WORD = re.compile(r"\w+(?:[-'’]\w+)*", re.UNICODE)


def words(text: str, loose: bool = True) -> list[str]:
    """loose=True はNFKC・casefold・行末ハイフン結合を行う。CJKは1文字を1語とする。"""
    if loose:
        text = unicodedata.normalize("NFKC", text).casefold()
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    out = []
    for token in WORD.findall(text):
        if any(unicodedata.east_asian_width(c) in "WF" for c in token):
            out.extend(token)
        else:
            out.append(token)
    return out


def grams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def match(candidate: str, source: str, n: int = DEFAULT_N, loose: bool = True,
          allowed: list[str] = ()) -> dict:
    """candidate のn-gramのうち source に現れるもの。allowed の抜粋に含まれるn-gramは除く。"""
    source_grams = set(grams(words(source, loose), n))
    allowed_grams = {g for a in allowed for g in grams(words(a, loose), n)}
    cand = grams(words(candidate, loose), n)
    hits = [g in source_grams and g not in allowed_grams for g in cand]
    longest = run = 0
    for hit in hits:
        run = run + 1 if hit else 0
        longest = max(longest, run)
    return {
        "candidate_grams": len(cand),
        "matched_grams": sum(hits),
        "ratio": round(sum(hits) / len(cand), 3) if cand else 0.0,
        "longest_run_words": longest + n - 1 if longest else 0,
    }


def detected(result: dict, min_run_words: int = DEFAULT_N) -> bool:
    return result["longest_run_words"] >= min_run_words
