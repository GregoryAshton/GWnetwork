from __future__ import annotations

from gwnetwork.extract.matcher import DesignationMatcher
from gwnetwork.extract.sections import SectionIndex, sentence_around

M = {
    "GW150914": "e-150914",
    "GW190521": "e-190521",
    "GW190521_074359": "e-190521b",
    "GW190814": "e-190814",
    "GW190814_192009": "e-190814b",
    "GW170817": "e-170817",
    "AT2017gfo": "e-170817",
}


def _find(text, mapping=M, ambiguous=None):
    return list(DesignationMatcher(mapping, ambiguous).find(text))


def test_longest_match_wins():
    """The trap: GW190521 and GW190521_074359 are different events."""
    hits = _find("We analyse GW190521_074359 in detail.")
    assert len(hits) == 1
    assert hits[0].text == "GW190521_074359"
    assert hits[0].event_id == "e-190521b"


def test_short_form_still_matches_its_own_event():
    hits = _find("The heaviest binary, GW190521, is notable.")
    assert [h.event_id for h in hits] == ["e-190521"]


def test_both_forms_in_one_sentence():
    hits = _find("We compare GW190814 with GW190814_192009.")
    assert [h.text for h in hits] == ["GW190814", "GW190814_192009"]
    assert hits[0].event_id != hits[1].event_id


def test_word_boundaries():
    assert _find("xGW150914") == []
    assert len(_find("GW150914-like signals")) == 1
    assert len(_find("(GW150914)")) == 1


def test_em_counterpart_alias_resolves_to_same_event():
    hits = _find("The kilonova AT2017gfo followed GW170817.")
    assert {h.event_id for h in hits} == {"e-170817"}


def test_unknown_event_is_surfaced_not_dropped():
    """The discovery channel for events we have never heard of."""
    hits = _find("A candidate GW251231_120000 was reported.")
    assert len(hits) == 1
    assert hits[0].event_id is None
    assert hits[0].text == "GW251231_120000"


def test_unknown_not_double_reported_when_known():
    hits = _find("GW150914 was the first.")
    assert len(hits) == 1


def test_ambiguous_flag_propagates():
    hits = _find("See GW150914.", ambiguous={"GW150914"})
    assert hits[0].is_ambiguous


def test_section_index_and_sentence():
    text = (
        "Title\n\nAbstract\n\nWe report GW150914.\n\n"
        "1. Introduction\n\nMany papers cite GW150914 here.\n\n"
        "2. Methods\n\nWe reanalyse the strain data for GW170817. It follows.\n"
    )
    idx = SectionIndex(text)
    a = text.index("GW150914")
    b = text.index("GW150914", a + 1)
    c = text.index("GW170817")
    assert idx.label_at(a) == "abstract"
    assert idx.label_at(b) == "introduction"
    assert idx.label_at(c) == "methods"
    assert sentence_around(text, c, c + 8) == "We reanalyse the strain data for GW170817."


def test_pdf_mangled_underscore_still_matches():
    """PDF extraction turns GW190521_074359 into 'GW190521 074359'."""
    for sep in [" ", "\n", " ", "-"]:
        hits = _find(f"Candidate GW190521{sep}074359 was analysed.")
        assert len(hits) == 1, sep
        assert hits[0].event_id == "e-190521b", sep


def test_mangled_form_does_not_shadow_the_short_event():
    hits = _find("We compare GW190521 with GW190814 192009 here.")
    assert [h.event_id for h in hits] == ["e-190521", "e-190814b"]


def test_normalise_canonicalises_unknowns():
    from gwnetwork.extract.matcher import normalise
    assert normalise("GW250101 120000") == "GW250101_120000"
    assert normalise("GW250101\n120000") == "GW250101_120000"
    assert normalise("GW250101_120000") == "GW250101_120000"


def test_marginal_candidates_have_no_gw_prefix():
    """GWOSC names 23 marginal candidates without a GW prefix, e.g. 200214_224526."""
    m = {"200214_224526": "e-marginal"}
    for sep in ["_", " ", "\n"]:
        hits = _find(f"The marginal candidate 200214{sep}224526 was rejected.", m)
        assert [h.event_id for h in hits] == ["e-marginal"], sep


def test_normalise_without_gw_prefix():
    from gwnetwork.extract.matcher import normalise
    assert normalise("200214 224526") == "200214_224526"


def test_word_designations_do_not_get_separator_tolerance():
    """GWOSC has an event named `blind_injection`.

    Blanket underscore tolerance made it match the ordinary English phrase
    "blind injection", which appears throughout the GW literature.
    """
    m = {"blind_injection": "e-bd"}
    assert _find("We also demonstrate, through blind injection analyses, that", m) == []
    assert len(_find("The blind_injection challenge was passed.", m)) == 1


def test_digit_separator_tolerance_still_works():
    m = {"GW190521_074359": "e-x"}
    assert len(_find("GW190521 074359", m)) == 1


def test_arxiv_table_of_contents_is_not_read_as_sections():
    """The TOC ends in "References"; it used to label the title and abstract."""
    text = ("Title of paper\n\nIntroduction\n\nII Methods\n\nReferences\n\n"
            " License: arXiv.org perpetual non-exclusive license\n"
            "arXiv:1903.06742v3 [gr-qc] 08 Nov 2019\n\n"
            "On the properties of GW170729\n\nAbstract\n\nWe study GW170729.\n\n"
            "Chapter 1 Introduction\n\nGW170729 is heavy.\n\nReferences\n\n[1] GW170729 paper.\n")
    ix = SectionIndex(text)
    assert ix.label_at(text.index("On the properties")) == "front_matter"
    assert ix.label_at(text.index("We study")) == "abstract"
    assert ix.label_at(text.index("is heavy")) == "introduction"
    assert ix.label_at(text.index("[1] GW170729")) == "references"


BARE = {"170405": "m-170405", "161202": "m-161202", "161217": "m-161217",
        "170616": "m-170616", "200214_224526": "m-200214"}


def _bare_ids(text):
    return [h.event_id for h in _find(text, BARE) if h.event_id]


def test_bare_candidate_in_a_trigger_list_matches():
    text = ("GBM data were not collected around triggers 161202, 161217, "
            "170405, and GW170823 due to passage through the SAA.")
    assert _bare_ids(text) == ["m-161202", "m-161217", "m-170405"]


def test_bare_candidate_in_a_pipeline_table_matches():
    assert _bare_ids("No artifacts 170616 19:47:20.8 PyCBC 1.94 9.1") == ["m-170616"]


def test_bare_number_without_trigger_context_does_not_match():
    """Before this, 170405 'appeared' in 46 papers back to 1999."""
    assert _bare_ids("C. Simon, V. Buzek and N. Gisin, Phys. Rev. Lett. 87, "
                     "170405 (2001).") == []
    # The DOI repeats the same number; that is not a second candidate.
    assert _bare_ids("Phys. Rev. Lett. 111, 170405 (2013). "
                     "doi:10.1103/PhysRevLett.111.170405") == []


def test_bare_number_glued_to_a_coordinate_does_not_match():
    # Trigger vocabulary nearby does not rescue a source name or a decimal.
    assert _bare_ids("the X-ray trigger source 3XMM J181923.7-170616 is a pulsar") == []
    assert _bare_ids("the X-ray trigger source 3XMM J181923.7−-170616") == []
    assert _bare_ids("trigger mass 170405.688 MeV") == []


def test_a_table_dash_is_not_a_coordinate():
    text = "161202 2016-12-02 03:53:44.9 0.034 -  161217 2016-12-17 07:16:24.4"
    assert _bare_ids(text) == ["m-161202", "m-161217"]


def test_long_form_marginal_candidates_need_no_context():
    assert _bare_ids("We also analyse 200214_224526.") == ["m-200214"]
