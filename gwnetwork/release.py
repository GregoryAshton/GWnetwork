"""Frozen data releases: the slim database deposited on Zenodo, and the check
that its full text can be rebuilt from arXiv.

Full text is not deposited (arXiv licensing). Every text records the arXiv
version, format and converter that produced it, and a SHA-256 of its bytes,
so `verify_fulltext` can re-fetch it and say exactly which texts still match.
"""
from __future__ import annotations

import hashlib
import random
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .config import CONFIG
from .db import session
from .models import Paper

# Run-scoped tables: only rows of the current run of each stage are released.
_RUN_SCOPED = ("mention", "event_usage", "unknown_designation")


def _db_path() -> Path:
    url = CONFIG.db_url
    if not url.startswith("sqlite:///"):
        raise RuntimeError(f"release export needs a SQLite database, not {url}")
    return Path(url.removeprefix("sqlite:///"))


def export_slim(out: Path, release: str, allow_dirty: bool = False) -> dict:
    """Copy the database keeping only the current extract and classify runs.

    Analysis history stays local; the release is the data one analysis is
    computed from. Absolute full-text paths are made relative to the full-text
    directory, which is what `verify_fulltext` rebuilds. A `release_info`
    table records the release name, the code commit and the runs, so the file
    identifies itself; exporting from uncommitted code is refused.
    """
    from datetime import datetime, timezone

    from .version import build_info
    info = build_info()
    if info["dirty"] and not allow_dirty:
        raise RuntimeError("uncommitted changes: commit (and tag) before exporting a release")
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(_db_path())
    dst = sqlite3.connect(out)
    with dst:
        src.backup(dst)
    src.close()

    stats = {}
    with dst:
        current = [r for (r,) in dst.execute(
            "select id from analysis_run where is_current")]
        stages = dict(dst.execute(
            "select stage, count(*) from analysis_run where is_current group by stage").fetchall())
        if stages != {"extract": 1, "classify": 1}:
            raise RuntimeError(f"expected one current extract and classify run, got {stages}")
        marks = ",".join("?" * len(current))
        for table in _RUN_SCOPED:
            dst.execute(f"delete from {table} where analysis_run_id not in ({marks})", current)
        dst.execute(f"delete from analysis_run where id not in ({marks})", current)
        prefix = str(Path(CONFIG.fulltext_dir).resolve()) + "/"
        dst.execute("update paper set fulltext_path = substr(fulltext_path, ?) "
                    "where fulltext_path like ?", (len(prefix) + 1, prefix + "%"))
        left = dst.execute("select count(*) from paper where fulltext_path like '/%'").fetchone()[0]
        if left:
            raise RuntimeError(f"{left} full-text paths are outside {prefix}")
        runs = dict(dst.execute("select stage, id from analysis_run").fetchall())
        dst.execute("create table release_info (key text primary key, value text)")
        dst.executemany("insert into release_info values (?, ?)", [
            ("release", release),
            ("code_commit", info["sha"] + ("+local" if info["dirty"] else "")),
            ("code_repository", info["repo_url"]),
            ("exported_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("extract_run_id", runs["extract"]),
            ("classify_run_id", runs["classify"]),
        ])
        for (table,) in dst.execute(
                "select name from sqlite_master where type='table' order by name").fetchall():
            stats[table] = dst.execute(f'select count(*) from "{table}"').fetchone()[0]
    # A deposited file must stand alone: no -wal/-shm side files.
    dst.execute("pragma journal_mode=delete")
    dst.execute("vacuum")
    dst.close()
    stats["bytes"] = out.stat().st_size
    return stats


def verify_fulltext(sample: Optional[int] = None, seed: int = 0,
                    rate_delay: float = 3.0, keep: Optional[Path] = None) -> dict:
    """Re-fetch stored texts at their recorded version and format; compare.

    Three outcomes per text: an exact byte match (raw bytes: some PDF texts
    contain \r); a body match, where arXiv's page chrome around an HTML paper
    has changed but the text from the arXiv stamp line on is identical
    through the paper's last mention, so every offset moves by one constant
    shift; or a real difference. A full run is ~14,000 fetches at the polite
    3 s (about 12 hours), so `sample` checks a random subset.
    """
    from sqlalchemy import func

    from .corpus.arxiv import (HTML_CONVERTER, ArxivFetcher, VersionGone, body_start,
                               pdf_converter)
    from .models import AnalysisRun, Mention

    current = {"arxiv_html": HTML_CONVERTER, "pdf": pdf_converter()}
    with session() as s:
        papers = s.execute(select(
            Paper.id, Paper.arxiv_id, Paper.fulltext_version, Paper.fulltext_source,
            Paper.fulltext_converter, Paper.fulltext_sha256, Paper.fulltext_path,
            Paper.fulltext_body_start).where(
            Paper.fulltext_version.is_not(None), ~Paper.withdrawn)).all()
        run = s.scalar(select(AnalysisRun.id).where(
            AnalysisRun.stage == "extract", AnalysisRun.is_current == True))  # noqa: E712
        last_end = dict(s.execute(select(Mention.paper_id, func.max(Mention.char_end))
                                  .where(Mention.analysis_run_id == run)
                                  .group_by(Mention.paper_id)).all())
    if sample:
        papers = random.Random(seed).sample(papers, min(sample, len(papers)))

    stats = {"checked": 0, "exact": 0, "body_match": 0, "shifts": {},
             "differs": [], "unavailable": [], "converter_changed": 0}
    out = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="gwn-verify-"))
    try:
        with ArxivFetcher(fulltext_dir=out, rate_delay=rate_delay) as fetcher:
            for pid, arxiv_id, version, source, converter, sha, path, start in papers:
                label = f"{arxiv_id}v{version} {source}"
                stats["checked"] += 1
                stats["converter_changed"] += converter != current.get(source)
                try:
                    got = fetcher.fetch(arxiv_id, version, (source,))
                except VersionGone:
                    got = None
                if got is None:
                    stats["unavailable"].append(label)
                    continue
                if hashlib.sha256(got.path.read_bytes()).hexdigest() == sha:
                    stats["exact"] += 1
                    continue
                new = got.path.read_text(errors="replace")
                new_start = body_start(new, source)
                if start is not None and new_start is not None:
                    ours = Path(CONFIG.fulltext_dir, path).read_text(errors="replace")
                    upto = max(last_end.get(pid, 0) - start, 0)
                    if ours[start:start + upto] == new[new_start:new_start + upto] \
                            and ours[start:start + 200] == new[new_start:new_start + 200]:
                        stats["body_match"] += 1
                        shift = new_start - start
                        stats["shifts"][shift] = stats["shifts"].get(shift, 0) + 1
                        continue
                stats["differs"].append(label)
    finally:
        if not keep:
            shutil.rmtree(out, ignore_errors=True)
    return stats
