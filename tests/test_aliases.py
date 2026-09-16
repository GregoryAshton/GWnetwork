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
