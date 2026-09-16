"""Runtime configuration. Everything overridable by environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader.

    Deliberately does not overwrite variables already set in the environment, so
    an explicit `ADS_DEV_KEY=... gwn ...` always wins over the file. No
    dependency: the format we need is KEY=value and nothing else.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(os.environ.get("GWN_ENV_FILE", ROOT / ".env")))

# Category scope for the literature search. Deliberately a config value: the
# right scope is an open scientific question, not a constant. nucl-th and hep-ph
# are included for the GW170817 equation-of-state literature.
DEFAULT_CATEGORIES = [
    "gr-qc",
    "astro-ph.HE", "astro-ph.CO", "astro-ph.GA", "astro-ph.SR",
    "astro-ph.IM", "astro-ph.EP",
    "hep-th", "hep-ph", "nucl-th",
]


@dataclass
class Config:
    db_url: str = field(default_factory=lambda: os.environ.get(
        "GWN_DB_URL", f"sqlite:///{ROOT / 'gwnetwork.db'}"))
    data_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("GWN_DATA_DIR", ROOT / "data")))
    fulltext_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("GWN_FULLTEXT_DIR", ROOT / "data" / "fulltext")))

    ads_token: str = field(default_factory=lambda: os.environ.get("ADS_DEV_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))

    categories: list = field(default_factory=lambda: list(DEFAULT_CATEGORIES))

    # Default to Haiku: the recurring cost here is re-runs after prompt changes,
    # and cheap re-runs are what make iteration possible. See DESIGN.md 7a.
    classify_model: str = field(default_factory=lambda: os.environ.get(
        "GWN_CLASSIFY_MODEL", "claude-haiku-4-5"))
    escalate_model: str = field(default_factory=lambda: os.environ.get(
        "GWN_ESCALATE_MODEL", "claude-sonnet-5"))

    gwosc_base: str = "https://gwosc.org"
    ads_base: str = "https://api.adsabs.harvard.edu/v1"
    user_agent: str = "GWnetwork/0.1 (https://github.com/; research crawler)"

    def ensure_dirs(self) -> None:
        self.fulltext_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "events").mkdir(parents=True, exist_ok=True)


CONFIG = Config()
