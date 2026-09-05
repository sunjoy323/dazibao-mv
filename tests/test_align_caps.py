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


def test_first_lyric_word_onset_skips_intro_bleed():
    """Word timestamps: skip stretched intro glyphs → onset ~17.7s."""
    lyrics = ["霓虹把黑夜照得太红", "笑声从四面八方失控"]
    cues = [
        {
            "start": 12.34,
            "end": 20.0,
            "text": "霓虹把黑夜照得太红",
            "words": [
                {"start": 12.34, "end": 12.36, "word": "霓"},
                {"start": 12.36, "end": 17.74, "word": "虹"},
                {"start": 17.74, "end": 18.0, "word": "把"},
                {"start": 18.0, "end": 18.34, "word": "黑"},
                {"start": 18.34, "end": 18.5, "word": "夜"},
                {"start": 18.5, "end": 18.76, "word": "照"},
                {"start": 18.76, "end": 18.94, "word": "得"},
                {"start": 18.94, "end": 19.5, "word": "太"},
                {"start": 19.5, "end": 20.0, "word": "红"},
            ],
        },
        {"start": 20.56, "end": 24.14, "text": "笑声从四面八方失控"},
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=9, max_line_sec=5.5)
    assert 16.5 <= aligned[0]["start"] <= 18.0, aligned[0]
    assert abs(aligned[0]["start"] - 17.74) < 0.05


def test_split_asr_cues_on_whitespace():
    cues = [{"start": 0.0, "end": 4.0, "text": "八零后的老灯 八零后的老灯"}]
    out = split_asr_cues(cues)
    assert len(out) == 2
    assert out[0]["text"] == "八零后的老灯"
    assert out[1]["text"] == "八零后的老灯"
    assert abs(out[0]["end"] - 2.0) < 1e-6


def test_repeated_chorus_does_not_skip_bridge():
    """Identical chorus lines must not jump to a later chorus over the bridge."""
    # Closing bar is one 老登 + 硬骨头; an extra lyric 老登 must not leap to chorus 3.
    lyrics = [
        "八零后的老登",
        "硬骨头敬此生",
        "二十岁想去仗剑天涯",
        "四十岁守着一锅热汤",
        "八零后的老登",
        "八零后的老登",
    ]
    cues = [
        {"start": 230.0, "end": 238.0, "text": "八零后的老灯 引骨头进此生"},
        {"start": 238.5, "end": 242.0, "text": "二十岁想去掌剑天涯"},
        {"start": 242.0, "end": 245.0, "text": "四十岁受着一锅热汤"},
        {"start": 262.0, "end": 266.0, "text": "八零后的老灯 八零后的老灯"},
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=12, max_line_sec=5.5)
    bridge = [a for a in aligned if a["text"] == "二十岁想去仗剑天涯"][0]
    assert bridge["start"] < 250.0, bridge
    assert [a for a in aligned if a["text"] == "四十岁守着一锅热汤"][0]["start"] < 255.0
    last_pair = [a for a in aligned if a["text"] == "八零后的老登"]
    assert last_pair[-1]["start"] >= 260.0, last_pair


def test_extra_repeated_lyric_synthesizes_instead_of_jumping():
    """If lyrics has an extra chorus repeat, synthesize — do not skip bridge."""
    lyrics = [
        "八零后的老登",
        "八零后的老登",  # extra vs ASR closing bar
        "硬骨头敬此生",
        "二十岁想去仗剑天涯",
    ]
    cues = [
        {"start": 230.0, "end": 238.0, "text": "八零后的老灯 引骨头进此生"},
        {"start": 238.5, "end": 242.0, "text": "二十岁想去掌剑天涯"},
        {"start": 262.0, "end": 266.0, "text": "八零后的老灯 八零后的老灯"},
    ]
    aligned = match_lyrics_to_cues(lyrics, cues, max_chars=12, max_line_sec=5.5)
    bridge = [a for a in aligned if a["text"] == "二十岁想去仗剑天涯"][0]
    assert bridge["start"] < 250.0, bridge
