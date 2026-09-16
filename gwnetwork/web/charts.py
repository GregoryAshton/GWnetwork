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
    """
    labelled = sorted([p for p in points if p.get("label")], key=lambda p: p["y"])
    last_bottom = -1e9
    for p in labelled:
        y = max(p["y"] + 4, last_bottom + LABEL_H)
        y = min(y, h - 2)
        width = len(p["label"]) * CHAR_W
        if p["x"] + p["r"] + 5 + width > w:
            p["lx"], p["anchor"] = p["x"] - p["r"] - 5, "end"
        else:
            p["lx"], p["anchor"] = p["x"] + p["r"] + 5, "start"
        p["ly"] = round(y, 2)
        # A leader line keeps the association visible once a label is nudged.
        p["leader"] = abs(y - (p["y"] + 4)) > 3
        last_bottom = y


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
