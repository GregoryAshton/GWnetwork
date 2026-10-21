"""Structured output schema for the classifier.

Evidence is required at the schema level, not by convention. A classification we
cannot point at in the source text is one we cannot defend on the website.
"""
from __future__ import annotations

from typing import List, Literal

from anthropic import transform_schema
from pydantic import BaseModel, Field

from ..models import ROLES

Role = Literal[tuple(ROLES)]  # type: ignore[valid-type]


class Evidence(BaseModel):
    quote: str = Field(description="Verbatim sentence from the paper supporting this call")
    section: str = Field(default="", description="Section the quote came from")


class EventUsageOut(BaseModel):
    event_name: str = Field(description="Event designation exactly as given in the input list")
    # The usage questions (prompts.DATA_QUESTIONS, FALLBACK_QUESTION). Field
    # names match models.USES so the runner can read them directly.
    strain_analysis: bool = Field(description="Q1: analyses the strain data for this event")
    posterior_samples: bool = Field(description="Q2: uses released samples for this event")
    published_results: bool = Field(
        description="Q3: uses published measurements with uncertainties, incl. comparisons")
    qualitative: bool = Field(
        description="Q4: uses anything the event showed (only matters if Q1-Q3 are false)")
    roles: List[Role] = Field(default_factory=list,
                              description="What the event is used FOR. May be empty or multiple.")
    is_primary_subject: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[Evidence] = Field(min_length=1,
                                     description="At least one verbatim supporting quote")


class PaperClassification(BaseModel):
    events: List[EventUsageOut]


# The API requires additionalProperties: false on every object and rejects
# numeric bounds; transform_schema adds the former and moves the latter into
# descriptions.
SCHEMA = transform_schema(PaperClassification)
