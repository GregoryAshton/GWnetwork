from __future__ import annotations

import math

from gwnetwork.web.charts import CHAR_W, LABEL_H, Axis, place_labels


def test_linear_axis_maps_endpoints():
    a = Axis(0, 100)
    assert a.px(0, 200) == 0
    assert a.px(100, 200) == 200
    assert a.px(50, 200) == 100


def test_flip_inverts_for_svg_y():
    a = Axis(0, 10)
    assert a.px(0, 100, flip=True) == 100      # zero at the bottom
    assert a.px(10, 100, flip=True) == 0


def test_log_axis_is_logarithmic_not_linear():
    a = Axis(1, 100, log=True)
    # A decade is half the span on a two-decade axis.
    assert math.isclose(a.px(10, 200), 100, abs_tol=1e-6)
    assert not math.isclose(a.px(10, 200), 200 * 10 / 100)


def test_degenerate_axis_does_not_divide_by_zero():
    assert Axis(5, 5).px(5, 100) == 0.0


def test_place_labels_removes_collisions():
    """Outlier labels cluster exactly where the data does."""
    pts = [{"x": 100, "y": 200 + i, "r": 6, "label": f"GW19052{i}"} for i in range(5)]
    place_labels(pts, w=760, h=460)
    ys = sorted(p["ly"] for p in pts)
    assert all(b - a >= LABEL_H - 0.01 for a, b in zip(ys, ys[1:]))


def test_place_labels_flips_at_the_right_edge():
    pts = [{"x": 755, "y": 100, "r": 6, "label": "GW231123_135430"}]
    place_labels(pts, w=760, h=460)
    p = pts[0]
    assert p["anchor"] == "end"
    assert p["lx"] - len(p["label"]) * CHAR_W > 0


def test_place_labels_stays_inside_the_plot():
    pts = [{"x": 10, "y": 455 + i, "r": 6, "label": f"E{i}"} for i in range(4)]
    place_labels(pts, w=760, h=460)
    assert all(p["ly"] <= 460 for p in pts)


def test_leader_flag_set_only_when_nudged():
    pts = [{"x": 100, "y": 100, "r": 6, "label": "A"},
           {"x": 100, "y": 101, "r": 6, "label": "B"}]
    place_labels(pts, w=760, h=460)
    assert pts[0]["leader"] is False       # sits where it wants
    assert pts[1]["leader"] is True        # pushed clear, needs a leader
