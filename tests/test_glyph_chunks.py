from dazibao_mv.split import glyph_chunks, split_line

def test_glyph_chunks_short_is_per_char():
    assert glyph_chunks("越热闹越寂寞") == list("越热闹越寂寞")

def test_glyph_chunks_not_whole_line():
    g = glyph_chunks("霓虹把黑夜照得太红")
    assert len(g) > 1
    assert "".join(g) == "霓虹把黑夜照得太红"

def test_split_line_still_phrase_level():
    # short line stays one phrase for timeline splitting
    assert split_line("霓虹把黑夜照得太红", max_chars=9) == ["霓虹把黑夜照得太红"]
