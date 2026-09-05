"""Per-style motion language: layouts / punch / gap ≠ color swaps."""

from dazibao_mv.render import (
    compute_punch_state,
    prepare_lines,
    resolve_gap_mode,
)
from dazibao_mv.styles import load_style
from dazibao_mv.timeline import (
    LAYOUTS,
    MODE_LAYOUTS,
    TimedLine,
    assign_style_layouts,
    layouts_for_style,
)

NEW = ("neon-cyber", "blueprint", "pop-comic", "ink-wash")


def test_layouts_not_default_cycle():
    for name in NEW:
        style = load_style(name)
        pool = layouts_for_style(style)
        assert pool, name
        assert pool != list(LAYOUTS), f"{name} still using default LAYOUTS"
        assert set(pool).isdisjoint({"diagonal", "right_cascade", "left_stack", "center_slam"}) or name == "never"


def test_assign_style_layouts_from_yaml_pool():
    for name in NEW:
        style = load_style(name)
        pool = set(style["layouts"])
        lines = [
            TimedLine(text="钩子很长一行", start=0, end=1, hook=True, chorus=True),
            TimedLine(text="普通一句", start=1, end=2),
            TimedLine(text="再一句", start=2, end=3),
            TimedLine(text="继续", start=3, end=4, chorus=True),
        ]
        assign_style_layouts(lines, style)
        for L in lines:
            assert L.layout in pool, f"{name}: {L.layout} not in {pool}"


def test_ink_never_diagonal():
    style = load_style("ink-wash")
    lines = [TimedLine(text=f"行{i}", start=i, end=i + 1) for i in range(12)]
    assign_style_layouts(lines, style)
    for L in lines:
        assert L.layout.startswith("ink_") or L.layout == "ink_seal"
        assert L.layout not in LAYOUTS or L.layout == "never"


def test_comic_short_hook_giant_char():
    style = load_style("pop-comic")
    lines = [TimedLine(text="啊", start=0, end=1, hook=True, chorus=True)]
    assign_style_layouts(lines, style)
    assert lines[0].layout == "giant_char"


def test_punch_kinds_differ():
    neon = load_style("neon-cyber")
    blue = load_style("blueprint")
    comic = load_style("pop-comic")
    ink = load_style("ink-wash")
    sc_n, k_n, ex_n = compute_punch_state(0.0, neon)
    sc_b, k_b, ex_b = compute_punch_state(0.0, blue)
    sc_c, k_c, _ = compute_punch_state(0.0, comic)
    sc_i, k_i, _ = compute_punch_state(0.0, ink)
    assert k_n == "glitch" and sc_n >= 1.5 and "glitch_ox" in ex_n
    assert k_b == "slide" and sc_b == 1.0 and ex_b.get("slide", 0) > 0
    assert k_c == "slam" and sc_c >= 2.0
    assert k_i == "soft" and 1.0 < sc_i <= 1.15
    # settled
    assert compute_punch_state(10.0, neon)[0] == 1.0
    assert compute_punch_state(10.0, comic)[0] == 1.0


def test_gap_mode_style_defaults():
    assert resolve_gap_mode(load_style("neon-cyber"), "auto") == "hold"
    assert resolve_gap_mode(load_style("pop-comic"), None) == "hold"
    assert resolve_gap_mode(load_style("blueprint"), "auto") == "hold"
    assert resolve_gap_mode(load_style("ink-wash"), "auto") == "hold"
    assert resolve_gap_mode(load_style("dazibao-ivory"), "auto") == "hold"
    assert resolve_gap_mode(load_style("neon-cyber"), "hold") == "hold"
    assert resolve_gap_mode(load_style("neon-cyber"), "flash") == "flash"  # CLI override
    assert resolve_gap_mode(load_style("ink-wash"), "cut") == "black"


def test_prepare_lines_uses_style_layouts_and_reveal():
    style = load_style("ink-wash")
    lines = prepare_lines(
        [{"text": "宣纸上的字", "start": 0.5, "end": 2.0}],
        style,
        lead=0.12,
        audio_dur=10.0,
    )
    assert lines[0].layout in style["layouts"]
    assert style["reveal_frac"] == 0.65


def test_ivory_poster_still_default_layouts():
    ivory = load_style("dazibao-ivory")
    lines = prepare_lines(
        [{"text": "象牙测试", "start": 0.5, "end": 1.5}],
        ivory,
        lead=0.12,
        audio_dur=5.0,
    )
    assert lines[0].layout in LAYOUTS

    poster = load_style("poster-wall")
    lines2 = prepare_lines(
        [{"text": "短", "start": 0.5, "end": 1.5}],
        poster,
        lead=0.12,
        audio_dur=5.0,
    )
    assert lines2[0].layout.startswith("poster_fill")


def test_mode_layout_constants():
    assert "neon_col_left" in MODE_LAYOUTS["neon"]
    assert "blueprint_h_rule" in MODE_LAYOUTS["blueprint"]
    assert "comic_panel_full" in MODE_LAYOUTS["comic"]
    assert "ink_vertical" in MODE_LAYOUTS["ink"]
