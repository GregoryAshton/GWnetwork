"""Super-gold set: a probability sample of papers labelled by collaborators.

The gold set in eval/gold.jsonl was drawn uniformly and half of it is model
consensus. This one is labelled by people only, and drawn so that it stays
usable for questions nobody has asked yet:

* **Stratified, with known inclusion probabilities.** Papers are grouped by
  what the frozen classify run predicted for them (any posterior-samples pair,
  else any strain pair, else any published-results pair, else neither), by era
  (submitted before 2020 or not), plus one stratum for LVK papers. Rare strata
  are oversampled; each paper records its stratum and its probability `pi` of
  being drawn, so any later estimate can be weighted back to the corpus
  (Horvitz-Thompson, or post-stratified on what was actually completed).
* **A uniform slice.** Inside each stratum the first `m_h` drawn papers, with
  `m_h` proportional to the stratum's size, are flagged `uniform`. Together
  they are a self-weighting sample of the whole frame, usable without any
  weights. Labellers also list every event they find in these papers, which
  is the only way to measure extraction recall.
* **Whole papers.** People label all pairs in a paper (up to `max_pairs`,
  subsampled at random above that, with the rate recorded as `pair_pi`).
* **Frozen.** The sample records the classify and extract runs, the release,
  the cutoff and the seed, and stores each pair's predicted labels, so the
  strata can be rebuilt and the frozen run scored without the database.

Labellers see no model output (anchoring would inflate exactly the agreement
the paper's error correction depends on) and no roles; each pair gets the
usage questions of eval/GUIDELINES.md and a three-level confidence.

Files, all in eval/supergold/:
  sample.json       the drawn sample (`gwn eval supergold draw`)
  assignments.json  reviewer tokens and their queues (`... assign`)
  labels.jsonl      one row per (reviewer, pair), rebuilt by `... import`
  papers.jsonl      one row per (reviewer, paper): skips, author flag, time,
                    the events listed for recall
"""
from __future__ import annotations

import csv
import json
import random
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .config import ROOT
from .evalset import GOLD_PATH, Agreement, load_gold, row_uses
from .models import FALLBACKS, USES, deepest_use, normalise_uses

SUPERGOLD_DIR = ROOT / "eval" / "supergold"
SAMPLE_PATH = SUPERGOLD_DIR / "sample.json"
ASSIGN_PATH = SUPERGOLD_DIR / "assignments.json"
LABELS_PATH = SUPERGOLD_DIR / "labels.jsonl"
PAPERS_PATH = SUPERGOLD_DIR / "papers.jsonl"

GOLD_VERSION = 3
GUIDE_VERSION = "3.1"
CONFIDENCE = ["clear", "probably", "ambiguous"]
SKIP_REASONS = ["cannot_judge", "conflict", "no_access", "other"]

ERA_SPLIT_YEAR = 2020
# As `papers()` in the paper repo (analysis/data.py): an LVK paper has at
# least this many authors or names a collaboration in its author list.
LVK_AUTHORS = 500
LVK_COLLABORATIONS = ("LIGO Scientific Collaboration", "Virgo Collaboration",
                      "KAGRA Collaboration")
# Paper class = its rarest predicted data product, in this order.
CLASS_ORDER = [("samples", "posterior_samples"), ("strain", "strain_analysis"),
               ("published", "published_results")]


# --------------------------------------------------------------------------
# Frame and strata
# --------------------------------------------------------------------------

def paper_class(pair_uses: Iterable[list]) -> str:
    """'samples' / 'strain' / 'published' for the rarest data product any pair
    is predicted to use, else 'other' (qualitative and cited-only only)."""
    got = {u for uses in pair_uses for u in uses or []}
    return next((name for name, use in CLASS_ORDER if use in got), "other")


def stratum_of(lvk: bool, year: Optional[int], cls: str) -> str:
    if lvk:
        return "lvk"
    era = "pre2020" if year is not None and year < ERA_SPLIT_YEAR else "2020+"
    return f"{cls}/{era}"


def is_lvk(authors: list) -> bool:
    authors = authors or []
    return len(authors) >= LVK_AUTHORS or any(
        c in a for a in authors for c in LVK_COLLABORATIONS)


def build_frame(cutoff: Optional[str] = "2026-10-01", exclude: Iterable[str] = ()) -> tuple:
    """(frame, meta): every classified paper with full text, submitted before
    `cutoff`, not withdrawn; one dict per paper with its stratum and pairs."""
    from sqlalchemy import select

    from .db import session
    from .models import AnalysisRun, Event, EventUsage, Mention, Paper
    from .version import RELEASES

    exclude = set(exclude)
    with session() as s:
        runs = {r.stage: r for r in s.scalars(select(AnalysisRun).where(
            AnalysisRun.is_current == True))}  # noqa: E712
        if "classify" not in runs or "extract" not in runs:
            raise RuntimeError("needs a current extract and classify run")
        crun, xrun = runs["classify"], runs["extract"]
        q = (select(Paper.arxiv_id, Paper.authors, Paper.submitted_at,
                    Event.canonical_name, EventUsage.uses, EventUsage.engagement)
             .join(EventUsage, EventUsage.paper_id == Paper.id)
             .join(Event, Event.id == EventUsage.event_id)
             .where(EventUsage.analysis_run_id == crun.id,
                    Paper.withdrawn == False,  # noqa: E712
                    Paper.fulltext_path.is_not(None),
                    Paper.fulltext_source != "abstract_only",
                    Paper.arxiv_id.is_not(None)))
        if cutoff:
            q = q.where(Paper.submitted_at < datetime.fromisoformat(cutoff))
        rows = s.execute(q).all()
        # A pair whose every mention is in the bibliography: the paper's own
        # analysis cut (reference-only pairs) is decided from this later.
        sections = defaultdict(set)
        for arxiv_id, event, section in s.execute(
                select(Paper.arxiv_id, Event.canonical_name, Mention.section)
                .join(Mention, Mention.paper_id == Paper.id)
                .join(Event, Event.id == Mention.event_id)
                .where(Mention.analysis_run_id == xrun.id)):
            sections[(arxiv_id, event)].add(section)
        release = next((r["name"] for r in RELEASES
                        if (r["extract_run_id"], r["classify_run_id"]) == (xrun.id, crun.id)),
                       None)
        meta = {"classify_run_id": crun.id, "classify_version": crun.pipeline_version,
                "classify_model": crun.model_id, "extract_run_id": xrun.id,
                "extract_version": xrun.pipeline_version, "release": release,
                "cutoff": cutoff}

    papers: dict = {}
    for arxiv_id, authors, submitted, event, uses, engagement in rows:
        if arxiv_id in exclude:
            continue
        p = papers.setdefault(arxiv_id, {
            "arxiv_id": arxiv_id, "lvk": is_lvk(authors),
            "year": submitted.year if submitted else None, "pairs": []})
        p["pairs"].append({
            "event": event, "predicted_uses": normalise_uses(uses),
            "predicted_engagement": engagement,
            "reference_only": sections.get((arxiv_id, event)) == {"references"}})
    frame = []
    for p in papers.values():
        p["pairs"].sort(key=lambda r: r["event"])
        p["class"] = paper_class(r["predicted_uses"] for r in p["pairs"])
        p["stratum"] = stratum_of(p["lvk"], p["year"], p["class"])
        frame.append(p)
    frame.sort(key=lambda p: p["arxiv_id"])
    return frame, meta


def _largest_remainder(total: int, sizes: dict) -> dict:
    """Split `total` across strata in proportion to their sizes."""
    n = sum(sizes.values())
    if not n:
        return {h: 0 for h in sizes}
    quota = {h: total * N / n for h, N in sizes.items()}
    out = {h: int(q) for h, q in quota.items()}
    for h in sorted(quota, key=lambda h: (-(quota[h] - out[h]), h))[:total - sum(out.values())]:
        out[h] += 1
    return out


def draw_sample(frame: list, seed: int = 0, n_uniform: int = 30,
                min_per_stratum: int = 10, max_pairs: int = 12,
                per_stratum: Optional[dict] = None) -> dict:
    """Stratified simple random sample of papers without replacement.

    Stratum h of size N_h gets m_h uniform-slice papers (largest-remainder
    share of `n_uniform`) and n_h = max(m_h, min_per_stratum) papers in all,
    or `per_stratum[h]` if given (never fewer than m_h). Every paper in h has
    inclusion probability n_h / N_h; a uniform-slice paper also has
    pi_uniform = m_h / N_h.
    """
    rng = random.Random(seed)
    by_h = defaultdict(list)
    for p in frame:
        by_h[p["stratum"]].append(p)
    sizes = {h: len(ps) for h, ps in sorted(by_h.items())}
    m = _largest_remainder(n_uniform, sizes)
    strata, papers = {}, []
    for h, N in sizes.items():
        want = (per_stratum or {}).get(h, max(m[h], min_per_stratum))
        n = min(N, max(want, m[h]))
        drawn = rng.sample(sorted(by_h[h], key=lambda p: p["arxiv_id"]), n)
        strata[h] = {"N": N, "n": n, "n_uniform": m[h]}
        for i, p in enumerate(drawn):
            pairs = p["pairs"]
            k = len(pairs)
            if k > max_pairs:
                pairs = sorted(rng.sample(pairs, max_pairs), key=lambda r: r["event"])
            papers.append({
                "arxiv_id": p["arxiv_id"], "stratum": h, "lvk": p["lvk"],
                "year": p["year"], "class": p["class"],
                "pi": n / N, "uniform": i < m[h],
                "pi_uniform": m[h] / N if i < m[h] else None,
                "n_pairs_in_paper": k, "pair_pi": len(pairs) / k,
                "pairs": pairs})
    papers.sort(key=lambda p: (p["stratum"], p["arxiv_id"]))
    return {"seed": seed, "n_uniform": n_uniform, "min_per_stratum": min_per_stratum,
            "max_pairs": max_pairs, "frame_size": len(frame), "strata": strata,
            "papers": papers}


def pick_calibration(gold_rows: list, available: set, k: int = 3, seed: int = 0) -> list:
    """k papers from the hand-labelled gold set for training at the start of
    every queue: every pair person-labelled, 1-4 pairs, full text available,
    chosen to cover different engagements."""
    rng = random.Random(seed)
    by_paper = defaultdict(list)
    for r in gold_rows:
        by_paper[r["arxiv_id"]].append(r)
    cands = [a for a, rs in sorted(by_paper.items())
             if a in available and 1 <= len(rs) <= 4
             and all(r.get("labelled_by", "human") == "human" and not r.get("unsure")
                     for r in rs)]
    rng.shuffle(cands)
    chosen, seen = [], set()
    for a in cands:     # first pass: a new engagement each time
        engs = {r["engagement"] for r in by_paper[a]}
        if engs - seen and len(chosen) < k:
            chosen.append(a)
            seen |= engs
    for a in cands:
        if len(chosen) >= k:
            break
        if a not in chosen:
            chosen.append(a)
    return [{"arxiv_id": a, "pairs": [
        {"event": r["event"], "uses": row_uses(r), "engagement": r["engagement"]}
        for r in sorted(by_paper[a], key=lambda r: r["event"])]} for a in chosen]


def draw(seed: int = 0, n_uniform: int = 50, min_per_stratum: int = 20,
         max_pairs: int = 12, cutoff: Optional[str] = "2026-10-01",
         n_calibration: int = 3, path: Optional[Path] = None, force: bool = False) -> dict:
    """Draw the sample from the database and write sample.json."""
    path = Path(path or SAMPLE_PATH)
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; the sample is frozen once drawn (--force to redraw)")
    gold = load_gold(GOLD_PATH)
    frame, meta = build_frame(cutoff)
    calibration = pick_calibration(gold, {p["arxiv_id"] for p in frame},
                                   k=n_calibration, seed=seed)
    # Calibration papers show their answers, so they cannot also be sampled.
    cal_ids = {c["arxiv_id"] for c in calibration}
    frame = [p for p in frame if p["arxiv_id"] not in cal_ids]
    sample = draw_sample(frame, seed=seed, n_uniform=n_uniform,
                         min_per_stratum=min_per_stratum, max_pairs=max_pairs)
    out = {"gold_version": GOLD_VERSION, "guide_version": GUIDE_VERSION,
           "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           **meta, "excluded_from_frame": sorted(cal_ids), **sample,
           "calibration": calibration}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    return out


def load_sample(path: Optional[Path] = None) -> dict:
    return json.loads(Path(path or SAMPLE_PATH).read_text())


# --------------------------------------------------------------------------
# Reviewer queues
# --------------------------------------------------------------------------

def _interleave(own: list, shared: list, every: int = 3) -> list:
    """own, own, shared, own, own, shared, ... then whatever is left."""
    out, a, b = [], list(own), list(shared)
    while a or b:
        for _ in range(every - 1):
            if a:
                out.append(a.pop(0))
        if b:
            out.append(b.pop(0))
    return out


def _spread(r: int) -> float:
    """Van der Corput sequence 0, 1/2, 1/4, 3/4, 1/8, 5/8, ...: for any m,
    the first m values cut the circle into gaps between 1/(2m) and 2/m."""
    x, f = 0.0, 0.5
    while r:
        x += f * (r & 1)
        r >>= 1
        f /= 2
    return x


def make_queues(ids: list, n_reviewers: int, seed: int = 0) -> list:
    """One queue per reviewer, each a permutation of all the papers.

    Robust to not knowing how many people take part: the papers are
    shuffled once into a circle, and reviewer r starts reading it at
    position `_spread(r)`. Whichever first m tokens are in use, their
    starting points are roughly evenly spaced, so they label different
    papers until each has done about n/m. Every 4th paper in a queue is
    instead taken from where reviewer r-1 starts reading, which r-1 labels
    early: about a third of the papers labelled are double-labelled,
    building up from the start even if people stop early. Hand tokens out
    in order. Reviewer 0 (the pilot) shares nothing.
    """
    rng = random.Random(seed)
    order = list(ids)
    rng.shuffle(order)
    n = len(order)

    def stream(r: int) -> list:
        k = int(_spread(r) * n)
        return order[k:] + order[:k]

    queues = []
    for r in range(max(1, n_reviewers)):
        own = iter(stream(r))
        shared = iter(stream(r - 1) if r else [])
        q, seen = [], set()

        def take(it) -> None:
            for p in it:
                if p not in seen:
                    seen.add(p)
                    q.append(p)
                    return

        while len(q) < n:
            for _ in range(3):
                take(own)
            take(shared)
        queues.append(q)
    return queues


def assign(n_reviewers: int, seed: int = 0, sample_path: Optional[Path] = None,
           path: Optional[Path] = None, force: bool = False) -> dict:
    path = Path(path or ASSIGN_PATH)
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; tokens may already be handed out (--force to redo)")
    sample = load_sample(sample_path)
    ids = [p["arxiv_id"] for p in sample["papers"]]
    cal = [c["arxiv_id"] for c in sample["calibration"]]
    tokens = set()
    while len(tokens) < n_reviewers:
        tokens.add(secrets.token_hex(4))
    reviewers = [{"token": t, "queue": cal + q}
                 for t, q in zip(sorted(tokens), make_queues(ids, n_reviewers, seed))]
    out = {"seed": seed, "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "calibration": cal, "reviewers": reviewers}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    return out


def load_assignments(path: Optional[Path] = None) -> dict:
    return json.loads(Path(path or ASSIGN_PATH).read_text())


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def read_submissions(path: Path) -> list:
    """Submissions from the Sheet (CSV download: received_at, token, arxiv_id,
    payload) or from a labeller's 'download my answers' file (JSON list, or
    JSON lines, of payloads)."""
    path = Path(path)
    text = path.read_text()
    if path.suffix == ".csv":
        out = []
        for row in csv.DictReader(text.splitlines()):
            if row.get("payload"):
                p = json.loads(row["payload"].lstrip("'"))
                p["_received_at"] = row.get("received_at") or p.get("submitted_at")
                out.append(p)
        return out
    data = json.loads(text) if text.lstrip().startswith("[") else [
        json.loads(l) for l in text.splitlines() if l.strip()]
    for p in data:
        p.setdefault("_received_at", p.get("submitted_at"))
    return data


def import_submissions(sources: list, sample: Optional[dict] = None,
                       assignments: Optional[dict] = None,
                       labels_path: Optional[Path] = None,
                       papers_path: Optional[Path] = None) -> dict:
    """Validate submissions and rewrite labels.jsonl and papers.jsonl from
    them. The submissions are the source of truth; a reviewer resubmitting a
    paper replaces their earlier answer (latest wins)."""
    sample = sample or load_sample()
    assignments = assignments or load_assignments()
    tokens = {r["token"] for r in assignments["reviewers"]}
    sampled = {p["arxiv_id"]: p for p in sample["papers"]}
    calib = {c["arxiv_id"]: c for c in sample["calibration"]}
    stats = Counter()
    latest: dict = {}
    for src in sources:
        for sub in read_submissions(src):
            stats["read"] += 1
            tok, aid = sub.get("token"), sub.get("arxiv_id")
            if tok not in tokens:
                stats["unknown token"] += 1
                continue
            if aid not in sampled and aid not in calib:
                stats["unknown paper"] += 1
                continue
            key = (tok, aid)
            if key in latest:
                stats["superseded"] += 1
                if str(sub["_received_at"]) < str(latest[key]["_received_at"]):
                    continue
            latest[key] = sub

    labels, papers = [], []
    for (tok, aid), sub in sorted(latest.items()):
        kind = "calibration" if aid in calib else "sample"
        info = sampled.get(aid) or {}
        expected = {r["event"]: r for r in (info.get("pairs") or calib[aid]["pairs"])}
        papers.append({
            "reviewer": tok, "arxiv_id": aid, "kind": kind,
            "skipped": sub.get("skipped") or None, "author": bool(sub.get("author")),
            "seconds": sub.get("seconds"), "comment": sub.get("comment") or "",
            "events_listed": sub.get("events_listed"), "site": sub.get("site"),
            "guide": sub.get("guide"), "received_at": sub["_received_at"]})
        if sub.get("skipped"):
            stats["skipped papers"] += 1
            continue
        for pr in sub.get("pairs") or []:
            ev = pr.get("event")
            if ev not in expected:
                stats["unknown pair"] += 1
                continue
            raw = pr.get("uses") or []
            data = [u for u in raw if u in USES]
            fb = [u for u in raw if u in FALLBACKS]
            if not data and not fb:
                stats["unanswered pair"] += 1
                continue
            uses = normalise_uses(data or fb)
            conf = pr.get("confidence") if pr.get("confidence") in CONFIDENCE else None
            exp = expected[ev]
            row = {"arxiv_id": aid, "event": ev, "engagement": deepest_use(uses),
                   "uses": uses, "confidence": conf, "which": pr.get("which") or "",
                   "unsure": conf == "ambiguous", "reviewer": tok, "kind": kind,
                   "labelled_by": "human", "gold_version": GOLD_VERSION,
                   "guide": sub.get("guide"), "author": bool(sub.get("author"))}
            if kind == "sample":
                row.update({k: info[k] for k in ("stratum", "pi", "uniform", "pi_uniform",
                                                 "pair_pi", "lvk", "year")})
                row.update({k: exp[k] for k in ("predicted_uses", "predicted_engagement",
                                                "reference_only")})
            else:
                row.update({"calibration_uses": exp["uses"],
                            "calibration_engagement": exp["engagement"]})
            labels.append(row)
            stats["pairs"] += 1
    for path, rows in ((labels_path or LABELS_PATH, labels), (papers_path or PAPERS_PATH, papers)):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    stats["papers"] = len(papers)
    return dict(stats)


def _jsonl(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

@dataclass
class Report:
    lines: list = field(default_factory=list)

    def __str__(self) -> str:
        return "\n".join(self.lines)


def _completed_weights(sample: dict, labels: list) -> dict:
    """Post-stratified paper weight N_h / (sample papers completed in h):
    labellers finish part of their queues, in random order, so the completed
    papers are a random subsample of each stratum."""
    done = defaultdict(set)
    for r in labels:
        if r["kind"] == "sample":
            done[r["stratum"]].add(r["arxiv_id"])
    return {h: s["N"] / len(done[h]) for h, s in sample["strata"].items() if done[h]}


def report(sample: Optional[dict] = None, labels: Optional[list] = None,
           papers: Optional[list] = None) -> Report:
    sample = sample or load_sample()
    labels = _jsonl(LABELS_PATH) if labels is None else labels
    papers = _jsonl(PAPERS_PATH) if papers is None else papers
    rep = Report()
    out = rep.lines.append

    sample_rows = [r for r in labels if r["kind"] == "sample"]
    cal_rows = [r for r in labels if r["kind"] == "calibration"]
    out(f"super-gold v{sample['gold_version']} (guide {sample['guide_version']}), "
        f"frozen run {sample['classify_version']} {sample['classify_run_id'][:8]}"
        f"{', release ' + sample['release'] if sample.get('release') else ''}")
    out(f"sample: {len(sample['papers'])} papers from a frame of {sample['frame_size']}; "
        f"{len(papers)} paper submissions, {len(sample_rows)} sample pair labels, "
        f"{len(cal_rows)} calibration pair labels")

    out("")
    out("coverage by stratum (papers completed / drawn / in frame):")
    done = defaultdict(set)
    for r in sample_rows:
        done[r["stratum"]].add(r["arxiv_id"])
    for h, s in sample["strata"].items():
        out(f"  {h:<18} {len(done[h]):>4} / {s['n']:>4} / {s['N']:>6}")

    out("")
    out("reviewers (papers done, skipped, author of, median s/paper, calibration agreement):")
    by_rev = defaultdict(list)
    for p in papers:
        by_rev[p["reviewer"]].append(p)
    for tok, ps in sorted(by_rev.items()):
        secs = sorted(p["seconds"] for p in ps if p.get("seconds") and not p.get("skipped"))
        med = secs[len(secs) // 2] if secs else None
        cal = [r for r in cal_rows if r["reviewer"] == tok]
        cal_ok = sum(r["engagement"] == r["calibration_engagement"] for r in cal)
        out(f"  {tok}  {sum(not p.get('skipped') for p in ps):>3} done, "
            f"{sum(bool(p.get('skipped')) for p in ps)} skipped, "
            f"{sum(p.get('author', False) for p in ps)} author, "
            f"{med if med is not None else '-':>5} s, "
            f"calibration {cal_ok}/{len(cal)}")

    usable = [r for r in sample_rows if not r.get("author")]
    out("")
    out(f"confidence: {dict(Counter(r.get('confidence') for r in usable))}")

    # People against people, on pairs two reviewers labelled.
    by_pair = defaultdict(list)
    for r in usable:
        by_pair[(r["arxiv_id"], r["event"])].append(r)
    ag = Agreement()
    for key, rs in sorted(by_pair.items()):
        if len(rs) >= 2:
            a, b = sorted(rs, key=lambda r: r["reviewer"])[:2]
            ag.add(key, a, row_uses(b), set(), a.get("unsure") or b.get("unsure"))
    out("")
    out("== between reviewers (double-labelled pairs) ==")
    out(_without_roles(ag.report(other_name="second reviewer")))

    # The frozen run against people. Each pair's labels share its weight.
    w_paper = _completed_weights(sample, usable)
    ag_c = Agreement()
    w_sum = w_same = 0.0
    for key, rs in sorted(by_pair.items()):
        for r in rs:
            ag_c.add(key, r, r["predicted_uses"], set(), r.get("unsure"))
            w = w_paper[r["stratum"]] / r["pair_pi"] / len(rs)
            w_sum += w
            w_same += w * (r["engagement"] == r["predicted_engagement"])
    out("")
    out("== frozen classifier run against reviewers (unweighted) ==")
    out(_without_roles(ag_c.report(other_name="classifier")))
    if w_sum:
        out(f"engagement agreement weighted to the corpus (post-stratified): "
            f"{100 * w_same / w_sum:.1f}%")
    uni = [r for r in usable if r["uniform"]]
    if uni:
        same = sum(r["engagement"] == r["predicted_engagement"] for r in uni)
        out(f"engagement agreement on the uniform slice alone: {same}/{len(uni)} "
            f"({100 * same / len(uni):.1f}%)")

    listed = [p for p in papers if p.get("events_listed")]
    out("")
    out(f"extraction recall: {len(listed)} paper submissions list events; "
        "compare them with the extracted pairs by hand")
    return rep


def _without_roles(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.startswith("roles:"))
