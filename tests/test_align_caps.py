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
