"""Tests for lyrics cleaner: strip section tags + English production notes."""

from pathlib import Path

from dazibao_mv.split import clean_lyrics_text, load_lyrics_file


NANQIANG_RAW = Path("/workspace/dazibao-samples/out/nanqiang/lyrics_raw.txt")


SAMPLE_RAW = """\
[Intro]

[Slow acoustic guitar fingerpicking, solitary deep cello drone]

[Verse 1]

站台上立着指向正南的标牌

所有人排着齐整的队，等着被推搡入海

[Chorus]

[Massive drum crash, heavy cello swell]

这世界定下的方向，不是我认可的方向！

凭什么要我低眉顺眼，活成你们指定的模样！

[Fade Out]

[End]
"""


def test_drops_section_tags_only():
    out = clean_lyrics_text("[Intro]\n[Verse 1]\n[Chorus]\n[Fade Out]\n[End]\n")
    assert out == []


def test_drops_english_production_notes():
    out = clean_lyrics_text(
        "[Slow acoustic guitar fingerpicking, solitary deep cello drone]\n"
        "[Massive drum crash, heavy cello swell]\n"
    )
    assert out == []


def test_keeps_chinese_lyrics():
    out = clean_lyrics_text(SAMPLE_RAW)
    assert "[Intro]" not in out
    assert not any(x.startswith("[") and x.endswith("]") for x in out)
    assert "站台上立着指向正南的标牌" in out
    assert "这世界定下的方向，不是我认可的方向！" in out
    assert "凭什么要我低眉顺眼，活成你们指定的模样！" in out
    # Chinese lines preserved (including ！)
    assert any("！" in x for x in out)


def test_keeps_plain_prior_song_lines():
    """Regression: plain lyrics without tags stay intact."""
    text = "清晨闹钟\n低头系紧鞋带\n# comment\n\n人海孤岛\n"
    out = clean_lyrics_text(text)
    assert out == ["清晨闹钟", "低头系紧鞋带", "人海孤岛"]


def test_nanqiang_raw_sample():
    if not NANQIANG_RAW.is_file():
        return
    raw = NANQIANG_RAW.read_text(encoding="utf-8")
    out = clean_lyrics_text(raw)
    joined = "\n".join(out)
    assert "[Intro]" not in joined
    assert "[Verse" not in joined
    assert "[Chorus]" not in joined
    assert "[Fade Out]" not in joined
    assert "[End]" not in joined
    assert "guitar" not in joined.lower()
    assert "drum" not in joined.lower()
    assert "cello" not in joined.lower()
    assert "站台上立着指向正南的标牌" in out
    assert "老子偏要撞南墙！" in joined or any("撞南墙" in x for x in out)
    assert "老子走自己的。" in out
    # all remaining lines are Chinese-ish (have CJK)
    for ln in out:
        assert any("\u4e00" <= c <= "\u9fff" for c in ln), ln


def test_load_lyrics_file_cleans_by_default(tmp_path):
    p = tmp_path / "raw.txt"
    p.write_text(SAMPLE_RAW, encoding="utf-8")
    lines = load_lyrics_file(str(p))
    assert all(not (ln.startswith("[") and ln.endswith("]")) for ln in lines)
    assert "站台上立着指向正南的标牌" in lines


def test_load_lyrics_file_clean_false(tmp_path):
    p = tmp_path / "raw.txt"
    p.write_text("[Intro]\n你好\n", encoding="utf-8")
    lines = load_lyrics_file(str(p), clean=False)
    assert "[Intro]" in lines
    assert "你好" in lines
