"""Event provider protocol.

A provider yields flat EventRecordIn objects. It does not decide event identity
-- the ingest layer does that -- so adding a new source never requires touching
the schema or the matcher.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Protocol, runtime_checkable


@dataclass
class DesignationIn:
    designation: str
    kind: str = "gw_name"
    precedence: int = 100
    valid_from: Optional[datetime] = None


@dataclass
class EventRecordIn:
    """One source's claim about one event."""
    common_name: str                 # groups records into a single Event
    source: str                      # gwosc | manual | 4-ogc | ...
    external_id: str                 # GW150914-v3
    catalog: Optional[str] = None
    version: Optional[int] = None
    gps: Optional[float] = None
    parameters: dict = field(default_factory=dict)
    reference: Optional[str] = None
    designations: list = field(default_factory=list)  # list[DesignationIn]


@runtime_checkable
class EventProvider(Protocol):
    name: str

    def fetch(self, since: Optional[datetime] = None) -> Iterable[EventRecordIn]:
        ...
