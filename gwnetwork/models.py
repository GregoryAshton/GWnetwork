"""Database schema.

Three ideas drive this design:

1. Event identity is modelled explicitly (event / event_designation /
   event_record) so a signal reported by GWOSC, by an independent pipeline, and
   by a preprint can all refer to one physical event without any of them being
   privileged.
2. Nothing analytical is ever overwritten.  Every mention and usage row points
   at the ``analysis_run`` that produced it, so prompt changes are diffable.
3. Every classification carries evidence offsets into the source text.  A claim
   we cannot point at is a claim we cannot defend.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

class DesignationKind(str, enum.Enum):
    gw_name = "gw_name"              # GW150914, GW190521_030229
    superevent = "superevent"        # S190425z
    em_counterpart = "em_counterpart" # AT2017gfo, GRB 170817A, SSS17a
    catalog_alias = "catalog_alias"   # LVT151012


class Event(Base):
    """One physical astrophysical event. Deliberately thin."""
    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    canonical_name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # How many papers ADS full-text search matches for ANY of this event's
    # designations. The ceiling our own extraction is working towards, and the
    # honest denominator for a mention count.
    ads_ceiling: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    designations: Mapped[list[EventDesignation]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    records: Mapped[list[EventRecord]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.canonical_name}>"


class EventDesignation(Base):
    """Every string that has ever referred to an event.

    ``precedence`` orders display; ``valid_from`` exists because names changed
    (short form -> long form at GWTC-2.1) and a 2019 paper saying "GW190521"
    means something different from a 2023 paper saying it.
    """
    __tablename__ = "event_designation"
    __table_args__ = (UniqueConstraint("designation", "event_id", name="uq_desig_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"), index=True)
    designation: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default=DesignationKind.gw_name.value)
    source: Mapped[str] = mapped_column(String(32), default="gwosc")
    precedence: Mapped[int] = mapped_column(Integer, default=100)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)

    event: Mapped[Event] = relationship(back_populates="designations")


class EventRecord(Base):
    """One source's claim about an event. Append-only; never updated in place."""
    __tablename__ = "event_record"
    __table_args__ = (
        UniqueConstraint("source", "catalog", "external_id", name="uq_record_external"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)   # gwosc | 4-ogc | manual
    catalog: Mapped[Optional[str]] = mapped_column(String(64))    # GWTC-4.0
    external_id: Mapped[str] = mapped_column(String(96))          # GW150914-v3
    version: Mapped[Optional[int]] = mapped_column(Integer)
    gps: Mapped[Optional[float]] = mapped_column(Float, index=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    reference: Mapped[Optional[str]] = mapped_column(String(512))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    event: Mapped[Event] = relationship(back_populates="records")


class EventMergeProposal(Base):
    """Cross-provider reconciliation is never automatic. Proposals land here."""
    __tablename__ = "event_merge_proposal"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id_a: Mapped[str] = mapped_column(String(36), index=True)
    event_id_b: Mapped[str] = mapped_column(String(36), index=True)
    reason: Mapped[str] = mapped_column(Text)
    gps_delta: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed|accepted|rejected
    decided_by: Mapped[Optional[str]] = mapped_column(String(128))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

class Paper(Base):
    __tablename__ = "paper"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    arxiv_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True)
    bibcode: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True)
    doi: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    version: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list] = mapped_column(JSON, default=list)
    primary_category: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    updated_at_src: Mapped[Optional[datetime]] = mapped_column(DateTime)

    fulltext_path: Mapped[Optional[str]] = mapped_column(String(512))
    fulltext_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    fulltext_source: Mapped[Optional[str]] = mapped_column(String(32))  # arxiv_html|pdf|abstract_only

    discovered_via: Mapped[Optional[str]] = mapped_column(String(64))
    citation_count: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    mentions: Mapped[list[Mention]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class EventView(Base):
    """Materialised, queryable projection of an event's parameters.

    Parameters live in `EventRecord.parameters` as JSON, one record per
    catalogue version, so filtering on mass or SNR means unpacking JSON across
    several rows per event. This table flattens that into indexed columns.

    It is DERIVED, not authoritative: drop and rebuild it freely with
    `gwn events rebuild-view`. `EventRecord` remains the source of truth, and
    `primary_record_id` / `params_from_catalog` record where each value came
    from so a displayed number can always be traced back.

    Every parameter is nullable, and that is load-bearing: only 68% of events
    have a mass measurement. A filter that treats NULL as zero, or silently
    drops those events, is wrong.
    """
    __tablename__ = "event_view"

    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"), primary_key=True)
    primary_record_id: Mapped[Optional[str]] = mapped_column(String(36))
    catalog: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    catalog_version: Mapped[Optional[int]] = mapped_column(Integer)
    params_from_catalog: Mapped[Optional[str]] = mapped_column(String(64))
    n_catalogs: Mapped[int] = mapped_column(Integer, default=0)
    is_marginal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    gps: Mapped[Optional[float]] = mapped_column(Float, index=True)
    observing_run: Mapped[Optional[str]] = mapped_column(String(8), index=True)

    mass_1_source: Mapped[Optional[float]] = mapped_column(Float, index=True)
    mass_2_source: Mapped[Optional[float]] = mapped_column(Float, index=True)
    total_mass_source: Mapped[Optional[float]] = mapped_column(Float, index=True)
    chirp_mass_source: Mapped[Optional[float]] = mapped_column(Float, index=True)
    final_mass_source: Mapped[Optional[float]] = mapped_column(Float)
    mass_ratio: Mapped[Optional[float]] = mapped_column(Float, index=True)
    chi_eff: Mapped[Optional[float]] = mapped_column(Float, index=True)
    luminosity_distance: Mapped[Optional[float]] = mapped_column(Float, index=True)
    redshift: Mapped[Optional[float]] = mapped_column(Float, index=True)
    network_matched_filter_snr: Mapped[Optional[float]] = mapped_column(Float, index=True)
    far: Mapped[Optional[float]] = mapped_column(Float, index=True)
    p_astro: Mapped[Optional[float]] = mapped_column(Float, index=True)

    # Derived. See events/view.py for the NS mass threshold and its caveats.
    mass_class: Mapped[Optional[str]] = mapped_column(String(16), index=True)

    n_papers: Mapped[int] = mapped_column(Integer, default=0, index=True)
    n_papers_fulltext: Mapped[int] = mapped_column(Integer, default=0)
    n_aliases: Mapped[int] = mapped_column(Integer, default=0)
    rebuilt_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PaperDiscovery(Base):
    """Every (paper, designation) pair ADS returned.

    Recording only the first designation that found a paper throws away the
    single best prior we have on which papers are event-dense: a paper returned
    by 30 different event queries is almost certainly a catalogue or population
    analysis. That prior is what makes a full-text subsample worth fetching,
    because full text is where population and multi-messenger usage lives.
    """
    __tablename__ = "paper_discovery"
    __table_args__ = (UniqueConstraint("paper_id", "designation", name="uq_paper_desig"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    designation: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="ads")


class HarvestState(Base):
    """Watermarks for incremental harvesting. One row per (source, key)."""
    __tablename__ = "harvest_state"
    __table_args__ = (UniqueConstraint("source", "key", name="uq_harvest"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(128))
    watermark: Mapped[Optional[str]] = mapped_column(String(128))
    resumption_token: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    stage: Mapped[str] = mapped_column(String(32))            # extract | classify
    pipeline_version: Mapped[str] = mapped_column(String(32), index=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(64))
    prompt_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Mention(Base):
    """A designation string found in a paper's text. Deterministic; no LLM."""
    __tablename__ = "mention"
    __table_args__ = (
        Index("ix_mention_paper_event", "paper_id", "event_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    event_id: Mapped[Optional[str]] = mapped_column(ForeignKey("event.id"), index=True)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_run.id"), index=True)

    matched_text: Mapped[str] = mapped_column(String(64))
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    sentence: Mapped[str] = mapped_column(Text, default="")
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)

    paper: Mapped[Paper] = relationship(back_populates="mentions")


class UnknownDesignation(Base):
    """A GW-shaped string matching no known event.

    This is the discovery channel for events we have never heard of -- including
    non-GWOSC candidates. Humans promote rows from here into `event`.
    """
    __tablename__ = "unknown_designation"
    __table_args__ = (
        UniqueConstraint("designation", "analysis_run_id", name="uq_unknown_desig_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    designation: Mapped[str] = mapped_column(String(64), index=True)
    # Run-scoped like every other analytical output. Without this the queue
    # accumulates artefacts from superseded pipeline versions and a fixed bug
    # keeps haunting the review list.
    analysis_run_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("analysis_run.id"), index=True)
    first_seen_paper_id: Mapped[Optional[str]] = mapped_column(String(36))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    example_sentence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|promoted|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Engagement(str, enum.Enum):
    """How deeply a paper touches an event. Ordinal, increasing."""
    cited_only = "cited_only"
    published_results = "published_results"
    posterior_samples = "posterior_samples"
    strain_reanalysis = "strain_reanalysis"


ROLES = [
    "single_event", "population", "multimessenger", "test_of_gr", "cosmology",
    "lensing", "formation_channels", "waveform_systematics",
    "exotic_compact_object", "detector_characterisation", "methods_demo",
    "review", "forecast",
]


class EventUsage(Base):
    """How one paper uses one event. Evidence is mandatory."""
    __tablename__ = "event_usage"
    __table_args__ = (
        UniqueConstraint("paper_id", "event_id", "analysis_run_id", name="uq_usage"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"), index=True)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_run.id"), index=True)

    engagement: Mapped[str] = mapped_column(String(32), index=True)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    is_primary_subject: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{quote,char_start,char_end,section}]
    method: Mapped[str] = mapped_column(String(16), default="llm")  # rule | llm
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

class Job(Base):
    """One work unit. Backfill and incremental updates share this table."""
    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("kind", "target_id", "pipeline_version", name="uq_job"),
        Index("ix_job_claim", "kind", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[str] = mapped_column(String(96), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
