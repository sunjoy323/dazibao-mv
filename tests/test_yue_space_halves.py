"""Cantonese / space-half ASR lessons from wudaokou jobs."""

from __future__ import annotations

from dazibao_mv.align import (
    _normalize_whisper_language,
    force_space_split,
    match_lyrics_to_cues,
    split_asr_cues,
    whisper_transcribe,
)
from dazibao_mv.cli import build_parser


def test_normalize_whisper_language_yue():
    assert _normalize_whisper_language("yue") == "yue"
    assert _normalize_whisper_language("Cantonese") == "yue"
    assert _normalize_whisper_language("zh") == "zh"


def test_whisper_yue_kwargs(monkeypatch):
    """language=yue disables condition_on_previous_text and tries silence threshold."""
    captured = {}

    class FakeSeg:
        def __init__(self):
            self.start = 0.0
            self.end = 1.0
            self.text = "五道口"
            self.words = None

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kwargs):
            captured.update(kwargs)
            return [FakeSeg()], object()

    import types, sys

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    cues = whisper_transcribe("dummy.mp3", language="yue")
    assert cues and cues[0]["text"] == "五道口"
    assert captured["language"] == "yue"
    assert captured["condition_on_previous_text"] is False
    assert captured.get("hallucination_silence_threshold") == 2.0


def test_whisper_yue_kwargs_without_silence_threshold(monkeypatch):
    """Older faster-whisper: TypeError on hallucination_silence_threshold → retry."""
    calls = []

    class FakeSeg:
        start = 0.0
        end = 1.0
        text = "ok"
        words = None

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kwargs):
            calls.append(dict(kwargs))
            if "hallucination_silence_threshold" in kwargs:
                raise TypeError("unexpected kw")
            return [FakeSeg()], object()

    import types, sys

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    cues = whisper_transcribe("dummy.mp3", language="cantonese")
    assert cues
    assert len(calls) == 2
    assert calls[0]["condition_on_previous_text"] is False
    assert "hallucination_silence_threshold" not in calls[1]
    assert calls[1]["language"] == "yue"


def test_cli_help_mentions_language():
    parser = build_parser()
    help_align = parser.parse_args.__doc__  # noqa: not useful
    # Subparser help text
    align_p = None
    for action in parser._subparsers._group_actions:
        for name, sp in action.choices.items():
            if name == "align":
                align_p = sp
            if name == "render":
                render_p = sp
    assert align_p is not None
    align_help = align_p.format_help()
    assert "--language" in align_help
    assert "yue" in align_help.lower() or "Cantonese" in align_help or "cantonese" in align_help.lower()
    render_help = render_p.format_help()
    assert "--language" in render_help


def test_force_space_split_street_shop_halves():
    """「街局飄香嘅小店 擺住長龍賣怪甜糖」→ two timed halves."""
    words = []
    # Approximate char timings across the cue
    t = 10.0
    for ch in "街局飄香嘅小店":
        words.append({"word": ch, "start": t, "end": t + 0.3})
        t += 0.3
    # Leading space on next group (Yue ASR style)
    words.append({"word": " 擺", "start": 12.5, "end": 12.8})
    t = 12.8
    for ch in "住長龍賣怪甜糖":
        words.append({"word": ch, "start": t, "end": t + 0.28})
        t += 0.28
    cue = {
        "start": 10.0,
        "end": t,
        "text": "街局飄香嘅小店 擺住長龍賣怪甜糖",
        "words": words,
    }
    out = force_space_split([cue])
    assert len(out) == 2, out
    assert out[0]["text"] == "街局飄香嘅小店"
    assert out[1]["text"] == "擺住長龍賣怪甜糖"
    assert abs(out[0]["start"] - 10.0) < 1e-6
    assert out[1]["start"] >= 12.4

    # split_asr_cues must also force-split
    out2 = split_asr_cues([cue])
    assert len(out2) == 2
    assert out2[0]["text"] == "街局飄香嘅小店"
    assert out2[1]["text"] == "擺住長龍賣怪甜糖"


def test_space_split_halves_without_words():
    cue = {
        "start": 0.0,
        "end": 4.0,
        "text": "街局飄香嘅小店 擺住長龍賣怪甜糖",
    }
    out = split_asr_cues([cue])
    assert len(out) == 2
    assert out[0]["text"] == "街局飄香嘅小店"
    assert out[1]["text"] == "擺住長龍賣怪甜糖"
    assert out[0]["end"] <= out[1]["start"] + 1e-9


def test_half_cue_match_keeps_second_onset_without_words():
    """Long first phrase must not push the next piece past the second ASR half onset.

    Synthetic: space-split lyric + two half-cues, no usable word timestamps
    (garbled Yue). Second piece start must stay at ~second half onset.
    """
    lyrics = ["独个行过无人街道 街角飘香的小店"]
    cues = [
        {"start": 40.0, "end": 44.0, "text": "独个行过无人街道"},
        {"start": 48.46, "end": 52.0, "text": "街角飘香的小店"},
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=14, max_line_sec=5.5)
    texts = [a["text"] for a in aligned]
    assert "独个行过无人街道" in texts[0] or texts[0].startswith("独个"), aligned
    second = [a for a in aligned if "街角" in a["text"]]
    assert second, aligned
    # Must not start after the second ASR half onset (the old bug).
    assert second[0]["start"] <= 48.46 + 0.35, second[0]
    assert second[0]["start"] >= 48.46 - 0.15, second[0]


def test_force_split_then_match_garbled_words():
    """One ASR cue with space + garbled words still maps halves 1:1."""
    lyrics = ["独个行过无人街道 街角飘香的小店"]
    # Garbled Yue-like words that won't match lyric glyphs
    words = [
        {"word": "独", "start": 40.0, "end": 40.4},
        {"word": "个", "start": 40.4, "end": 40.8},
        {"word": "行", "start": 40.8, "end": 41.2},
        {"word": "过", "start": 41.2, "end": 41.6},
        {"word": "无", "start": 41.6, "end": 42.0},
        {"word": "人", "start": 42.0, "end": 42.4},
        {"word": "街", "start": 42.4, "end": 42.8},
        {"word": "道", "start": 42.8, "end": 43.2},
        {"word": " 街", "start": 48.46, "end": 48.9},
        {"word": "角", "start": 48.9, "end": 49.3},
        {"word": "飘", "start": 49.3, "end": 49.7},
        {"word": "香", "start": 49.7, "end": 50.1},
        {"word": "的", "start": 50.1, "end": 50.5},
        {"word": "小", "start": 50.5, "end": 50.9},
        {"word": "店", "start": 50.9, "end": 51.5},
    ]
    cues = [
        {
            "start": 40.0,
            "end": 51.5,
            "text": "独个行过无人街道 街角飘香的小店",
            "words": words,
        }
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=14, max_line_sec=5.5)
    second = [a for a in aligned if "街角" in a["text"]]
    assert second, aligned
    assert second[0]["start"] <= 48.46 + 0.4, second[0]
