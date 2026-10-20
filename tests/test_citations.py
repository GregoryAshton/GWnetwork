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
