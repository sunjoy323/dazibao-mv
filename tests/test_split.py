"""Tests for lyric splitting + orphan merge."""

from dazibao_mv.split import split_line, split_lyrics, _merge_orphans


def test_short_line_unchanged():
    assert split_line("清晨闹钟", max_chars=9) == ["清晨闹钟"]


def test_punctuation_split():
    parts = split_line("低头系紧鞋带，腰背发出钝响", max_chars=9)
    assert len(parts) >= 2
    assert all("，" not in p and "," not in p for p in parts)


def test_max_chars_respected_mostly():
    # long run without punctuation
    text = "一二三四五六七八九十十一十二十三"
    parts = split_line(text, max_chars=9)
    assert parts
    # after orphan merge, pieces may be up to max_chars+1
    assert all(len(p) <= 10 for p in parts)


def test_orphan_merge_blocked_when_too_long():
    # 9 + 2 = 11 > max+1 (10) → do not merge
    chunks = ["ABCDEFGHI", "JK"]
    assert _merge_orphans(chunks, max_chars=9) == ["ABCDEFGHI", "JK"]


def test_orphan_merge_when_fits():
    # 8 + 2 = 10 <= max+1 → merge
    chunks = ["ABCDEFGH", "IJ"]
    assert _merge_orphans(chunks, max_chars=9) == ["ABCDEFGHIJ"]


def test_orphan_merge_leq_two():
    chunks = ["ABCDEFG", "X"]  # 7+1=8
    assert _merge_orphans(chunks, max_chars=9) == ["ABCDEFGX"]


def test_split_lyrics_skips_blank_and_comment():
    lines = ["# comment", "", "第一句歌词", "第二句，带逗号的长句子内容啊啊"]
    out = split_lyrics(lines, max_chars=9)
    assert out[0] == "第一句歌词"
    assert all(not x.startswith("#") for x in out)


def test_empty():
    assert split_line("") == []
    assert split_line("   ") == []


def test_space_split_two_phrases():
    assert split_line("谈论千秋 谈论前朝风雅", max_chars=14) == ["谈论千秋", "谈论前朝风雅"]


def test_fullwidth_space_split():
    assert split_line("谈论千秋\u3000谈论前朝风雅", max_chars=14) == ["谈论千秋", "谈论前朝风雅"]


def test_four_char_alone():
    assert split_line("削肉成纸", max_chars=14) == ["削肉成纸"]
