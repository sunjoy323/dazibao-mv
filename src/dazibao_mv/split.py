"""Split lyric lines by punctuation and jieba, with orphan merge."""

from __future__ import annotations

import re
from typing import List

import jieba

# Sentence / clause break punctuation (kept out of output pieces)
_PUNCT_SPLIT = re.compile(r"[，,。.!！？?；;、：:\n\r…—\-]+")


def _clean(text: str) -> str:
    return text.replace(" ", "").replace("\u3000", "").strip()


def _jieba_chunks(text: str, max_chars: int) -> List[str]:
    """Chunk a punctuation-free segment with jieba, respecting max_chars."""
    text = _clean(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    words = [w for w in jieba.cut(text) if w.strip()]
    if not words:
        # fallback: hard slice
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    chunks: List[str] = []
    buf = ""
    for w in words:
        if len(w) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(w), max_chars):
                chunks.append(w[i : i + max_chars])
            continue
        if not buf:
            buf = w
        elif len(buf) + len(w) <= max_chars:
            buf += w
        else:
            chunks.append(buf)
            buf = w
    if buf:
        chunks.append(buf)
    return chunks


def _merge_orphans(chunks: List[str], max_chars: int) -> List[str]:
    """Merge trailing/short orphans (≤2 chars) into previous if ≤ max_chars+1."""
    if len(chunks) <= 1:
        return chunks
    out = list(chunks)
    i = len(out) - 1
    while i > 0:
        if len(out[i]) <= 2 and len(out[i - 1]) + len(out[i]) <= max_chars + 1:
            out[i - 1] = out[i - 1] + out[i]
            del out[i]
        i -= 1
    # also merge leading orphan into next if needed
    if len(out) > 1 and len(out[0]) <= 2 and len(out[0]) + len(out[1]) <= max_chars + 1:
        out[1] = out[0] + out[1]
        del out[0]
    return out


def split_line(text: str, max_chars: int = 9) -> List[str]:
    """Split one lyric line into display pieces.

    1. Split on punctuation.
    2. Further chunk long pieces with jieba / max_chars.
    3. Merge orphans ≤2 chars when combined length ≤ max_chars+1.
    """
    text = (text or "").strip()
    if not text:
        return []

    parts = [p for p in _PUNCT_SPLIT.split(text) if _clean(p)]
    if not parts:
        parts = [_clean(text)] if _clean(text) else []

    chunks: List[str] = []
    for part in parts:
        chunks.extend(_jieba_chunks(part, max_chars))

    return _merge_orphans(chunks, max_chars)


def split_lyrics(lines: List[str], max_chars: int = 9) -> List[str]:
    """Split many raw lyric lines into flat display lines."""
    result: List[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result.extend(split_line(line, max_chars=max_chars))
    return result


def load_lyrics_file(path: str) -> List[str]:
    """Load non-empty lyric lines from a text file."""
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]


def glyph_chunks(text: str) -> List[str]:
    """Kinetic reveal units: per-character (short) or 1–2 glyph groups (long).

    This is NOT line-breaking (`split_line`). Used so each punch reveals one
    glyph/word-piece, matching the approved dazibao MV behaviour.
    """
    text = _clean(text)
    if not text:
        return []
    if len(text) <= 6:
        return list(text)
    chunks: List[str] = []
    i = 0
    while i < len(text):
        # first glyph alone; prefer pairs later; leave last 1–2 intact
        if i == 0 or len(text) - i <= 2:
            n = 1
        else:
            n = 2 if (i % 3) else 1
        chunks.append(text[i : i + n])
        i += n
    return chunks
