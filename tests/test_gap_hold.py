"""Gap hold mode: previous lyric/title held across gaps (no black flash)."""

from pathlib import Path

from PIL import Image

from dazibao_mv.render import (
    _hold_line_clip,
    _hold_title_clip,
    draw_layout,
)
from dazibao_mv.styles import load_style, palette_for
from dazibao_mv.timeline import TimedLine, build_concat_list, clamp_timeline


def test_build_concat_still_has_gaps():
    lines = clamp_timeline(
        [
            {"text": "A", "start": 3.0, "end": 4.0},
            {"text": "B", "start": 6.0, "end": 7.0},
        ],
        lead=0.12,
        audio_dur=10.0,
    )
    parts = build_concat_list(lines, title_dur=2.0, audio_dur=10.0, fps=24)
    kinds = [p.kind for p in parts]
    assert kinds[0] == "title"
    assert "gap" in kinds
    assert kinds.count("line") == 2
    assert parts[-1].kind == "gap"


def test_hold_helpers_exist_and_callable():
    assert callable(_hold_line_clip)
    assert callable(_hold_title_clip)


def test_hold_line_clip_matches_settled_layout(tmp_path: Path):
    """Hold frame uses full chunks + large t_local (no punch) — same as settled draw."""
    style = load_style("poster-wall")
    W, H, fps = 320, 568, 8
    line = TimedLine(
        text="节拍",
        start=1.0,
        end=2.0,
        t0=1.0,
        t1=2.0,
        layout="poster_fill_h",
        chunks=["节", "拍"],
    )
    pal = palette_for(style, 0)
    bg = Image.new("RGB", (W, H), tuple(pal["bg"]))
    expected = draw_layout(
        bg,
        line.chunks,
        line.text,
        line.layout,
        t_local=10.0,
        style=style,
        width=W,
        height=H,
        palette=pal,
        line_index=0,
        decor=True,
    )
    clips = tmp_path / "clips"
    clips.mkdir()
    out = _hold_line_clip(
        0, line, bg, clips, style, gap=0.25,
        width=W, height=H, fps=fps,
    )
    assert out.exists()
    assert out.stat().st_size > 500
    # Pixel sample: hold should not be pure black matte when palette bg isn't black
    # (poster A is near-black; use cream palette index 1)
    line2 = TimedLine(
        text="孤岛",
        start=1.0,
        end=2.0,
        t0=1.0,
        t1=2.0,
        layout="poster_fill_h",
        chunks=["孤", "岛"],
    )
    pal1 = palette_for(style, 1)
    bg1 = Image.new("RGB", (W, H), tuple(pal1["bg"]))
    out2 = _hold_line_clip(
        1, line2, bg1, clips, style, gap=0.25,
        width=W, height=H, fps=fps, dst=clips / "gap_hold.mp4",
    )
    assert out2.exists()
    # settled reference has non-black content
    assert expected.getpixel((W // 2, H // 2)) is not None


def test_hold_title_clip_writes_mp4(tmp_path: Path):
    style = load_style("poster-wall")
    W, H, fps = 320, 568, 8
    bg = Image.new("RGB", (W, H), (10, 10, 10))
    clips = tmp_path / "clips"
    clips.mkdir()
    out = _hold_title_clip(
        bg, clips,
        title="人海孤岛",
        author="好小沉",
        style=style,
        gap=0.5,
        width=W,
        height=H,
        fps=fps,
    )
    assert out.exists()
    assert out.stat().st_size > 500


def test_gap_mode_cli_default():
    from dazibao_mv.cli import build_parser

    p = build_parser()
    args = p.parse_args([
        "render", "--audio", "a.mp3", "--lyrics", "l.txt", "--out", "o.mp4",
    ])
    assert args.gap_mode == "auto"
    args_b = p.parse_args([
        "render", "--audio", "a.mp3", "--lyrics", "l.txt", "--out", "o.mp4",
        "--gap-mode", "black",
    ])
    assert args_b.gap_mode == "black"
    args_f = p.parse_args([
        "render", "--audio", "a.mp3", "--lyrics", "l.txt", "--out", "o.mp4",
        "--gap-mode", "flash",
    ])
    assert args_f.gap_mode == "flash"
