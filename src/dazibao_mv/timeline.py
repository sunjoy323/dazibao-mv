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
