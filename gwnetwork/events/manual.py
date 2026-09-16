"""Manual event provider.

Non-GWOSC events enter the database through reviewed YAML in data/events/, not
through a direct database write. A file looks like:

    common_name: GW230101_example
    source: 4-ogc
    catalog: 4-OGC
    gps: 1356566418.0
    reference: "arXiv:2105.09151"
    designations:
      - designation: GW230101_example
      - {designation: "AT2023xyz", kind: em_counterpart}
    parameters:
      mass_1_source: 35.0
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import yaml

from ..config import CONFIG
from .base import DesignationIn, EventRecordIn


class ManualProvider:
    name = "manual"

    def __init__(self, directory: Optional[Path] = None):
        self.directory = Path(directory or (CONFIG.data_dir / "events"))

    def fetch(self, since: Optional[datetime] = None) -> Iterable[EventRecordIn]:
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.y*ml")):
            doc = yaml.safe_load(path.read_text()) or {}
            for entry in (doc if isinstance(doc, list) else [doc]):
                common = entry["common_name"]
                source = entry.get("source", "manual")
                desigs = [
                    DesignationIn(
                        d["designation"] if isinstance(d, dict) else d,
                        kind=(d.get("kind", "gw_name") if isinstance(d, dict) else "gw_name"),
                        precedence=(d.get("precedence", 50) if isinstance(d, dict) else 50),
                    )
                    for d in entry.get("designations", [{"designation": common}])
                ]
                yield EventRecordIn(
                    common_name=common,
                    source=source,
                    external_id=entry.get("external_id", f"{common}-{source}"),
                    catalog=entry.get("catalog"),
                    version=entry.get("version"),
                    gps=entry.get("gps"),
                    parameters=entry.get("parameters", {}) or {},
                    reference=entry.get("reference"),
                    designations=desigs,
                )
