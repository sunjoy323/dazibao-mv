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
# Shared punct class for ASR cue splits and word-punct skipping.
# Includes ASCII/fullwidth CJK marks plus Small Form Variants (U+FE50–U+FE5F)
# e.g. ﹔ U+FE54 — Whisper often emits these instead of ； U+FF1B — and
# halfwidth ideographic marks ｡､.
_PUNCT_CLASS = r"，,。.!！？?；;、：:\n\r…—\-｡､\uFE50-\uFE5F"
_PUNCT_SPLIT = re.compile(f"[{_PUNCT_CLASS}]+")


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
    # Prefer cues that match the lyric's leading phrase (ASR punct splits).
    if nc and lyric_norm.startswith(nc):
        len_ratio = len(nc) / max(len(lyric_norm), 1)
        r = max(r, 0.82 + 0.15 * len_ratio)
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


_WORD_PUNCT = re.compile(f"[{_PUNCT_CLASS}]+")


def _split_cue_by_words(cue: Dict[str, Any], parts: Sequence[str]) -> Optional[List[Dict[str, Any]]]:
    """Split one ASR cue into sub-cues using word-timestamp boundaries on punct.

    When Whisper merges two clauses (e.g. 「路边摊的油烟，熏黄了夹克领口」)
    into one segment, word timestamps still mark the real break near the comma.
    """
    words = cue.get("words") or []
    if len(parts) <= 1 or len(words) < 2:
        return None
    # Build cumulative CJK/alnum char stream from words (skip pure punct words).
    char_words: List[Tuple[str, Dict[str, Any]]] = []
    for w in words:
        raw = (w.get("word") or "").strip()
        if not raw:
            continue
        if _WORD_PUNCT.fullmatch(raw):
            # punct-only word: mark a soft break candidate at this boundary
            char_words.append(("", w))
            continue
        for ch in raw:
            if _normalize(ch):
                char_words.append((ch, w))
            elif _WORD_PUNCT.match(ch):
                char_words.append(("", w))
    if not char_words:
        return None

    out: List[Dict[str, Any]] = []
    wi = 0  # index into char_words
    cue_start, cue_end = float(cue["start"]), float(cue["end"])
    for pi, part in enumerate(parts):
        need = list(_normalize(part))
        if not need:
            continue
        matched_words: List[Dict[str, Any]] = []
        for ch in need:
            # skip punct markers in the word stream
            while wi < len(char_words) and char_words[wi][0] == "":
                wi += 1
            if wi >= len(char_words):
                return None  # cannot map; fall back to char-weight
            got, wobj = char_words[wi]
            if got != ch:
                # fuzzy: allow mismatch but still consume one char slot
                pass
            if not matched_words or matched_words[-1] is not wobj:
                matched_words.append(wobj)
            wi += 1
        # consume a following punct marker so the next part starts after it
        while wi < len(char_words) and char_words[wi][0] == "":
            wi += 1
        if not matched_words:
            return None
        st = float(matched_words[0]["start"])
        en = float(matched_words[-1]["end"])
        if pi == 0:
            st = min(st, cue_start)
        if pi == len(parts) - 1:
            en = max(en, cue_end)
        if en <= st:
            en = st + 0.25
        out.append({
            "start": st,
            "end": en,
            "text": part,
            "words": list(matched_words),
        })
    return out if len(out) == len([p for p in parts if _normalize(p)]) else None


def split_asr_cues(cues: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split long ASR segments on punctuation/whitespace.

    When word timestamps exist, break on ``，。！？、；`` (and comma in word text)
    using real word boundaries so merged verse cues become separate timed phrases.
    Otherwise fall back to character-weight redistribution.
    """
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
        by_words = _split_cue_by_words(c, parts)
        if by_words is not None:
            out.extend(by_words)
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
    max_word_dur: float = 1.0,
) -> Optional[float]:
    """Skip Whisper intro-bleed word timestamps; return real sung onset.

    Faster-whisper often parks the first glyphs at the segment start with a
    stretched duration across the instrumental gap (e.g. 路 15.16–16.38 = 1.22s).
    Skip leading bleed words and use the first remaining word start.
    """
    words = [w for w in (cue.get("words") or []) if (w.get("word") or "").strip()]
    # Ignore pure-punctuation words for duration stats / onset
    content = []
    for w in words:
        raw = (w.get("word") or "").strip()
        if _WORD_PUNCT.fullmatch(raw):
            continue
        content.append(w)
    if len(content) < 2:
        return None
    durs = [max(0.0, float(w["end"]) - float(w["start"])) for w in content]
    durs_sorted = sorted(durs)
    mid = durs_sorted[len(durs_sorted) // 2]
    median_cap = max(0.85, 2.0 * mid)
    cue_start = float(cue.get("start", content[0]["start"]))
    i = 0
    n = len(content)
    skipped = False
    while i < n - 1:
        w = content[i]
        dur = float(w["end"]) - float(w["start"])
        nxt = content[i + 1]
        nxt_dur = float(nxt["end"]) - float(nxt["start"])
        parked = abs(float(w["start"]) - cue_start) < 0.05 and dur > 0.8
        bleed = (
            dur > max_word_dur
            or dur > median_cap
            or parked
            or (dur < 0.08 and nxt_dur > max_word_dur)
        )
        if bleed:
            i += 1
            skipped = True
            continue
        break
    onset = float(content[i]["start"])
    # Correction when we skipped bleed, or first kept word is clearly after cue start
    if skipped or onset > cue_start + 0.3:
        return onset
    return None


def lyric_subspan_from_words(
    lyric: str,
    cue: Dict[str, Any],
) -> Optional[Tuple[float, float]]:
    """Map lyric characters onto cue words (ignore punct); return timed subspan.

    Strongest fix when one ASR cue covers multiple lyric phrases: locate the
    glyph run that matches the lyric and use those word start/end times.
    """
    words = cue.get("words") or []
    if len(words) < 2:
        return None
    want = _normalize(lyric)
    if not want:
        return None
    # Flatten content words into a char→word index (skip punct-only tokens).
    chars: List[str] = []
    owners: List[Dict[str, Any]] = []
    for w in words:
        raw = (w.get("word") or "").strip()
        if not raw or _WORD_PUNCT.fullmatch(raw):
            continue
        for ch in raw:
            n = _normalize(ch)
            if not n:
                continue
            chars.append(n)
            owners.append(w)
    stream = "".join(chars)
    if not stream:
        return None
    idx = stream.find(want)
    if idx < 0:
        # Prefix fallback: longest prefix of lyric found in stream
        idx = -1
        for L in range(len(want), max(1, len(want) // 2) - 1, -1):
            j = stream.find(want[:L])
            if j >= 0:
                idx = j
                want = want[:L]
                break
        if idx < 0:
            return None
    i0 = idx
    i1 = idx + len(want) - 1
    if i1 >= len(owners):
        return None
    st = float(owners[i0]["start"])
    en = float(owners[i1]["end"])
    if en <= st:
        en = st + 0.25
    return st, en



def _cue_remainder_after_lyric(
    cue: Dict[str, Any],
    lyric: str,
    consumed_end: float,
) -> Optional[Dict[str, Any]]:
    """If ``cue`` still has a trailing phrase after ``lyric``, return residual cue.

    Used when Whisper merges two chorus halves into one segment (with or without
    punctuation). After the first half claims a word-subspan, the unmatched tail
    is re-inserted so the next lyric can reuse it instead of leaping ahead.
    """
    nt = _normalize(lyric)
    cue_norm = _normalize(cue.get("text") or "")
    if not nt or not cue_norm:
        return None
    idx = cue_norm.find(nt)
    if idx < 0:
        return None
    rem_norm = cue_norm[idx + len(nt) :]
    if len(rem_norm) < 4:
        return None
    orig_end = float(cue["end"])
    rem_start = max(float(consumed_end), float(cue["start"]))
    if orig_end - rem_start < 0.2:
        return None
    words = cue.get("words") or []
    rem_words = [w for w in words if float(w["start"]) >= rem_start - 0.05]
    if rem_words:
        rem_start = min(rem_start, float(rem_words[0]["start"]))
    item: Dict[str, Any] = {
        "start": rem_start,
        "end": orig_end,
        "text": rem_norm,
    }
    if rem_words:
        item["words"] = rem_words
    return item


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
    Default is 20s: covers typical instrumental breaks without packing the next
    verse into the gap; remainder reuse still owns merged-cue chorus halves.
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
                # Overlong early blobs (intro bleed) → word subspan / onset / end-anchor.
                # Do not shrink the cue in place here: the shared consume path applies
                # subspan + remainder reuse so a merged first cue can feed lyric 2.
                st = float(cues[fj]["start"])
                en = float(cues[fj]["end"])
                expect = expected_line_dur(raw, max_sec=max_line_sec)
                sub = lyric_subspan_from_words(raw, cues[fj])
                word_onset = first_lyric_onset_from_words(cues[fj])
                if sub is not None:
                    anchor = "start"
                    # Stash onset preference on a shallow copy without dropping remainder text.
                    if word_onset is not None and word_onset > sub[0] + 0.15:
                        cues[fj] = dict(cues[fj])
                        cues[fj]["start"] = word_onset
                elif word_onset is not None and word_onset > st + 0.3:
                    cues[fj] = dict(cues[fj])
                    cues[fj]["start"] = word_onset
                    anchor = "start"
                elif (en - st) > expect + 0.8 and st < 22.0:
                    # Raise floor from 15→22: bleed blobs often start just after 15s.
                    anchor = "end"
                elif (en - st) > expect + 0.8:
                    # Last resort: keep start, shorten to expect (cap_span default).
                    anchor = "start"
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
                    sub = lyric_subspan_from_words(raw, cues[best_j])
                    word_onset = first_lyric_onset_from_words(cues[best_j])
                    if sub is not None:
                        anchor = "start"
                        if word_onset is not None and word_onset > sub[0] + 0.15:
                            cues[best_j] = dict(cues[best_j])
                            cues[best_j]["start"] = word_onset
                    elif word_onset is not None and word_onset > st + 0.3:
                        cues[best_j] = dict(cues[best_j])
                        cues[best_j]["start"] = word_onset
                        anchor = "start"
                    elif (en - st) > expect + 0.8 and st < 22.0:
                        anchor = "end"
            first_lyric_done = True
        elif cues:
            # Prefer the earliest cue above threshold so repeated chorus
            # lines cannot jump ahead to a later identical occurrence and
            # orphan the bridge that sits between them in the ASR stream.
            best_j, best_r = _pick_sequential_cue(nt, cues, ai, cursor=cursor, window=16, max_ahead=20.0)

        if not (best_j is not None and best_r >= 0.32):
            # Sequential pick may miss a cue just past max_ahead (e.g. post-
            # instrumental verse). Before synthesizing at cursor, look once more
            # for a *strong* match within a slightly wider wait window so we do
            # not pack the next lyric into the instrumental break. Remainder
            # reuse still owns merged-cue halves; this only waits for real cues.
            wait_horizon = cursor + 25.0
            strong_j: Optional[int] = None
            strong_r = -1.0
            if cues and cursor > 1.0:
                for j in range(ai, len(cues)):
                    st = float(cues[j]["start"])
                    if st > wait_horizon:
                        break
                    if st < cursor - 0.05:
                        continue
                    r = _cue_similarity(nt, cues[j].get("text") or "")
                    if r >= 0.5 and r > strong_r:
                        strong_r, strong_j = r, j
            if strong_j is not None:
                best_j, best_r = strong_j, strong_r

        if best_j is not None and best_r >= 0.32:
            cue = cues[best_j]
            orig_end = float(cue["end"])
            sub = lyric_subspan_from_words(raw, cue)
            if sub is not None:
                start, end = sub
                # Honor intro-bleed onset if first-lyric path raised cue start.
                cue_st = float(cue["start"])
                if cue_st > start + 0.15:
                    start = cue_st
            else:
                start = float(cue["start"])
                end = orig_end
                start, end = cap_span(start, end, raw, max_sec=max_line_sec, anchor=anchor)
            # If this cue still has an unmatched trailing phrase, keep it at
            # best_j for the next lyric (remainder reuse) instead of advancing.
            rem = _cue_remainder_after_lyric(cue, raw, end)
            if rem is not None:
                cues[best_j] = rem
                ai = best_j
            else:
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
