"""Split lyric lines by spaces, punctuation and jieba, with orphan merge."""

from __future__ import annotations

import re
from typing import List

import jieba

# Sentence / clause break punctuation (kept out of output pieces)
_PUNCT_SPLIT = re.compile(r"[，,。.!！？?；;、：:\n\r…—\-]+")
# Intentional phrase breaks in lyrics (ASCII / fullwidth Ideographic space)
_SPACE_SPLIT = re.compile(r"[ \u3000]+")


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

    1. First split on ASCII space / fullwidth Ideographic space (\u3000) /
       multiple whitespace — spaces are intentional phrase breaks and must
       not be glued across (e.g. ``谈论千秋 谈论前朝风雅`` → two screens).
    2. For each phrase: split on punctuation, then jieba / max_chars.
    3. Only ``_clean`` (strip spaces) *inside* a phrase after the space-split.
    4. Merge orphans ≤2 chars when combined length ≤ max_chars+1, per phrase
       (never across a space boundary).
    """
    text = (text or "").strip()
    if not text:
        return []

    phrases = [p for p in _SPACE_SPLIT.split(text) if p.strip()]
    if not phrases:
        return []

    chunks: List[str] = []
    for phrase in phrases:
        parts = [p for p in _PUNCT_SPLIT.split(phrase) if _clean(p)]
        if not parts:
            cleaned = _clean(phrase)
            if cleaned:
                parts = [cleaned]
        phrase_chunks: List[str] = []
        for part in parts:
            phrase_chunks.extend(_jieba_chunks(part, max_chars))
        chunks.extend(_merge_orphans(phrase_chunks, max_chars))
    return chunks


def split_lyrics(lines: List[str], max_chars: int = 9) -> List[str]:
    """Split many raw lyric lines into flat display lines."""
    result: List[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result.extend(split_line(line, max_chars=max_chars))
    return result


_BRACKET_LINE = re.compile(r"^\[(.*)\]$")


def _is_section_or_production_note(line: str) -> bool:
    """True for full-line ``[Intro]`` / ``[Slow acoustic…]`` style tags.

    Keeps lines that contain CJK inside the brackets (rare lyric markup).
    """
    m = _BRACKET_LINE.match(line.strip())
    if not m:
        return False
    inner = m.group(1).strip()
    if not inner:
        return True
    if any("\u4e00" <= c <= "\u9fff" for c in inner):
        return False
    # Mostly ASCII letters / digits / punctuation → section tag or English note
    non_space = [c for c in inner if not c.isspace()]
    if not non_space:
        return True
    ascii_count = sum(1 for c in non_space if ord(c) < 128)
    return ascii_count / len(non_space) >= 0.7


def clean_lyrics_text(text: str) -> List[str]:
    """Strip section tags and English production notes; keep Chinese lyrics.

    Drops lines that are **only** bracket tags (``[Intro]``, ``[Verse 1]``,
    ``[Chorus]``, ``[Fade Out]``, ``[End]``, …) or bracketed English
    production notes (``[Slow acoustic guitar…]``). Preserves Chinese lyric
    lines (including punctuation like ``！``). Blank / ``#`` comments skipped.
    """
    out: List[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if _is_section_or_production_note(s):
            continue
        out.append(s)
    return out


def load_lyrics_file(path: str, *, clean: bool = True) -> List[str]:
    """Load non-empty lyric lines from a text file.

    By default runs :func:`clean_lyrics_text` so ``[Intro]`` / English
    production notes are stripped. Pass ``clean=False`` for raw lines.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if clean:
        return clean_lyrics_text(raw)
    return [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]


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
