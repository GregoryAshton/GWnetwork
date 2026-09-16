"""Fetch paper full text from arXiv.

Prefers the native HTML rendering (LaTeXML, available for most papers from
Dec 2023) and falls back to ar5iv for older ones. Abstract-only is an accepted
degraded mode -- extraction still works, with lower recall.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx

from ..config import CONFIG

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    html = _SCRIPT.sub(" ", html)
    html = re.sub(r"</(p|div|h[1-6]|li|section|tr)>", "\n\n", html, flags=re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = _TAG.sub("", html)
    for a, b in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&#x27;", "'"), ("&quot;", '"'), ("&#38;", "&")]:
        text = text.replace(a, b)
    text = _WS.sub(" ", text)
    return _NL.sub("\n\n", text).strip()


class ArxivFetcher:
    def __init__(self, fulltext_dir: Optional[Path] = None, rate_delay: float = 3.0):
        self.dir = Path(fulltext_dir or CONFIG.fulltext_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rate_delay = rate_delay
        self._client = httpx.Client(
            timeout=60.0, follow_redirects=True,
            headers={"User-Agent": CONFIG.user_agent},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _path_for(self, arxiv_id: str) -> Path:
        safe = arxiv_id.replace("/", "_")
        sub = self.dir / safe[:4]
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"{safe}.txt"

    def _pdf_text(self, base: str) -> Optional[str]:
        """PDF fallback.

        Not a nicety: the large LVK collaboration papers (GWTC-2, GWTC-3) fail
        LaTeXML conversion outright -- ar5iv returns "No content available" --
        and those are the highest-value documents in the corpus, mentioning
        dozens of events each. Without this path we would systematically lose
        exactly the papers with the most edges.
        """
        try:
            import pypdf
        except ImportError:
            return None
        try:
            r = self._client.get(f"https://arxiv.org/pdf/{base}")
        except httpx.HTTPError:
            return None
        finally:
            time.sleep(self.rate_delay)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            return None
        import io
        try:
            reader = pypdf.PdfReader(io.BytesIO(r.content))
            pages = [(pg.extract_text() or "") for pg in reader.pages]
        except Exception:  # noqa: BLE001
            return None
        # Join with blank lines so the section/sentence heuristics still see
        # paragraph boundaries.
        return _NL.sub("\n\n", _WS.sub(" ", "\n\n".join(pages))).strip()

    def fetch(self, arxiv_id: str, force: bool = False) -> Optional[Tuple[Path, str, str]]:
        """Return (path, sha256, source) or None. Cached on disk by arXiv id."""
        path = self._path_for(arxiv_id)
        if path.exists() and not force:
            body = path.read_text(errors="replace")
            return path, hashlib.sha256(body.encode()).hexdigest(), "cached"

        base = arxiv_id.split("v")[0] if re.search(r"v\d+$", arxiv_id) else arxiv_id
        for source, url in (("arxiv_html", f"https://arxiv.org/html/{base}"),
                            ("ar5iv", f"https://ar5iv.labs.arxiv.org/html/{base}")):
            try:
                r = self._client.get(url)
            except httpx.HTTPError:
                continue
            finally:
                time.sleep(self.rate_delay)
            if r.status_code != 200 or len(r.text) < 2000:
                continue
            text = html_to_text(r.text)
            if len(text) < 1000:
                continue
            path.write_text(text)
            return path, hashlib.sha256(text.encode()).hexdigest(), source

        text = self._pdf_text(base)
        if text and len(text) >= 1000:
            path.write_text(text)
            return path, hashlib.sha256(text.encode()).hexdigest(), "pdf"
        return None


# --- metadata ---------------------------------------------------------------

_ATOM = {
    "id": re.compile(r"<id>https?://arxiv\.org/abs/([^<]+)</id>"),
    "title": re.compile(r"<title>(.*?)</title>", re.S),
    "summary": re.compile(r"<summary>(.*?)</summary>", re.S),
    "published": re.compile(r"<published>([^<]+)</published>"),
}
_AUTHOR = re.compile(r"<author>\s*<name>([^<]+)</name>", re.S)
_CATEGORY = re.compile(r'<category[^>]*term="([^"]+)"')


def fetch_metadata(arxiv_ids: list) -> list:
    """Metadata for explicit arXiv ids via the public API.

    Used to seed a gold/eval set, and to run the pipeline end to end without an
    ADS token.
    """
    import html as _html

    out = []
    with httpx.Client(timeout=60.0, headers={"User-Agent": CONFIG.user_agent},
                      follow_redirects=True) as c:
        r = c.get("https://export.arxiv.org/api/query",
                  params={"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)})
        r.raise_for_status()
    for entry in r.text.split("<entry>")[1:]:
        def one(key):
            m = _ATOM[key].search(entry)
            return _html.unescape(" ".join(m.group(1).split())) if m else ""
        raw_id = one("id")
        out.append({
            "arxiv_id": raw_id.split("v")[0] if re.search(r"v\d+$", raw_id) else raw_id,
            "title": one("title"),
            "abstract": one("summary"),
            "published": one("published"),
            "authors": [_html.unescape(a) for a in _AUTHOR.findall(entry)],
            "categories": _CATEGORY.findall(entry),
        })
    return out
