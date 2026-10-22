"""Fetch paper full text from arXiv.

Every fetch is pinned to an explicit arXiv version (`/html/{id}v{N}`, then
`/pdf/{id}v{N}`), so the stored text -- and the char offsets into it -- can be
re-fetched exactly later. The native HTML rendering (LaTeXML) is preferred and
exists for most papers, old ones included; the PDF is the fallback. ar5iv is
no longer used: it cannot serve a given version, and its pages do not say which
version they rendered. Abstract-only is an accepted degraded mode -- extraction
still works, with lower recall.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
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


def is_abstract_page(url: str, text: str) -> bool:
    """ar5iv redirects papers it never converted to the arXiv abstract page.

    That page is long enough to pass the length checks, so 208 of the first
    291 "ar5iv" fetches were silently just the abstract -- including the
    GW170817 discovery paper. Fall through to the PDF instead.
    """
    if "arxiv.org/abs/" in url:
        return True
    head = text[:4000]
    return "Skip to main content" in head and "Submitted on" in head


# Bump when html_to_text's output changes for the same HTML: stored texts
# record the converter that produced them, so a re-fetch can be compared.
HTML_CONVERTER = "html_to_text/1"


# pypdf stops following form XObjects after 5,000 per page and silently drops
# the rest: the GWTC-4.0 cosmology paper lost 105 of its events that way.
PDF_XFORM_LIMIT = 100_000


def pdf_converter() -> str:
    """Everything that determines pypdf's text: its version, whether the
    optional fontTools is installed (it changes font decoding), and our
    form-XObject limit."""
    import pypdf
    try:
        import fontTools
        fonttools = fontTools.version
    except ImportError:
        fonttools = "none"
    return f"pypdf/{pypdf.__version__};fonttools={fonttools};xform-limit={PDF_XFORM_LIMIT}"


# Two designations run together ("...AGW190413AGW190413_052954GW190719...")
# means a macro-generated table was not expanded; GWTC-2's v3 HTML has 7,498
# of them and yields 6 of its 47 events. Figure labels in a PDF produce a few
# (21 in 2110.06815), hence the threshold.
_GLUED = re.compile(r"GW\d{6}(?:_\d{6})?[A-Za-z]{0,2}GW\d{6}")
_TEX_CONTROL = re.compile(r"\\(?:IfEqCase|ifx|ifthenelse|csname|expandafter|newcommand|def)\b")


def text_issues(text: str) -> list:
    """Signs that a rendering lost or garbled content, for review.

    Recorded on the paper, never acted on automatically: the fix is a reviewed
    entry in data/fulltext_overrides.yaml pinning a version that renders.
    """
    issues = []
    glued = len(_GLUED.findall(text))
    if glued >= 100:
        issues.append(f"glued-designations:{glued}")
    macros = len(_TEX_CONTROL.findall(text))
    if macros >= 20:
        issues.append(f"tex-macros:{macros}")
    return issues


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def strip_version(arxiv_id: str) -> str:
    """Drop a trailing vN. Old-style ids such as solv-int/9901001 contain a
    "v" of their own, so splitting on "v" mangles them."""
    return _VERSION.sub("", arxiv_id)


class VersionGone(Exception):
    """Both the HTML and the PDF of the version are 404.

    That is what a withdrawal looks like: arXiv has no withdrawn flag, the
    notice is a new version with no files, and its free-text comment is not
    reliable ("To be modified", "merged with 2004.01632"; and one live paper
    says it was "forced to withdraw, now re-submitted").
    """


@dataclass
class FetchResult:
    path: Path
    sha256: str
    source: str
    converter: str
    issues: list


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

    def _get(self, url: str) -> Optional[httpx.Response]:
        try:
            return self._client.get(url)
        except httpx.HTTPError:
            return None
        finally:
            time.sleep(self.rate_delay)

    def _pdf_text(self, r: Optional[httpx.Response]) -> Tuple[Optional[str], list]:
        """PDF fallback.

        Not a nicety: the large LVK collaboration papers (GWTC-2, GWTC-3) fail
        LaTeXML conversion outright, and those are the highest-value documents
        in the corpus, mentioning dozens of events each. Without this path we
        would systematically lose exactly the papers with the most edges.
        """
        try:
            import pypdf
        except ImportError:
            return None, []
        if r is None or r.status_code != 200 or not r.content.startswith(b"%PDF"):
            return None, []
        import io
        import logging
        # pypdf reports skipped content only as a log warning; catch it.
        caught = []
        handler = logging.Handler()
        handler.emit = lambda rec: caught.append(rec.getMessage())
        log = logging.getLogger("pypdf")
        log.addHandler(handler)
        try:
            with pypdf.apply_configuration(
                    xform_maximum_invocations_per_extraction=PDF_XFORM_LIMIT):
                reader = pypdf.PdfReader(io.BytesIO(r.content))
                pages = [(pg.extract_text() or "") for pg in reader.pages]
        except Exception:  # noqa: BLE001
            return None, []
        finally:
            log.removeHandler(handler)
        issues = ["pdf-truncated"] if any("further form content is skipped" in m
                                          for m in caught) else []
        # Join with blank lines so the section/sentence heuristics still see
        # paragraph boundaries.
        return _NL.sub("\n\n", _WS.sub(" ", "\n\n".join(pages))).strip(), issues

    def fetch(self, arxiv_id: str, version: int,
              sources: Tuple[str, ...] = ("arxiv_html", "pdf")) -> Optional[FetchResult]:
        """Fetch exactly `version` of the paper and store its text.

        Always downloads: a cached file of unknown origin is how provenance
        got lost before (texts recorded only as "cached"). `sources` lets an
        override force one format. Raises VersionGone if neither format
        exists; returns None for any other failure.
        """
        pinned = f"{strip_version(arxiv_id)}v{version}"
        path = self._path_for(arxiv_id)

        html = pdf = None
        if "arxiv_html" in sources:
            html = self._get(f"https://arxiv.org/html/{pinned}")
            if html is not None and html.status_code == 200 and len(html.text) >= 2000:
                text = html_to_text(html.text)
                if len(text) >= 1000 and not is_abstract_page(str(html.url), text):
                    return self._store(path, text, "arxiv_html", HTML_CONVERTER, [])

        if "pdf" in sources:
            pdf = self._get(f"https://arxiv.org/pdf/{pinned}")
            text, issues = self._pdf_text(pdf)
            if text and len(text) >= 1000:
                return self._store(path, text, "pdf", pdf_converter(), issues)
        if (html is not None and html.status_code == 404
                and pdf is not None and pdf.status_code == 404):
            raise VersionGone(pinned)
        return None

    @staticmethod
    def _store(path: Path, text: str, source: str, converter: str,
               issues: list) -> FetchResult:
        path.write_text(text)
        return FetchResult(path, sha256_text(text), source, converter,
                           issues + text_issues(text))


# --- metadata ---------------------------------------------------------------

_ATOM = {
    "id": re.compile(r"<id>https?://arxiv\.org/abs/([^<]+)</id>"),
    "title": re.compile(r"<title>(.*?)</title>", re.S),
    "summary": re.compile(r"<summary>(.*?)</summary>", re.S),
    "published": re.compile(r"<published>([^<]+)</published>"),
    "updated": re.compile(r"<updated>([^<]+)</updated>"),
    "comment": re.compile(r"<arxiv:comment[^>]*>(.*?)</arxiv:comment>", re.S),
}
_VERSION = re.compile(r"v(\d+)$")
_AUTHOR = re.compile(r"<author>\s*<name>([^<]+)</name>", re.S)
_CATEGORY = re.compile(r'<category[^>]*term="([^"]+)"')


def fetch_metadata(arxiv_ids: list) -> list:
    """Metadata for explicit arXiv ids via the public API.

    Used to seed a gold/eval set, to run the pipeline end to end without an
    ADS token, and for version dates: `published` is the first version,
    `updated` and `version` the latest -- which is the text we fetch.
    """
    with httpx.Client(timeout=60.0, headers={"User-Agent": CONFIG.user_agent},
                      follow_redirects=True) as c:
        r = c.get("https://export.arxiv.org/api/query",
                  params={"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)})
        r.raise_for_status()
    return [_parse_entry(e) for e in r.text.split("<entry>")[1:]]


def version_dates(arxiv_id: str) -> dict:
    """{version: submission datetime (UTC)} for every version, from OAI-PMH.

    The query API gives only the first and latest dates; an override pinning
    an older version needs that version's own date.
    """
    from email.utils import parsedate_to_datetime
    with httpx.Client(timeout=60.0, headers={"User-Agent": CONFIG.user_agent},
                      follow_redirects=True) as c:
        r = c.get("https://oaipmh.arxiv.org/oai",
                  params={"verb": "GetRecord", "metadataPrefix": "arXivRaw",
                          "identifier": f"oai:arXiv.org:{strip_version(arxiv_id)}"})
        r.raise_for_status()
    found = re.findall(r'<version version="v(\d+)">\s*<date>([^<]+)</date>', r.text)
    return {int(v): parsedate_to_datetime(d).replace(tzinfo=None) for v, d in found}


def _parse_entry(entry: str) -> dict:
    import html as _html

    def one(key):
        m = _ATOM[key].search(entry)
        return _html.unescape(" ".join(m.group(1).split())) if m else ""
    raw_id = one("id")
    v = _VERSION.search(raw_id)
    # Strip only a trailing vN: old-style ids such as solv-int/9901001 contain
    # a "v" of their own, so splitting on "v" mangled them.
    return {
        "arxiv_id": _VERSION.sub("", raw_id),
        "version": int(v.group(1)) if v else None,
        "title": one("title"),
        "abstract": one("summary"),
        "published": one("published"),
        "updated": one("updated"),
        "comment": one("comment"),
        "authors": [_html.unescape(a) for a in _AUTHOR.findall(entry)],
        "categories": _CATEGORY.findall(entry),
    }
