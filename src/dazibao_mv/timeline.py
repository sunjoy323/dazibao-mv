"""Timeline clamp (LEAD) and concat part list with gaps — no overlap drift."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class TimedLine:
    text: str
    start: float
    end: float
    t0: float = 0.0
    t1: float = 0.0
    chorus: bool = False
    hook: bool = False
    layout: str = "center_slam"
    chunks: List[str] = field(default_factory=list)
    chunk_times: List[float] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "t0": self.t0,
            "t1": self.t1,
            "chorus": self.chorus,
            "hook": self.hook,
            "layout": self.layout,
            "chunks": list(self.chunks),
            "chunk_times": list(self.chunk_times),
        }
        d.update(self.extra)
        return d


LAYOUTS = [
    "center_slam",
    "left_stack",
    "right_cascade",
    "diagonal",
    "giant_char",
    "top_heavy",
    "bottom_banner",
]

POSTER_LAYOUTS = [
    "poster_fill_h",
    "poster_fill_v",
]

# Per-mode layout pools (used when style.layouts is unset)
NEON_LAYOUTS = [
    "neon_col_left",
    "neon_col_right",
    "neon_stack_center",
    "giant_char",
]
BLUEPRINT_LAYOUTS = [
    "blueprint_titleblock",
    "blueprint_h_rule",
    "blueprint_v_rule",
    "blueprint_corner",
]
COMIC_LAYOUTS = [
    "comic_panel_full",
    "comic_slash",
    "comic_stack_burst",
    "giant_char",
]
INK_LAYOUTS = [
    "ink_vertical",
    "ink_two_col",
    "ink_seal",
]

MODE_LAYOUTS = {
    "neon": NEON_LAYOUTS,
    "blueprint": BLUEPRINT_LAYOUTS,
    "comic": COMIC_LAYOUTS,
    "ink": INK_LAYOUTS,
    "ink_wash": INK_LAYOUTS,
    "ink-wash": INK_LAYOUTS,
}

REVEAL_FRAC = 0.48
MAX_PER_CHAR = 0.30


def clamp_timeline(
    lines: Sequence[Dict[str, Any] | TimedLine],
    *,
    lead: float = 0.12,
    audio_dur: Optional[float] = None,
    min_dur: float = 0.25,
) -> List[TimedLine]:
    """Apply LEAD clamp so clips never overlap.

    t0 = max(prev_t1, start - lead)
    t1 = max(end, t0 + min_dur)  (capped by audio_dur if given)
    """
    out: List[TimedLine] = []
    prev_t1 = 0.0
    for raw in lines:
        if isinstance(raw, TimedLine):
            L = TimedLine(
                text=raw.text,
                start=float(raw.start),
                end=float(raw.end),
                chorus=raw.chorus,
                hook=raw.hook,
                layout=raw.layout,
                chunks=list(raw.chunks),
                extra=dict(raw.extra),
            )
        else:
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            L = TimedLine(
                text=text,
                start=float(raw["start"]),
                end=float(raw["end"]),
                chorus=bool(raw.get("chorus", False)),
                hook=bool(raw.get("hook", False) or raw.get("card", False)),
                layout=str(raw.get("layout", "center_slam")),
                chunks=list(raw.get("chunks") or []),
                extra={k: v for k, v in raw.items() if k not in {
                    "text", "start", "end", "chorus", "hook", "card", "layout",
                    "chunks", "chunk_times", "t0", "t1",
                }},
            )
        L.t0 = max(prev_t1, max(0.0, L.start - lead))
        L.t1 = max(L.end, L.t0 + min_dur)
        if audio_dur is not None:
            L.t1 = min(float(audio_dur), L.t1)
            if L.t1 <= L.t0:
                L.t1 = min(float(audio_dur), L.t0 + min_dur)
        prev_t1 = L.t1
        out.append(L)
    return out


def assign_layouts(
    lines: Sequence[TimedLine],
    layouts: Sequence[str] = LAYOUTS,
) -> None:
    """Assign layout by separate chorus/hook vs verse counters."""
    ci = vi = 0
    n = len(layouts)
    for L in lines:
        if L.chorus or L.hook:
            L.layout = layouts[ci % n]
            ci += 1
        else:
            L.layout = layouts[vi % n]
            vi += 1


def assign_poster_layouts(lines: Sequence[TimedLine]) -> None:
    """Screen-fill poster layouts: short→H, long→V, mid alternate H/V.

    Never allow 3 identical layouts in a row (flip H↔V to break a run).
    """
    prev: Optional[str] = None
    run = 0
    alt = 0
    for L in lines:
        nchar = len(L.text.replace(" ", "").replace("　", ""))
        if nchar <= 6:
            cand = "poster_fill_h"
        elif nchar >= 9:
            cand = "poster_fill_v"
        else:
            cand = POSTER_LAYOUTS[alt % 2]
            alt += 1
        if prev is not None and cand == prev and run >= 2:
            cand = "poster_fill_v" if cand == "poster_fill_h" else "poster_fill_h"
        if cand == prev:
            run += 1
        else:
            run = 1
        prev = cand
        L.layout = cand



def layouts_for_style(style: Dict[str, Any]) -> List[str]:
    """Explicit style.layouts, else mode default pool, else classic LAYOUTS."""
    explicit = style.get("layouts")
    if isinstance(explicit, (list, tuple)) and explicit:
        return [str(x) for x in explicit]
    mode = str(style.get("mode") or "").strip().lower()
    if mode in MODE_LAYOUTS:
        return list(MODE_LAYOUTS[mode])
    return list(LAYOUTS)


def _pick_anti_repeat_layout(
    pool: Sequence[str],
    *,
    preferred: str,
    prev: Optional[str],
    prev2: Optional[str],
    run_len: int,
    allow_same: bool,
) -> str:
    """Pick next layout with consecutive anti-repeat rules.

    - Never same as ``prev`` unless ``allow_same`` (shared split_group) and run < 2.
    - Cap identical run length at 2 even for splits.
    - Prefer ``preferred`` (verse/chorus counter); consecutive constraint wins.
    - Small pools: skip last used; if only leftover equals last-2, still ≠ last.
    """
    layouts = [str(x) for x in pool if x]
    if not layouts:
        return preferred or "center_slam"
    if preferred not in layouts:
        preferred = layouts[0]

    if allow_same and prev is not None and run_len < 2:
        return prev

    candidates = [x for x in layouts if x != prev] if prev is not None else list(layouts)
    if not candidates:
        candidates = list(layouts)

    def _best(cands: List[str]) -> str:
        if preferred in cands:
            if prev2 is not None and preferred == prev2 and len(cands) > 1:
                alt = [c for c in cands if c != prev2]
                if alt:
                    # still prefer preferred only if it differs from prev2
                    pass
                else:
                    return preferred
                # Prefer next-after-preferred among alt, else first alt
                try:
                    i = layouts.index(preferred)
                except ValueError:
                    return alt[0]
                for j in range(1, len(layouts) + 1):
                    nxt = layouts[(i + j) % len(layouts)]
                    if nxt in alt:
                        return nxt
                return alt[0]
            return preferred
        if prev2 is not None:
            alt = [c for c in cands if c != prev2]
            if alt:
                return alt[0]
        return cands[0]

    return _best(candidates)


def assign_style_layouts(lines: Sequence[TimedLine], style: Dict[str, Any]) -> None:
    """Assign layouts from style/mode pool with anti-repeat consecutive rules.

    Walk lines in order; never assign the same layout as the previous line
    unless both share ``extra["split_group"]`` (one source lyric split into
    timed pieces). Even then, cap runs at 2 (never 3 identical in a row).
    Verse/chorus counters still prefer alternating slots, but the consecutive
    constraint wins. Comic: prefer giant_char for ≤2-char hooks (still subject
    to anti-repeat).
    """
    layouts = layouts_for_style(style)
    n = len(layouts)
    if n == 0:
        assign_layouts(lines, LAYOUTS)
        return

    mode = str(style.get("mode") or "").strip().lower()
    ci = vi = 0
    prev: Optional[str] = None
    prev2: Optional[str] = None
    prev_sg: Any = None
    run_len = 0
    punch_i = 0

    for L in lines:
        sg = L.extra.get("split_group")
        if L.chorus or L.hook:
            preferred = layouts[ci % n]
            ci += 1
        else:
            preferred = layouts[vi % n]
            vi += 1

        if mode == "comic":
            nchar = len(L.text.replace(" ", "").replace("　", ""))
            if nchar <= 2 and (L.hook or L.chorus) and "giant_char" in layouts:
                preferred = "giant_char"

        allow_same = (
            sg is not None
            and prev_sg is not None
            and sg == prev_sg
            and prev is not None
        )
        chosen = _pick_anti_repeat_layout(
            layouts,
            preferred=preferred,
            prev=prev,
            prev2=prev2,
            run_len=run_len,
            allow_same=allow_same,
        )
        L.layout = chosen
        L.extra["punch_variant"] = punch_i % 3
        punch_i += 1

        if chosen == prev:
            run_len += 1
        else:
            run_len = 1
        prev2 = prev
        prev = chosen
        prev_sg = sg


MIN_TAIL_HOLD = 0.35


def _apply_min_tail_hold(
    times: Sequence[float],
    t0: float,
    t1: float,
    *,
    min_tail_hold: float = MIN_TAIL_HOLD,
) -> List[float]:
    """Compress chunk starts so the last glyph stays visible ≥ min_tail_hold."""
    n = len(times)
    if n == 0:
        return []
    t0, t1 = float(t0), float(t1)
    hold = float(min_tail_hold)
    limit = t1 - hold
    if limit <= t0:
        if n == 1:
            return [t0]
        # Window shorter than hold: pack into [t0, t0] (last still at t0).
        return [t0] * n
    clamped = [min(t1, max(t0, float(t))) for t in times]
    if clamped[-1] <= limit + 1e-9:
        return clamped
    src0 = clamped[0]
    src1 = clamped[-1]
    if src1 <= src0 + 1e-9:
        out = [min(limit, max(t0, t)) for t in clamped]
        out[-1] = limit
        return out
    out = [src0 + (t - src0) / (src1 - src0) * (limit - src0) for t in clamped]
    out[-1] = limit
    # Keep monotonic
    prev = t0
    fixed: List[float] = []
    for t in out:
        t = min(limit if fixed and len(fixed) == n - 1 else t1, max(prev, t))
        fixed.append(t)
        prev = t
    fixed[-1] = min(fixed[-1], limit)
    return fixed


def assign_chunk_times(
    lines: Sequence[TimedLine],
    *,
    reveal_frac: float = REVEAL_FRAC,
    max_per_char: float = MAX_PER_CHAR,
    min_tail_hold: float = MIN_TAIL_HOLD,
) -> None:
    """Distribute reveal times within each clamped [t0, t1] (uniform)."""
    for L in lines:
        if not L.chunks:
            from .split import glyph_chunks

            L.chunks = glyph_chunks(L.text) or [L.text]
        n = max(1, len(L.chunks))
        dur = max(0.2, L.t1 - L.t0)
        hold = float(min_tail_hold)
        # Leave room for last-glyph hold before screen change.
        usable = max(0.05, dur - hold)
        reveal_dur = min(dur * reveal_frac, n * max_per_char, usable * (n / max(n - 1, 1)))
        reveal_dur = max(reveal_dur, min(usable, min(dur * 0.35, n * 0.18)))
        times = [L.t0 + reveal_dur * (i / n) for i in range(n)]
        L.chunk_times = _apply_min_tail_hold(
            times, L.t0, L.t1, min_tail_hold=hold
        )


def _content_words(words: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop empty / pure-punctuation Whisper word tokens."""
    import re
    punct = re.compile(r"^[，,。.!！？?；;、：:\s\-—…｡､\uFE50-\uFE5F]+$")
    out: List[Dict[str, Any]] = []
    for w in words or []:
        raw = str(w.get("word") or "").strip()
        if not raw or punct.fullmatch(raw):
            continue
        out.append(w)
    return out


def _glyph_starts_from_words(
    text: str,
    words: Sequence[Dict[str, Any]],
    *,
    t0: float,
    t1: float,
) -> Optional[List[float]]:
    """Map each glyph of ``text`` to an absolute start time from word stamps."""
    content = _content_words(words)
    if not content:
        return None
    # Flatten word tokens into per-CJK/alnum char owners with interpolated starts.
    char_starts: List[float] = []
    char_ends: List[float] = []
    stream_chars: List[str] = []
    for w in content:
        raw = str(w.get("word") or "").strip()
        glyphs = [ch for ch in raw if ch.strip() and not ch.isspace()]
        if not glyphs:
            continue
        ws, we = float(w["start"]), float(w["end"])
        if we < ws:
            we = ws + 0.05
        span = max(0.05, we - ws)
        for i, ch in enumerate(glyphs):
            stream_chars.append(ch)
            char_starts.append(ws + span * (i / len(glyphs)))
            char_ends.append(ws + span * ((i + 1) / len(glyphs)))
    if not stream_chars:
        return None

    # Same cleaning as glyph_chunks / split._clean (drop spaces)
    cleaned = text.replace(" ", "").replace("\u3000", "").strip()
    if not cleaned:
        return None

    def _is_content(ch: str) -> bool:
        o = ord(ch)
        return ("\u4e00" <= ch <= "\u9fff") or ch.isalnum()

    # Greedy match content glyphs; punctuation inherits previous start (no ASR consume).
    starts: List[float] = []
    si = 0
    prev = t0
    content_matched = 0
    for ch in cleaned:
        if not _is_content(ch):
            starts.append(prev)
            continue
        if si >= len(stream_chars):
            starts.append(min(t1, prev + 0.12))
            prev = starts[-1]
            continue
        found = None
        for look in range(si, min(si + 4, len(stream_chars))):
            if stream_chars[look] == ch:
                found = look
                break
        if found is not None:
            st = char_starts[found]
            si = found + 1
        else:
            st = char_starts[si]
            si += 1
        st = min(t1, max(t0, st))
        starts.append(st)
        prev = st
        content_matched += 1
    if content_matched < 1:
        return None
    if len(starts) != len(cleaned):
        return None
    return starts


def _chunk_punch_intensities(
    chunk_starts: Sequence[float],
    chunk_ends: Sequence[float],
    *,
    t1: float,
) -> List[float]:
    """Map duration + gap-before-next → punch intensity in [0.7, 1.4]."""
    n = len(chunk_starts)
    if n == 0:
        return []
    scores: List[float] = []
    for i in range(n):
        dur = max(0.04, float(chunk_ends[i]) - float(chunk_starts[i]))
        if i + 1 < n:
            gap = max(0.0, float(chunk_starts[i + 1]) - float(chunk_ends[i]))
        else:
            gap = max(0.0, float(t1) - float(chunk_ends[i]))
        # Longer held syllables + pause before next → heavier punch
        scores.append(dur + 0.55 * gap)
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-6:
        return [1.0] * n
    out: List[float] = []
    for s in scores:
        # normalize 0..1 then map to 0.7..1.4
        x = (s - lo) / (hi - lo)
        out.append(round(0.7 + 0.7 * x, 3))
    return out


def assign_chunk_times_rhythm(
    lines: Sequence[TimedLine],
    *,
    reveal_frac: float = REVEAL_FRAC,
    max_per_char: float = MAX_PER_CHAR,
    min_tail_hold: float = MIN_TAIL_HOLD,
) -> None:
    """Map glyph_chunks to Whisper word starts; store per-chunk punch intensity.

    Falls back to uniform :func:`assign_chunk_times` per line when no usable
    words are attached. Intensities land in ``TimedLine.extra["chunk_punch"]``
    (floats 0.7–1.4). Absolute reveal times go in ``chunk_times``.

    Ensures the last chunk starts by ``t1 - min_tail_hold`` so the final glyph
    is not cut off at the screen change. When word ends exceed ``line.end``,
    slightly extend ``t1`` to match (sync with word-aware align spans).
    """
    from .split import glyph_chunks

    for L in lines:
        if not L.chunks:
            L.chunks = glyph_chunks(L.text) or [L.text]
        words = L.extra.get("words") or []
        content = _content_words(words)
        if content:
            last_word_end = max(float(w["end"]) for w in content)
            if last_word_end > L.end + 0.02:
                L.end = last_word_end
            if last_word_end > L.t1 + 0.02:
                L.t1 = last_word_end
        glyph_starts = _glyph_starts_from_words(L.text, words, t0=L.t0, t1=L.t1)
        if not glyph_starts or len(glyph_starts) < 1:
            assign_chunk_times(
                [L],
                reveal_frac=reveal_frac,
                max_per_char=max_per_char,
                min_tail_hold=min_tail_hold,
            )
            L.extra["chunk_punch"] = [1.0] * len(L.chunks)
            L.extra["punch_fallback"] = "uniform"
            continue

        # Build cleaned glyph list matching glyph_starts length
        clean_text = L.text.replace(" ", "").replace("\u3000", "")
        # glyph_chunks works on _clean text; rebuild index into clean_text
        chunks = L.chunks
        # Map each chunk to start = first glyph start; end = last glyph end
        # Walk clean_text with same chunking
        idx = 0
        chunk_starts: List[float] = []
        chunk_ends: List[float] = []
        # Estimate per-glyph ends from neighboring starts
        g_ends = []
        for i, st in enumerate(glyph_starts):
            if i + 1 < len(glyph_starts):
                g_ends.append(max(st + 0.04, glyph_starts[i + 1]))
            else:
                g_ends.append(min(L.t1, st + 0.2))

        for ch in chunks:
            n = max(1, len(ch))
            if idx >= len(glyph_starts):
                # pad from last
                st = chunk_starts[-1] if chunk_starts else L.t0
                en = min(L.t1, st + 0.1)
            else:
                st = glyph_starts[idx]
                en = g_ends[min(idx + n - 1, len(g_ends) - 1)]
            chunk_starts.append(st)
            chunk_ends.append(en)
            idx += n

        # Enforce monotonic non-decreasing starts within [t0,t1]
        prev = L.t0
        fixed: List[float] = []
        for st in chunk_starts:
            st = min(L.t1, max(prev, min(L.t1, max(L.t0, st))))
            fixed.append(st)
            prev = st
        fixed = _apply_min_tail_hold(
            fixed, L.t0, L.t1, min_tail_hold=min_tail_hold
        )
        L.chunk_times = fixed
        L.extra["chunk_punch"] = _chunk_punch_intensities(
            fixed, chunk_ends, t1=L.t1
        )
        L.extra.pop("punch_fallback", None)


@dataclass
class ConcatPart:
    kind: str  # "title" | "line" | "gap"
    t0: float
    t1: float
    line_index: Optional[int] = None  # for kind=="line"


def build_concat_list(
    lines: Sequence[TimedLine],
    *,
    title_dur: float = 2.0,
    audio_dur: float,
    fps: int = 24,
) -> List[ConcatPart]:
    """Build ordered concat parts: title, then gaps + lines, then end gap.

    Title and lyrics share the same audio clock: title occupies [0, title_dur],
    then a gap is inserted when first_line.t0 > title_dur (typically ~1s when
    auto title_dur = first.t0 - 1). Lyric t0/t1 stay absolute audio times —
    never shift them to compensate for the title.
    """
    parts: List[ConcatPart] = []
    if title_dur > 0:
        parts.append(ConcatPart(kind="title", t0=0.0, t1=title_dur))

    # After title, follow absolute audio clock of lyric lines
    t = title_dur if title_dur > 0 else 0.0
    # Lyric lines use audio-absolute t0/t1; if title is shown, we still
    # place gaps relative to audio clock starting from title_dur cursor
    # matching reference: title then jump to first line t0 on audio clock.
    t = title_dur  # cursor on concat timeline == audio clock after title
    # Reference dazibao: title 0..TITLE_DUR, then if line.t0 > t insert gap
    for i, L in enumerate(lines):
        if L.t0 > t + 1.0 / fps:
            parts.append(ConcatPart(kind="gap", t0=t, t1=L.t0))
        parts.append(ConcatPart(kind="line", t0=L.t0, t1=L.t1, line_index=i))
        t = L.t1
    if t < audio_dur - 0.05:
        parts.append(ConcatPart(kind="gap", t0=t, t1=audio_dur))
    return parts


def no_overlap(lines: Sequence[TimedLine], eps: float = 1e-6) -> bool:
    """Return True if consecutive t0/t1 never overlap."""
    prev = 0.0
    for L in lines:
        if L.t0 < prev - eps:
            return False
        if L.t1 < L.t0 - eps:
            return False
        prev = L.t1
    return True
