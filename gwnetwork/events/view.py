"""Rebuild the materialised event view.

Two decisions are made here that nothing else in the codebase makes, so they are
written down rather than implied.

CATALOGUE PRECEDENCE. One event may appear in a dozen catalogue versions with
different parameters. `CATALOG_RANK` fixes which is authoritative. Confident
GWTC releases outrank discovery papers, which outrank independent pipelines,
which outrank preliminary and marginal listings.

FIELD-LEVEL FALLBACK. The highest-ranked record is not always the most complete
-- an O4 discovery listing may carry SNR but no masses, while an earlier record
has full parameter estimation. So each field is taken from the highest-ranked
record that actually has it, and `params_from_catalog` records where the masses
came from. Taking every field from one record would throw away real
measurements; pretending the merge did not happen would hide it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select

from ..db import session
from ..models import (
    AnalysisRun, Event, EventDesignation, EventRecord, EventView, Mention, Paper,
)

# Highest authority first. Anything unlisted ranks last.
CATALOG_RANK = [
    "GWTC-5.0", "GWTC-4.1", "GWTC-4.0",
    "GWTC-3-confident", "GWTC-2.1-confident", "GWTC-2", "GWTC-1-confident",
    "O4_Discovery_Papers", "O3_Discovery_Papers",
    "GWTC-2.1-auxiliary",
    "IAS-O3a",
    "GWTC-3-marginal", "GWTC-2.1-marginal", "GWTC-1-marginal", "O3_IMBH_marginal",
    "O1_O2-Preliminary", "Initial_LIGO_Virgo",
]
_RANK = {c: i for i, c in enumerate(CATALOG_RANK)}
UNRANKED = len(CATALOG_RANK) + 1

PARAM_FIELDS = [
    "mass_1_source", "mass_2_source", "total_mass_source", "chirp_mass_source",
    "final_mass_source", "chi_eff", "luminosity_distance", "redshift",
    "network_matched_filter_snr", "far", "p_astro",
]

# Compact-object classification threshold, in solar masses. 3.0 is the
# conventional dividing line but it is a CONVENTION, not a measurement: objects
# near it (GW190814's 2.6 M_sun secondary is the famous case) are genuinely
# ambiguous, and the classification here ignores measurement uncertainty
# entirely. Treat `mass_class` as a browsing aid, never as a physical claim.
NS_MAX_MASS = 3.0

# GPS bounds of the observing runs, for the `observing_run` facet.
RUN_BOUNDS = [
    ("O1", 1126051217, 1137254417), ("O2", 1164556817, 1187733618),
    ("O3a", 1238166018, 1253977218), ("O3b", 1256655618, 1269363618),
    ("O4", 1368975618, 1451606400),
]


def observing_run(gps: Optional[float]) -> Optional[str]:
    if gps is None:
        return None
    for name, lo, hi in RUN_BOUNDS:
        if lo <= gps < hi:
            return name
    return None


def classify_masses(m1: Optional[float], m2: Optional[float]) -> Optional[str]:
    if m1 is None or m2 is None:
        return None
    hi, lo = max(m1, m2), min(m1, m2)
    if lo >= NS_MAX_MASS:
        return "BBH"
    if hi < NS_MAX_MASS:
        return "BNS"
    return "NSBH"


def _rank(record: EventRecord) -> tuple:
    """Sort key: catalogue authority, then newest version."""
    return (_RANK.get(record.catalog or "", UNRANKED), -(record.version or 0))


@dataclass
class ViewStats:
    events: int = 0
    with_masses: int = 0
    merged: int = 0

    def __str__(self) -> str:
        return (f"{self.events} events projected, {self.with_masses} with masses, "
                f"{self.merged} needed a lower-ranked catalogue for some field")


def rebuild() -> ViewStats:
    stats = ViewStats()
    with session() as s:
        run = s.scalar(select(AnalysisRun).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712

        paper_counts: dict = {}
        ft_counts: dict = {}
        if run is not None:
            for eid, n in s.execute(
                    select(Mention.event_id, func.count(func.distinct(Mention.paper_id)))
                    .where(Mention.analysis_run_id == run.id)
                    .group_by(Mention.event_id)).all():
                paper_counts[eid] = n
            for eid, n in s.execute(
                    select(Mention.event_id, func.count(func.distinct(Mention.paper_id)))
                    .join(Paper, Paper.id == Mention.paper_id)
                    .where(Mention.analysis_run_id == run.id,
                           Paper.fulltext_path.is_not(None))
                    .group_by(Mention.event_id)).all():
                ft_counts[eid] = n

        alias_counts = dict(s.execute(
            select(EventDesignation.event_id, func.count())
            .group_by(EventDesignation.event_id)).all())

        by_event: dict = {}
        for rec in s.scalars(select(EventRecord)):
            by_event.setdefault(rec.event_id, []).append(rec)

        s.execute(delete(EventView))

        for event in s.scalars(select(Event)):
            records = sorted(by_event.get(event.id, []), key=_rank)
            primary = records[0] if records else None

            values: dict = {}
            source_of: dict = {}
            for rec in records:
                for field in PARAM_FIELDS:
                    if field not in values and (rec.parameters or {}).get(field) is not None:
                        values[field] = rec.parameters[field]
                        source_of[field] = rec.catalog
            if primary is not None and any(
                    source_of.get(f) not in (None, primary.catalog) for f in values):
                stats.merged += 1

            gps = next((r.gps for r in records if r.gps is not None), None)
            m1, m2 = values.get("mass_1_source"), values.get("mass_2_source")
            catalogs = {r.catalog for r in records if r.catalog}

            view = EventView(
                event_id=event.id,
                primary_record_id=primary.id if primary else None,
                catalog=primary.catalog if primary else None,
                catalog_version=primary.version if primary else None,
                params_from_catalog=source_of.get("mass_1_source"),
                n_catalogs=len(catalogs),
                is_marginal=all("marginal" in (c or "").lower() for c in catalogs) if catalogs else False,
                gps=gps,
                observing_run=observing_run(gps),
                mass_ratio=(min(m1, m2) / max(m1, m2)) if (m1 and m2) else None,
                mass_class=classify_masses(m1, m2),
                n_papers=paper_counts.get(event.id, 0),
                n_papers_fulltext=ft_counts.get(event.id, 0),
                n_aliases=alias_counts.get(event.id, 0),
                rebuilt_at=datetime.utcnow(),
                **{f: values.get(f) for f in PARAM_FIELDS},
            )
            s.add(view)
            stats.events += 1
            if m1 is not None:
                stats.with_masses += 1
    return stats
