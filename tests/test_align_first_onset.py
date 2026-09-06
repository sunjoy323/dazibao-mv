"""First-lyric onset recovery from Whisper intro-bleed word blobs."""

from dazibao_mv.align import (
    first_lyric_onset_from_words,
    lyric_subspan_from_words,
    match_lyrics_to_cues,
    split_asr_cues,
)


def _baiyueguang_bleed_cue():
    """Synthetic full-song Whisper merge: verse1 as one cue with intro bleed."""
    words = [
        {"start": 15.16, "end": 16.38, "word": "路"},  # 1.22s intro bleed
        {"start": 16.38, "end": 17.60, "word": "边"},  # also long
        {"start": 17.60, "end": 17.90, "word": "摊"},
        {"start": 17.90, "end": 18.10, "word": "的"},
        {"start": 18.10, "end": 18.40, "word": "油"},
        {"start": 18.40, "end": 19.10, "word": "烟"},
        {"start": 19.10, "end": 19.15, "word": "，"},
        {"start": 19.10, "end": 19.40, "word": "熏"},
        {"start": 19.40, "end": 19.70, "word": "黄"},
        {"start": 19.70, "end": 19.90, "word": "了"},
        {"start": 19.90, "end": 20.20, "word": "夹"},
        {"start": 20.20, "end": 20.50, "word": "克"},
        {"start": 20.50, "end": 20.80, "word": "领"},
        {"start": 20.80, "end": 23.50, "word": "口"},
    ]
    return {
        "start": 15.16,
        "end": 23.5,
        "text": "路边摊的油烟，熏黄了夹克领口",
        "words": words,
    }


def test_first_lyric_onset_skips_1_22s_bleed():
    cue = _baiyueguang_bleed_cue()
    onset = first_lyric_onset_from_words(cue)
    assert onset is not None
    assert 16.5 <= onset <= 17.8, onset


def test_split_asr_cues_uses_word_boundaries_on_punct():
    cue = _baiyueguang_bleed_cue()
    out = split_asr_cues([cue])
    assert len(out) == 2
    assert out[0]["text"] == "路边摊的油烟"
    assert out[1]["text"] == "熏黄了夹克领口"
    assert abs(out[0]["end"] - 19.1) < 0.05
    assert abs(out[1]["start"] - 19.1) < 0.05
    assert out[0].get("words") and out[0]["words"][0]["word"] == "路"
    assert out[1].get("words") and out[1]["words"][0]["word"] == "熏"


def test_lyric_subspan_maps_first_phrase():
    cue = _baiyueguang_bleed_cue()
    span = lyric_subspan_from_words("路边摊的油烟", cue)
    assert span is not None
    st, en = span
    assert abs(st - 15.16) < 0.05
    assert abs(en - 19.1) < 0.05


def test_match_first_line_not_late_after_bleed():
    """Regression: first display line must start ~17s, not >=19."""
    cue = _baiyueguang_bleed_cue()
    lyrics = ["路边摊的油烟，熏黄了夹克领口", "路灯把被生活压弯的影子，拉得像条老狗"]
    aligned = match_lyrics_to_cues(lyrics, [cue], max_chars=9, max_line_sec=5.5)
    assert aligned, aligned
    assert aligned[0]["text"] == "路边摊的油烟"
    assert 16.5 <= aligned[0]["start"] <= 17.8, aligned[0]
    assert aligned[0]["start"] < 19.0


def test_match_first_line_with_split_lyrics():
    cue = _baiyueguang_bleed_cue()
    lyrics = ["路边摊的油烟", "熏黄了夹克领口"]
    aligned = match_lyrics_to_cues(lyrics, [cue], max_chars=9, max_line_sec=5.5)
    assert 16.5 <= aligned[0]["start"] <= 17.8, aligned[0]
    assert aligned[1]["start"] >= 18.5
    assert aligned[1]["text"] == "熏黄了夹克领口"


def test_end_anchor_floor_raised_past_15():
    """Overlong blob starting at 15.16 (no words) should end-anchor / cap."""
    lyrics = ["路边摊的油烟"]
    cues = [{"start": 15.16, "end": 23.5, "text": "路边摊的油烟"}]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=9, max_line_sec=5.5)
    assert aligned[0]["end"] <= 23.55
    assert aligned[0]["start"] >= 15.0
    assert aligned[0]["end"] - aligned[0]["start"] <= 5.6
