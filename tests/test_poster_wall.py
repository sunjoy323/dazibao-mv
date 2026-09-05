"""poster-wall style: palettes, fill layouts, hard shadow."""

from PIL import Image, ImageDraw

from dazibao_mv.render import (
    draw_layout,
    hard_block_shadow_text,
    prepare_lines,
)
from dazibao_mv.styles import (
    is_poster_fill,
    list_builtin_styles,
    load_style,
    palette_for,
)
from dazibao_mv.timeline import TimedLine, assign_poster_layouts


def test_poster_wall_in_builtins_and_loads():
    names = list_builtin_styles()
    assert "poster-wall" in names
    style = load_style("poster-wall")
    assert is_poster_fill(style)
    assert style["mode"] == "poster_fill"
    assert style.get("decor") is True
    assert style["shadow_offset"] == (18, 18)
    assert len(style["palettes"]) >= 3


def test_palette_cycle_different_bg():
    style = load_style("poster-wall")
    a = palette_for(style, 0)
    b = palette_for(style, 1)
    c = palette_for(style, 2)
    assert a["bg"] != b["bg"]
    assert b["bg"] != c["bg"]
    assert a["bg"] != c["bg"]
    # wraps
    assert palette_for(style, 3)["bg"] == a["bg"]


def test_assign_poster_layouts_by_length():
    lines = [
        TimedLine(text="节拍", start=0, end=1),           # 2 → H
        TimedLine(text="要写成墙", start=1, end=2),         # 4 → H
        TimedLine(text="越热闹越寂寞", start=2, end=3),     # 6 → H
        TimedLine(text="霓虹把黑夜照得太红", start=3, end=4),  # 9 → V
        TimedLine(text="笑声失控", start=4, end=5),         # 4 → mid alt
    ]
    assign_poster_layouts(lines)
    assert lines[0].layout == "poster_fill_h"
    assert lines[1].layout == "poster_fill_h"
    # third short would make 3×H — flip to V
    assert lines[2].layout == "poster_fill_v"
    assert lines[3].layout == "poster_fill_v"
    assert lines[4].layout in ("poster_fill_h", "poster_fill_v")
    # never 3 identical
    lays = [L.layout for L in lines]
    for i in range(len(lays) - 2):
        assert not (lays[i] == lays[i + 1] == lays[i + 2])


def test_font_fit_short_string_fills_width():
    style = load_style("poster-wall")
    W, H = 1080, 1920
    bg = Image.new("RGB", (W, H), (10, 10, 10))
    pal = palette_for(style, 0)
    im = draw_layout(
        bg,
        ["节", "拍"],
        "节拍",
        "poster_fill_h",
        t_local=1.0,
        style=style,
        width=W,
        height=H,
        palette=pal,
        line_index=0,
        decor=True,
    )
    assert im.size == (W, H)
    # Non-bg pixels should cover a substantial horizontal span (fill ~width)
    px = im.load()
    bg_c = pal["bg"]
    xs = [x for x in range(0, W, 4) for y in range(H // 3, 2 * H // 3, 8)
          if px[x, y][:3] != bg_c]
    assert xs, "expected drawn pixels"
    span = max(xs) - min(xs)
    assert span >= int(W * 0.55), f"text span {span} too narrow for poster fill"


def test_hard_shadow_offset_smoke():
    im = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    from dazibao_mv.styles import resolve_font
    from dazibao_mv.render import _font

    font = _font(resolve_font(), 80)
    hard_block_shadow_text(
        draw, (40, 40), "墙", font,
        fill=(242, 237, 228, 255),
        shadow=(196, 30, 58, 255),
        offset=(18, 18),
    )
    px = im.load()
    # Shadow pixel roughly at glyph origin + offset should be red-ish
    # Sample a few candidates near offset region
    found_shadow = False
    found_fill = False
    for x in range(40, 200):
        for y in range(40, 180):
            r, g, b, a = px[x, y]
            if a < 200:
                continue
            if r > 150 and g < 80 and b < 100:
                found_shadow = True
            if r > 200 and g > 200 and b > 180:
                found_fill = True
    assert found_shadow, "hard red shadow not found"
    assert found_fill, "cream fill not found"


def test_prepare_lines_uses_poster_layouts():
    style = load_style("poster-wall")
    aligned = [
        {"text": "节拍", "start": 1.0, "end": 2.0},
        {"text": "霓虹把黑夜照得太红", "start": 2.0, "end": 4.0},
    ]
    lines = prepare_lines(aligned, style, lead=0.12, audio_dur=10.0)
    assert lines[0].layout == "poster_fill_h"
    assert lines[1].layout == "poster_fill_v"
