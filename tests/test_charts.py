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


EPS = 1e-6      # label gaps land exactly on LABEL_H; compare with tolerance


def test_two_pass_placement_lifts_labels_that_overflow_the_bottom():
    """A single downward pass stacks labels on the bottom edge.

    On a short plot the clamp put several labels on the same line, which looked
    like a collision the placement had already been written to prevent.
    """
    pts = [{"x": 100, "y": 238 + i, "r": 4, "label": f"paper {i}"} for i in range(5)]
    place_labels(pts, w=760, h=244)
    ys = sorted(p["ly"] for p in pts)
    assert all(b - a >= LABEL_H - EPS for a, b in zip(ys, ys[1:])), ys
    assert all(0 <= p["ly"] <= 244 for p in pts)


def test_labels_stay_ordered_after_the_upward_pass():
    pts = [{"x": 50, "y": 240, "r": 4, "label": "a"},
           {"x": 50, "y": 241, "r": 4, "label": "b"},
           {"x": 50, "y": 242, "r": 4, "label": "c"}]
    place_labels(pts, w=760, h=244)
    assert pts[0]["ly"] < pts[1]["ly"] < pts[2]["ly"]


def test_single_label_is_not_moved():
    pts = [{"x": 100, "y": 120, "r": 4, "label": "only"}]
    place_labels(pts, w=760, h=244)
    assert pts[0]["ly"] == 124.0
    assert pts[0]["leader"] is False


def test_named_events_keep_their_colour_and_order_across_measures():
    """Colour follows the event, not its rank: the second view reuses the first's order."""
    from gwnetwork.web.charts import STACK_LIGHT, STACK_OTHER

    class Fake:
        pass
    import gwnetwork.web.charts as charts
    rows = [("A", "2016", 5), ("B", "2016", 9), ("A", "2017", 7), ("C", "2017", 1)]

    class S:
        def execute(self, *_):
            class R:
                def all(self_inner): return rows
            return R()
    out = charts.attention_over_time(S(), None, top_n=2, named=["A", "B"])
    colours = {b["name"]: b["light"] for b in out["bands"]}
    assert out["named"] == ["A", "B"]
    assert colours["A"] == STACK_LIGHT[0] and colours["B"] == STACK_LIGHT[1]
    assert colours["Other events"] == STACK_OTHER
