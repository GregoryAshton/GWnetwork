from gwnetwork.citations import find_citations

NUMBERED = (
    "Intro. The first detection [3] was followed by many [2, 84] and more [80–86].\n"
    "Unrelated [8] and [184].\n\nReferences\n\n"
    "[83]\nSomeone, Something.\n\n"
    "[84]\nLIGO and Virgo, “GW150914: Implications for the stochastic background”.\n")


def test_numbered_reference_maps_to_every_in_text_citation():
    c = find_citations(NUMBERED, NUMBERED.index("GW150914"))
    assert c.key == "[84]"
    cited = [NUMBERED[a:b] for a, b in c.spans]
    assert cited == ["[2, 84]", "[80–86]"]


def test_author_year_reference():
    text = ("As shown by Abbott et al. (2016a), and later (Abbott et al. 2016b; Smith 2019).\n"
            "References\n"
            "Abbott et al. (2016a)\nB. P. Abbott et al., Observation of GW150914.\n")
    c = find_citations(text, text.index("GW150914"))
    assert c.key == "Abbott 2016a"
    assert [text[a:b] for a, b in c.spans] == ["Abbott et al. (2016a"]


def test_body_text_without_entry_label_returns_none():
    text = "Title: Properties of GW170729\n\nWe study GW170729 in detail.\n"
    assert find_citations(text, text.index("We study")) is None


def test_ar5iv_redirect_to_abstract_page_is_not_full_text():
    from gwnetwork.corpus.arxiv import is_abstract_page
    assert is_abstract_page("https://arxiv.org/abs/1710.05832", "x" * 2000)
    page = "[1710.05832] GW170817\n Skip to main content\n Search\n [Submitted on 16 Oct 2017]\n"
    assert is_abstract_page("https://ar5iv.labs.arxiv.org/html/1710.05832", page + "x" * 2000)
    assert not is_abstract_page("https://ar5iv.labs.arxiv.org/html/1602.03837",
                                "Abstract\n\nOn September 14, 2015 ..." + "x" * 2000)


def test_arxiv_entry_parsing_keeps_old_style_ids_and_reads_versions():
    from gwnetwork.corpus.arxiv import _parse_entry
    e = ("<id>http://arxiv.org/abs/solv-int/9901001v3</id><updated>2024-05-01T10:00:00Z</updated>"
         "<published>1999-01-05T00:00:00Z</published><title>T</title>")
    m = _parse_entry(e)
    assert m["arxiv_id"] == "solv-int/9901001"            # used to become "sol"
    assert m["version"] == 3
    assert (m["published"][:4], m["updated"][:4]) == ("1999", "2024")


def test_papers_are_dated_by_their_latest_version(temp_db):
    from datetime import datetime
    from sqlalchemy import select
    from gwnetwork.models import Paper, paper_date
    with temp_db.session() as s:
        s.add_all([Paper(arxiv_id="astro-ph/0211481", title="Palle", abstract="",
                         submitted_at=datetime(2002, 11, 1), updated_at_src=datetime(2024, 3, 1)),
                   Paper(arxiv_id="1", title="never revised", abstract="",
                         submitted_at=datetime(2019, 1, 1))])
    with temp_db.session() as s:
        got = dict(s.execute(select(Paper.title, paper_date())).all())
    assert got["Palle"].year == 2024 and got["never revised"].year == 2019
