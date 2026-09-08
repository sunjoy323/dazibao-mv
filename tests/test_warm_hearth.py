"""warm-hearth: cozy healing classic kinetic (soft punch, no giant_char)."""

from dazibao_mv.styles import list_builtin_styles, load_style
from dazibao_mv.timeline import layouts_for_style


def test_warm_hearth_in_builtins():
    assert "warm-hearth" in list_builtin_styles()


def test_warm_hearth_soft_punch_no_giant_char():
    style = load_style("warm-hearth")
    assert style["name"] == "warm-hearth"
    assert style["punch"]["kind"] == "soft"
    assert 1.0 < style["punch"]["amount"] <= 1.2
    assert style.get("gap_mode") == "hold"
    assert not style.get("mode")
    assert style.get("decor") is False
    pool = layouts_for_style(style)
    assert "giant_char" not in pool
    assert set(pool) == {"left_stack", "top_heavy", "center_slam", "bottom_banner"}
    assert style["bg"] == (246, 237, 227)
    assert style["title"]["fill"] == (74, 58, 50)
