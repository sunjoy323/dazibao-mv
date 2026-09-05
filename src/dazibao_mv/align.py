"""Align user lyrics to SRT or faster-whisper ASR via fuzzy matching."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .split import load_lyrics_file, split_line

_SRT_TS = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    ms = (ms + "000")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: str | Path) -> List[Dict[str, Any]]:
    """Parse an SRT file into [{start, end, text}, ...]."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: List[Dict[str, Any]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # optional index line
        if lines[0].isdigit() and len(lines) >= 3:
            ts_line, content = lines[1], " ".join(lines[2:])
        else:
            ts_line, content = lines[0], " ".join(lines[1:])
        m = _SRT_TS.search(ts_line)
        if not m:
            continue
        start = _ts_to_sec(*m.group(1, 2, 3, 4))
        end = _ts_to_sec(*m.group(5, 6, 7, 8))
        content = content.replace("<br>", " ").strip()
        content = re.sub(r"<[^>]+>", "", content)
        if content:
            cues.append({"start": start, "end": end, "text": content})
    return cues


def _normalize(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE).lower()


def _best_match(
    target: str,
    cues: Sequence[Dict[str, Any]],
    used: set,
    window: int = 8,
) -> Optional[int]:
    """Find best unused cue index for target text near sequential progress."""
    nt = _normalize(target)
    if not nt:
        return None
    best_i = None
    best_score = 0.0
    # prefer cues near the next unused index
    next_free = 0
    while next_free in used and next_free < len(cues):
        next_free += 1
    lo = max(0, next_free - 2)
    hi = min(len(cues), next_free + window)
    for i in range(lo, hi):
        if i in used:
            continue
        nc = _normalize(cues[i]["text"])
        if not nc:
            continue
        score = SequenceMatcher(None, nt, nc).ratio()
        # substring bonus
        if nt in nc or nc in nt:
            score = max(score, 0.85)
        if score > best_score:
            best_score = score
            best_i = i
    if best_i is None or best_score < 0.35:
        # global fallback
        for i, c in enumerate(cues):
            if i in used:
                continue
            score = SequenceMatcher(None, nt, _normalize(c["text"])).ratio()
            if score > best_score:
                best_score = score
                best_i = i
    if best_i is not None and best_score >= 0.35:
        return best_i
    return None


def redistribute_times(
    start: float,
    end: float,
    pieces: Sequence[str],
) -> List[Tuple[float, float]]:
    """Redistribute [start,end] across split pieces by character weight."""
    if not pieces:
        return []
    weights = [max(1, len(p)) for p in pieces]
    total = sum(weights)
    dur = max(0.05, end - start)
    out: List[Tuple[float, float]] = []
    t = start
    for i, w in enumerate(weights):
        slice_dur = dur * (w / total)
        t1 = end if i == len(weights) - 1 else t + slice_dur
        out.append((t, t1))
        t = t1
    return out


def match_lyrics_to_cues(
    lyrics: Sequence[str],
    cues: Sequence[Dict[str, Any]],
    *,
    max_chars: int = 9,
) -> List[Dict[str, Any]]:
    """Fuzzy-match user lyric lines to timed cues; split & redistribute."""
    used: set = set()
    aligned: List[Dict[str, Any]] = []
    # estimate audio end for fallbacks
    cue_end = cues[-1]["end"] if cues else 0.0
    cursor = 0.0

    for raw in lyrics:
        raw = raw.strip()
        if not raw:
            continue
        idx = _best_match(raw, cues, used) if cues else None
        if idx is not None:
            used.add(idx)
            start = float(cues[idx]["start"])
            end = float(cues[idx]["end"])
            cursor = end
        else:
            # fallback: place after cursor with heuristic duration
            start = cursor
            end = cursor + max(1.2, 0.28 * max(1, len(_normalize(raw))))
            cursor = end

        pieces = split_line(raw, max_chars=max_chars)
        if not pieces:
            pieces = [raw]
        # If the matched cue text already equals one piece, keep single span;
        # otherwise always redistribute across our display pieces.
        spans = redistribute_times(start, end, pieces)
        for piece, (t0, t1) in zip(pieces, spans):
            aligned.append({"start": t0, "end": t1, "text": piece})

    # ensure monotonic non-decreasing starts
    prev_end = 0.0
    for item in aligned:
        if item["start"] < prev_end:
            shift = prev_end - item["start"]
            item["start"] += shift
            item["end"] += shift
        if item["end"] <= item["start"]:
            item["end"] = item["start"] + 0.25
        prev_end = item["end"]

    return aligned


def whisper_transcribe(audio: str, model_size: str = "base") -> List[Dict[str, Any]]:
    """Run faster-whisper and return segment cues."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ImportError(
            "faster-whisper is required for ASR. "
            "Install with: pip install 'dazibao-mv[align]'"
        ) from e

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio, language="zh", vad_filter=True)
    cues: List[Dict[str, Any]] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            cues.append({"start": float(seg.start), "end": float(seg.end), "text": text})
    return cues


def align(
    audio: Optional[str],
    lyrics_path: str,
    *,
    srt: Optional[str] = None,
    max_chars: int = 9,
    whisper_model: str = "base",
) -> List[Dict[str, Any]]:
    """Align lyrics file to audio/SRT timing."""
    lyrics = load_lyrics_file(lyrics_path)
    if srt:
        cues = parse_srt(srt)
    elif audio:
        cues = whisper_transcribe(audio, model_size=whisper_model)
    else:
        raise ValueError("Either --srt or --audio is required for alignment")
    return match_lyrics_to_cues(lyrics, cues, max_chars=max_chars)


def save_aligned(aligned: List[Dict[str, Any]], out_path: str | Path) -> None:
    Path(out_path).write_text(
        json.dumps(aligned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
