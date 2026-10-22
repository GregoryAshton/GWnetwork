"""Pinned full-text fetches and version provenance."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from gwnetwork.models import Paper


def test_strip_version_keeps_old_style_ids():
    from gwnetwork.corpus.arxiv import strip_version
    assert strip_version("1602.03837v2") == "1602.03837"
    assert strip_version("solv-int/9901001v3") == "solv-int/9901001"
    assert strip_version("solv-int/9901001") == "solv-int/9901001"


def test_fetch_requests_the_pinned_version(tmp_path):
    from gwnetwork.corpus.arxiv import HTML_CONVERTER, ArxivFetcher
    body = "<p>" + "GW150914 was observed. " * 200 + "</p>"
    urls = []
    with ArxivFetcher(fulltext_dir=tmp_path, rate_delay=0) as f:
        def fake_get(url):
            urls.append(url)
            return SimpleNamespace(status_code=200, text=body, url=url)
        f._get = fake_get
        got = f.fetch("1710.09458v1", 2)
    assert urls == ["https://arxiv.org/html/1710.09458v2"]
    assert got.source == "arxiv_html" and got.converter == HTML_CONVERTER
    assert got.path.read_text().startswith("GW150914")


def test_fetch_falls_back_to_the_pinned_pdf(tmp_path):
    from gwnetwork.corpus.arxiv import ArxivFetcher
    urls = []
    with ArxivFetcher(fulltext_dir=tmp_path, rate_delay=0) as f:
        def fake_get(url):
            urls.append(url)
            return SimpleNamespace(status_code=503, text="", url=url, content=b"")
        f._get = fake_get
        assert f.fetch("2111.03634", 5) is None
    assert urls == ["https://arxiv.org/html/2111.03634v5",
                    "https://arxiv.org/pdf/2111.03634v5"]


def _paper_with_file(s, tmp_path, arxiv_id, source, updated, fetched, text="text"):
    from gwnetwork.corpus.arxiv import sha256_text
    from gwnetwork.models import Paper
    path = tmp_path / f"{arxiv_id}.txt"
    path.write_bytes(text.encode())
    ts = fetched.replace(tzinfo=timezone.utc).timestamp()
    os.utime(path, (ts, ts))
    s.add(Paper(arxiv_id=arxiv_id, title=arxiv_id, abstract="", version=3,
                updated_at_src=updated, fulltext_path=str(path),
                fulltext_sha256=sha256_text(text), fulltext_source=source))


def test_pin_existing_only_pins_provable_versions(temp_db, tmp_path):
    from gwnetwork.corpus.discover import pin_existing
    from gwnetwork.models import Paper
    fetched = datetime(2026, 10, 14, 12)
    with temp_db.session() as s:
        _paper_with_file(s, tmp_path, "old-html", "arxiv_html", datetime(2025, 1, 1), fetched)
        _paper_with_file(s, tmp_path, "old-pdf", "pdf", datetime(2025, 1, 1), fetched)
        _paper_with_file(s, tmp_path, "revised", "arxiv_html", datetime(2026, 10, 15), fetched)
        _paper_with_file(s, tmp_path, "ar5iv", "ar5iv", datetime(2025, 1, 1), fetched)
        _paper_with_file(s, tmp_path, "cached", "cached", datetime(2025, 1, 1), fetched)
        _paper_with_file(s, tmp_path, "pdf-cr", "pdf", datetime(2025, 1, 1), fetched,
                         text="page one\rpage two")
    (tmp_path / "old-pdf.txt").write_text("edited since")      # hash no longer matches
    os.utime(tmp_path / "old-pdf.txt",
             (fetched.replace(tzinfo=timezone.utc).timestamp(),) * 2)

    stats = pin_existing()
    assert stats["pinned"] == 2
    assert stats["revised_since_fetch"] == 1
    assert stats["unpinnable_source"] == 2
    assert stats["hash_mismatch"] == 1
    with temp_db.session() as s:
        got = {p.arxiv_id: p for p in s.scalars(select(Paper))}
    assert got["old-html"].fulltext_version == 3
    assert got["old-html"].fulltext_fetched_at == fetched
    assert got["old-html"].fulltext_converter == "html_to_text/1"
    assert got["revised"].fulltext_version is None


def test_paper_date_prefers_the_fetched_version(temp_db):
    from gwnetwork.models import Paper, paper_date
    with temp_db.session() as s:
        s.add(Paper(arxiv_id="1", title="revised after fetch", abstract="",
                    submitted_at=datetime(2019, 1, 1), updated_at_src=datetime(2026, 1, 1),
                    fulltext_version_date=datetime(2020, 6, 1)))
    with temp_db.session() as s:
        (_, date), = s.execute(select(Paper.title, paper_date())).all()
    assert date.year == 2020


def test_both_formats_missing_means_withdrawn(tmp_path):
    import pytest
    from gwnetwork.corpus.arxiv import ArxivFetcher, VersionGone
    with ArxivFetcher(fulltext_dir=tmp_path, rate_delay=0) as f:
        f._get = lambda url: SimpleNamespace(status_code=404, text="", url=url, content=b"")
        with pytest.raises(VersionGone):
            f.fetch("2410.11858", 2)
        # A server error is not evidence of withdrawal.
        f._get = lambda url: SimpleNamespace(status_code=503, text="", url=url, content=b"")
        assert f.fetch("2410.11858", 2) is None


def test_extract_skips_withdrawn_papers(temp_db):
    from gwnetwork.extract.runner import extract_all
    from gwnetwork.models import Paper
    with temp_db.session() as s:
        s.add_all([Paper(arxiv_id="1", title="kept", abstract="About GW150914."),
                   Paper(arxiv_id="2", title="gone", abstract="About GW150914.",
                         withdrawn=True)])
    stats = extract_all()
    assert (stats.papers, stats.withdrawn) == (1, 1)


def test_text_issues_flag_glued_designations_and_raw_tex():
    from gwnetwork.corpus.arxiv import text_issues
    assert text_issues("GW150914 and GW170817 are separate. " * 50) == []
    # A few glued figure labels are normal in PDF text.
    assert text_issues("GW150914GW151012GW151226 " * 10) == []
    broken = "\\IfEqCase{x}AGW190413AGW190413_052954GW190719 " * 100
    assert text_issues(broken) == ["glued-designations:100", "tex-macros:100"]


def test_fetch_honours_a_restricted_source(tmp_path):
    from gwnetwork.corpus.arxiv import ArxivFetcher
    urls = []
    with ArxivFetcher(fulltext_dir=tmp_path, rate_delay=0) as f:
        def fake_get(url):
            urls.append(url)
            return SimpleNamespace(status_code=503, text="", url=url, content=b"")
        f._get = fake_get
        f.fetch("2010.14527", 3, ("pdf",))
    assert urls == ["https://arxiv.org/pdf/2010.14527v3"]


def test_overrides_need_a_version_and_a_reason(tmp_path):
    import pytest
    from gwnetwork.corpus.discover import load_overrides
    good = tmp_path / "ok.yaml"
    good.write_text("2010.14527:\n  version: 3\n  source: pdf\n  reason: macros\n")
    assert load_overrides(good) == {"2010.14527": {"version": 3, "source": "pdf",
                                                  "reason": "macros"}}
    bad = tmp_path / "bad.yaml"
    bad.write_text("2010.14527:\n  version: 3\n")
    with pytest.raises(ValueError, match="reason"):
        load_overrides(bad)
    assert load_overrides(tmp_path / "missing.yaml") == {}


def test_repo_overrides_file_is_valid():
    from gwnetwork.corpus.discover import load_overrides
    from gwnetwork.config import ROOT
    assert "2010.14527" in load_overrides(ROOT / "data" / "fulltext_overrides.yaml")


def test_unapplied_override_is_refetched_at_its_version(temp_db, tmp_path, monkeypatch):
    from gwnetwork.corpus import discover
    from gwnetwork.corpus.arxiv import FetchResult
    from gwnetwork.models import Paper
    monkeypatch.setattr(discover, "load_overrides", lambda: {
        "2010.14527": {"version": 2, "source": "pdf", "reason": "test"}})
    monkeypatch.setattr(discover, "update_versions", lambda **kw: None)
    monkeypatch.setattr(discover, "version_dates", lambda a: {2: datetime(2021, 2, 26)})
    calls = []

    class FakeFetcher:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def fetch(self, arxiv_id, version, sources):
            calls.append((arxiv_id, version, sources))
            return FetchResult(tmp_path / "t.txt", "h", "pdf", "pypdf/x", [])
    monkeypatch.setattr(discover, "ArxivFetcher", FakeFetcher)
    with temp_db.session() as s:
        s.add_all([
            Paper(arxiv_id="2010.14527", title="GWTC-2", abstract="", version=3,
                  updated_at_src=datetime(2021, 3, 8), fulltext_path="x",
                  fulltext_version=3, fulltext_source="arxiv_html"),
            Paper(arxiv_id="1", title="fine", abstract="", version=1,
                  fulltext_path="y", fulltext_version=1, fulltext_source="arxiv_html")])
    discover.fetch_fulltext(rate_delay=0)
    assert calls == [("2010.14527", 2, ("pdf",))]
    with temp_db.session() as s:
        p = s.scalar(select(Paper).where(Paper.arxiv_id == "2010.14527"))
    assert (p.fulltext_version, p.fulltext_version_date) == (2, datetime(2021, 2, 26))


def test_unpinned_also_selects_outdated_converters_but_not_withdrawn(temp_db, monkeypatch):
    from gwnetwork.corpus import discover
    from gwnetwork.corpus.arxiv import HTML_CONVERTER, pdf_converter
    from gwnetwork.models import Paper
    monkeypatch.setattr(discover, "load_overrides", lambda: {})
    seen = []
    monkeypatch.setattr(discover, "update_versions",
                        lambda **kw: seen.extend(kw["arxiv_ids"]))

    class NoFetch:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def fetch(self, *a): return None
    monkeypatch.setattr(discover, "ArxivFetcher", NoFetch)
    common = dict(title="t", abstract="", version=1, fulltext_path="p", fulltext_version=1)
    with temp_db.session() as s:
        s.add_all([
            Paper(arxiv_id="old-pdf", fulltext_source="pdf",
                  fulltext_converter="pypdf/6.19.0", **common),
            Paper(arxiv_id="new-pdf", fulltext_source="pdf",
                  fulltext_converter=pdf_converter(), **common),
            Paper(arxiv_id="html", fulltext_source="arxiv_html",
                  fulltext_converter=HTML_CONVERTER, **common),
            Paper(arxiv_id="gone", title="t", abstract="", version=2,
                  fulltext_source="abstract_only", withdrawn=True)])
    discover.fetch_fulltext(unpinned=True, rate_delay=0)
    assert seen == ["old-pdf"]


def test_mention_override_reassigns_the_event(temp_db, tmp_path):
    from gwnetwork.extract.runner import extract_all
    from gwnetwork.models import Event, EventDesignation, Mention
    (tmp_path / "mention_overrides.yaml").write_text(
        "1903.01998:\n  - from: GW190521\n    to: GW190521_074359\n    reason: masses\n")
    with temp_db.session() as s:
        for name in ("GW190521", "GW190521_074359"):
            e = Event(canonical_name=name)
            s.add(e)
            s.flush()
            s.add(EventDesignation(event_id=e.id, designation=name))
        from gwnetwork.models import Paper
        s.add_all([Paper(arxiv_id="1903.01998", title="t", abstract="We study GW190521."),
                   Paper(arxiv_id="other", title="t", abstract="We study GW190521.")])
    stats = extract_all()
    assert stats.reassigned == 1
    with temp_db.session() as s:
        got = dict(s.execute(select(Paper.arxiv_id, Event.canonical_name)
                             .join(Mention, Mention.paper_id == Paper.id)
                             .join(Event, Event.id == Mention.event_id)).all())
    assert got == {"1903.01998": "GW190521_074359", "other": "GW190521"}
