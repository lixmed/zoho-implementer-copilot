"""Live lookup in Zoho's official Deluge / developer docs, per app.

How "search" works without a search engine:
  1. Download Zoho's public index page for the app (e.g. /deluge/help/crm-tasks.html),
     which links every Deluge task for that app.
  2. Rank those links LOCALLY against the query (anchor text + URL slug).
  3. The agent then reads the best pages with read_doc().

The query never leaves this machine. Only plain GETs to public zoho.com doc pages are
made, and only to an allowlist of paths. Pages are cached on disk for 7 days.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from copilot.orgs.registry import home_dir

BASE = "https://www.zoho.com"
INTEGRATION_INDEX = f"{BASE}/deluge/help/integration-tasks.html"
USER_AGENT = "zoho-implementer-copilot (docs lookup)"
CACHE_TTL = 7 * 24 * 3600
MAX_TEXT = 15_000

# Allowed doc paths on www.zoho.com. Anything else is refused.
ALLOWED_PREFIXES = ("/deluge/help/",)
ALLOWED_PATTERNS = (re.compile(r"^/[a-z]+/(help|developer)/"),)  # e.g. /crm/developer/docs/...

# Language reference pages that are useful for every app
CORE_PAGES = {
    "invokeurl: call an external API, send an http request (GET POST PUT), webhook, json": (
        f"{BASE}/deluge/help/web-data/invokeurl-task.html"
    ),
    "connections: oauth authentication for API calls, connection link name": (
        f"{BASE}/deluge/help/deluge-connections.html"
    ),
    "CRM field and module api names": f"{BASE}/deluge/help/script/crm/api-names.html",
    "for each loop over a list": f"{BASE}/deluge/help/list-manipulations/for-each-element.html",
}


class DocAccessError(ValueError):
    pass


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "www.zoho.com":
        raise DocAccessError(f"only https://www.zoho.com docs are allowed, got {url!r}")
    path = parts.path
    if not (path.startswith(ALLOWED_PREFIXES) or any(p.match(path) for p in ALLOWED_PATTERNS)):
        raise DocAccessError(f"path not in the docs allowlist: {path}")
    return f"https://www.zoho.com{path}"  # drop query/fragment


class DocFetcher:
    def __init__(self, http: httpx.Client | None = None, cache_dir: Path | None = None):
        self._http = http or httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self.cache_dir = cache_dir or home_dir() / "doc-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, url: str) -> str:
        url = check_url(url)
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".html")
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL:
            return path.read_text(encoding="utf-8")
        resp = self._http.get(url)
        if resp.status_code != 200:
            raise DocAccessError(f"{url} returned HTTP {resp.status_code}")
        final = str(resp.url)
        if final != url:
            check_url(final)  # a redirect must stay inside the allowlist too
        path.write_text(resp.text, encoding="utf-8")
        return resp.text


# ---------------- HTML helpers ----------------


class _LinkParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__()
        self.base, self.links, self._href, self._text = base, [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append(
                (urljoin(self.base, self._href), " ".join("".join(self._text).split()))
            )
            self._href = None


def extract_links(html: str, base: str) -> list[tuple[str, str]]:
    p = _LinkParser(base)
    p.feed(html)
    out, seen = [], set()
    for url, text in p.links:
        try:
            url = check_url(url)
        except DocAccessError:
            continue
        if url not in seen and url.endswith(".html"):
            seen.add(url)
            out.append((url, text))
    return out


class _TextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "header", "footer", "nav", "form"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "tr", "div", "br", "table"}

    def __init__(self):
        super().__init__()
        self.out, self._skip, self._pre, self.title = [], 0, 0, ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "pre":
            self._pre += 1
            self.out.append("\n```\n")
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "pre":
            self._pre = max(0, self._pre - 1)
            self.out.append("\n```\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data if self._pre else re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextParser()
    p.feed(html)
    text = re.sub(r"\n[ \t]+", "\n", "".join(p.out))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return p.title.strip(), text


# ---------------- search and read ----------------


@dataclass
class DocHit:
    title: str
    url: str
    score: float


_STOP = {
    "the",
    "a",
    "an",
    "to",
    "in",
    "of",
    "for",
    "and",
    "how",
    "with",
    "on",
    "by",
    "from",
    "zoho",
}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _STOP]


def app_index_url(fetcher: DocFetcher, app: str) -> str | None:
    """Find the app's task index (e.g. crm-tasks.html) from the integration-tasks page."""
    app = app.lower().strip()
    for url, _ in extract_links(fetcher.get(INTEGRATION_INDEX), INTEGRATION_INDEX):
        if url.rsplit("/", 1)[-1] == f"{app}-tasks.html":
            return url
    return None


def search(
    query: str, app: str = "crm", limit: int = 8, fetcher: DocFetcher | None = None
) -> list[DocHit]:
    fetcher = fetcher or DocFetcher()
    candidates: dict[str, str] = {url: name for name, url in CORE_PAGES.items()}
    index = app_index_url(fetcher, app)
    indexes = [index] if index else []
    if app.lower() == "crm":
        indexes.append(f"{BASE}/deluge/help/crm-integration-tasks-V8.html")
    for idx in indexes:
        try:
            for url, text in extract_links(fetcher.get(idx), idx):
                if url.startswith(f"{BASE}/deluge/help/") and url != idx:
                    candidates.setdefault(url, text)
        except DocAccessError:
            continue

    q = _tokens(query)
    hits = []
    for url, text in candidates.items():
        slug = url.split("/deluge/help/", 1)[-1].replace(".html", "")
        hay_text, hay_slug = (
            set(_tokens(text)),
            set(_tokens(slug.replace("-", " ").replace("/", " "))),
        )
        score = sum(2.0 for t in q if t in hay_text) + sum(1.5 for t in q if t in hay_slug)
        score += 0.5 * sum(
            1 for t in q for h in hay_slug if len(t) > 3 and (t in h or h in t) and t != h
        )
        if app.lower() in hay_slug:
            score += 0.25
        if score > 0:
            hits.append(DocHit(title=text or slug, url=url, score=round(score, 2)))
    hits.sort(key=lambda h: (-h.score, h.url))
    return hits[:limit]


def read_doc(url: str, fetcher: DocFetcher | None = None) -> dict:
    fetcher = fetcher or DocFetcher()
    title, text = html_to_text(fetcher.get(url))
    truncated = len(text) > MAX_TEXT
    return {"url": check_url(url), "title": title, "text": text[:MAX_TEXT], "truncated": truncated}
