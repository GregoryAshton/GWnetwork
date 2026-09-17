"""Chart geometry, computed server-side.

The maths lives here and the SVG lives in the templates: scales are testable in
isolation, and the markup stays readable.

Colour follows the validated palette in the dataviz reference. Categorical slots
1-3 (blue / orange / aqua) carry compact-object class and clear the all-pairs
CVD gate in both themes; the diverging blue<->red pair carries residual sign.
Class is ALSO encoded by marker shape, so identity never rests on hue alone --
the aqua slot sits at 2.82:1 on the light surface, below the 3:1 line.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select

from ..models import AnalysisRun, Event, EventView, Mention, Paper

# Categorical slots 1-3, light / dark.
CLASS_STYLE = {
    "BBH":  {"light": "#2a78d6", "dark": "#3987e5", "shape": "circle"},
    "NSBH": {"light": "#eb6834", "dark": "#d95926", "shape": "triangle"},
    "BNS":  {"light": "#1baf7a", "dark": "#199e70", "shape": "square"},
}
CLASS_ORDER = ["BBH", "NSBH", "BNS"]

# Diverging poles: blue (below trend) <-> red (above trend), gray midpoint.
DIVERGING = {"low": ("#2a78d6", "#3987e5"), "high": ("#e34948", "#e66767"),
             "mid": ("#8d8d86", "#8d8d86")}

PLOT = {"w": 760, "h": 460, "l": 62, "r": 18, "t": 16, "b": 46}


def _inner(box=PLOT):
    return box["w"] - box["l"] - box["r"], box["h"] - box["t"] - box["b"]


@dataclass
class Axis:
    lo: float
    hi: float
    log: bool = False
    ticks: list = field(default_factory=list)   # [(value, px, label)]

    def px(self, v: float, size: float, flip: bool = False) -> float:
        if self.log:
            a, b, x = math.log10(self.lo), math.log10(self.hi), math.log10(max(v, 1e-9))
        else:
            a, b, x = self.lo, self.hi, v
        frac = 0.0 if b == a else (x - a) / (b - a)
        return (1 - frac) * size if flip else frac * size


def _nice_log_ticks(lo: float, hi: float) -> list:
    out = []
    for decade in range(int(math.floor(math.log10(lo))), int(math.ceil(math.log10(hi))) + 1):
        for m in (1, 2, 5):
            v = m * 10 ** decade
            if lo <= v <= hi:
                out.append(v)
    return out or [lo, hi]


def _nice_linear_ticks(lo: float, hi: float, target: int = 6) -> list:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10)), key=lambda s: abs(s - raw))
    start = math.ceil(lo / step) * step
    out, v = [], start
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


LABEL_H = 13.0          # line box for an 11px label
CHAR_W = 6.2            # mean advance at 11px IBM Plex Sans


def place_labels(points: list, w: float, h: float) -> None:
    """Assign non-overlapping label positions in place.

    Outlier labels cluster exactly where the data does, so naive placement put
    nine pairs on top of each other. Walk them in y order, push each clear of
    the last, and flip to the left of the mark when the text would run off the
    right edge.

    A single downward pass is not enough on short plots: clamping at the bottom
    edge stacks whatever is left on the same line. A second pass lifts the run
    back up when it overflows, which is what a crowded timeline needs.
    """
    labelled = sorted([p for p in points if p.get("label")], key=lambda p: p["y"])
    if not labelled:
        return

    # Pass 1: push each label clear of the one above it.
    last_bottom = -1e9
    for p in labelled:
        y = max(p["y"] + 4, last_bottom + LABEL_H)
        p["ly"] = y
        last_bottom = y

    # Pass 2: if the run overflowed the bottom, lift it and re-space upward.
    overflow = labelled[-1]["ly"] - (h - 2)
    if overflow > 0:
        next_top = h - 2
        for p in reversed(labelled):
            y = min(p["ly"] - overflow, next_top)
            p["ly"] = max(y, LABEL_H)
            next_top = p["ly"] - LABEL_H

    for p in labelled:
        width = len(p["label"]) * CHAR_W
        if p["x"] + p["r"] + 5 + width > w:
            p["lx"], p["anchor"] = p["x"] - p["r"] - 5, "end"
        else:
            p["lx"], p["anchor"] = p["x"] + p["r"] + 5, "start"
        p["ly"] = round(p["ly"], 2)
        # A leader line keeps the association visible once a label is nudged.
        p["leader"] = abs(p["ly"] - (p["y"] + 4)) > 3


def _fmt(v: float) -> str:
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10 or float(v).is_integer():
        return f"{v:.0f}"
    return f"{v:g}"


# --------------------------------------------------------------------------
# 1. Mass plane, sized by literature attention
# --------------------------------------------------------------------------

def mass_plane(s) -> dict:
    rows = s.execute(
        select(Event.canonical_name, EventView)
        .join(EventView, EventView.event_id == Event.id)
        .where(EventView.mass_1_source.is_not(None),
               EventView.mass_2_source.is_not(None))).all()
    if not rows:
        return {"points": [], "empty": True}

    m1s = [v.mass_1_source for _, v in rows]
    m2s = [v.mass_2_source for _, v in rows]
    xa = Axis(max(min(m1s) * 0.8, 0.8), max(m1s) * 1.25, log=True)
    ya = Axis(max(min(m2s) * 0.8, 0.8), max(m2s) * 1.25, log=True)
    w, h = _inner()
    xa.ticks = [(v, xa.px(v, w), _fmt(v)) for v in _nice_log_ticks(xa.lo, xa.hi)]
    ya.ticks = [(v, ya.px(v, h, flip=True), _fmt(v)) for v in _nice_log_ticks(ya.lo, ya.hi)]

    # Area encodes papers, so radius goes as the square root -- radius-encoding
    # would exaggerate GW170817 by a factor of its own count.
    most = max((v.n_papers for _, v in rows), default=1) or 1
    labelled = sorted(rows, key=lambda r: -r[1].n_papers)[:6]
    label_names = {n for n, _ in labelled}

    points = []
    for name, v in sorted(rows, key=lambda r: -(r[1].n_papers or 0)):
        style = CLASS_STYLE.get(v.mass_class or "", CLASS_STYLE["BBH"])
        points.append({
            "name": name, "cls": v.mass_class or "unknown",
            "x": round(xa.px(v.mass_1_source, w), 2),
            "y": round(ya.px(v.mass_2_source, h, flip=True), 2),
            "r": round(4 + 13 * math.sqrt((v.n_papers or 0) / most), 2),
            "shape": style["shape"], "light": style["light"], "dark": style["dark"],
            "m1": v.mass_1_source, "m2": v.mass_2_source,
            "papers": v.n_papers, "label": name if name in label_names else None,
        })

    # m1 = m2 is a real boundary: masses are ordered by convention, so no event
    # can sit above it. Drawing it makes the empty half legible as physics.
    lo = max(xa.lo, ya.lo)
    hi = min(xa.hi, ya.hi)
    equal = {"x1": round(xa.px(lo, w), 2), "y1": round(ya.px(lo, h, flip=True), 2),
             "x2": round(xa.px(hi, w), 2), "y2": round(ya.px(hi, h, flip=True), 2)}

    place_labels(points, w, h)
    return {"points": points, "xa": xa, "ya": ya, "w": w, "h": h, "box": PLOT,
            "equal": equal, "most": most, "n": len(points), "empty": False,
            "legend": [{"cls": c, **CLASS_STYLE[c]} for c in CLASS_ORDER]}


# --------------------------------------------------------------------------
# 2. Attention over time
# --------------------------------------------------------------------------

def attention_over_time(s, run, top_n: int = 5) -> dict:
    year = func.strftime("%Y", Paper.submitted_at)
    rows = s.execute(
        select(Event.canonical_name, year, func.count(func.distinct(Mention.paper_id)))
        .join(Mention, Mention.event_id == Event.id)
        .join(Paper, Paper.id == Mention.paper_id)
        .where(Mention.analysis_run_id == (run.id if run else None),
               Paper.submitted_at.is_not(None))
        .group_by(Event.id, year)).all()
    if not rows:
        return {"empty": True}

    # Pre-detection years carry a handful of stray matches; they compress the
    # axis for no information. Start at the first detection.
    rows = [(n, int(y), c) for n, y, c in rows if y and 2015 <= int(y) <= 2100]
    if not rows:
        return {"empty": True}

    totals: dict = {}
    for name, _, c in rows:
        totals[name] = totals.get(name, 0) + c
    top = [n for n, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:top_n]]

    years = sorted({y for _, y, _ in rows})
    series: dict = {n: {y: 0 for y in years} for n in top}
    other = {y: 0 for y in years}
    for name, y, c in rows:
        (series[name] if name in top else other)[y] += c
    names = top + (["Other events"] if any(other.values()) else [])
    if "Other events" in names:
        series["Other events"] = other

    stack_max = max(sum(series[n][y] for n in names) for y in years) or 1
    w, h = _inner()
    xa = Axis(min(years), max(years))
    ya = Axis(0, stack_max * 1.05)
    xa.ticks = [(y, xa.px(y, w), str(y)) for y in years if y % 2 == 0 or y == years[-1]]
    ya.ticks = [(v, ya.px(v, h, flip=True), _fmt(v)) for v in _nice_linear_ticks(0, ya.hi)]

    # Slot 1-3 then muted greys: the first three are the validated all-pairs set.
    ramp_light = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#8d8d86"]
    ramp_dark = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#8d8d86"]

    bands, baseline = [], {y: 0.0 for y in years}
    for i, name in enumerate(names):
        upper, lower = [], []
        for y in years:
            base = baseline[y]
            top_v = base + series[name][y]
            upper.append((round(xa.px(y, w), 2), round(ya.px(top_v, h, flip=True), 2)))
            lower.append((round(xa.px(y, w), 2), round(ya.px(base, h, flip=True), 2)))
            baseline[y] = top_v
        path = ("M " + " L ".join(f"{x},{y}" for x, y in upper) +
                " L " + " L ".join(f"{x},{y}" for x, y in reversed(lower)) + " Z")
        bands.append({"name": name, "path": path, "total": totals.get(name, sum(other.values())),
                      "light": ramp_light[i % len(ramp_light)],
                      "dark": ramp_dark[i % len(ramp_dark)]})

    per_year = [{"year": y, "x": round(xa.px(y, w), 2),
                 "total": sum(series[n][y] for n in names)} for y in years]
    return {"bands": bands, "xa": xa, "ya": ya, "w": w, "h": h, "box": PLOT,
            "years": years, "per_year": per_year, "empty": False}


# --------------------------------------------------------------------------
# 3. Attention against loudness
# --------------------------------------------------------------------------

def attention_vs_snr(s) -> dict:
    rows = s.execute(
        select(Event.canonical_name, EventView)
        .join(EventView, EventView.event_id == Event.id)
        .where(EventView.network_matched_filter_snr.is_not(None),
               EventView.n_papers > 0)).all()
    if len(rows) < 5:
        return {"empty": True}

    pts = [(v.network_matched_filter_snr, math.log10(v.n_papers), n, v) for n, v in rows]
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    slope = (sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx) if sxx else 0.0
    intercept = my - slope * mx
    resid = [p[1] - (slope * p[0] + intercept) for p in pts]
    sd = math.sqrt(sum(r * r for r in resid) / n) or 1.0

    w, h = _inner()
    lo_snr, hi_snr = min(p[0] for p in pts), max(p[0] for p in pts)
    pad = (hi_snr - lo_snr) * (8.0 / w) or 0.5      # >= widest marker radius
    xa = Axis(lo_snr - pad, hi_snr + pad)
    ya = Axis(math.log10(0.8), math.log10(max(v.n_papers for _, v in rows) * 1.35), log=True)
    ya.lo, ya.hi = 0.8, max(v.n_papers for _, v in rows) * 1.35
    xa.ticks = [(v, xa.px(v, w), _fmt(v)) for v in _nice_linear_ticks(xa.lo, xa.hi)]
    ya.ticks = [(v, ya.px(v, h, flip=True), _fmt(v)) for v in _nice_log_ticks(ya.lo, ya.hi)]

    points = []
    for (snr, logp, name, v), r in zip(pts, resid):
        z = r / sd
        pole = "high" if z > 1 else ("low" if z < -1 else "mid")
        points.append({
            "name": name, "x": round(xa.px(snr, w), 2),
            "y": round(ya.px(v.n_papers, h, flip=True), 2),
            "r": 5.0 if pole == "mid" else 6.5,
            "light": DIVERGING[pole][0], "dark": DIVERGING[pole][1],
            "snr": round(snr, 1), "papers": v.n_papers, "z": round(z, 2), "pole": pole,
            "label": None,
        })
    for p in sorted(points, key=lambda p: -abs(p["z"]))[:7]:
        p["label"] = p["name"]
    place_labels(points, w, h)

    def trend_y(x):
        return ya.px(10 ** (slope * x + intercept), h, flip=True)
    trend = {"x1": round(xa.px(xa.lo, w), 2), "y1": round(trend_y(xa.lo), 2),
             "x2": round(xa.px(xa.hi, w), 2), "y2": round(trend_y(xa.hi), 2)}

    above = sum(1 for p in points if p["pole"] == "high")
    below = sum(1 for p in points if p["pole"] == "low")
    return {"points": points, "xa": xa, "ya": ya, "w": w, "h": h, "box": PLOT,
            "trend": trend, "n": len(points), "above": above, "below": below,
            "slope": slope, "empty": False,
            "legend": [{"label": "far above trend", "pole": "high",
                        "light": DIVERGING["high"][0], "dark": DIVERGING["high"][1]},
                       {"label": "as expected", "pole": "mid",
                        "light": DIVERGING["mid"][0], "dark": DIVERGING["mid"][1]},
                       {"label": "far below trend", "pole": "low",
                        "light": DIVERGING["low"][0], "dark": DIVERGING["low"][1]}]}


# --------------------------------------------------------------------------
# 4. Per-event timeline: when papers appeared, and how cited they became
# --------------------------------------------------------------------------

TIMELINE = {"w": 760, "h": 300, "l": 54, "r": 16, "t": 14, "b": 42}

# Citation counts include real zeros (10% of GW170817's papers), and zero has
# no place on a log axis. Rather than drop those papers or fake them as 1, the
# bottom band of the plot is a dedicated "0" row, separated by a rule.
ZERO_BAND = 26
MAX_POINTS = 600
KEEP_TOP = 120      # always plotted: these carry the direct labels


def event_timeline(s, event, view, run) -> dict:
    from datetime import datetime

    from ..events.aliases import gps_to_utc

    rows = s.execute(
        select(Paper.arxiv_id, Paper.title, Paper.submitted_at, Paper.citation_count)
        .join(Mention, Mention.paper_id == Paper.id)
        .where(Mention.event_id == event.id,
               Mention.analysis_run_id == (run.id if run else None),
               Paper.submitted_at.is_not(None))
        .group_by(Paper.id)).all()
    if len(rows) < 3:
        return {"empty": True}

    # Sampling must not distort the time axis. Taking simply the most-cited
    # N drops recent low-citation papers wholesale -- on GW170817 that removed
    # every one of its ~300 zero-citation papers and made recent activity look
    # like it had stopped. Keep the most-cited (they carry the labels) and add
    # an evenly spaced sample of the rest in date order.
    by_cites = sorted(rows, key=lambda r: -(r[3] or 0))
    total = len(rows)
    if total > MAX_POINTS:
        keep = by_cites[:KEEP_TOP]
        rest = sorted(by_cites[KEEP_TOP:], key=lambda r: r[2])
        budget = max(MAX_POINTS - KEEP_TOP, 1)
        if len(rest) > budget:
            # Evenly spaced indices rather than a slice stride. An integer
            # stride either overshoots the budget -- and a trailing cut then
            # deletes the most recent papers, which erased 2026 entirely -- or
            # undershoots it badly: 673 papers became 397 points while the
            # caption still claimed 600. Interpolating hits the budget and
            # keeps both endpoints.
            idx = sorted({round(i * (len(rest) - 1) / (budget - 1))
                          for i in range(budget)})
            rest = [rest[i] for i in idx]
        rows = sorted(keep + rest, key=lambda r: -(r[3] or 0))
    else:
        rows = by_cites
    truncated = total - len(rows)

    event_dt = gps_to_utc(view.gps) if (view and view.gps) else None
    dates = [r[2] for r in rows] + ([event_dt] if event_dt else [])
    lo_d, hi_d = min(dates), max(dates)
    span = max((hi_d - lo_d).days, 1)
    pad = max(span * 0.03, 30)

    w = TIMELINE["w"] - TIMELINE["l"] - TIMELINE["r"]
    h = TIMELINE["h"] - TIMELINE["t"] - TIMELINE["b"]
    plot_h = h - ZERO_BAND

    xa = Axis(-pad, span + pad)

    def xpx(d):
        return xa.px((d - lo_d).days, w)

    cites = [c or 0 for *_, c in rows]
    top = max(cites) or 1
    ya = Axis(1, max(top * 1.3, 2), log=True)

    ya.ticks = [(v, ya.px(v, plot_h, flip=True), _fmt(v))
                for v in _nice_log_ticks(1, ya.hi)]
    ya.ticks.append((0, h - 7, "0"))

    years = sorted({d.year for d in dates})
    xa.ticks = []
    for y in years:
        d = datetime(y, 1, 1)
        if lo_d <= d <= hi_d:
            xa.ticks.append((y, xpx(d), str(y)))
    if not xa.ticks:
        xa.ticks = [(lo_d.year, xpx(lo_d), str(lo_d.year))]

    points = []
    for arxiv, title, dt, c in rows:
        c = c or 0
        points.append({
            "x": round(xpx(dt), 2),
            "y": round(h - 7 if c == 0 else ya.px(c, plot_h, flip=True), 2),
            "r": 3.2 if c < 50 else (4.6 if c < 500 else 6.2),
            "arxiv": arxiv, "title": title,
            "date": dt.strftime("%Y-%m"), "cites": c,
            "before": bool(event_dt and dt < event_dt),
            "label": None,
        })
    for p in points[:3]:
        p["label"] = p["title"][:34] + ("…" if len(p["title"]) > 34 else "")
    place_labels(points, w, h)

    marker = None
    if event_dt:
        marker = {"x": round(xpx(event_dt), 2), "label": event_dt.strftime("%Y-%m-%d")}

    return {
        "empty": False, "points": points, "xa": xa, "ya": ya, "w": w, "h": h,
        "box": TIMELINE, "marker": marker, "n": len(points),
        "truncated": truncated, "zero_rule": round(h - ZERO_BAND + 4, 2),
        "n_zero": sum(1 for p in points if p["cites"] == 0),
        "n_before": sum(1 for p in points if p["before"]),
    }
