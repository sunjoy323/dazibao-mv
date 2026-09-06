"""Detect timed lyrics (SRT / LRC) and convert to aligned line lists."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from ..align import expected_line_dur, parse_srt
from ..split import clean_lyrics_text, split_line

LyricsKind = Literal["plain", "srt", "lrc"]

# [mm:ss.xx] or [mm:ss.xxx] or [mm:ss] — optionally with hours [h:mm:ss.xx]
_LRC_LINE = re.compile(
    r"^\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]\s*(.*)$"
)
_LRC_TAG = re.compile(r"^\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")
_SRT_ARROW = re.compile(
    r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}"
)


def _frac_ms(frac: Optional[str]) -> float:
    if not frac:
        return 0.0
    return int((frac + "000")[:3]) / 1000.0


def _lrc_ts_to_sec(m: str, s: str, frac: Optional[str]) -> float:
    return int(m) * 60 + int(s) + _frac_ms(frac)


def detect_lyrics_kind(text: str) -> LyricsKind:
    """Classify lyrics blob as plain / srt / lrc."""
    raw = (text or "").strip()
    if not raw:
        return "plain"
    if _SRT_ARROW.search(raw):
        return "srt"
    # Count LRC timestamp tags on non-empty lines
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return "plain"
    tagged = sum(1 for ln in lines if _LRC_TAG.match(ln))
    if tagged >= max(1, len(lines) // 3) and tagged >= 1:
        # Prefer LRC when a meaningful share of lines are tagged
        # (and we didn't already hit SRT arrow above).
        return "lrc"
    return "plain"


def parse_lrc(text: str) -> List[Dict[str, Any]]:
    """Parse LRC text into [{start, end, text}, ...] (end from next cue or heuristic)."""
    entries: List[Tuple[float, str]] = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = _LRC_LINE.match(ln)
        if not m:
            continue
        start = _lrc_ts_to_sec(m.group(1), m.group(2), m.group(3))
        body = (m.group(4) or "").strip()
        # Skip metadata-only tags like [ar:…] that don't match mm:ss well —
        # our regex requires mm:ss so [ti:foo] won't match.
        if not body:
            continue
        entries.append((start, body))

    entries.sort(key=lambda x: x[0])
    cues: List[Dict[str, Any]] = []
    for i, (start, body) in enumerate(entries):
        if i + 1 < len(entries):
            end = entries[i + 1][0]
            if end <= start:
                end = start + expected_line_dur(body)
        else:
            end = start + expected_line_dur(body)
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": body})
    return cues


def parse_srt_text(text: str) -> List[Dict[str, Any]]:
    """Parse SRT from an in-memory string via temporary file + parse_srt."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".srt", encoding="utf-8", delete=False
    ) as f:
        f.write(text)
        path = f.name
    try:
        return parse_srt(path)
    finally:
        Path(path).unlink(missing_ok=True)


def timed_cues_to_aligned(
    cues: List[Dict[str, Any]],
    *,
    max_chars: int = 9,
) -> List[Dict[str, Any]]:
    """Split timed cues into display lines, redistributing each cue's span."""
    from ..align import redistribute_times, cap_span

    aligned: List[Dict[str, Any]] = []
    for c in cues:
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        start = float(c["start"])
        end = float(c["end"])
        pieces = split_line(text, max_chars=max_chars) or [text]
        spans = redistribute_times(start, end, pieces)
        for piece, (t0, t1) in zip(pieces, spans):
            t0, t1 = cap_span(t0, t1, piece)
            aligned.append({"start": round(t0, 3), "end": round(t1, 3), "text": piece})
    # monotonic
    prev_end = 0.0
    for item in aligned:
        if item["start"] < prev_end:
            shift = prev_end - item["start"]
            item["start"] = round(item["start"] + shift, 3)
            item["end"] = round(item["end"] + shift, 3)
        if item["end"] <= item["start"]:
            item["end"] = round(item["start"] + 0.25, 3)
        prev_end = item["end"]
    return aligned


def prepare_aligned_from_lyrics(
    lyrics_text: str,
    *,
    audio: Optional[str] = None,
    max_chars: int = 9,
    whisper_model: str = "medium",
    initial_prompt: Optional[str] = None,
    max_line_sec: float = 5.5,
) -> Tuple[List[Dict[str, Any]], bool, LyricsKind]:
    """Build aligned lines; return (aligned, align_skipped, kind).

    SRT/LRC → skip Whisper. Plain text → require audio and call library ``align``.
    """
    kind = detect_lyrics_kind(lyrics_text)
    if kind == "srt":
        cues = parse_srt_text(lyrics_text)
        return timed_cues_to_aligned(cues, max_chars=max_chars), True, kind
    if kind == "lrc":
        cues = parse_lrc(lyrics_text)
        return timed_cues_to_aligned(cues, max_chars=max_chars), True, kind

    # plain — strip [Section]/English notes, then Whisper-align
    cleaned = clean_lyrics_text(lyrics_text)
    lyrics_text = "\n".join(cleaned) + ("\n" if cleaned else "")
    if not audio:
        raise ValueError("Plain lyrics require audio for Whisper alignment")
    from ..align import align

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(lyrics_text if lyrics_text.endswith("\n") else lyrics_text + "\n")
        lyrics_path = f.name
    try:
        aligned = align(
            audio=audio,
            lyrics_path=lyrics_path,
            srt=None,
            max_chars=max_chars,
            whisper_model=whisper_model,
            initial_prompt=initial_prompt,
            max_line_sec=max_line_sec,
        )
    finally:
        Path(lyrics_path).unlink(missing_ok=True)
    return aligned, False, kind
