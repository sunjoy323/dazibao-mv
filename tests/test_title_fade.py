"""Title auto-duration and fade-out."""

from dazibao_mv.render import compute_title_dur, title_fade_alpha
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
