from __future__ import annotations

from sqlalchemy import select

from gwnetwork.events.base import DesignationIn, EventRecordIn
from gwnetwork.events.ingest import ingest
from gwnetwork.events.view import classify_masses, observing_run, rebuild
from gwnetwork.models import Event, EventView


def rec(name, catalog, version, params, gps=1126259462.4):
    return EventRecordIn(common_name=name, source="gwosc", catalog=catalog,
                         version=version, gps=gps, parameters=params,
                         external_id=f"{name}-{catalog}-v{version}",
                         designations=[DesignationIn(name)])


def test_higher_ranked_catalogue_wins(temp_db):
    ingest([
        rec("GW150914", "GWTC-1-confident", 1, {"mass_1_source": 36.0}),
        rec("GW150914", "GWTC-2.1-confident", 4, {"mass_1_source": 34.6}),
    ])
    rebuild()
    with temp_db.session() as s:
        v = s.scalar(select(EventView))
    assert v.mass_1_source == 34.6
    assert v.params_from_catalog == "GWTC-2.1-confident"


def test_fields_fall_back_to_a_lower_ranked_catalogue(temp_db):
    """The newest catalogue entry is not always the most complete.

    An O4 discovery listing may carry SNR but no masses while an older record
    has full parameter estimation. Taking every field from the top-ranked
    record would throw away real measurements.
    """
    ingest([
        rec("GW999999", "GWTC-5.0", 1, {"network_matched_filter_snr": 12.0}),
        rec("GW999999", "GWTC-3-confident", 2,
            {"mass_1_source": 20.0, "mass_2_source": 15.0}),
    ])
    stats = rebuild()
    with temp_db.session() as s:
        v = s.scalar(select(EventView))
    assert v.network_matched_filter_snr == 12.0      # from the top-ranked record
    assert v.mass_1_source == 20.0                   # recovered from the older one
    assert v.params_from_catalog == "GWTC-3-confident"
    assert stats.merged == 1


def test_missing_parameters_stay_null_never_zero(temp_db):
    ingest([rec("GW888888", "GWTC-1-marginal", 1, {"far": 1.2})])
    rebuild()
    with temp_db.session() as s:
        v = s.scalar(select(EventView))
    assert v.mass_1_source is None
    assert v.mass_class is None
    assert v.is_marginal is True


def test_mass_classification():
    assert classify_masses(35.0, 30.0) == "BBH"
    assert classify_masses(1.46, 1.27) == "BNS"
    assert classify_masses(23.3, 2.6) == "NSBH"
    assert classify_masses(None, 30.0) is None


def test_observing_run_from_gps():
    assert observing_run(1126259462.4) == "O1"      # GW150914
    assert observing_run(1187008882.4) == "O2"      # GW170817
    assert observing_run(1242442967.4) == "O3a"     # GW190521
    assert observing_run(None) is None
