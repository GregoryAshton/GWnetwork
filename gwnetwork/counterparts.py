"""Which matched designations name an event's counterpart, not its GW signal.

GRB 170817A and AT2017gfo are aliases of GW170817 on purpose -- one Event per
physical source -- but they are different observations. Engagement measures
use of the GW results, so a mention by counterpart name has to stay visible
as such to both the classifier and the labelling UI.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from .models import DesignationKind, EventDesignation


def _key(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", text).upper()


def counterpart_names(s) -> frozenset:
    """Normalised forms of every `em_counterpart` designation."""
    return frozenset(_key(d) for d in s.scalars(
        select(EventDesignation.designation).where(
            EventDesignation.kind == DesignationKind.em_counterpart.value)))


def is_counterpart(matched_text: str, names: frozenset) -> bool:
    return _key(matched_text or "") in names
