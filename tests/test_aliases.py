from __future__ import annotations

import pytest

from gwnetwork.events.aliases import gps_to_utc, hhmmss_of

# Ground truth: published GPS times and UTC times for well-known events.
KNOWN = [
    (1126259462.4, "2015-09-14", "095045"),   # GW150914
    (1187008882.4, "2017-08-17", "124104"),   # GW170817
    (1242442967.4, "2019-05-21", "030229"),   # GW190521
    (1240215503.0, "2019-04-25", "081805"),   # GW190425
]


@pytest.mark.parametrize("gps,date,hhmmss", KNOWN)
def test_gps_to_utc_matches_published_times(gps, date, hhmmss):
    utc = gps_to_utc(gps)
    assert utc.strftime("%Y-%m-%d") == date
    assert hhmmss_of(gps) == hhmmss


def test_leap_seconds_change_across_the_gw_era():
    """17 leap seconds before 2017-01-01, 18 after. O1 and O3 differ."""
    o1 = gps_to_utc(1126259462.4)      # 2015, offset 17
    o3 = gps_to_utc(1242442967.4)      # 2019, offset 18
    assert o1.strftime("%H%M%S") == "095045"
    assert o3.strftime("%H%M%S") == "030229"


def test_suffix_mismatch_is_detectable():
    """GW190425_133124 is NOT an alias for GW190425 (GPS says 081805).

    Accepting it would merge two distinct events.
    """
    assert hhmmss_of(1240215503.0) == "081805"
    assert hhmmss_of(1240215503.0) != "133124"


def test_write_yaml_keeps_existing_aliases(tmp_path):
    """Aliased names leave the unknown queue; a rewrite must not drop them."""
    import yaml

    from gwnetwork.events.aliases import AliasReport, Proposal, write_yaml

    path = tmp_path / "aliases-auto.yaml"
    first = AliasReport(safe=[
        Proposal("GW150914_095045", "GW150914", 31, "long form; GPS matches"),
        Proposal("GW230529", "GW230529_181500", 12, "short form; one candidate"),
    ])
    write_yaml(first, path)

    # Next run: the old aliases are matched now, only a new one is proposed,
    # plus a refreshed count for an existing one.
    second = AliasReport(safe=[
        Proposal("GW190521_030229", "GW190521", 7, "long form; GPS matches"),
        Proposal("GW230529", "GW230529_181500", 20, "short form; one candidate"),
    ])
    assert write_yaml(second, path) == 3

    docs = {d["common_name"]: [x["designation"] for x in d["designations"]]
            for d in yaml.safe_load(path.read_text())}
    assert docs == {
        "GW150914": ["GW150914", "GW150914_095045"],
        "GW190521": ["GW190521", "GW190521_030229"],
        "GW230529_181500": ["GW230529_181500", "GW230529"],
    }
    text = path.read_text()
    assert "x31; long form" in text          # old note kept
    assert "x20; short form" in text and "x12" not in text   # refreshed
