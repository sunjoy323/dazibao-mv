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


def cap_span(
    start: float,
    end: float,
    text: str,
    *,
    max_sec: float = 5.5,
    anchor: str = "start",
) -> Tuple[float, float]:
    """Clamp an ASR span so one display line cannot swallow half a verse.

    anchor="start" keeps the ASR start and shortens the end (default).
    anchor="end" keeps the ASR end and pulls start forward — useful when
    Whisper bleeds instrumental intro into the first lyric cue.
    """
    dur = end - start
    expect = expected_line_dur(text, max_sec=max_sec)
    if dur > expect + 0.8:
        if anchor == "end":
            start = max(0.0, end - expect)
        else:
            end = start + expect
    if end <= start:
        end = start + 0.4
    return start, end


def _cue_similarity(lyric_norm: str, cue_text: str) -> float:
    """Fuzzy similarity between a normalized lyric and an ASR cue."""
    nc = _normalize(cue_text)
    if not lyric_norm or not nc:
        return 0.0
    r = SequenceMatcher(None, lyric_norm, nc).ratio()
    if abs(len(nc) - len(lyric_norm)) <= max(4, len(lyric_norm) // 3):
        r += 0.05
    if lyric_norm in nc or nc in lyric_norm:
        len_ratio = min(len(lyric_norm), len(nc)) / max(len(lyric_norm), len(nc))
        r = max(r, 0.55 + 0.4 * len_ratio)
    return r


def _pick_first_lyric_cue(
    nt: str,
    cues: Sequence[Dict[str, Any]],
    *,
    min_ratio: float = 0.5,
    intro_floor: float = 8.0,
    search_until: float = 45.0,
) -> Optional[int]:
    """Pick ASR index for the first lyric — prefer strong matches after intro bleed.

    Whisper often attaches the first sung line to an early instrumental blob
    (e.g. start=12s) while a cleaner cue exists near the real vocal onset (~17s),
    or a single overlong blob covers intro→lyric. We search all early cues and
    prefer high-similarity hits; among strong hits, prefer those at/after
    intro_floor when available.
    """
    candidates: List[Tuple[float, int, float]] = []  # (ratio, index, start)
    for j, c in enumerate(cues):
        st = float(c["start"])
        if st > search_until:
            break
        r = _cue_similarity(nt, c.get("text") or "")
        if r >= min_ratio:
            candidates.append((r, j, st))
    if not candidates:
        return None
    # Highest similarity first; tie-break by preferring post-intro starts, then earlier.
    strong = [c for c in candidates if c[0] >= min_ratio]
    post = [c for c in strong if c[2] >= intro_floor]
    pool = post if post else strong
    pool.sort(key=lambda x: (-x[0], x[2]))
    return pool[0][1]


def _asr_phrase_parts(text: str) -> List[str]:
    """Split an ASR cue into phrase parts on punctuation and whitespace.

    Whisper often emits space-separated CJK clauses without punctuation
    (e.g. ``八零后的老灯 八零后的老灯``). Splitting those keeps sequential
    lyric matching from consuming a whole repeated chorus in one step.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts: List[str] = []
    for punct_part in _PUNCT_SPLIT.split(text):
        punct_part = punct_part.strip()
        if not punct_part:
            continue
        space_bits = [b.strip() for b in re.split(r"\s+", punct_part) if b.strip()]
        # Keep short heads (e.g. 「二十岁 想去…」) glued; still split chorus repeats.
        if len(space_bits) > 1 and all(len(_normalize(b)) >= 4 for b in space_bits):
            parts.extend(space_bits)
        else:
            parts.append(punct_part)
    return parts


def split_asr_cues(cues: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split long ASR segments on punctuation/whitespace, by char weight."""
    out: List[Dict[str, Any]] = []
    for c in cues:
        text = (c.get("text") or "").strip()
        start, end = float(c["start"]), float(c["end"])
        parts = _asr_phrase_parts(text)
        if len(parts) <= 1:
            item = {"start": start, "end": end, "text": text}
            if c.get("words"):
                item["words"] = list(c["words"])
            out.append(item)
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



def first_lyric_onset_from_words(
    cue: Dict[str, Any],
    *,
    max_word_dur: float = 1.5,
) -> Optional[float]:
    """Skip Whisper intro-bleed word timestamps; return real sung onset.

    Faster-whisper often parks the first glyphs at the segment start with a
    tiny duration, then stretches the next word across the instrumental gap.
    Skip leading words with absurd duration (or micro+mega pairs) and use the
    first remaining word start.
    """
    words = cue.get("words") or []
    if len(words) < 2:
        return None
    i = 0
    n = len(words)
    while i < n - 1:
        w = words[i]
        dur = float(w["end"]) - float(w["start"])
        nxt = words[i + 1]
        nxt_dur = float(nxt["end"]) - float(nxt["start"])
        if dur > max_word_dur or (dur < 0.08 and nxt_dur > max_word_dur):
            i += 1
            continue
        break
    onset = float(words[i]["start"])
    # Only treat as a correction when we actually skipped bleed
    if i == 0:
        return None
    return onset


def _pick_sequential_cue(
    nt: str,
    cues: Sequence[Dict[str, Any]],
    ai: int,
    *,
    cursor: float = 0.0,
    window: int = 16,
    min_ratio: float = 0.32,
    max_ahead: float = 20.0,
) -> Tuple[Optional[int], float]:
    """Return (index, ratio) for the next lyric — earliest acceptable match.

    Scoring the whole window and taking the max lets identical chorus lines
    skip forward to a later identical occurrence (e.g. chorus 3) while bridge
    cues in between are never claimed. Greedy earliest-above-threshold keeps
    the pointer monotonic with the song.

    ``max_ahead`` further refuses matches whose cue start is too far past the
    current cursor, so an extra repeated lyric (ASR merged two lines) falls
    back to a short synthetic span instead of leaping over the bridge.
    """
    end = min(ai + window, len(cues))
    best_j: Optional[int] = None
    best_r = -1.0
    horizon = cursor + max_ahead
    for j in range(ai, end):
        st = float(cues[j]["start"])
        if st > horizon and cursor > 1.0:
            # Too far ahead of where we are in the song — stop scanning.
            break
        r = _cue_similarity(nt, cues[j].get("text") or "")
        if r >= min_ratio:
            return j, r
        if r > best_r:
            best_r, best_j = r, j
    # If the only candidate was beyond horizon, treat as no match.
    if best_j is not None and float(cues[best_j]["start"]) > horizon and cursor > 1.0:
        return None, -1.0
    return best_j, best_r


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
    first_lyric_done = False

    for raw in lyrics:
        raw = raw.strip()
        if not raw:
            continue
        nt = _normalize(raw)
        best_j, best_r = None, -1.0
        anchor = "start"

        if cues and not first_lyric_done:
            # First lyric: search all early cues; lock to strong post-intro match.
            fj = _pick_first_lyric_cue(nt, cues)
            if fj is not None:
                best_j = fj
                best_r = _cue_similarity(nt, cues[fj].get("text") or "")
                # Overlong early blobs (intro bleed) → prefer word onset, else end-anchor.
                st = float(cues[fj]["start"])
                en = float(cues[fj]["end"])
                expect = expected_line_dur(raw, max_sec=max_line_sec)
                word_onset = first_lyric_onset_from_words(cues[fj])
                if word_onset is not None and word_onset > st + 0.3:
                    # Stash corrected start onto cue for cap/match below
                    cues[fj] = dict(cues[fj])
                    cues[fj]["start"] = word_onset
                    anchor = "start"
                elif (en - st) > expect + 0.8 and st < 15.0:
                    anchor = "end"
            else:
                # fall back to normal window search from ai
                for j in range(ai, min(ai + 8, len(cues))):
                    r = _cue_similarity(nt, cues[j].get("text") or "")
                    if r > best_r:
                        best_r, best_j = r, j
                if best_j is not None:
                    st = float(cues[best_j]["start"])
                    en = float(cues[best_j]["end"])
                    expect = expected_line_dur(raw, max_sec=max_line_sec)
                    if (en - st) > expect + 0.8 and st < 15.0:
                        anchor = "end"
            first_lyric_done = True
        elif cues:
            # Prefer the earliest cue above threshold so repeated chorus
            # lines cannot jump ahead to a later identical occurrence and
            # orphan the bridge that sits between them in the ASR stream.
            best_j, best_r = _pick_sequential_cue(nt, cues, ai, cursor=cursor, window=16, max_ahead=20.0)

        if best_j is not None and best_r >= 0.32:
            start = float(cues[best_j]["start"])
            end = float(cues[best_j]["end"])
            start, end = cap_span(start, end, raw, max_sec=max_line_sec, anchor=anchor)
            ai = best_j + 1
            cursor = end
        else:
            start = cursor
            end = cursor + expected_line_dur(raw, max_sec=max_line_sec)
            cursor = end

        pieces = split_line(raw, max_chars=max_chars) or [raw]
        spans = redistribute_times(start, end, pieces)
        # Mark pieces from one source lyric so layout anti-repeat can allow a pair
        split_group = f"src-{len(aligned)}" if len(pieces) > 1 else None
        for piece, (t0, t1) in zip(pieces, spans):
            t0, t1 = cap_span(t0, t1, piece, max_sec=max_line_sec)
            item = {"start": round(t0, 3), "end": round(t1, 3), "text": piece}
            if split_group is not None:
                item["split_group"] = split_group
            aligned.append(item)

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
        if not text:
            continue
        item: Dict[str, Any] = {
            "start": float(seg.start),
            "end": float(seg.end),
            "text": text,
        }
        if getattr(seg, "words", None):
            item["words"] = [
                {
                    "start": float(w.start),
                    "end": float(w.end),
                    "word": (w.word or "").strip(),
                }
                for w in seg.words
                if w.word and str(w.word).strip()
            ]
        cues.append(item)
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
