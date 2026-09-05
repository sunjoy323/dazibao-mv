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


def assign_chunk_times(
    lines: Sequence[TimedLine],
    *,
    reveal_frac: float = REVEAL_FRAC,
    max_per_char: float = MAX_PER_CHAR,
) -> None:
    """Distribute reveal times within each clamped [t0, t1]."""
    for L in lines:
        if not L.chunks:
            from .split import glyph_chunks

            L.chunks = glyph_chunks(L.text) or [L.text]
        n = max(1, len(L.chunks))
        dur = max(0.2, L.t1 - L.t0)
        reveal_dur = min(dur * reveal_frac, n * max_per_char)
        reveal_dur = max(reveal_dur, min(dur * 0.35, n * 0.18))
        L.chunk_times = [L.t0 + reveal_dur * (i / n) for i in range(n)]


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
