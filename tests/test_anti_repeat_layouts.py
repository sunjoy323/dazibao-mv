"""Anti-repeat layout assignment for style + poster pools."""

from dazibao_mv.styles import load_style
from dazibao_mv.timeline import (
    TimedLine,
    assign_poster_layouts,
    assign_style_layouts,
)


def _assert_no_triple(layouts):
    for i in range(len(layouts) - 2):
        assert not (layouts[i] == layouts[i + 1] == layouts[i + 2]), layouts


def _assert_no_dup_without_split(lines):
    for i in range(1, len(lines)):
        a, b = lines[i - 1], lines[i]
        if a.layout == b.layout:
            sa = a.extra.get("split_group")
            sb = b.extra.get("split_group")
            assert sa is not None and sa == sb, (
                f"identical layouts without split_group at {i}: {a.layout}"
            )


def test_style_layouts_never_three_identical_in_a_row():
    for name in ("neon-cyber", "blueprint", "pop-comic", "ink-wash"):
        style = load_style(name)
        lines = [
            TimedLine(text=f"行{i}字很多", start=float(i), end=float(i) + 1,
                      chorus=(i % 3 == 0), hook=(i % 5 == 0))
            for i in range(24)
        ]
        assign_style_layouts(lines, style)
        layouts = [L.layout for L in lines]
        _assert_no_triple(layouts)
        _assert_no_dup_without_split(lines)


def test_no_consecutive_dup_without_split_group():
    style = load_style("neon-cyber")
    lines = [TimedLine(text=f"独立{i}", start=float(i), end=float(i) + 1) for i in range(12)]
    assign_style_layouts(lines, style)
    for i in range(1, len(lines)):
        assert lines[i].layout != lines[i - 1].layout


def test_split_group_allows_exactly_two_then_must_change():
    style = load_style("blueprint")
    lines = [
        TimedLine(text="前半", start=0, end=1, extra={"split_group": "sg-a"}),
        TimedLine(text="后半", start=1, end=2, extra={"split_group": "sg-a"}),
        TimedLine(text="后半续", start=2, end=3, extra={"split_group": "sg-a"}),
        TimedLine(text="下一句", start=3, end=4),
        TimedLine(text="再一句", start=4, end=5),
    ]
    assign_style_layouts(lines, style)
    # First two may share layout (same split_group)
    assert lines[0].layout == lines[1].layout
    # Third must change (cap run at 2)
    assert lines[2].layout != lines[1].layout
    _assert_no_triple([L.layout for L in lines])
    # Independent line must differ from previous
    assert lines[3].layout != lines[2].layout


def test_different_split_groups_never_share_consecutive():
    style = load_style("pop-comic")
    lines = [
        TimedLine(text="A1", start=0, end=1, extra={"split_group": "g1"}),
        TimedLine(text="B1", start=1, end=2, extra={"split_group": "g2"}),
    ]
    assign_style_layouts(lines, style)
    assert lines[0].layout != lines[1].layout


def test_poster_never_three_identical_in_a_row():
    # Many short lines would all map to H without anti-repeat
    lines = [TimedLine(text="短句", start=float(i), end=float(i) + 1) for i in range(9)]
    assign_poster_layouts(lines)
    layouts = [L.layout for L in lines]
    _assert_no_triple(layouts)
    # Still only poster layouts
    assert set(layouts) <= {"poster_fill_h", "poster_fill_v"}


def test_punch_variant_stored():
    style = load_style("neon-cyber")
    lines = [TimedLine(text=f"x{i}", start=float(i), end=float(i) + 1) for i in range(5)]
    assign_style_layouts(lines, style)
    assert [L.extra.get("punch_variant") for L in lines] == [0, 1, 2, 0, 1]


def test_three_styles_yaml_gap_mode_hold():
    for name in ("neon-cyber", "blueprint", "pop-comic"):
        style = load_style(name)
        assert style.get("gap_mode") == "hold", name
