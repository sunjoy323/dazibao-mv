"""Word-aware split spans + chunk tail hold so last glyphs aren't swallowed."""

from dazibao_mv.align import match_lyrics_to_cues, min_piece_duration, piece_spans_from_words
from dazibao_mv.split import glyph_chunks
from dazibao_mv.timeline import TimedLine, assign_chunk_times, assign_chunk_times_rhythm


def _nanqiang_src2_cue():
    """One ASR cue covering both halves; word stamps for the first clause."""
    words = [
        {"word": "所有", "start": 13.58, "end": 14.46},
        {"word": "人", "start": 14.46, "end": 15.02},
        {"word": "排", "start": 15.02, "end": 15.30},
        {"word": "着", "start": 15.30, "end": 15.46},
        {"word": "齐", "start": 15.46, "end": 15.90},
        {"word": "整", "start": 15.90, "end": 16.24},
        {"word": "的", "start": 16.24, "end": 16.56},
        {"word": "队，", "start": 16.56, "end": 16.88},
        # second clause present in cue text/words so piece1 can match
        {"word": "等", "start": 16.88, "end": 17.10},
        {"word": "着", "start": 17.10, "end": 17.28},
        {"word": "被", "start": 17.28, "end": 17.50},
        {"word": "推", "start": 17.50, "end": 17.80},
        {"word": "搡", "start": 17.80, "end": 18.10},
        {"word": "入", "start": 18.10, "end": 18.35},
        {"word": "海", "start": 18.35, "end": 18.70},
    ]
    return {
        "start": 13.58,
        "end": 18.70,
        "text": "所有人排着齐整的队，等着被推搡入海",
        "words": words,
    }


def test_piece0_end_tracks_last_word_of_dui():
    """After match+split, piece0.end >= last word of 队; duration allows ≥0.3s tail hold."""
    lyrics = ["所有人排着齐整的队，等着被推搡入海"]
    aligned = match_lyrics_to_cues(lyrics, [_nanqiang_src2_cue()], max_chars=9)
    assert len(aligned) >= 2
    piece0 = aligned[0]
    assert "队" in piece0["text"]
    # last word of 队 is at 16.88
    assert piece0["end"] >= 16.88 - 1e-6, piece0
    assert piece0["end"] - piece0["start"] >= 0.55
    # last glyph hold budget: end - last word start of 队 (16.56) 
    assert piece0["end"] - 16.56 >= 0.3 - 1e-6, piece0
    piece1 = aligned[1]
    assert piece1["start"] >= piece0["end"] - 1e-6
    assert piece1["end"] - piece1["start"] >= 0.55


def test_min_piece_duration_formula():
    assert min_piece_duration("路旁") >= 0.55
    # 9 content glyphs → max(0.55, 0.12*9+0.35)=1.43
    assert abs(min_piece_duration("所有人排着齐整的队") - 1.43) < 1e-6


def test_short_middle_piece_gets_min_duration():
    """Tiny middle piece like 路旁 must not stay at ~0.39s."""
    words = [
        {"word": "我", "start": 59.2, "end": 59.54},
        {"word": "把", "start": 59.54, "end": 59.66},
        {"word": "生", "start": 59.66, "end": 60.12},
        {"word": "锈", "start": 60.12, "end": 60.42},
        {"word": "的", "start": 60.42, "end": 60.78},
        {"word": "罗", "start": 60.78, "end": 60.96},
        {"word": "盘", "start": 60.96, "end": 61.10},
        {"word": "摔", "start": 61.10, "end": 61.38},
        {"word": "在", "start": 61.38, "end": 61.64},
        {"word": "路", "start": 61.64, "end": 61.96},
        {"word": "旁，", "start": 61.96, "end": 62.70},
        {"word": "任", "start": 62.70, "end": 62.95},
        {"word": "由", "start": 62.95, "end": 63.15},
        {"word": "它", "start": 63.15, "end": 63.35},
        {"word": "指", "start": 63.35, "end": 63.55},
        {"word": "针", "start": 63.55, "end": 63.80},
        {"word": "断", "start": 63.80, "end": 64.10},
        {"word": "裂", "start": 64.10, "end": 64.40},
    ]
    cue = {
        "start": 59.2,
        "end": 64.40,
        "text": "我把生锈的罗盘摔在路旁，任由它指针断裂",
        "words": words,
    }
    lyrics = ["我把生锈的罗盘摔在路旁，任由它指针断裂"]
    aligned = match_lyrics_to_cues(lyrics, [cue], max_chars=9)
    lupang = [a for a in aligned if a["text"] == "路旁"]
    assert lupang, aligned
    dur = lupang[0]["end"] - lupang[0]["start"]
    assert dur >= 0.55 - 1e-6, lupang[0]
    # word-aware: should cover 路…旁
    assert lupang[0]["start"] <= 61.64 + 0.05
    assert lupang[0]["end"] >= 62.70 - 0.05


def test_rhythm_last_chunk_keeps_tail_hold():
    words = [
        {"word": "所", "start": 10.0, "end": 10.2},
        {"word": "有", "start": 10.2, "end": 10.4},
        {"word": "人", "start": 10.4, "end": 10.6},
        {"word": "排", "start": 10.6, "end": 10.8},
        {"word": "着", "start": 10.8, "end": 11.0},
        {"word": "齐", "start": 11.0, "end": 11.2},
        {"word": "整", "start": 11.2, "end": 11.4},
        {"word": "的", "start": 11.4, "end": 11.6},
        {"word": "队", "start": 11.6, "end": 11.95},
    ]
    # Tight window where last word would sit at t1 without hold
    L = TimedLine(text="所有人排着齐整的队", start=10.0, end=12.0, t0=10.0, t1=12.0)
    L.chunks = glyph_chunks(L.text) or [L.text]
    L.extra["words"] = words
    assign_chunk_times_rhythm([L], min_tail_hold=0.3)
    assert L.chunk_times[-1] <= L.t1 - 0.3 + 1e-6, L.chunk_times


def test_uniform_last_chunk_keeps_tail_hold():
    L = TimedLine(text="一二三四五六七八", start=1.0, end=1.8, t0=1.0, t1=1.8)
    L.chunks = list("一二三四五六七八")
    assign_chunk_times([L], min_tail_hold=0.3)
    assert L.chunk_times[-1] <= L.t1 - 0.3 + 1e-6, L.chunk_times


def test_piece_spans_from_words_none_without_words():
    pieces = ["甲句", "乙句"]
    assert piece_spans_from_words(pieces, {"start": 0, "end": 2}, parent_start=0.0, parent_end=2.0) is None


def test_piece0_when_second_half_words_missing():
    """ASR cue ends at 队; second piece still placed after word end with min dur."""
    words = [
        {"word": "所有", "start": 13.58, "end": 14.46},
        {"word": "人", "start": 14.46, "end": 15.02},
        {"word": "排", "start": 15.02, "end": 15.30},
        {"word": "着", "start": 15.30, "end": 15.46},
        {"word": "齐", "start": 15.46, "end": 15.90},
        {"word": "整", "start": 15.90, "end": 16.24},
        {"word": "的", "start": 16.24, "end": 16.56},
        {"word": "队", "start": 16.56, "end": 16.88},
    ]
    cue = {
        "start": 13.58,
        "end": 16.88,
        "text": "所有人排着齐整的队等着被推搡入海",
        "words": words,
    }
    aligned = match_lyrics_to_cues(
        ["所有人排着齐整的队，等着被推搡入海"], [cue], max_chars=9
    )
    assert len(aligned) >= 2
    assert aligned[0]["end"] >= 16.88 - 1e-6
    assert aligned[1]["start"] >= aligned[0]["end"] - 1e-6
    assert aligned[1]["end"] - aligned[1]["start"] >= 0.55 - 1e-6


def test_space_split_multi_piece_min_hold():
    """Space-split pair: first phrase holds ≥1.8s (multi-piece floor)."""
    words = [
        {"word": "谈", "start": 10.0, "end": 10.4},
        {"word": "论", "start": 10.4, "end": 10.7},
        {"word": "千", "start": 10.7, "end": 11.0},
        {"word": "秋", "start": 11.0, "end": 11.3},
        {"word": "谈", "start": 11.3, "end": 11.6},
        {"word": "论", "start": 11.6, "end": 11.9},
        {"word": "前", "start": 11.9, "end": 12.2},
        {"word": "朝", "start": 12.2, "end": 12.5},
        {"word": "风", "start": 12.5, "end": 12.8},
        {"word": "雅", "start": 12.8, "end": 13.2},
    ]
    cue = {
        "start": 10.0,
        "end": 13.2,
        "text": "谈论千秋 谈论前朝风雅",
        "words": words,
    }
    aligned = match_lyrics_to_cues(
        ["谈论千秋 谈论前朝风雅"], [cue], max_chars=14
    )
    assert [a["text"] for a in aligned[:2]] == ["谈论千秋", "谈论前朝风雅"], aligned
    first, second = aligned[0], aligned[1]
    assert first["end"] - first["start"] >= 1.8 - 1e-6, first
    assert second["end"] - second["start"] >= 1.8 - 1e-6, second
    assert first["end"] >= 11.3 - 1e-6  # holds through last word of 秋
    assert second["start"] >= first["end"] - 1e-6


def test_min_piece_duration_multi_piece_floor():
    assert min_piece_duration("谈论千秋", multi_piece=True) >= 1.8
    assert min_piece_duration("路旁") < 1.8
