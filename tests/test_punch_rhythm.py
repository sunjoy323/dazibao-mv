"""Tests for rhythm punch mode from word timestamps."""

from dazibao_mv.render import compute_punch_state, prepare_lines
from dazibao_mv.styles import load_style
from dazibao_mv.timeline import (
    TimedLine,
    assign_chunk_times,
    assign_chunk_times_rhythm,
)
from dazibao_mv.split import glyph_chunks


def _line_with_words(text, t0, t1, words):
    L = TimedLine(text=text, start=t0, end=t1, t0=t0, t1=t1)
    L.chunks = glyph_chunks(text) or [text]
    L.extra["words"] = words
    return L


def test_uniform_default_unchanged():
    L = TimedLine(text="撞南墙", start=1.0, end=3.0, t0=1.0, t1=3.0)
    L.chunks = glyph_chunks(L.text)
    assign_chunk_times([L])
    assert len(L.chunk_times) == len(L.chunks)
    # uniform: evenly spaced within reveal window
    assert L.chunk_times[0] == L.t0
    assert L.chunk_times[-1] < L.t1


def test_rhythm_maps_to_word_starts():
    words = [
        {"word": "老", "start": 10.0, "end": 10.25},
        {"word": "子", "start": 10.25, "end": 10.4},
        {"word": "偏", "start": 10.55, "end": 10.9},
        {"word": "要", "start": 10.9, "end": 11.05},
        {"word": "撞", "start": 11.2, "end": 11.7},
        {"word": "南", "start": 11.7, "end": 11.95},
        {"word": "墙", "start": 11.95, "end": 12.3},
    ]
    L = _line_with_words("老子偏要撞南墙", 10.0, 12.5, words)
    assign_chunk_times_rhythm([L])
    assert len(L.chunk_times) == len(L.chunks)
    # first chunk near first word
    assert abs(L.chunk_times[0] - 10.0) < 0.05
    # times monotonic and within [t0,t1]
    for i, ct in enumerate(L.chunk_times):
        assert L.t0 - 1e-6 <= ct <= L.t1 + 1e-6
        if i:
            assert ct >= L.chunk_times[i - 1] - 1e-9
    punches = L.extra.get("chunk_punch")
    assert punches is not None
    assert len(punches) == len(L.chunks)
    assert all(0.7 - 1e-6 <= p <= 1.4 + 1e-6 for p in punches)
    # longer / gappier "偏" or "撞" should be heavier than short connectors
    assert max(punches) >= min(punches)


def test_rhythm_fallback_without_words():
    L = TimedLine(text="撞南墙", start=1.0, end=3.0, t0=1.0, t1=3.0)
    L.chunks = glyph_chunks(L.text)
    assign_chunk_times_rhythm([L])
    # falls back to uniform-like times
    assert len(L.chunk_times) == len(L.chunks)
    assert L.extra.get("chunk_punch") == [1.0] * len(L.chunks)
    assert L.extra.get("punch_fallback") == "uniform"


def test_compute_punch_intensity_scales():
    style = load_style("dazibao-ivory")
    base, kind, _ = compute_punch_state(0.0, style, intensity=1.0)
    heavy, _, _ = compute_punch_state(0.0, style, intensity=1.4)
    light, _, _ = compute_punch_state(0.0, style, intensity=0.7)
    assert kind == "scale"
    assert heavy > base > 1.0
    assert light < base
    assert light >= 1.0


def test_prepare_lines_rhythm_mode():
    style = load_style("poster-wall")
    aligned = [
        {
            "text": "撞南墙",
            "start": 1.0,
            "end": 2.5,
            "words": [
                {"word": "撞", "start": 1.1, "end": 1.6},
                {"word": "南", "start": 1.6, "end": 1.9},
                {"word": "墙", "start": 1.9, "end": 2.4},
            ],
        }
    ]
    lines = prepare_lines(aligned, style, lead=0.0, audio_dur=10.0, punch_mode="rhythm")
    assert lines[0].extra.get("chunk_punch")
    assert len(lines[0].chunk_times) == len(lines[0].chunks)


def test_prepare_lines_uniform_default():
    style = load_style("poster-wall")
    aligned = [{"text": "撞南墙", "start": 1.0, "end": 2.5}]
    lines = prepare_lines(aligned, style, lead=0.0, audio_dur=10.0)
    assert "chunk_punch" not in lines[0].extra or lines[0].extra.get("punch_mode") != "rhythm"


def test_last_punch_before_singing_end():
    """Long line / short window: last glyph punch finishes by last word end."""
    from dazibao_mv.timeline import PUNCH_COMPLETE_SEC

    words = [
        {"word": ch, "start": 1.0 + i * 0.08, "end": 1.0 + i * 0.08 + 0.07}
        for i, ch in enumerate("一二三四五六七八九十")
    ]
    # Screen holds past singing, but punch must finish by last word end (~1.79)
    L = TimedLine(text="一二三四五六七八九十", start=1.0, end=2.5, t0=1.0, t1=2.5)
    L.chunks = list("一二三四五六七八九十")
    L.extra["words"] = words
    assign_chunk_times([L], min_tail_hold=0.35)
    last_word_end = words[-1]["end"]
    assert L.chunk_times[-1] + PUNCH_COMPLETE_SEC <= last_word_end + 1e-6, (
        L.chunk_times[-1], last_word_end
    )
