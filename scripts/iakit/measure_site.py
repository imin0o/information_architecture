"""ビルド済みサイトのHTMLを巡回し、導線の指標を測る（P2-1）。サイト固有の構造に依存しない。

ページはディレクトリ内の *.html、トップは index.html とする。リンクはサイト内の相対・ルート相対だけを数える。
"""

import posixpath
import re
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return SPACE.sub(" ", text).strip()


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.h1, self.links = "", None, []
        self._in, self._nav, self._href, self._text = None, 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "nav":
            self._nav += 1
        elif tag == "a":
            self._href, self._text = dict(attrs).get("href"), []
        elif tag in ("title", "h1") and self._in is None:
            self._in, self._buf = tag, []

    def handle_endtag(self, tag):
        if tag == "nav":
            self._nav = max(0, self._nav - 1)
        elif tag == "a" and self._href is not None:
            self.links.append({"href": self._href, "text": _norm("".join(self._text)), "nav": self._nav > 0})
            self._href = None
        elif tag == self._in:
            text = _norm("".join(self._buf))
            if tag == "title":
                self.title = text
            elif self.h1 is None:
                self.h1 = text
            self._in = None

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
        if self._in is not None:
            self._buf.append(data)


def _resolve(page: str, href: str, pages: set[str]) -> str | None:
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.path:
        return None
    path = unquote(parts.path)
    path = path.lstrip("/") if path.startswith("/") else posixpath.join(posixpath.dirname(page), path)
    path = posixpath.normpath(path)
    for cand in (path, f"{path}.html", posixpath.join(path, "index.html")):
        cand = cand.removeprefix("./")
        if cand in pages:
            return cand
    return None


def crawl(root: Path) -> dict:
    """ページごとの名称とリンク。リンク先はサイト内のページに解決できたものだけ。"""
    files = {p.relative_to(root).as_posix(): p for p in root.rglob("*.html")}
    if "index.html" not in files:
        raise ValueError(f"{root}: トップの index.html がない")
    site = {}
    for rel, path in sorted(files.items()):
        parser = _Page()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        links = [dict(link, dest=_resolve(rel, link["href"], set(files))) for link in parser.links]
        site[rel] = {"name": parser.h1 or parser.title, "links": [link for link in links if link["dest"]]}
    return site


def metrics(site: dict) -> dict:
    inbound = {p: 0 for p in site}
    texts: dict[str, set[str]] = {}
    dests: dict[str, set[str]] = {}
    mismatch = set()
    for page, info in site.items():
        for link in info["links"]:
            if link["dest"] != page:
                inbound[link["dest"]] += 1
            if link["text"]:
                texts.setdefault(link["dest"], set()).add(link["text"])
                dests.setdefault(link["text"], set()).add(link["dest"])
                name = site[link["dest"]]["name"]
                if name and link["text"] != name:
                    mismatch.add((link["text"], link["dest"], name))
    return {
        "pages": len(site),
        "clicks": clicks(site),
        "one_click": sorted({link["dest"] for link in site["index.html"]["links"]} - {"index.html"}),
        "nav_listed": sorted({link["dest"] for info in site.values() for link in info["links"] if link["nav"]}),
        "landing_name_mismatch": [{"text": t, "dest": d, "name": n} for t, d, n in sorted(mismatch)],
        "link_text_variants": {d: sorted(t) for d, t in sorted(texts.items()) if len(t) > 1},
        "same_text_different_dest": {t: sorted(d) for t, d in sorted(dests.items()) if len(d) > 1},
        "orphans": sorted(p for p, n in inbound.items() if n == 0 and p != "index.html"),
    }


def clicks(site: dict) -> dict[str, int]:
    """トップからの最短クリック数。到達できないページは含めない。"""
    dist, todo = {"index.html": 0}, deque(["index.html"])
    while todo:
        cur = todo.popleft()
        for link in site[cur]["links"]:
            if link["dest"] not in dist:
                dist[link["dest"]] = dist[cur] + 1
                todo.append(link["dest"])
    return dict(sorted(dist.items()))
