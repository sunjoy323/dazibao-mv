from dazibao_mv.align import cap_span, expected_line_dur, match_lyrics_to_cues, split_asr_cues


def test_expected_line_dur_bounded():
    d = expected_line_dur("霓虹把黑夜照得太红")
    assert 0.9 <= d <= 5.5
    short = expected_line_dur("短")
    long = expected_line_dur("这是一句比较长的歌词用来测试时长上界是否被钳住")
    assert short <= long <= 5.5


def test_cap_span_shrinks_17s_blob():
    s, e = cap_span(3.0, 20.0, "霓虹把黑夜照得太红")  # 17s span
    assert e - s <= 5.5 + 0.01
    assert e - s < 17.0


def test_match_never_keeps_50s_line():
    lyrics = ["我像卡拍的旧时钟", "下一句很短"]
    cues = [
        {"start": 90.0, "end": 144.0, "text": "我像卡拍的旧时钟下一句很短还夹了别的"},
        {"start": 145.0, "end": 148.0, "text": "下一句很短"},
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=9, max_line_sec=5.5)
    assert aligned
    assert all(a["end"] - a["start"] <= 5.6 for a in aligned)


def test_split_asr_cues_on_punct():
    cues = [{"start": 0.0, "end": 4.0, "text": "甲句，乙句"}]
    out = split_asr_cues(cues)
    assert len(out) == 2
    assert out[0]["text"] == "甲句"
    assert out[1]["text"] == "乙句"
    assert abs(out[0]["start"] - 0.0) < 1e-6
    assert abs(out[1]["end"] - 4.0) < 1e-6


def test_first_lyric_end_anchors_overlong_early_blob():
    """Whisper intro-bleed 12.34→20 must land near vocal end, not start-cap to 16.16."""
    lyrics = ["霓虹把黑夜照得太红", "笑声从四面八方失控"]
    cues = [
        {"start": 2.38, "end": 3.66, "text": "人海孤岛"},
        {"start": 12.34, "end": 20.0, "text": "霓虹把黑夜照得太红"},
        {"start": 20.56, "end": 24.14, "text": "笑声从四面八方失控"},
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=9, max_line_sec=5.5)
    assert aligned[0]["text"] == "霓虹把黑夜照得太红"
    # end-anchored: start ≈ 20 - expected (~3.82) ≈ 16.18
    assert 15.5 <= aligned[0]["start"] <= 18.0, aligned[0]
    assert aligned[0]["end"] <= 20.05


def test_first_lyric_prefers_strong_post_intro_cue():
    """When a weak early cue and a strong ~17s cue both exist, lock to ~17s."""
    lyrics = ["霓虹把黑夜照得太红", "笑声从四面八方失控"]
    cues = [
        {"start": 2.4, "end": 3.5, "text": "人海孤岛"},
        {"start": 11.0, "end": 13.0, "text": "黑夜太红"},  # weak partial
        {"start": 16.98, "end": 20.0, "text": "霓虹把黑夜照得太红。"},
        {"start": 20.64, "end": 23.84, "text": "笑声从四面八方失控。"},
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=9, max_line_sec=5.5)
    assert 16.5 <= aligned[0]["start"] <= 18.0, aligned[0]
    assert aligned[0]["text"] == "霓虹把黑夜照得太红"
