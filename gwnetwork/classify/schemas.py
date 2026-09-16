"""Structured output schema for the classifier.

Evidence is required at the schema level, not by convention. A classification we
cannot point at in the source text is one we cannot defend on the website.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

from ..models import ROLES

Engagement = Literal[
    "cited_only", "published_results", "posterior_samples", "strain_reanalysis"
]
Role = Literal[tuple(ROLES)]  # type: ignore[valid-type]


class Evidence(BaseModel):
    quote: str = Field(description="Verbatim sentence from the paper supporting this call")
    section: str = Field(default="", description="Section the quote came from")


class EventUsageOut(BaseModel):
    event_name: str = Field(description="Event designation exactly as given in the input list")
    engagement: Engagement = Field(
        description=(
            "cited_only: mentioned, no analysis. "
            "published_results: uses quoted catalogue parameters. "
            "posterior_samples: uses released PE samples. "
            "strain_reanalysis: reanalyses the strain data itself."
        )
    )
    roles: List[Role] = Field(default_factory=list,
                              description="What the event is used FOR. May be empty or multiple.")
    is_primary_subject: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[Evidence] = Field(min_length=1,
                                     description="At least one verbatim supporting quote")


class PaperClassification(BaseModel):
    events: List[EventUsageOut]


SCHEMA = PaperClassification.model_json_schema()
