"""PIL kinetic layouts + ffmpeg encode / concat / mux."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .bg import prepare_background, solid_bg
from .split import glyph_chunks, split_line
from .styles import (
    classify_line,
    color_tuple,
    is_poster_fill,
    load_style,
    palette_for,
    resolve_font,
    style_bg_hex,
)
from .timeline import (
    LAYOUTS,
    POSTER_LAYOUTS,
    TimedLine,
    assign_chunk_times,
    assign_layouts,
    assign_poster_layouts,
    assign_style_layouts,
    build_concat_list,
    clamp_timeline,
    layouts_for_style,
)

REVEAL_FRAC = 0.48
MAX_PER_CHAR = 0.30


def compute_punch_state(t_local: float, style: Dict[str, Any]) -> Tuple[float, str, Dict[str, Any]]:
    """Return (scale, kind, extras) for the newest glyph.

    kinds: scale (default), glitch, slide, slam, soft
    extras may include glitch_ox, slide (0..1 remaining).
    """
    cfg = style.get("punch") or {}
    kind = str(cfg.get("kind") or "scale").strip().lower()
    try:
        amount = float(cfg.get("amount", 1.32))
    except (TypeError, ValueError):
        amount = 1.32
    try:
        glitch_px = int(cfg.get("glitch_px", 12))
    except (TypeError, ValueError):
        glitch_px = 12
    t = max(0.0, float(t_local))
    extras: Dict[str, Any] = {}

    if kind == "slide":
        # No bounce scale; slide progress 1→0 (~0.2s)
        extras["slide"] = max(0.0, 1.0 - t * 6.0)
        return 1.0, kind, extras
    if kind == "slam":
        # 2.2 → 1.0 in ~3–4 frames @24fps
        decay = max(0.0, 1.0 - t / 0.14)
        return 1.0 + (amount - 1.0) * decay, kind, extras
    if kind == "glitch":
        decay = max(0.0, 1.0 - t * 20.0)
        scale = 1.0 + (amount - 1.0) * decay
        # ~1-frame horizontal glitch on newest glyph
        if t < (1.0 / 24.0):
            extras["glitch_ox"] = glitch_px if (int(t * 48) % 2 == 0) else -glitch_px
        else:
            extras["glitch_ox"] = 0
        return scale, kind, extras
    if kind == "soft":
        decay = max(0.0, 1.0 - t * 5.0)
        return 1.0 + (amount - 1.0) * decay, kind, extras
    # scale (classic ivory/poster): 1.0 + 0.32 * decay
    overshoot = (amount - 1.0) if amount > 1.0 else 0.32
    decay = max(0.0, 1.0 - t * 12.0)
    return 1.0 + overshoot * decay, "scale", extras


def resolve_gap_mode(style: Dict[str, Any], gap_mode: Optional[str] = None) -> str:
    """CLI/explicit wins; else style.gap_mode; else hold. cut→black.

    Neon/comic styles never default to flash via YAML — only CLI ``--gap-mode flash``.
    """
    cli_explicit = gap_mode not in (None, "", "auto")
    raw = gap_mode if cli_explicit else None
    if raw is None:
        raw = style.get("gap_mode") or "hold"
        mode = str(style.get("mode") or "").strip().lower()
        if str(raw).lower().strip() == "flash" and mode in ("neon", "comic"):
            raw = "hold"
    g = str(raw).lower().strip()
    if g == "cut":
        g = "black"
    if g not in ("hold", "black", "flash"):
        g = "hold"
    return g


def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install ffmpeg and ensure it is available."
        )
    return exe


def _finite_duration(value: Any) -> Optional[float]:
    """Return a positive finite float duration, or None if unusable."""
    if value is None:
        return None
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _hhmmss_to_seconds(h: int, m: int, s: float) -> float:
    return h * 3600 + m * 60 + s


def _parse_ffmpeg_duration_line(text: str) -> Optional[float]:
    """Parse ``Duration: HH:MM:SS.xx`` from ffmpeg -i stderr; skip N/A."""
    import re

    m = re.search(r"Duration:\s*(?:N/A|(\d+):(\d+):(\d+(?:\.\d+)?))", text or "", re.I)
    if not m or m.group(1) is None:
        return None
    return _finite_duration(_hhmmss_to_seconds(int(m.group(1)), int(m.group(2)), float(m.group(3))))


def _duration_from_packet_pts(audio: str, pad: float = 0.02) -> Optional[float]:
    """Last audio packet pts_time/dts_time via ffprobe (Chrome WebM often lacks container duration)."""
    exe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.check_output(
            [
                exe,
                "-v",
                "error",
                "-show_entries",
                "packet=pts_time,dts_time",
                "-select_streams",
                "a:0",
                "-of",
                "csv=p=0",
                audio,
            ],
            text=True,
            timeout=600,
        )
    except Exception:
        return None
    last: Optional[float] = None
    for line in out.splitlines():
        for part in line.split(","):
            part = part.strip()
            if not part:
                continue
            v = _finite_duration(part)
            if v is not None:
                last = v
    if last is None:
        return None
    return last + max(0.0, float(pad))


def _duration_from_decode(audio: str) -> Optional[float]:
    """Decode with ffmpeg -f null and parse last ``time=`` progress value."""
    import re

    exe = shutil.which("ffmpeg") or "ffmpeg"
    try:
        r = subprocess.run(
            [exe, "-hide_banner", "-i", audio, "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception:
        return None
    text = (r.stderr or "") + "\n" + (r.stdout or "")
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not times:
        return None
    h, m, s = times[-1]
    return _finite_duration(_hhmmss_to_seconds(int(h), int(m), float(s)))


def _probe_audio_duration(audio: str) -> float:
    """Probe media duration; harden for Chrome WebM with ``Duration: N/A``.

    Order: format duration → stream a:0 duration → ffmpeg -i Duration line →
    last audio packet pts/dts → full decode ``time=`` progress.
    """
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    # 1) format=duration
    try:
        out = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                audio,
            ],
            text=True,
            timeout=120,
        ).strip()
        v = _finite_duration(out)
        if v is not None:
            return v
    except Exception:
        pass

    # 2) stream=duration for a:0
    try:
        out = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=duration",
                "-select_streams",
                "a:0",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                audio,
            ],
            text=True,
            timeout=120,
        ).strip()
        v = _finite_duration(out)
        if v is not None:
            return v
    except Exception:
        pass

    # 3) Parse Duration: HH:MM:SS.xx from ffmpeg -i (skip N/A)
    try:
        r = subprocess.run(
            [ffmpeg, "-i", audio],
            capture_output=True,
            text=True,
            timeout=120,
        )
        v = _parse_ffmpeg_duration_line(r.stderr or "")
        if v is not None:
            return v
    except Exception:
        pass

    # 4) last audio packet pts_time / dts_time
    v = _duration_from_packet_pts(audio)
    if v is not None:
        return v

    # 5) decode and parse time= progress
    v = _duration_from_decode(audio)
    if v is not None:
        return v

    raise RuntimeError(f"Cannot probe audio duration: {audio}")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


def _rgba(rgb: Tuple[int, ...], a: int = 255) -> Tuple[int, int, int, int]:
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]), int(a))


def layered_text(draw, xy, text, font, fill, shadow, accent, is_new=False):
    x, y = xy
    for ox, oy in [(8, 8), (6, 6), (10, 7), (7, 10)]:
        draw.text((x + ox, y + oy), text, font=font, fill=shadow)
    draw.text((x - 4, y + 2), text, font=font, fill=accent)
    draw.text((x + 3, y - 3), text, font=font, fill=accent)
    for ox in range(-3, 4, 2):
        for oy in range(-3, 4, 2):
            if ox or oy:
                draw.text((x + ox, y + oy), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)
    if is_new:
        draw.text((x - 1, y - 1), text, font=font, fill=(255, 255, 255, 90))


def hard_block_shadow_text(
    draw,
    xy,
    text,
    font,
    fill,
    shadow,
    offset: Tuple[int, int] = (18, 18),
    is_new: bool = False,
):
    """Single thick hard-offset block shadow (poster style — no soft multi-layer)."""
    x, y = xy
    ox, oy = int(offset[0]), int(offset[1])
    draw.text((x + ox, y + oy), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)
    if is_new:
        draw.text((x - 1, y - 1), text, font=font, fill=(255, 255, 255, 70))


def _style_mode(style: Dict[str, Any]) -> str:
    return str(style.get("mode") or "").strip().lower()


def neon_glow_text(
    overlay: Image.Image,
    xy,
    text,
    font,
    fill,
    cyan,
    magenta,
    is_new: bool = False,
):
    """Electric dual-glow (magenta outer + cyan inner) then bright core fill."""
    x, y = xy
    W, H = overlay.size
    # Magenta outer bloom
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((x, y), text, font=font, fill=_rgba(magenta[:3], 200))
    overlay.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius=14)))
    # Cyan tighter bloom
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((x, y), text, font=font, fill=_rgba(cyan[:3], 220))
    overlay.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius=5)))
    draw = ImageDraw.Draw(overlay)
    # slight dual-offset accents
    draw.text((x - 2, y + 1), text, font=font, fill=_rgba(magenta[:3], 120))
    draw.text((x + 2, y - 1), text, font=font, fill=_rgba(cyan[:3], 140))
    draw.text((x, y), text, font=font, fill=fill)
    if is_new:
        draw.text((x - 1, y - 1), text, font=font, fill=(255, 255, 255, 110))


def comic_outline_text(
    draw,
    xy,
    text,
    font,
    fill,
    outline=(10, 10, 10, 255),
    thickness: int = 5,
    is_new: bool = False,
):
    """Thick black outline + flat primary fill (pop-comic punch)."""
    x, y = xy
    o = _rgba(outline[:3], outline[3] if len(outline) > 3 else 255)
    for ox in range(-thickness, thickness + 1):
        for oy in range(-thickness, thickness + 1):
            if ox * ox + oy * oy <= thickness * thickness + thickness:
                if ox or oy:
                    draw.text((x + ox, y + oy), text, font=font, fill=o)
    draw.text((x, y), text, font=font, fill=fill)
    if is_new:
        draw.text((x - 1, y - 1), text, font=font, fill=(255, 255, 255, 100))


def soft_ink_text(
    overlay: Image.Image,
    xy,
    text,
    font,
    fill,
    shadow,
    offset: Tuple[int, int] = (6, 8),
    is_new: bool = False,
):
    """Deep ink fill with soft smudged (blurred) shadow — calligraphy poster feel."""
    x, y = xy
    W, H = overlay.size
    ox, oy = int(offset[0]), int(offset[1])
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    sh = shadow if len(shadow) == 4 else _rgba(shadow[:3], 100)
    ld.text((x + ox, y + oy), text, font=font, fill=sh)
    # slight extra smear offsets before blur
    ld.text((x + ox + 2, y + oy + 1), text, font=font, fill=_rgba(sh[:3], max(40, sh[3] // 2)))
    overlay.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius=4)))
    draw = ImageDraw.Draw(overlay)
    draw.text((x, y), text, font=font, fill=fill)
    if is_new:
        draw.text((x - 1, y - 1), text, font=font, fill=(255, 255, 255, 50))


def draw_neon_scanlines(overlay: Image.Image, alpha: int = 28) -> None:
    """Subtle CRT-ish scanlines over near-black neon frames."""
    W, H = overlay.size
    lines = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lines)
    for y in range(0, H, 3):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, alpha), width=1)
    # faint cyan vignette edges
    d.rectangle([0, 0, W - 1, H - 1], outline=(0, 255, 255, 35), width=3)
    overlay.alpha_composite(lines)


def draw_blueprint_decor(
    draw: ImageDraw.ImageDraw,
    W: int,
    H: int,
    *,
    line_index: int = 0,
    grid: int = 54,
    ink: Tuple[int, int, int, int] = (160, 210, 255, 55),
) -> None:
    """Faint drafting grid + crosshair marks (blueprint mode)."""
    # major grid
    major = ink
    minor = (ink[0], ink[1], ink[2], max(25, ink[3] // 2))
    for x in range(0, W, grid // 2):
        col = major if x % grid == 0 else minor
        draw.line([(x, 0), (x, H)], fill=col, width=1)
    for y in range(0, H, grid // 2):
        col = major if y % grid == 0 else minor
        draw.line([(0, y), (W, y)], fill=col, width=1)
    # crosshairs (center + corners)
    cx, cy = W // 2, H // 2
    arm = 48
    cross = (200, 240, 255, 90)
    for px, py in ((cx, cy), (120, 160), (W - 120, H - 200)):
        draw.line([(px - arm, py), (px + arm, py)], fill=cross, width=2)
        draw.line([(px, py - arm), (px, py + arm)], fill=cross, width=2)
        draw.ellipse([px - 6, py - 6, px + 6, py + 6], outline=cross, width=2)
    # sheet border
    m = 28
    draw.rectangle([m, m, W - m, H - m], outline=(180, 220, 255, 70), width=2)
    # tiny sheet label
    lab = f"DWG-{(line_index % 99) + 1:02d}"
    try:
        f = ImageFont.load_default()
        draw.text((m + 8, m + 6), lab, font=f, fill=(200, 230, 255, 100))
    except Exception:
        pass


def draw_comic_burst(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    radius: float,
    *,
    fill=(255, 255, 255, 180),
    outline=(10, 10, 10, 255),
    rays: int = 14,
) -> None:
    """Radial comic burst / speed-starburst behind the newest glyph."""
    pts_outer = []
    for i in range(rays * 2):
        ang = -math.pi / 2 + i * math.pi / rays
        r = radius if i % 2 == 0 else radius * 0.42
        pts_outer.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    draw.polygon(pts_outer, fill=fill, outline=outline)
    # halftone-ish dots ring
    for i in range(rays):
        ang = i * 2 * math.pi / rays + 0.2
        rr = radius * 0.72
        dx = cx + rr * math.cos(ang)
        dy = cy + rr * math.sin(ang)
        draw.ellipse([dx - 4, dy - 4, dx + 4, dy + 4], fill=outline)


def draw_styled_text(
    overlay: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy,
    text,
    font,
    fill,
    shadow,
    accent,
    style: Dict[str, Any],
    *,
    is_new: bool = False,
    use_hard: bool = False,
) -> None:
    """Dispatch text paint by style mode (neon / comic / ink / poster / default)."""
    mode = _style_mode(style)
    shadow_off = tuple(style.get("shadow_offset") or (18, 18))
    if mode == "neon":
        # cyan from shadow channel, magenta from accent
        neon_glow_text(overlay, xy, text, font, fill, shadow, accent, is_new=is_new)
    elif mode == "comic":
        comic_outline_text(draw, xy, text, font, fill, outline=shadow, thickness=5, is_new=is_new)
    elif mode in ("ink", "ink_wash", "ink-wash"):
        soft_ink_text(overlay, xy, text, font, fill, shadow, offset=shadow_off, is_new=is_new)
    elif mode == "blueprint":
        # crisp drafting type: thin cyan underglow + white fill
        x, y = xy
        for ox, oy in ((2, 2), (1, 1), (-1, 0), (0, -1)):
            draw.text((x + ox, y + oy), text, font=font, fill=shadow)
        draw.text((x - 1, y), text, font=font, fill=accent)
        draw.text((x, y), text, font=font, fill=fill)
        if is_new:
            draw.text((x - 1, y - 1), text, font=font, fill=(255, 255, 255, 80))
    elif use_hard or mode == "poster_fill":
        hard_block_shadow_text(
            draw, xy, text, font, fill, shadow, offset=shadow_off, is_new=is_new
        )
    else:
        layered_text(draw, xy, text, font, fill, shadow, accent, is_new)


def fit_font_size(draw, text, font_path, max_size, max_w, max_h=None):
    size = max_size
    while size >= 28:
        f = _font(font_path, size)
        bb = draw.textbbox((0, 0), text, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if tw <= max_w and (max_h is None or th <= max_h):
            return f, bb
        size -= 4
    f = _font(font_path, 28)
    return f, draw.textbbox((0, 0), text, font=f)


def size_scale_for(nchar: int) -> float:
    if nchar <= 6:
        return 1.0
    if nchar <= 8:
        return 0.82
    if nchar <= 10:
        return 0.68
    return 0.55


def _star_points(cx: float, cy: float, r_outer: float, r_inner: float, n: int = 5):
    pts = []
    for i in range(n * 2):
        ang = -math.pi / 2 + i * math.pi / n
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def draw_poster_decor(
    draw: ImageDraw.ImageDraw,
    overlay: Image.Image,
    W: int,
    H: int,
    palette: Dict[str, Any],
    *,
    line_index: int = 0,
    font_path: str = "",
    with_stars: bool = False,
    alpha: float = 1.0,
) -> None:
    """Double border, mid rules, corner stamp「大字报」, serial labels."""
    a = max(0, min(255, int(round(255 * alpha))))
    if a <= 0:
        return
    border = _rgba(palette.get("border", (242, 237, 228)), a)
    rule = _rgba(palette.get("rule", border[:3]), a)
    stamp_bg = _rgba(palette.get("stamp_bg", (196, 30, 58)), a)
    stamp_fg = _rgba(palette.get("stamp_fg", (10, 10, 10)), a)
    label_c = _rgba(palette.get("label", border[:3]), a)

    margin = 36
    # outer thick + inner thin double rect
    draw.rectangle([margin, margin, W - margin, H - margin], outline=border, width=6)
    inset = margin + 14
    draw.rectangle([inset, inset, W - inset, H - inset], outline=border, width=2)

    # 1–2 thin horizontal rules behind text zone
    y1 = int(H * 0.38)
    y2 = int(H * 0.62)
    draw.line([(inset + 8, y1), (W - inset - 8, y1)], fill=rule, width=2)
    draw.line([(inset + 8, y2), (W - inset - 8, y2)], fill=rule, width=2)

    if with_stars:
        r_star = 22
        # top-left accent star (stamp-ish), top-right border color
        for cx, cy, col in (
            (inset + 48, inset + 48, stamp_bg),
            (W - inset - 48, inset + 48, border),
        ):
            draw.polygon(_star_points(cx, cy, r_star, r_star * 0.42), fill=col)

    # tiny corner serials
    serial = f"字第{(line_index % 999) + 1:03d}号"
    lab_font = _font(font_path, 22) if font_path else ImageFont.load_default()
    draw.text((inset + 10, H - inset - 36), serial, font=lab_font, fill=label_c)
    draw.text((W - inset - 130, H - inset - 36), "工厂样片", font=lab_font, fill=label_c)

    # tilted stamp bottom-right 「大字报」
    stamp_w, stamp_h = 78, 110
    stamp = Image.new("RGBA", (stamp_w + 20, stamp_h + 20), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.rectangle([8, 8, 8 + stamp_w, 8 + stamp_h], fill=stamp_bg)
    sf = _font(font_path, 28) if font_path else ImageFont.load_default()
    # vertical characters
    chars = "大字报"
    cy = 18
    for ch in chars:
        bb = sd.textbbox((0, 0), ch, font=sf)
        tw = bb[2] - bb[0]
        sd.text((8 + (stamp_w - tw) // 2, cy - bb[1]), ch, font=sf, fill=stamp_fg)
        cy += (bb[3] - bb[1]) + 4
    stamp = stamp.rotate(18, expand=True, resample=Image.Resampling.BICUBIC)
    sx = W - inset - stamp.width - 10
    sy = H - inset - stamp.height - 50
    overlay.alpha_composite(stamp, (max(0, sx), max(0, sy)))


def _fit_poster_h_font(draw, text, font_path, W, H, shadow_off, target_w_frac=0.91):
    """Auto-fit horizontal string to ~88–94% width and as tall as margins allow."""
    ox, oy = shadow_off
    margin = 70
    max_w = int(W * target_w_frac) - ox
    max_h = H - 2 * margin - oy
    # binary-ish descent from large size
    lo, hi = 40, int(min(W, H) * 0.72)
    best = _font(font_path, lo)
    best_bb = draw.textbbox((0, 0), text, font=best)
    while lo <= hi:
        mid = (lo + hi) // 2
        f = _font(font_path, mid)
        bb = draw.textbbox((0, 0), text, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if tw <= max_w and th <= max_h:
            best, best_bb = f, bb
            lo = mid + 2
        else:
            hi = mid - 2
    return best, best_bb


def _fit_poster_v_size(draw, chunks, font_path, W, H, shadow_off, target_h_frac=0.88):
    """Font size so vertical stack fills ~85–92% height; each glyph fits width."""
    ox, oy = shadow_off
    margin = 80
    max_h = int(H * target_h_frac) - oy
    max_w = W - 2 * margin - ox
    n = max(1, len(chunks))
    lo, hi = 40, int(H * 0.55)
    best = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        f = _font(font_path, mid)
        total_h = 0
        max_gw = 0
        ok = True
        for ch in chunks:
            bb = draw.textbbox((0, 0), ch, font=f)
            gw, gh = bb[2] - bb[0], bb[3] - bb[1]
            total_h += gh + 8
            max_gw = max(max_gw, gw)
            if gw > max_w:
                ok = False
                break
        if ok and total_h - 8 <= max_h:
            best = mid
            lo = mid + 2
        else:
            hi = mid - 2
    return best


def draw_layout(
    base: Image.Image,
    chunks_visible: Sequence[str],
    full_text: str,
    layout: str,
    t_local: float,
    style: Dict[str, Any],
    *,
    chorus: bool = False,
    hook: bool = False,
    width: int = 1080,
    height: int = 1920,
    palette: Optional[Dict[str, Any]] = None,
    line_index: int = 0,
    decor: bool = False,
) -> Image.Image:
    W, H = width, height
    canvas = base.copy().convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # expose image for stamp compositing
    shown = "".join(chunks_visible)
    font_path = resolve_font(style.get("font"))

    mode = _style_mode(style)
    # Mode / poster decor under text
    if decor and palette is not None and mode in ("", "poster_fill"):
        with_stars = (line_index % 3 == 2) or layout == "poster_fill_v"
        draw_poster_decor(
            draw, overlay, W, H, palette,
            line_index=line_index,
            font_path=font_path,
            with_stars=with_stars,
        )
    elif mode == "blueprint" and (decor or style.get("decor", True)):
        draw_blueprint_decor(draw, W, H, line_index=line_index)

    if not shown:
        if mode == "neon":
            draw_neon_scanlines(overlay)
        return Image.alpha_composite(canvas, overlay).convert("RGB")

    role = "hook" if hook else ("chorus" if chorus else "verse")
    if palette is not None:
        fill = _rgba(palette.get("fill", (242, 237, 228)))
        shadow = _rgba(palette.get("shadow", (196, 30, 58)))
        accent = _rgba(palette.get("accent", fill[:3]))
    else:
        fill = color_tuple(style, role, "fill")
        shadow = color_tuple(style, role, "shadow")
        accent = color_tuple(style, role, "accent")
    box_c = color_tuple(style, role, "box")
    verse_fill = color_tuple(style, "verse", "fill")
    verse_shadow = color_tuple(style, "verse", "shadow")
    verse_accent = color_tuple(style, "verse", "accent")

    n = len(chunks_visible)
    punch, punch_kind, punch_extras = compute_punch_state(t_local, style)
    glitch_ox = int(punch_extras.get("glitch_ox") or 0)
    slide_rem = float(punch_extras.get("slide") or 0.0)
    nchar = max(1, len(shown.replace(" ", "")))
    sc_all = size_scale_for(nchar)
    shadow_off = tuple(style.get("shadow_offset") or (18, 18))
    use_hard = palette is not None or layout.startswith("poster_fill")
    # Smash bars: keep classic dark bars only when style box has opacity
    # (new modes set box alpha 0 — avoid muddy rectangles on colored bgs).
    bar_fallback = box_c if (len(box_c) > 3 and box_c[3] == 0) or mode in (
        "neon", "blueprint", "comic", "ink", "ink_wash", "ink-wash"
    ) else (8, 8, 10, 150)

    def font_for(i: int, base_size: int):
        sc = punch if i == n - 1 else 1.0
        return _font(font_path, max(24, int(base_size * sc_all * sc)))

    def put_text(d, xy, text, font, is_new=False, target=None,
                  fill_=None, shadow_=None, accent_=None, ox=0, oy=0):
        nonlocal draw, overlay
        tgt = target if target is not None else overlay
        fl = fill_ if fill_ is not None else fill
        sh = shadow_ if shadow_ is not None else shadow
        ac = accent_ if accent_ is not None else accent
        x0, y0 = xy
        # Neon glitch offset on newest glyph
        if is_new and glitch_ox:
            x0 += glitch_ox
        x0 += int(ox)
        y0 += int(oy)
        # Comic burst behind newest glyph
        if mode == "comic" and is_new and (decor or style.get("decor", True)):
            bb = d.textbbox((x0, y0), text, font=font)
            cx = (bb[0] + bb[2]) / 2
            cy = (bb[1] + bb[3]) / 2
            rad = max(40, (bb[2] - bb[0]) * 0.85)
            burst_fill = (255, 255, 255, 200) if hook or chorus else (255, 250, 200, 190)
            draw_comic_burst(d, cx, cy, rad, fill=burst_fill, outline=sh)
        # Neon dual ghost layers already in neon_glow_text; brief magenta/cyan offset
        if mode == "neon" and is_new and punch > 1.05:
            draw_styled_text(
                tgt, d, (x0 - 6, y0), text, font,
                _rgba(accent[:3], 90), sh, ac, style,
                is_new=False, use_hard=False,
            )
            draw_styled_text(
                tgt, d, (x0 + 6, y0), text, font,
                _rgba(shadow[:3], 90), sh, ac, style,
                is_new=False, use_hard=False,
            )
            if target is None:
                draw = ImageDraw.Draw(overlay)
                d = draw
        draw_styled_text(
            tgt, d, (x0, y0), text, font, fl, sh, ac, style,
            is_new=is_new, use_hard=use_hard and mode in ("", "poster_fill"),
        )
        # neon/ink mutate target; refresh draw handle when painting main overlay
        if target is None:
            draw = ImageDraw.Draw(overlay)

    if layout == "poster_fill_h":
        # Size against FULL line so layout stays stable while glyphs punch in
        full = full_text or shown
        f_base, bb_full = _fit_poster_h_font(draw, full, font_path, W, H, shadow_off)
        base_size = getattr(f_base, "size", 120)
        # Draw visible chunks left-to-right with stable full-line centering
        # Measure each chunk at settled size; newest may punch
        settled = []
        for i, ch in enumerate(chunks_visible):
            sc = punch if i == n - 1 else 1.0
            f = _font(font_path, max(24, int(base_size * sc)))
            bb = draw.textbbox((0, 0), ch, font=f)
            settled.append((ch, f, bb))
        # Total width of settled (approx) — for centering use full-line width at base
        full_w = bb_full[2] - bb_full[0]
        # Build positions from left of full string box
        # Recompute using settled sizes for visible only, centered as a group
        gap = 0
        total_w = sum(bb[2] - bb[0] for _, _, bb in settled) + gap * max(0, n - 1)
        # Center visible string; full_w keeps sizing stable as glyphs punch in
        _ = full_w  # sizing reference (fit against full line)
        x = (W - total_w - shadow_off[0]) // 2
        # vertical center using full glyph height
        full_h = bb_full[3] - bb_full[1]
        y = (H - full_h - shadow_off[1]) // 2 - bb_full[1]
        for i, (ch, f, bb) in enumerate(settled):
            tw = bb[2] - bb[0]
            # recenter each punched glyph vertically if scaled
            th = bb[3] - bb[1]
            yi = (H - th - shadow_off[1]) // 2 - bb[1]
            put_text(draw, (x, yi), ch, f, is_new=(i == n - 1))
            x += tw + gap

    elif layout == "poster_fill_v":
        full_chunks = glyph_chunks(full_text) if full_text else list(chunks_visible)
        if not full_chunks:
            full_chunks = list(chunks_visible)
        base_size = _fit_poster_v_size(draw, full_chunks, font_path, W, H, shadow_off)
        # Measure full stack height at settled size for centering
        gap = 8
        full_bbs = []
        for ch in full_chunks:
            f = _font(font_path, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            full_bbs.append(bb)
        total_h = sum(bb[3] - bb[1] for bb in full_bbs) + gap * max(0, len(full_bbs) - 1)
        y = (H - total_h - shadow_off[1]) // 2
        for i, ch in enumerate(chunks_visible):
            sc = punch if i == n - 1 else 1.0
            f = _font(font_path, max(24, int(base_size * sc)))
            bb = draw.textbbox((0, 0), ch, font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = (W - tw - shadow_off[0]) // 2
            # use settled slot y from full stack
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            # advance by settled (non-punched) height for stable slots
            f_set = _font(font_path, base_size)
            bb_set = draw.textbbox((0, 0), ch, font=f_set)
            y += (bb_set[3] - bb_set[1]) + gap

    elif layout == "giant_char":
        ch = chunks_visible[-1]
        mode_now = _style_mode(style)
        fill_frac = 0.82 if mode_now in ("neon", "comic", "blueprint") else 0.60
        f = font_for(n - 1, int(min(W, H) * fill_frac))
        bb = draw.textbbox((0, 0), ch, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        pad = 28 if mode_now in ("neon", "comic", "blueprint") else 52
        bx0, by0 = (W - tw) // 2 - pad, (H - th) // 2 - pad
        draw.rectangle([bx0, by0, bx0 + tw + 2 * pad, by0 + th + 2 * pad], fill=box_c)
        put_text(draw, ((W - tw) // 2, (H - th) // 2 - bb[1]), ch, f, is_new=True)
        if n > 1:
            trail = "".join(chunks_visible[:-1])[-6:]
            f2 = _font(font_path, 86)
            put_text(
                draw, (48, H - 210), trail, f2, is_new=False,
                fill_=verse_fill, shadow_=verse_shadow, accent_=verse_accent,
            )

    elif layout == "diagonal":
        base_size = 160
        for i, ch in enumerate(chunks_visible):
            tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            td = ImageDraw.Draw(tmp)
            f = font_for(i, base_size)
            bb = td.textbbox((0, 0), ch, font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = 55 + i * 105
            y = 250 + i * 125
            td.rectangle(
                [x - 14, y - 10, x + tw + 14, y + th + 10],
                fill=box_c if i == n - 1 else bar_fallback,
            )
            put_text(td, (x, y - bb[1]), ch, f, is_new=(i == n - 1), target=tmp)
            if i == n - 1:
                tmp = tmp.rotate(-8, resample=Image.Resampling.BICUBIC, center=(x + tw / 2, y + th / 2))
            overlay = Image.alpha_composite(overlay, tmp)
            draw = ImageDraw.Draw(overlay)

    elif layout == "left_stack":
        base_size = 195 if len(shown) <= 5 else 148
        while base_size >= 40:
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                total_h += (bb[3] - bb[1]) + 12
                max_w = max(max_w, bb[2] - bb[0] + 40)
            if total_h <= H - 200 and max_w <= W - 80:
                break
            base_size -= 8
        y = 200
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = 56
            draw.rectangle(
                [x - 18, y - 12, x + tw + 18, y + th + 12],
                fill=box_c if i == n - 1 else bar_fallback,
            )
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            y += th + 12

    elif layout == "right_cascade":
        base_size = 170
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = W - 72 - tw - i * 26
            y = 370 + i * (th + 6)
            draw.rectangle([x - 16, y - 10, x + tw + 16, y + th + 10], fill=bar_fallback)
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))

    elif layout == "top_heavy":
        f1 = font_for(0, 250)
        head = chunks_visible[0]
        bb = draw.textbbox((0, 0), head, font=f1)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        draw.rectangle([20, 150, W - 20, 150 + th + 68], fill=box_c)
        put_text(draw, ((W - tw) // 2, 178 - bb[1]), head, f1, is_new=(n == 1))
        if n > 1:
            rest = "".join(chunks_visible[1:])
            size2 = 120
            f2 = _font(font_path, size2)
            bb2 = draw.textbbox((0, 0), rest, font=f2)
            while bb2[2] - bb2[0] > W - 50 and size2 > 48:
                size2 -= 6
                f2 = _font(font_path, size2)
                bb2 = draw.textbbox((0, 0), rest, font=f2)
            tw2 = bb2[2] - bb2[0]
            put_text(draw, ((W - tw2) // 2, 150 + th + 100 - bb2[1]), rest, f2, is_new=False)

    elif layout == "bottom_banner":
        base_size = 150
        y = H - 500
        draw.rectangle([0, y - 55, W, y + 280], fill=box_c)
        fonts_bbs = []
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            fonts_bbs.append((ch, f, bb))
        total_w = sum(bb[2] - bb[0] for _, _, bb in fonts_bbs) + 8 * max(0, n - 1)
        while total_w > W - 50 and base_size > 56:
            base_size -= 8
            fonts_bbs = []
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                fonts_bbs.append((ch, f, bb))
            total_w = sum(bb[2] - bb[0] for _, _, bb in fonts_bbs) + 8 * max(0, n - 1)
        x = (W - total_w) // 2
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw = bb[2] - bb[0]
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            x += tw + 8


    elif layout == "neon_col_left" or layout == "neon_col_right" or layout == "neon_stack_center":
        # Tall edge/center columns filling ~90–94% height; margins ≤40–50px
        side = "left" if layout == "neon_col_left" else ("right" if layout == "neon_col_right" else "center")
        target_h = int(H * 0.93)
        margin_x = 36
        glyph_gap = 4
        max_col_w = W * 0.55 if side == "center" else W * 0.50
        base_size = int(H * 0.18)
        while base_size >= 36:
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                total_h += (bb[3] - bb[1]) + glyph_gap
                max_w = max(max_w, bb[2] - bb[0])
            if total_h <= target_h and max_w <= max_col_w:
                break
            base_size -= 6
        fonts_bbs = []
        total_h = 0
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            fonts_bbs.append((ch, f, bb))
            total_h += (bb[3] - bb[1]) + glyph_gap
        y = max(24, (H - total_h) // 2)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            if side == "left":
                x = margin_x
            elif side == "right":
                x = W - margin_x - tw
            else:
                x = (W - tw) // 2
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            y += th + glyph_gap

    elif layout == "blueprint_titleblock":
        # Small caption top + large body type (~90% width)
        caption = f"LINE {(line_index % 99) + 1:02d}  /  SCALE 1:1"
        cf = _font(font_path, 28)
        draw.text((40, 36), caption, font=cf, fill=(180, 220, 255, 160))
        draw.line([(40, 76), (W - 40, 76)], fill=(160, 210, 255, 120), width=2)
        for tx in range(40, W - 40, 48):
            draw.line([(tx, 70), (tx, 82)], fill=(160, 210, 255, 140), width=1)
        full = full_text or shown
        f_base, bb_full = fit_font_size(
            draw, full, font_path, int(H * 0.30), int(W * 0.90), max_h=int(H * 0.30),
        )
        base_size = getattr(f_base, "size", 160)
        settled = []
        for i, ch in enumerate(chunks_visible):
            f = _font(font_path, max(24, int(base_size * (punch if i == n - 1 else 1.0))))
            if punch_kind == "slide":
                f = _font(font_path, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            settled.append((ch, f, bb))
        total_w = sum(bb[2] - bb[0] for _, _, bb in settled)
        x = (W - total_w) // 2
        y = int(H * 0.38)
        for i, (ch, f, bb) in enumerate(settled):
            tw = bb[2] - bb[0]
            ox = int(slide_rem * 80) if (i == n - 1 and punch_kind == "slide") else 0
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1), ox=-ox)
            x += tw
        th_ref = max((bb[3] - bb[1]) for _, _, bb in settled) if settled else 80
        y_rule = y + th_ref + 24
        draw.line([(40, y_rule), (W - 40, y_rule)], fill=(200, 240, 255, 100), width=2)
        draw.line([(40, y_rule - 10), (40, y_rule + 10)], fill=(200, 240, 255, 120), width=2)
        draw.line([(W - 40, y_rule - 10), (W - 40, y_rule + 10)], fill=(200, 240, 255, 120), width=2)

    elif layout == "blueprint_h_rule":
        # Full-width horizontal type ~90% W on mid rule; slide from left
        cy = H // 2
        draw.line([(36, cy), (W - 36, cy)], fill=(180, 220, 255, 160), width=3)
        for tx in range(36, W - 36, 36):
            h = 18 if (tx - 36) % 108 == 0 else 10
            draw.line([(tx, cy - h), (tx, cy + h)], fill=(160, 210, 255, 140), width=1)
        full = full_text or shown
        f_base, _ = fit_font_size(
            draw, full, font_path, int(H * 0.22), int(W * 0.90), max_h=int(H * 0.22),
        )
        base_size = getattr(f_base, "size", 140)
        settled = []
        for i, ch in enumerate(chunks_visible):
            f = _font(font_path, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            settled.append((ch, f, bb))
        total_w = sum(bb[2] - bb[0] for _, _, bb in settled)
        x = max(36, (W - total_w) // 2)
        for i, (ch, f, bb) in enumerate(settled):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            yi = cy - th // 2 - bb[1]
            ox = int(slide_rem * min(W * 0.35, 200)) if (i == n - 1 and punch_kind == "slide") else 0
            put_text(draw, (x, yi), ch, f, is_new=(i == n - 1), ox=-ox)
            x += tw

    elif layout == "blueprint_v_rule":
        # Full-height vertical stack ~90% H; slide from top
        cx = W // 2
        draw.line([(cx, 40), (cx, H - 40)], fill=(180, 220, 255, 160), width=3)
        for ty in range(40, H - 40, 40):
            w = 18 if (ty - 40) % 120 == 0 else 10
            draw.line([(cx - w, ty), (cx + w, ty)], fill=(160, 210, 255, 140), width=1)
        target_h = int(H * 0.90)
        glyph_gap = 6
        base_size = int(min(W, H) * 0.14)
        while base_size >= 36:
            total_h = 0
            max_w = 0
            for ch in chunks_visible:
                f = _font(font_path, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                total_h += (bb[3] - bb[1]) + glyph_gap
                max_w = max(max_w, bb[2] - bb[0])
            if total_h <= target_h and max_w <= W - 72:
                break
            base_size -= 6
        total_h = 0
        fonts_bbs = []
        for ch in chunks_visible:
            f = _font(font_path, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            fonts_bbs.append((ch, f, bb))
            total_h += (bb[3] - bb[1]) + glyph_gap
        y = max(40, (H - total_h) // 2)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = cx - tw // 2
            oy = int(slide_rem * 120) if (i == n - 1 and punch_kind == "slide") else 0
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1), oy=-oy)
            y += th + glyph_gap

    elif layout == "blueprint_corner":
        # Anchored bottom-left corner — still big (~85% H stack)
        draw.line([(36, H - 36), (W - 48, H - 36)], fill=(180, 220, 255, 130), width=2)
        draw.line([(36, 48), (36, H - 36)], fill=(180, 220, 255, 130), width=2)
        for tx in range(36, W - 48, 50):
            draw.line([(tx, H - 44), (tx, H - 28)], fill=(160, 210, 255, 120), width=1)
        for ty in range(48, H - 36, 50):
            draw.line([(28, ty), (44, ty)], fill=(160, 210, 255, 120), width=1)
        glyph_gap = 8
        base_size = int(H * 0.14) if len(shown) <= 5 else int(H * 0.11)
        while base_size >= 40:
            fonts_bbs = []
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size) if punch_kind != "slide" else _font(font_path, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                fonts_bbs.append((ch, f, bb))
                total_h += (bb[3] - bb[1]) + glyph_gap
                max_w = max(max_w, bb[2] - bb[0])
            if total_h <= H * 0.85 and max_w <= W * 0.88:
                break
            base_size -= 8
        y = max(48, H - 48 - total_h)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = 56
            ox = int(slide_rem * 60) if (i == n - 1 and punch_kind == "slide") else 0
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1), ox=-ox)
            y += th + glyph_gap

    elif layout == "comic_panel_full":
        # Thick black comic panel — small yellow margin (~24–36), glyphs max inside
        m = 28
        draw.rectangle([m, m, W - m, H - m], fill=(255, 245, 120, 255))
        draw.rectangle([m, m, W - m, H - m], outline=(10, 10, 10, 255), width=14)
        draw.rectangle([m + 14, m + 14, W - m - 14, H - m - 14], outline=(10, 10, 10, 255), width=3)
        pad = 28
        inner_w, inner_h = W - 2 * m - 2 * pad, H - 2 * m - 2 * pad
        glyph_gap = 6
        base_size = int(H * 0.16) if len(shown) <= 4 else (int(H * 0.12) if len(shown) <= 8 else int(H * 0.09))
        while base_size >= 40:
            fonts_bbs = []
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                fonts_bbs.append((ch, f, bb))
                total_h += (bb[3] - bb[1]) + glyph_gap
                max_w = max(max_w, bb[2] - bb[0] + 12)
            if total_h <= inner_h and max_w <= inner_w:
                break
            base_size -= 8
        y = m + pad + max(0, (inner_h - total_h) // 2)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = (W - tw) // 2
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            y += th + glyph_gap

    elif layout == "comic_slash":
        # Oversized diagonal banner (~width-filling type)
        banner = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(banner)
        # thick diagonal band
        band_h = int(H * 0.36)
        pts = [
            (-80, H // 2 - band_h // 2 + 140),
            (W + 80, H // 2 - band_h // 2 - 140),
            (W + 80, H // 2 + band_h // 2 - 140),
            (-80, H // 2 + band_h // 2 + 140),
        ]
        bd.polygon(pts, fill=(255, 255, 255, 255), outline=(10, 10, 10, 255))
        # black outline by drawing offset polygons
        for off in range(12, 0, -2):
            pts_o = [
                (-80 - off, H // 2 - band_h // 2 + 140),
                (W + 80 + off, H // 2 - band_h // 2 - 140),
                (W + 80 + off, H // 2 + band_h // 2 - 140),
                (-80 - off, H // 2 + band_h // 2 + 140),
            ]
            bd.polygon(pts_o, outline=(10, 10, 10, 255))
        overlay = Image.alpha_composite(overlay, banner)
        draw = ImageDraw.Draw(overlay)
        text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(text_layer)
        base_size = int(H * 0.12) if len(shown) <= 6 else int(H * 0.09)
        fonts_bbs = []
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = td.textbbox((0, 0), ch, font=f)
            fonts_bbs.append((ch, f, bb))
        total_w = sum(bb[2] - bb[0] for _, _, bb in fonts_bbs) + 4 * max(0, n - 1)
        while total_w > int(W * 0.92) and base_size > 48:
            base_size -= 8
            fonts_bbs = []
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = td.textbbox((0, 0), ch, font=f)
                fonts_bbs.append((ch, f, bb))
            total_w = sum(bb[2] - bb[0] for _, _, bb in fonts_bbs) + 4 * max(0, n - 1)
        x = (W - total_w) // 2
        y = H // 2 - int(base_size * 0.35)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw = bb[2] - bb[0]
            # put_text on text_layer
            put_text(td, (x, y - bb[1]), ch, f, is_new=(i == n - 1), target=text_layer)
            x += tw + 6
        text_layer = text_layer.rotate(-18, resample=Image.Resampling.BICUBIC, expand=False)
        overlay = Image.alpha_composite(overlay, text_layer)
        draw = ImageDraw.Draw(overlay)

    elif layout == "comic_stack_burst":
        # Oversized stack + thick panel border (tight margin)
        m = 28
        draw.rectangle([m, m, W - m, H - m], outline=(10, 10, 10, 255), width=14)
        glyph_gap = 8
        target_h = int(H * 0.90)
        base_size = int(H * 0.15) if len(shown) <= 4 else int(H * 0.11)
        while base_size >= 40:
            fonts_bbs = []
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                fonts_bbs.append((ch, f, bb))
                total_h += (bb[3] - bb[1]) + glyph_gap
                max_w = max(max_w, bb[2] - bb[0] + 20)
            if total_h <= target_h and max_w <= W - 64:
                break
            base_size -= 8
        y = max(m + 24, (H - total_h) // 2)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = (W - tw) // 2
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            y += th + glyph_gap

    elif layout == "ink_vertical":
        # Classic top→bottom single column center, generous paper margins
        margin = int(min(W, H) * 0.12)
        target_h = H - 2 * margin
        base_size = int(H * 0.12)
        while base_size >= 40:
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                total_h += (bb[3] - bb[1]) + 14
                max_w = max(max_w, bb[2] - bb[0])
            if total_h <= target_h and max_w <= W - 2 * margin:
                break
            base_size -= 6
        fonts_bbs = []
        total_h = 0
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            fonts_bbs.append((ch, f, bb))
            total_h += (bb[3] - bb[1]) + 14
        y = margin + max(0, (target_h - total_h) // 2)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = (W - tw) // 2
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            y += th + 14

    elif layout == "ink_two_col":
        # Two short columns (split mid)
        margin = int(min(W, H) * 0.10)
        mid = (n + 1) // 2
        cols = [list(chunks_visible[:mid]), list(chunks_visible[mid:])]
        if not cols[1]:
            cols[1] = cols[0][-1:]
            cols[0] = cols[0][:-1] or cols[0]
        base_size = int(H * 0.10)
        col_w = (W - 3 * margin) // 2

        def _fit_col(chs, bs):
            while bs >= 36:
                th = 0
                mw = 0
                for j, ch in enumerate(chs):
                    # punch only on global newest
                    f = _font(font_path, max(24, int(bs * sc_all * (punch if (chs is cols[1] and j == len(chs) - 1 and cols[1] and chunks_visible and chs[j] == chunks_visible[-1] and j == len(chs) - 1) else 1.0))))
                    bb = draw.textbbox((0, 0), ch, font=f)
                    th += (bb[3] - bb[1]) + 12
                    mw = max(mw, bb[2] - bb[0])
                if th <= H - 2 * margin and mw <= col_w:
                    return bs
                bs -= 6
            return 36

        base_size = min(_fit_col(cols[0], base_size), _fit_col(cols[1], base_size))
        xs = [margin + col_w // 2, margin * 2 + col_w + col_w // 2]
        global_i = 0
        for ci, chs in enumerate(cols):
            fonts_bbs = []
            total_h = 0
            for j, ch in enumerate(chs):
                is_newest = (global_i + j == n - 1)
                sc = punch if is_newest else 1.0
                f = _font(font_path, max(24, int(base_size * sc_all * sc)))
                bb = draw.textbbox((0, 0), ch, font=f)
                fonts_bbs.append((ch, f, bb, is_newest))
                f_set = _font(font_path, max(24, int(base_size * sc_all)))
                bb_set = draw.textbbox((0, 0), ch, font=f_set)
                total_h += (bb_set[3] - bb_set[1]) + 12
            y = margin + max(0, (H - 2 * margin - total_h) // 2)
            for ch, f, bb, is_newest in fonts_bbs:
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
                x = xs[ci] - tw // 2
                put_text(draw, (x, y - bb[1]), ch, f, is_new=is_newest)
                f_set = _font(font_path, max(24, int(base_size * sc_all)))
                bb_set = draw.textbbox((0, 0), ch, font=f_set)
                y += (bb_set[3] - bb_set[1]) + 12
            global_i += len(chs)

    elif layout == "ink_seal":
        # Main vertical + small red seal stamp bottom-right
        margin = int(min(W, H) * 0.12)
        target_h = H - 2 * margin - 80
        base_size = int(H * 0.11)
        while base_size >= 40:
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                total_h += (bb[3] - bb[1]) + 12
                max_w = max(max_w, bb[2] - bb[0])
            if total_h <= target_h and max_w <= W - 2 * margin:
                break
            base_size -= 6
        fonts_bbs = []
        total_h = 0
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            fonts_bbs.append((ch, f, bb))
            total_h += (bb[3] - bb[1]) + 12
        y = margin + max(0, (target_h - total_h) // 2)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = (W - tw) // 2 - 20
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            y += th + 12
        # red seal stamp
        seal_s = 92
        seal = Image.new("RGBA", (seal_s + 8, seal_s + 8), (0, 0, 0, 0))
        sd = ImageDraw.Draw(seal)
        sd.rounded_rectangle([4, 4, seal_s, seal_s], radius=8, outline=(180, 36, 36, 230), width=5)
        sd.rounded_rectangle([10, 10, seal_s - 6, seal_s - 6], radius=4, outline=(180, 36, 36, 180), width=2)
        sf = _font(font_path, 28)
        label = "印"
        sbb = sd.textbbox((0, 0), label, font=sf)
        sx = 4 + (seal_s - (sbb[2] - sbb[0])) // 2
        sy = 4 + (seal_s - (sbb[3] - sbb[1])) // 2 - sbb[1]
        sd.text((sx, sy), label, font=sf, fill=(180, 36, 36, 230))
        seal = seal.rotate(12, expand=True, resample=Image.Resampling.BICUBIC)
        overlay.alpha_composite(seal, (W - margin - seal.width, H - margin - seal.height + 10))
        draw = ImageDraw.Draw(overlay)


    else:  # center_slam
        base_size = 220 if len(shown) <= 4 else (180 if len(shown) <= 8 else 140)
        while base_size >= 40:
            fonts_bbs = []
            total_h = 0
            max_w = 0
            for i, ch in enumerate(chunks_visible):
                f = font_for(i, base_size)
                bb = draw.textbbox((0, 0), ch, font=f)
                fonts_bbs.append((ch, f, bb))
                total_h += (bb[3] - bb[1]) + 12
                max_w = max(max_w, bb[2] - bb[0] + 44)
            if total_h <= H - 160 and max_w <= W - 48:
                break
            base_size -= 8
        y = max(80, (H - total_h) // 2)
        for i, (ch, f, bb) in enumerate(fonts_bbs):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = (W - tw) // 2
            draw.rectangle(
                [x - 22, y - 14, x + tw + 22, y + th + 14],
                fill=box_c if i == n - 1 else bar_fallback,
            )
            put_text(draw, (x, y - bb[1]), ch, f, is_new=(i == n - 1))
            y += th + 12

    if mode == "neon":
        draw_neon_scanlines(overlay)
    return Image.alpha_composite(canvas, overlay).convert("RGB")


def _encode_frames(fdir: Path, dst: Path, fps: int, dur: float) -> None:
    _require_ffmpeg()
    subprocess.run(
        [
            "ffmpeg", "-y", "-framerate", str(fps), "-i", str(fdir / "%04d.jpg"),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-t", f"{dur:.4f}", "-an", str(dst),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _cleanup_frames(fdir: Path) -> None:
    for p in fdir.glob("*.jpg"):
        p.unlink()
    try:
        fdir.rmdir()
    except OSError:
        pass


def _scale_rgba(color: Tuple[int, ...], alpha_scale: float) -> Tuple[int, ...]:
    """Scale an RGB or RGBA color's alpha (default 255 if RGB)."""
    if len(color) == 4:
        r, g, b, a = color
    else:
        r, g, b = color[:3]
        a = 255
    return (r, g, b, max(0, min(255, int(round(a * alpha_scale)))))



def title_anim_phases(
    title_dur: float,
    fade_dur: float,
    *,
    title_punch_max: float = 2.0,
    author_max: float = 1.0,
) -> Tuple[float, float, float]:
    """Return (title_end, author_end, usable) seconds within the title card.

    Timeline before fade:
      0 → title_end: song title glyph punch (≤ title_punch_max, default 2s)
      title_end → author_end: author typewriter (≤ author_max, default 1s)
      rest → hold, then fade_dur fade-out

    Long title cards stay snappy (punch capped); short cards scale down but
    keep punchy — shrink author first, then title (title still ≤ max).
    """
    fade = max(0.0, min(float(fade_dur), max(0.0, float(title_dur) - 0.05)))
    usable = max(0.05, float(title_dur) - fade)
    punch_max = max(0.05, float(title_punch_max))
    auth_max = max(0.0, float(author_max))

    title_end = min(punch_max, usable)
    # Prefer a quick author beat after title when there is leftover time
    leftover = max(0.0, usable - title_end)
    if leftover > 0:
        author_span = min(auth_max, max(0.15, leftover) if leftover >= 0.15 else leftover)
    else:
        author_span = 0.0

    if title_end + author_span > usable:
        # Shrink author first
        author_span = max(0.0, usable - title_end)

    # Very short cards: squeeze a minimal author window, keep title punchy
    if author_span < 0.15 and usable >= 0.5 and auth_max > 0:
        author_span = min(auth_max, max(0.15, usable * 0.25))
        title_end = min(punch_max, max(0.2, usable - author_span))
        if title_end + author_span > usable:
            author_span = max(0.0, usable - title_end)

    author_end = min(usable, title_end + author_span)
    return title_end, author_end, usable


def title_reveal_counts(
    t: float,
    *,
    title_dur: float,
    fade_dur: float,
    n_title: int,
    n_author: int,
) -> Tuple[int, int, float]:
    """How many title/author glyphs are visible at time t (seconds into card).

    Returns (n_title_visible, n_author_visible, newest_title_punch) where
    newest_title_punch is 1.0 at the instant a glyph appears and decays to 0.
    During/after fade window all glyphs are considered revealed (fade handles opacity).
    """
    n_title = max(0, int(n_title))
    n_author = max(0, int(n_author))
    title_end, author_end, usable = title_anim_phases(title_dur, fade_dur)
    fade = max(0.0, float(title_dur) - usable)
    # After title phase (and during fade), show everything
    if t >= author_end or (fade > 0 and t >= usable):
        return n_title, n_author, 0.0
    if t >= title_end:
        # author typewriter
        span = max(1e-6, author_end - title_end)
        frac = max(0.0, min(1.0, (t - title_end) / span))
        n_a = int(math.ceil(frac * n_author)) if n_author else 0
        if frac >= 1.0:
            n_a = n_author
        return n_title, min(n_author, max(0, n_a)), 0.0
    # title glyph punch
    if n_title <= 0:
        return 0, 0, 0.0
    span = max(1e-6, title_end)
    frac = max(0.0, min(1.0, t / span))
    if t <= 0:
        return 0, 0, 0.0
    n_vis = min(n_title, max(1, int(math.ceil(frac * n_title))))
    slot = span / n_title
    # age within current glyph's slot (0 at appear → 1 at next)
    appear_t = (n_vis - 1) * slot
    age = (t - appear_t) / max(1e-6, slot)
    punch = max(0.0, 1.0 - max(0.0, min(1.0, age)))
    return n_vis, 0, punch


def _title_palette(style: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve poster title-card palette (defaults to index 0 = high-contrast A)."""
    if not is_poster_fill(style):
        return None
    pals = style.get("palettes") or []
    idx = int(style.get("title_palette", 0) or 0)
    if pals:
        return pals[idx % len(pals)]
    return palette_for(style, idx)


def _title_author_gap_px(style: Dict[str, Any], poster: bool) -> int:
    if poster:
        return int(style.get("title_author_gap", 140) or 140)
    # Non-poster: lightly larger than legacy 48
    return int(style.get("title_author_gap", 72) or 72)


def _title_colors(
    style: Dict[str, Any], title_pal: Optional[Dict[str, Any]], alpha: float
) -> Tuple[Tuple[int, ...], Tuple[int, ...], Tuple[int, ...], Tuple[int, ...], Optional[Tuple[int, int, int]]]:
    """Return (fill, shadow, author_fill, author_shadow, bg_rgb|None) with alpha applied."""
    tb = style.get("title") or {}
    if title_pal is not None:
        bg = tuple(tb.get("bg") or title_pal.get("bg") or (10, 10, 10))
        fill_rgb = tuple(tb.get("fill") or title_pal.get("fill") or (242, 237, 228))
        shadow_rgb = tuple(tb.get("shadow") or title_pal.get("shadow") or (196, 30, 58))
        if "author_fill" in tb:
            af = tuple(tb["author_fill"])
        else:
            lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
            af = (40, 36, 32) if lum > 128 else (184, 176, 164)
        if "author_shadow" in tb:
            ash = tuple(tb["author_shadow"])
        else:
            ash = (17, 17, 17) if (0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]) > 128 else (196, 30, 58)
        a = int(255 * alpha)
        return (
            _rgba(fill_rgb, a),
            _rgba(shadow_rgb, a),
            _rgba(af, a),
            _rgba(ash, a),
            (int(bg[0]), int(bg[1]), int(bg[2])),
        )
    # non-poster: callers still use role colors; bg None
    return (
        _scale_rgba(color_tuple(style, "hook", "fill"), alpha),
        _scale_rgba(color_tuple(style, "hook", "shadow"), alpha),
        _scale_rgba(color_tuple(style, "verse", "fill"), alpha),
        _scale_rgba(color_tuple(style, "verse", "shadow"), alpha),
        None,
    )


def _draw_title_glyphs(
    draw,
    glyphs: Sequence[str],
    n_vis: int,
    *,
    font_path: str,
    base_size: int,
    bb_full,
    W: int,
    H: int,
    y_top: int,
    fill,
    shadow,
    shadow_off: Tuple[int, int],
    punch: float,
    poster: bool,
    accent=None,
) -> Tuple[int, int]:
    """Draw first n_vis title glyphs; return (title_bottom_y, title_height).

    Newest glyph gets a scale punch when punch>0. Settled glyphs stay at base size.
    """
    n_vis = max(0, min(int(n_vis), len(glyphs)))
    if n_vis <= 0 or not glyphs:
        th = (bb_full[3] - bb_full[1]) if bb_full else 0
        return y_top + th, th
    items = []
    for i in range(n_vis):
        ch = glyphs[i]
        sc = 1.0
        if i == n_vis - 1 and punch > 0:
            sc = 1.0 + 0.32 * punch
        f = _font(font_path, max(24, int(base_size * sc)))
        bb = draw.textbbox((0, 0), ch, font=f)
        items.append((ch, f, bb, i == n_vis - 1 and punch > 0.15))
    total_w = sum(bb[2] - bb[0] for _, _, bb, _ in items)
    x = (W - total_w - (shadow_off[0] if poster else 0)) // 2
    full_h = bb_full[3] - bb_full[1]
    # y_top is draw-y for full title (= row_top - bb_full[1]).
    # Per-glyph: row_top - bb[1], plus recenter when punched taller/shorter.
    row_top = y_top + bb_full[1]
    max_bottom = y_top + full_h
    for ch, f, bb, is_new in items:
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        yi = row_top - bb[1] + (full_h - th) // 2
        if poster:
            hard_block_shadow_text(
                draw, (x, yi), ch, f, fill, shadow, offset=shadow_off, is_new=is_new
            )
        else:
            layered_text(draw, (x, yi), ch, f, fill, shadow, accent or fill, is_new)
        x += tw
        max_bottom = max(max_bottom, yi + bb[3])
    return max_bottom, full_h


def title_fade_alpha(frame_index: int, frames: int, fade_dur: float, fps: int) -> float:
    """Opacity 1→0 over the last fade_dur seconds (linear). Full opacity before fade."""
    if frames <= 1 or fade_dur <= 0:
        return 1.0
    fade_frames = max(1, int(round(fade_dur * fps)))
    fade_start = max(0, frames - fade_frames)
    if frame_index < fade_start:
        return 1.0
    # linear 1→0 across fade window (last frame ~0)
    t = (frame_index - fade_start) / max(1, fade_frames - 1) if fade_frames > 1 else 1.0
    return max(0.0, min(1.0, 1.0 - t))


def compute_title_dur(
    lines: Sequence[Any],
    *,
    title_dur: Optional[float] = None,
    title_before_lyric: float = 1.0,
    min_title: float = 0.8,
) -> float:
    """Auto title duration: first_line.t0 - title_before_lyric (floored at min_title).

    Explicit title_dur overrides auto. Returns 0 if no lines / no usable t0.
    """
    if title_dur is not None:
        return float(title_dur)
    if not lines:
        return max(min_title, 2.0)
    first = lines[0]
    if hasattr(first, "t0"):
        first_t0 = float(first.t0)
    elif isinstance(first, dict):
        first_t0 = float(first.get("t0", first.get("start", 0.0)))
    else:
        first_t0 = 0.0
    return max(min_title, first_t0 - title_before_lyric)


def build_title_clip(
    bg_im: Image.Image,
    clips_dir: Path,
    *,
    title: str,
    author: str,
    style: Dict[str, Any],
    title_dur: float,
    width: int,
    height: int,
    fps: int,
    fade_dur: float = 0.8,
) -> Path:
    W, H = width, height
    frames = max(4, int(round(title_dur * fps)))
    fade_dur = max(0.0, min(float(fade_dur), max(0.0, title_dur - 0.05)))
    fdir = clips_dir / "f_title"
    fdir.mkdir(parents=True, exist_ok=True)
    font_path = resolve_font(style.get("font"))
    poster = is_poster_fill(style)
    title_pal = _title_palette(style)
    shadow_off = tuple(style.get("shadow_offset") or (18, 18))
    author_gap = _title_author_gap_px(style, poster)
    hook_accent = color_tuple(style, "hook", "accent")
    verse_accent = color_tuple(style, "verse", "accent")

    title_glyphs = glyph_chunks(title) if title else []
    if title and not title_glyphs:
        title_glyphs = list(title)
    author_chars = list(author) if author else []

    # Pre-fit fonts against full strings for stable layout
    measure = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    if poster and title:
        f1, bb1 = _fit_poster_h_font(
            measure, title, font_path, W, H, shadow_off, target_w_frac=0.86
        )
    else:
        f1, bb1 = fit_font_size(measure, title or " ", font_path, 150, W - 100)
    base_size = int(getattr(f1, "size", 120) or 120)
    tw, th = bb1[2] - bb1[0], bb1[3] - bb1[1]
    x_title = (W - tw - (shadow_off[0] if poster else 0)) // 2
    y_title = int(H * (0.34 if poster else 0.40)) - bb1[1]

    f2 = None
    bb2 = None
    if author:
        f2, bb2 = fit_font_size(measure, author, font_path, 72, W - 160)

    for fi in range(frames):
        t = fi / float(fps)
        alpha = title_fade_alpha(fi, frames, fade_dur, fps)
        fill, shadow, author_fill, author_shadow, bg_rgb = _title_colors(
            style, title_pal, alpha
        )
        if poster and bg_rgb is not None:
            canvas = Image.new("RGBA", (W, H), _rgba(bg_rgb, 255))
        else:
            canvas = bg_im.copy().convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        mode = _style_mode(style)
        if poster and title_pal is not None and style.get("decor", True):
            draw_poster_decor(
                draw, overlay, W, H, title_pal,
                line_index=0,
                font_path=font_path,
                with_stars=True,
                alpha=alpha,
            )
        elif not poster:
            if mode == "blueprint" and style.get("decor", True):
                draw_blueprint_decor(draw, W, H, line_index=0)
            elif mode in ("neon", "comic", "ink", "ink_wash", "ink-wash"):
                pass  # no classic dark band — mode bg + type carry the look
            else:
                band_a = int(round(140 * alpha))
                draw.rectangle([0, int(H * 0.32), W, int(H * 0.68)], fill=(8, 6, 5, band_a))

        if poster:
            n_t, n_a, punch = title_reveal_counts(
                t,
                title_dur=title_dur,
                fade_dur=fade_dur,
                n_title=len(title_glyphs),
                n_author=len(author_chars),
            )
            # During fade, keep full text visible (opacity via alpha)
            if alpha < 1.0 and t >= title_dur - fade_dur - 1e-9:
                n_t = len(title_glyphs)
                n_a = len(author_chars)
                punch = 0.0
        else:
            # Non-poster: static full title/author (spacing only)
            n_t, n_a, punch = len(title_glyphs), len(author_chars), 0.0

        title_bottom = y_title + th
        if title and alpha > 0.01:
            if poster:
                title_bottom, _ = _draw_title_glyphs(
                    draw,
                    title_glyphs,
                    n_t,
                    font_path=font_path,
                    base_size=base_size,
                    bb_full=bb1,
                    W=W,
                    H=H,
                    y_top=y_title,
                    fill=fill,
                    shadow=shadow,
                    shadow_off=shadow_off,
                    punch=punch,
                    poster=True,
                )
            else:
                # Non-poster: static full title (mode-aware paint)
                if title:
                    draw_styled_text(
                        overlay, draw, (x_title, y_title), title, f1,
                        fill, shadow, _scale_rgba(hook_accent, alpha), style,
                        is_new=True, use_hard=False,
                    )
                    draw = ImageDraw.Draw(overlay)
                title_bottom = y_title + th

        if author and alpha > 0.01 and n_a > 0 and f2 is not None and bb2 is not None:
            shown = "".join(author_chars[:n_a])
            # optional typewriter cursor while still revealing
            cursor_on = False
            title_end, author_end, _usable = title_anim_phases(title_dur, fade_dur)
            if poster and n_a < len(author_chars) and title_end <= t < author_end:
                cursor_on = (int(t * 6) % 2 == 0)
            draw_text = shown + ("▌" if cursor_on else "")
            tw2 = draw.textbbox((0, 0), draw_text, font=f2)
            tw2w = tw2[2] - tw2[0]
            x2 = (W - tw2w) // 2
            y2 = title_bottom + author_gap - bb2[1]
            if poster:
                hard_block_shadow_text(
                    draw, (x2, y2), draw_text, f2,
                    author_fill, author_shadow, offset=(8, 8), is_new=False,
                )
            else:
                draw_styled_text(
                    overlay, draw, (x2, y2), author, f2,
                    author_fill, author_shadow,
                    _scale_rgba(verse_accent, alpha), style,
                    is_new=False, use_hard=False,
                )
                draw = ImageDraw.Draw(overlay)

        if mode == "neon":
            draw_neon_scanlines(overlay)
        Image.alpha_composite(canvas, overlay).convert("RGB").save(
            fdir / f"{fi:04d}.jpg", quality=88
        )

    dst = clips_dir / "c_title.mp4"
    _encode_frames(fdir, dst, fps, title_dur)
    _cleanup_frames(fdir)
    return dst



def build_line_clip(
    idx: int,
    line: TimedLine,
    bg_im: Image.Image,
    clips_dir: Path,
    style: Dict[str, Any],
    *,
    width: int,
    height: int,
    fps: int,
) -> Path:
    t0, t1 = line.t0, line.t1
    dur = max(0.25, t1 - t0)
    frames = max(4, int(round(dur * fps)))
    dst = clips_dir / f"c_{idx:03d}.mp4"
    fdir = clips_dir / f"f_{idx:03d}"
    fdir.mkdir(parents=True, exist_ok=True)

    poster = is_poster_fill(style)
    palette = palette_for(style, idx) if poster else None
    if poster and palette is not None:
        bg_rgb = tuple(palette.get("bg", (10, 10, 10)))
        line_bg = Image.new("RGB", (width, height), bg_rgb)
    else:
        line_bg = bg_im

    for fi in range(frames):
        t = t0 + fi / fps
        n_show = sum(1 for ct in line.chunk_times if t >= ct)
        n_show = max(1, min(n_show, len(line.chunks)))
        t_local = t - line.chunk_times[n_show - 1]
        im = draw_layout(
            line_bg,
            line.chunks[:n_show],
            line.text,
            line.layout,
            t_local,
            style,
            chorus=line.chorus,
            hook=line.hook,
            width=width,
            height=height,
            palette=palette,
            line_index=idx,
            decor=bool(style.get("decor", False)),
        )
        im.save(fdir / f"{fi:04d}.jpg", quality=88)

    _encode_frames(fdir, dst, fps, dur)
    _cleanup_frames(fdir)
    return dst


def _gap_clip(
    bg_path: Path,
    dst: Path,
    gap: float,
    *,
    width: int,
    height: int,
    fps: int,
) -> Path:
    _require_ffmpeg()
    nf = max(1, int(round(gap * fps)))
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(bg_path),
            "-vf", f"scale={width}:{height},format=yuv420p",
            "-frames:v", str(nf), "-r", str(fps),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-an", str(dst),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return dst



def _solid_gap_clip(
    dst: Path,
    gap: float,
    color: Tuple[int, int, int],
    *,
    width: int,
    height: int,
    fps: int,
    work: Path,
) -> Path:
    """Solid-color gap (black cut / flash body)."""
    dur = max(1.0 / fps, float(gap))
    frames = max(1, int(round(dur * fps)))
    fdir = work / "clips" / f"fgap_{dst.stem}"
    fdir.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (width, height), tuple(color[:3]))
    for fi in range(frames):
        im.save(fdir / f"{fi:04d}.jpg", quality=85)
    _encode_frames(fdir, dst, fps, dur)
    _cleanup_frames(fdir)
    return dst


def _flash_gap_clip(
    dst: Path,
    gap: float,
    style: Dict[str, Any],
    *,
    width: int,
    height: int,
    fps: int,
    work: Path,
) -> Path:
    """2–3 frame white/black flash then solid matte for remaining gap."""
    dur = max(1.0 / fps, float(gap))
    frames = max(1, int(round(dur * fps)))
    flash_n = min(3, frames)
    fc = style.get("flash_color")
    mode = _style_mode(style)
    if fc and isinstance(fc, (list, tuple)) and len(fc) >= 3:
        flash_rgb = (int(fc[0]), int(fc[1]), int(fc[2]))
    elif mode == "comic":
        flash_rgb = (255, 255, 255)
    else:
        flash_rgb = (0, 0, 0)
    body = (0, 0, 0)
    fdir = work / "clips" / f"fflash_{dst.stem}"
    fdir.mkdir(parents=True, exist_ok=True)
    flash_im = Image.new("RGB", (width, height), flash_rgb)
    body_im = Image.new("RGB", (width, height), body)
    for fi in range(frames):
        (flash_im if fi < flash_n else body_im).save(fdir / f"{fi:04d}.jpg", quality=85)
    _encode_frames(fdir, dst, fps, dur)
    _cleanup_frames(fdir)
    return dst


def _hold_line_clip(
    idx: int,
    line: TimedLine,
    bg_im: Image.Image,
    clips_dir: Path,
    style: Dict[str, Any],
    gap: float,
    *,
    width: int,
    height: int,
    fps: int,
    dst: Optional[Path] = None,
) -> Path:
    """Hold previous lyric fully settled (all glyphs, no punch) for `gap` seconds."""
    dur = max(1.0 / fps, float(gap))
    frames = max(1, int(round(dur * fps)))
    out = dst or (clips_dir / f"hold_line_{idx:03d}.mp4")
    fdir = clips_dir / f"fh_line_{idx:03d}_{out.stem}"
    fdir.mkdir(parents=True, exist_ok=True)

    poster = is_poster_fill(style)
    palette = palette_for(style, idx) if poster else None
    if poster and palette is not None:
        bg_rgb = tuple(palette.get("bg", (10, 10, 10)))
        line_bg = Image.new("RGB", (width, height), bg_rgb)
    else:
        line_bg = bg_im

    chunks = list(line.chunks) if line.chunks else (glyph_chunks(line.text) or [line.text])
    # t_local large → punch scale settles to 1.0
    im = draw_layout(
        line_bg,
        chunks,
        line.text,
        line.layout,
        t_local=10.0,
        style=style,
        chorus=line.chorus,
        hook=line.hook,
        width=width,
        height=height,
        palette=palette,
        line_index=idx,
        decor=bool(style.get("decor", False)),
    )
    hold_op = style.get("hold_opacity")
    if hold_op is not None:
        try:
            op = float(hold_op)
        except (TypeError, ValueError):
            op = 1.0
        if 0.0 < op < 1.0:
            # Lightly faded previous lyric (blueprint measured hold)
            im = Image.blend(line_bg.convert("RGB"), im.convert("RGB"), op)
    for fi in range(frames):
        im.save(fdir / f"{fi:04d}.jpg", quality=88)

    _encode_frames(fdir, out, fps, dur)
    _cleanup_frames(fdir)
    return out


def _hold_title_clip(
    bg_im: Image.Image,
    clips_dir: Path,
    *,
    title: str,
    author: str,
    style: Dict[str, Any],
    gap: float,
    width: int,
    height: int,
    fps: int,
    dst: Optional[Path] = None,
) -> Path:
    """Hold title card at full opacity (no fade) for the post-title gap."""
    dur = max(1.0 / fps, float(gap))
    frames = max(1, int(round(dur * fps)))
    out = dst or (clips_dir / "hold_title.mp4")
    fdir = clips_dir / f"fh_title_{out.stem}"
    fdir.mkdir(parents=True, exist_ok=True)
    font_path = resolve_font(style.get("font"))
    poster = is_poster_fill(style)
    title_pal = _title_palette(style)
    shadow_off = tuple(style.get("shadow_offset") or (18, 18))
    author_gap = _title_author_gap_px(style, poster)
    hook_accent = color_tuple(style, "hook", "accent")
    verse_accent = color_tuple(style, "verse", "accent")
    W, H = width, height
    alpha = 1.0
    fill, shadow, author_fill, author_shadow, bg_rgb = _title_colors(
        style, title_pal, alpha
    )

    if poster and bg_rgb is not None:
        canvas = Image.new("RGBA", (W, H), _rgba(bg_rgb, 255))
    else:
        canvas = bg_im.copy().convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    mode = _style_mode(style)
    if poster and title_pal is not None and style.get("decor", True):
        draw_poster_decor(
            draw, overlay, W, H, title_pal,
            line_index=0,
            font_path=font_path,
            with_stars=True,
            alpha=alpha,
        )
    elif not poster:
        if mode == "blueprint" and style.get("decor", True):
            draw_blueprint_decor(draw, W, H, line_index=0)
        elif mode in ("neon", "comic", "ink", "ink_wash", "ink-wash"):
            pass
        else:
            draw.rectangle([0, int(H * 0.32), W, int(H * 0.68)], fill=(8, 6, 5, 140))

    f1, bb1 = fit_font_size(draw, title or " ", font_path, 150, W - 100)
    if poster and title:
        f1, bb1 = _fit_poster_h_font(draw, title, font_path, W, H, shadow_off, target_w_frac=0.86)
    tw, th = bb1[2] - bb1[0], bb1[3] - bb1[1]
    x = (W - tw - (shadow_off[0] if poster else 0)) // 2
    y = int(H * (0.34 if poster else 0.40)) - bb1[1]
    if title:
        if poster:
            hard_block_shadow_text(
                draw, (x, y), title, f1, fill, shadow, offset=shadow_off, is_new=False
            )
        else:
            draw_styled_text(
                overlay, draw, (x, y), title, f1, fill, shadow, hook_accent, style,
                is_new=False, use_hard=False,
            )
            draw = ImageDraw.Draw(overlay)
    if author:
        f2, bb2 = fit_font_size(draw, author, font_path, 72, W - 160)
        tw2 = bb2[2] - bb2[0]
        x2 = (W - tw2) // 2
        y2 = y + th + author_gap - bb2[1]
        if poster:
            hard_block_shadow_text(
                draw, (x2, y2), author, f2,
                author_fill, author_shadow, offset=(8, 8), is_new=False,
            )
        else:
            draw_styled_text(
                overlay, draw, (x2, y2), author, f2,
                author_fill, author_shadow, verse_accent, style,
                is_new=False, use_hard=False,
            )
            draw = ImageDraw.Draw(overlay)
    if mode == "neon":
        draw_neon_scanlines(overlay)
    frame = Image.alpha_composite(canvas, overlay).convert("RGB")
    for fi in range(frames):
        frame.save(fdir / f"{fi:04d}.jpg", quality=88)

    _encode_frames(fdir, out, fps, dur)
    _cleanup_frames(fdir)
    return out



def prepare_lines(
    aligned: Sequence[Dict[str, Any]],
    style: Dict[str, Any],
    *,
    lead: float,
    audio_dur: float,
    max_chars: int = 9,
) -> List[TimedLine]:
    enriched = []
    for raw in aligned:
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        hook, chorus = classify_line(text, style)
        item = dict(raw)
        item["hook"] = bool(raw.get("hook") or raw.get("card") or hook)
        item["chorus"] = bool(raw.get("chorus") or chorus or item["hook"])
        enriched.append(item)

    lines = clamp_timeline(enriched, lead=lead, audio_dur=audio_dur)
    for L in lines:
        if not L.chunks:
            # kinetic glyph units (not line-split pieces)
            L.chunks = glyph_chunks(L.text) or [L.text]
    if is_poster_fill(style):
        assign_poster_layouts(lines)
    else:
        assign_style_layouts(lines, style)
    rf = style.get("reveal_frac")
    mpc = style.get("max_per_char")
    assign_chunk_times(
        lines,
        reveal_frac=float(rf) if rf is not None else REVEAL_FRAC,
        max_per_char=float(mpc) if mpc is not None else MAX_PER_CHAR,
    )
    return lines


def render_mv(
    *,
    audio: str,
    aligned: Sequence[Dict[str, Any]],
    out: str,
    style: Dict[str, Any],
    bg_path: Optional[str] = None,
    bg_color: str = "#141210",
    bg_generate: bool = False,
    bg_config: Optional[str] = None,
    title: str = "",
    author: str = "",
    title_dur: Optional[float] = None,
    title_before_lyric: float = 1.0,
    title_fade: float = 0.8,
    min_title: float = 0.8,
    lead: float = 0.12,
    max_chars: int = 9,
    lite: bool = False,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
    gap_mode: Optional[str] = None,
) -> Path:
    """Full render pipeline → final mp4 at `out`."""
    _require_ffmpeg()
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = out_path.parent / (out_path.stem + "_work")
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    clips = work / "clips"
    clips.mkdir(parents=True, exist_ok=True)

    audio_dur = _probe_audio_duration(audio)
    poster = is_poster_fill(style)
    # poster_fill: solid palettes win — ignore --bg-color / --bg / --bg-generate
    if poster:
        bg_im = solid_bg("#0a0a0a", width, height)
        bg_jpg = work / "bg.jpg"
        bg_im.save(bg_jpg, quality=95)
        print(
            "poster_fill: ignoring --bg/--bg-color/--bg-generate; "
            "using per-line palette backgrounds",
            flush=True,
        )
    else:
        # Prefer style-declared solid bg when no custom image / generate
        effective_bg = bg_color
        if not bg_path and not bg_generate and style.get("bg_color"):
            effective_bg = style_bg_hex(style, default=bg_color)
        bg_im, bg_jpg = prepare_background(
            width=width,
            height=height,
            bg_path=bg_path,
            bg_color=effective_bg,
            bg_generate=bg_generate,
            bg_config=bg_config,
            work_dir=work,
        )

    lines = prepare_lines(
        aligned, style, lead=lead, audio_dur=audio_dur, max_chars=max_chars
    )

    # Auto title: hold until title_before_lyric seconds before first lyric.
    effective_title_dur = 0.0
    if title:
        effective_title_dur = compute_title_dur(
            lines,
            title_dur=title_dur,
            title_before_lyric=title_before_lyric,
            min_title=min_title,
        )
        print(
            f"title_dur={effective_title_dur:.3f}s fade={title_fade:.3f}s "
            f"(before_lyric={title_before_lyric})",
            flush=True,
        )

    line_paths: List[Path] = []
    for i, line in enumerate(lines):
        print(f"[{i+1}/{len(lines)}] {line.t0:.1f}-{line.t1:.1f} {line.text}", flush=True)
        line_paths.append(
            build_line_clip(
                i, line, bg_im, clips, style, width=width, height=height, fps=fps
            )
        )

    parts_meta = build_concat_list(
        lines, title_dur=effective_title_dur, audio_dur=audio_dur, fps=fps
    )

    parts: List[Path] = []
    title_path = None
    if title and effective_title_dur > 0:
        title_path = build_title_clip(
            bg_im, clips,
            title=title, author=author or "", style=style,
            title_dur=effective_title_dur, width=width, height=height, fps=fps,
            fade_dur=title_fade,
        )

    gap_mode = resolve_gap_mode(style, gap_mode)

    # Track previous visual for hold gaps (title or last lyric line index)
    prev_visual: Optional[str] = None  # "title" | "line"
    prev_line_index: Optional[int] = None

    for pi, part in enumerate(parts_meta):
        if part.kind == "title":
            assert title_path is not None
            parts.append(title_path)
            prev_visual = "title"
            prev_line_index = None
        elif part.kind == "gap":
            gap = part.t1 - part.t0
            gpath = clips / f"gap_{pi:03d}.mp4"
            if gap_mode == "hold" and prev_visual == "line" and prev_line_index is not None:
                parts.append(
                    _hold_line_clip(
                        prev_line_index,
                        lines[prev_line_index],
                        bg_im,
                        clips,
                        style,
                        gap,
                        width=width,
                        height=height,
                        fps=fps,
                        dst=gpath,
                    )
                )
            elif gap_mode == "hold" and prev_visual == "title" and title:
                parts.append(
                    _hold_title_clip(
                        bg_im,
                        clips,
                        title=title,
                        author=author or "",
                        style=style,
                        gap=gap,
                        width=width,
                        height=height,
                        fps=fps,
                        dst=gpath,
                    )
                )
            elif gap_mode == "flash":
                parts.append(
                    _flash_gap_clip(
                        gpath, gap, style,
                        width=width, height=height, fps=fps, work=work,
                    )
                )
            else:
                # black / cut matte (solid black, not style bg)
                parts.append(
                    _solid_gap_clip(gpath, gap, (0, 0, 0), width=width, height=height, fps=fps, work=work)
                )
        elif part.kind == "line":
            assert part.line_index is not None
            parts.append(line_paths[part.line_index])
            prev_visual = "line"
            prev_line_index = part.line_index

    lst = clips / "list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            # absolute paths — concat demuxer resolves relative to list.txt dir
            sp = str(Path(p).resolve()).replace("'", "'\\''")
            f.write(f"file '{sp}'\n")

    silent = work / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-pix_fmt", "yuv420p", "-t", str(audio_dur), str(silent),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(silent), "-i", audio,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0", "-shortest",
            "-movflags", "+faststart", str(out_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if lite:
        lite_path = out_path.with_name(out_path.stem + "-lite" + out_path.suffix)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(out_path),
                "-c:v", "libx264", "-preset", "fast", "-b:v", "1600k",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                str(lite_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"lite: {lite_path}", flush=True)

    print(f"DONE {out_path} ({out_path.stat().st_size} bytes)", flush=True)
    return out_path
