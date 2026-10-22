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


def fetch_detectors(base: Optional[str] = None, rate_delay: float = 0.3,
                    timeout: float = 120.0) -> dict:
    """{external_id: {detectors, strain_detectors, data_doi}} for every
    GWOSC event version, from the v2 API.

    One paginated listing gives the summary `detectors` and DOI; one request
    per version gives its strain files. The two disagree (GW190425's summary
    lists H1; its strain files are L1 and V1), so both are kept.
    """
    import time
    base = (base or CONFIG.gwosc_base).rstrip("/")
    out = {}

    def get(c, url, **params):
        # ~670 requests in a row: GWOSC occasionally drops a connection, and
        # one dropped request must not lose the rest.
        for attempt in range(5):
            try:
                return c.get(url, params=params or None).raise_for_status().json()
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt * 2)

    with httpx.Client(timeout=timeout, headers={"User-Agent": CONFIG.user_agent},
                      follow_redirects=True) as c:
        url = f"{base}/api/v2/event-versions?format=json&pagesize=500"
        while url:
            page = get(c, url)
            for r in page["results"]:
                out[r["shortName"]] = {"detectors": sorted(r.get("detectors") or []),
                                       "data_doi": r.get("doi")}
            url = page.get("next")
        for short in out:
            files = get(c, f"{base}/api/v2/event-versions/{short}/strain-files",
                        format="json", pagesize=500)
            if files.get("next"):
                raise RuntimeError(f"{short}: more than 500 strain files; paginate")
            out[short]["strain_detectors"] = sorted(
                {f["detector"] for f in files["results"] if f.get("detector")})
            time.sleep(rate_delay)
    return out
