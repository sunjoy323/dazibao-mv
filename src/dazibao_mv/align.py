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
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_PUNCT_SPLIT = re.compile(r"[，,。.!！？?；;、：:\n\r…—\-]+")


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
    """Prefer CJK characters for matching; fall back to alnum strip."""
    return "".join(_CJK.findall(s)) or re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE).lower()


def expected_line_dur(text: str, *, max_sec: float = 5.5) -> float:
    """Heuristic sung duration for a short Chinese lyric piece."""
    n = max(1, len(_normalize(text)))
    return max(0.9, min(max_sec, n * 0.38 + 0.4))


def cap_span(start: float, end: float, text: str, *, max_sec: float = 5.5) -> Tuple[float, float]:
    """Clamp an ASR span so one display line cannot swallow half a verse."""
    dur = end - start
    expect = expected_line_dur(text, max_sec=max_sec)
    if dur > expect + 0.8:
        end = start + expect
    if end <= start:
        end = start + 0.4
    return start, end


def split_asr_cues(cues: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split long ASR segments on punctuation, proportionally by char weight."""
    out: List[Dict[str, Any]] = []
    for c in cues:
        text = (c.get("text") or "").strip()
        start, end = float(c["start"]), float(c["end"])
        parts = [p.strip() for p in _PUNCT_SPLIT.split(text) if p.strip()]
        if len(parts) <= 1:
            out.append({"start": start, "end": end, "text": text})
            continue
        weights = [max(1, len(_normalize(p)) or len(p)) for p in parts]
        total = sum(weights)
        dur = max(0.05, end - start)
        t = start
        for i, (p, w) in enumerate(zip(parts, weights)):
            t1 = end if i == len(parts) - 1 else t + dur * (w / total)
            out.append({"start": t, "end": t1, "text": p})
            t = t1
    return out


def redistribute_times(
    start: float,
    end: float,
    pieces: Sequence[str],
) -> List[Tuple[float, float]]:
    """Redistribute [start,end] across split pieces by character weight."""
    if not pieces:
        return []
    weights = [max(1, len(_normalize(p)) or len(p)) for p in pieces]
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
    max_line_sec: float = 5.5,
) -> List[Dict[str, Any]]:
    """Sequential fuzzy-match user lyric lines to timed cues; split & redistribute."""
    cues = split_asr_cues(cues)
    aligned: List[Dict[str, Any]] = []
    cursor = 0.0
    ai = 0  # sequential ASR pointer

    for raw in lyrics:
        raw = raw.strip()
        if not raw:
            continue
        nt = _normalize(raw)
        best_j, best_r = None, -1.0
        if cues:
            for j in range(ai, min(ai + 8, len(cues))):
                nc = _normalize(cues[j]["text"])
                if not nc:
                    continue
                r = SequenceMatcher(None, nt, nc).ratio()
                if abs(len(nc) - len(nt)) <= max(4, len(nt) // 3):
                    r += 0.05
                # mild substring bonus scaled by length ratio (NOT flat 0.85)
                if nt and nc and (nt in nc or nc in nt):
                    len_ratio = min(len(nt), len(nc)) / max(len(nt), len(nc))
                    r = max(r, 0.55 + 0.4 * len_ratio)
                if r > best_r:
                    best_r, best_j = r, j

        if best_j is not None and best_r >= 0.32:
            start = float(cues[best_j]["start"])
            end = float(cues[best_j]["end"])
            start, end = cap_span(start, end, raw, max_sec=max_line_sec)
            ai = best_j + 1
            cursor = end
        else:
            start = cursor
            end = cursor + expected_line_dur(raw, max_sec=max_line_sec)
            cursor = end

        pieces = split_line(raw, max_chars=max_chars) or [raw]
        spans = redistribute_times(start, end, pieces)
        for piece, (t0, t1) in zip(pieces, spans):
            t0, t1 = cap_span(t0, t1, piece, max_sec=max_line_sec)
            aligned.append({"start": round(t0, 3), "end": round(t1, 3), "text": piece})

    # ensure monotonic non-decreasing starts
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


def whisper_transcribe(
    audio: str,
    model_size: str = "medium",
    *,
    initial_prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run faster-whisper and return segment cues.

    Uses vad_filter=False so choruses / soft passages are less likely to be
    dropped or merged into multi-sentence blobs.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ImportError(
            "faster-whisper is required for ASR. "
            "Install with: pip install 'dazibao-mv[align]'"
        ) from e

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    kwargs: Dict[str, Any] = {
        "language": "zh",
        "vad_filter": False,
        "word_timestamps": True,
        "beam_size": 5,
        "condition_on_previous_text": True,
    }
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments, _info = model.transcribe(audio, **kwargs)
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
    whisper_model: str = "medium",
    initial_prompt: Optional[str] = None,
    max_line_sec: float = 5.5,
) -> List[Dict[str, Any]]:
    """Align lyrics file to audio/SRT timing."""
    lyrics = load_lyrics_file(lyrics_path)
    if srt:
        cues = parse_srt(srt)
    elif audio:
        prompt = initial_prompt
        if not prompt and lyrics:
            prompt = "。".join(lyrics[:4])[:120]
        cues = whisper_transcribe(audio, model_size=whisper_model, initial_prompt=prompt)
    else:
        raise ValueError("Either --srt or --audio is required for alignment")
    return match_lyrics_to_cues(
        lyrics, cues, max_chars=max_chars, max_line_sec=max_line_sec
    )


def save_aligned(aligned: List[Dict[str, Any]], out_path: str | Path) -> None:
    Path(out_path).write_text(
        json.dumps(aligned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def save_srt(aligned: List[Dict[str, Any]], out_path: str | Path) -> None:
    """Write aligned lines as SRT for reproducible --srt renders."""

    def ts(s: float) -> str:
        if s < 0:
            s = 0.0
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")

    lines: List[str] = []
    for i, item in enumerate(aligned, 1):
        lines.append(str(i))
        lines.append(f"{ts(float(item['start']))} --> {ts(float(item['end']))}")
        lines.append(str(item["text"]))
        lines.append("")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
