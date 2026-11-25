from __future__ import annotations

import xml.etree.ElementTree as ET

from gwnetwork.arxiv_volume import (Quarter, cat_term, derive_counts, parse_entries,
                                    quarters, union_term)


def test_quarters_and_date_clauses_tile_without_gaps():
    qs = quarters("2015Q4", "2016Q2")
    assert [str(q) for q in qs] == ["2015Q4", "2016Q1", "2016Q2"]
    assert qs[0].date_clause() == "submittedDate:[201510010000 TO 201512312359]"
    assert qs[1].date_clause() == "submittedDate:[201601010000 TO 201603312359]"
    assert qs[0].month_clauses() == [
        "submittedDate:[201510010000 TO 201510312359]",
        "submittedDate:[201511010000 TO 201511302359]",
        "submittedDate:[201512010000 TO 201512312359]",
    ]


def test_subdivided_archives_need_a_wildcard():
    """`cat:astro-ph` matches no papers on the live API; `cat:astro-ph*` does."""
    assert cat_term("astro-ph") == "cat:astro-ph*"
    assert cat_term("astro-ph.HE") == "cat:astro-ph.HE"
    assert cat_term("gr-qc") == "cat:gr-qc"
    assert union_term(["astro-ph", "gr-qc"]) == "(cat:astro-ph* OR cat:gr-qc)"


ATOM = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2501.01234v3</id><published>2025-01-02T10:00:00Z</published>
 <arxiv:primary_category term="gr-qc"/>
 <category term="gr-qc"/><category term="astro-ph.HE"/></entry>
<entry><id>http://arxiv.org/abs/hep-th/9901001v1</id><published>1999-01-01T00:00:00Z</published>
 <arxiv:primary_category term="hep-th"/><category term="hep-th"/></entry>
</feed>"""


def test_parse_entries_strips_version_and_keeps_categories():
    rows = parse_entries(ET.fromstring(ATOM))
    assert rows[0] == {"arxiv_id": "2501.01234", "submitted": "2025-01-02T10:00:00Z",
                       "primary": "gr-qc", "categories": "gr-qc astro-ph.HE"}
    assert rows[1]["arxiv_id"] == "hep-th/9901001"


def test_derive_counts_any_vs_primary_and_untracked_groups():
    rows = [
        {"quarter": "2025Q1", "primary": "gr-qc", "categories": "gr-qc astro-ph.HE astro-ph.CO"},
        {"quarter": "2025Q1", "primary": "astro-ph.HE", "categories": "astro-ph.HE"},
        # From outside the tracked archives, cross-listed in: counts towards
        # gr-qc (any) and the union, never towards hep-ex or a primary count.
        {"quarter": "2025Q1", "primary": "hep-ex", "categories": "hep-ex gr-qc"},
    ]
    c = {(r["group"], r["basis"]): r["n"] for r in derive_counts(rows)}
    assert c[("gr-qc", "any")] == 2
    assert c[("gr-qc", "primary")] == 1
    assert c[("astro-ph", "any")] == 2          # one paper per archive, not per subcategory
    assert c[("astro-ph.HE", "any")] == 2
    assert c[("astro-ph.HE", "primary")] == 1
    assert c[("astro-ph.CO", "any")] == 1
    assert ("astro-ph.CO", "primary") not in c
    assert c[("union", "any")] == 3
    assert c[("union", "primary")] == 2
    assert not any(g.startswith("hep-ex") for g, _ in c)
