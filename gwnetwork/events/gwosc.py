"""GWOSC event provider.

`/eventapi/json/allevents/` returns every event-record across every catalog in
one document, keyed `NAME-vN` with a separate `commonName` and
`catalog.shortName`. That is the whole provider: one request, ~670 records,
~433 distinct events.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

import httpx

from ..config import CONFIG
from .base import DesignationIn, EventRecordIn

# Parameter fields worth keeping. GWOSC also ships _lower/_upper/_unit siblings
# for most of these; we keep them all when present.
_PARAM_KEYS = [
    "mass_1_source", "mass_2_source", "total_mass_source", "chirp_mass_source",
    "final_mass_source", "chi_eff", "luminosity_distance", "redshift",
    "network_matched_filter_snr", "far", "p_astro",
]


class GWOSCProvider:
    name = "gwosc"

    def __init__(self, base: Optional[str] = None, timeout: float = 120.0):
        self.base = (base or CONFIG.gwosc_base).rstrip("/")
        self.timeout = timeout

    def fetch(self, since: Optional[datetime] = None) -> Iterable[EventRecordIn]:
        url = f"{self.base}/eventapi/json/allevents/"
        with httpx.Client(timeout=self.timeout, headers={"User-Agent": CONFIG.user_agent},
                          follow_redirects=True) as c:
            payload = c.get(url).raise_for_status().json()

        for external_id, ev in payload.get("events", {}).items():
            common = ev.get("commonName") or external_id.rsplit("-v", 1)[0]
            params = {}
            for key in _PARAM_KEYS:
                for suffix in ("", "_lower", "_upper", "_unit"):
                    k = key + suffix
                    if ev.get(k) is not None:
                        params[k] = ev[k]

            yield EventRecordIn(
                common_name=common,
                source=self.name,
                external_id=external_id,
                catalog=ev.get("catalog.shortName"),
                version=ev.get("version"),
                gps=ev.get("GPS"),
                parameters=params,
                reference=ev.get("jsonurl"),
                designations=[DesignationIn(common, kind="gw_name", precedence=10)],
            )
