"""Title auto-duration, fade-out, and poster title-card reveal helpers."""

from dazibao_mv.render import (
    compute_title_dur,
    title_anim_phases,
    title_fade_alpha,
    title_reveal_counts,
)
from dazibao_mv.styles import load_style, is_poster_fill
from dazibao_mv.timeline import TimedLine, clamp_timeline


def test_auto_title_dur_is_first_t0_minus_one():
    lines = clamp_timeline(
        [{"text": "霓虹把黑夜照得太红", "start": 16.98, "end": 20.0}],
        lead=0.12,
        audio_dur=30.0,
    )
    # t0 = 16.98 - 0.12 = 16.86
    dur = compute_title_dur(lines, title_dur=None, title_before_lyric=1.0, min_title=0.8)
    assert abs(dur - (lines[0].t0 - 1.0)) < 1e-6
    assert dur >= 0.8


def test_explicit_title_dur_overrides_auto():
    lines = [TimedLine(text="A", start=17.0, end=20.0, t0=16.88, t1=20.0)]
    dur = compute_title_dur(lines, title_dur=2.0, title_before_lyric=1.0)
    assert dur == 2.0


def test_title_fade_alpha_full_then_drops():
    fps = 24
    title_dur = 5.0
    fade_dur = 0.8
    frames = int(round(title_dur * fps))  # 120
    # before fade: full opacity
    early = title_fade_alpha(0, frames, fade_dur, fps)
    mid = title_fade_alpha(frames // 2, frames, fade_dur, fps)
    assert early == 1.0
    assert mid == 1.0
    # last frame near 0
    last = title_fade_alpha(frames - 1, frames, fade_dur, fps)
    assert last < 0.15
    # mid-fade lower than start of fade
    fade_frames = int(round(fade_dur * fps))
    fade_start = frames - fade_frames
    a0 = title_fade_alpha(fade_start, frames, fade_dur, fps)
    a1 = title_fade_alpha(fade_start + fade_frames // 2, frames, fade_dur, fps)
    assert a0 > a1 > last


def test_title_anim_phases_long_card_caps_punch():
    """Long auto title (~18s): punch ≤2s, author ≤1s, rest hold."""
    te, ae, usable = title_anim_phases(18.0, 0.8)
    assert abs(usable - 17.2) < 1e-6
    assert te <= 2.0
    assert ae - te <= 1.0 + 1e-6
    assert te < ae < usable
    # all glyphs revealed by ~3s (title+author)
    assert ae <= 3.0 + 1e-6


def test_title_anim_phases_short_card_still_punchy():
    """Short card (5s): still punchy under caps, author after title."""
    te, ae, usable = title_anim_phases(5.0, 0.8)
    assert abs(usable - 4.2) < 1e-6
    assert te <= 2.0
    assert ae - te <= 1.0 + 1e-6
    assert te < ae <= usable
    # keep punchy (not the old 45% of usable ≈ 1.89 — still fine; just ensure order)
    assert te >= 0.35


def test_title_reveal_counts_full_by_two_seconds_long_title():
    """11-glyph title fully revealed by t=2.0 on a long title card."""
    title = "谁又不曾是别人的白月光"  # 11 glyphs
    n_title = len(title)
    assert n_title == 11
    title_dur, fade_dur = 18.0, 0.8
    te, ae, usable = title_anim_phases(title_dur, fade_dur)
    assert te <= 2.0
    n_t, n_a, _punch = title_reveal_counts(
        2.0, title_dur=title_dur, fade_dur=fade_dur, n_title=n_title, n_author=3
    )
    assert n_t == n_title
    assert n_a == 0 or ae <= 2.0  # author may start after title_end


def test_title_reveal_counts_title_then_author():
    title_dur, fade_dur = 5.0, 0.8
    n_title, n_author = 8, 3
    te, ae, usable = title_anim_phases(title_dur, fade_dur)

    # start: nothing
    assert title_reveal_counts(
        0.0, title_dur=title_dur, fade_dur=fade_dur, n_title=n_title, n_author=n_author
    ) == (0, 0, 0.0)

    # mid title phase: some glyphs, punch > 0, no author
    n_t, n_a, punch = title_reveal_counts(
        te * 0.5, title_dur=title_dur, fade_dur=fade_dur, n_title=n_title, n_author=n_author
    )
    assert 1 <= n_t < n_title
    assert n_a == 0
    assert 0.0 <= punch <= 1.0

    # end of title phase: all title glyphs
    n_t, n_a, punch = title_reveal_counts(
        te, title_dur=title_dur, fade_dur=fade_dur, n_title=n_title, n_author=n_author
    )
    assert n_t == n_title
    assert n_a == 0

    # mid author phase
    mid_a = te + (ae - te) * 0.5
    n_t, n_a, punch = title_reveal_counts(
        mid_a, title_dur=title_dur, fade_dur=fade_dur, n_title=n_title, n_author=n_author
    )
    assert n_t == n_title
    assert 1 <= n_a <= n_author
    assert punch == 0.0

    # hold / after author
    n_t, n_a, punch = title_reveal_counts(
        ae, title_dur=title_dur, fade_dur=fade_dur, n_title=n_title, n_author=n_author
    )
    assert (n_t, n_a) == (n_title, n_author)


def test_poster_wall_title_palette_is_high_contrast_a():
    style = load_style("poster-wall")
    assert is_poster_fill(style)
    assert style.get("title_palette") == 0
    assert style.get("title_author_gap") >= 120
    pals = style["palettes"]
    assert pals[0]["bg"] == (10, 10, 10)
    assert pals[0]["fill"] == (242, 237, 228)
    # author colors distinct from title fill
    assert style["title"]["author_fill"] != pals[0]["fill"]
