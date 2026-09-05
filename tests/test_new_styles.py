"""New builtin styles: neon-cyber, blueprint, pop-comic, ink-wash."""

from PIL import Image

from dazibao_mv.render import draw_layout, draw_styled_text, _style_mode
from dazibao_mv.styles import (
    is_poster_fill,
    list_builtin_styles,
    load_style,
    style_bg_hex,
)


NEW = ("neon-cyber", "blueprint", "pop-comic", "ink-wash")
MODES = {
    "neon-cyber": "neon",
    "blueprint": "blueprint",
    "pop-comic": "comic",
    "ink-wash": "ink",
}


def test_new_styles_in_builtins_and_load():
    names = list_builtin_styles()
    for n in NEW:
        assert n in names, f"missing builtin {n}"
        style = load_style(n)
        assert style["name"] == n
        assert style["mode"] == MODES[n]
        assert not is_poster_fill(style)
        assert style.get("bg_color", "").startswith("#")
        assert style_bg_hex(style) != "#141210" or n == "never"


def test_poster_wall_still_poster_fill():
    style = load_style("poster-wall")
    assert is_poster_fill(style)
    assert style["mode"] == "poster_fill"


def test_mode_draw_layout_smoke():
    """Each new mode paints a frame without crashing; pixels change from bg."""
    W, H = 540, 960  # smaller for speed
    for name in NEW:
        style = load_style(name)
        bg_hex = style_bg_hex(style)
        from dazibao_mv.bg import parse_hex_color

        bg_rgb = parse_hex_color(bg_hex)
        bg = Image.new("RGB", (W, H), bg_rgb)
        im = draw_layout(
            bg,
            ["长", "得", "好", "笑"],
            "长得好笑",
            "center_slam",
            t_local=0.05,
            style=style,
            width=W,
            height=H,
            line_index=1,
            decor=bool(style.get("decor", False)),
            hook=True,
        )
        assert im.size == (W, H)
        px = im.load()
        # find some non-bg pixel
        found = False
        for y in range(H // 4, 3 * H // 4, 6):
            for x in range(W // 6, 5 * W // 6, 6):
                if px[x, y][:3] != bg_rgb:
                    found = True
                    break
            if found:
                break
        assert found, f"{name}: expected painted pixels vs bg {bg_rgb}"


def test_style_mode_helper():
    assert _style_mode(load_style("neon-cyber")) == "neon"
    assert _style_mode(load_style("pop-comic")) == "comic"
