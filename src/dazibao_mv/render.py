"""PIL kinetic layouts + ffmpeg encode / concat / mux."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .bg import prepare_background, solid_bg
from .split import glyph_chunks, split_line
from .styles import (
    classify_line,
    color_tuple,
    is_poster_fill,
    load_style,
    palette_for,
    resolve_font,
)
from .timeline import (
    LAYOUTS,
    POSTER_LAYOUTS,
    TimedLine,
    assign_chunk_times,
    assign_layouts,
    assign_poster_layouts,
    build_concat_list,
    clamp_timeline,
)

REVEAL_FRAC = 0.48
MAX_PER_CHAR = 0.30


def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install ffmpeg and ensure it is available."
        )
    return exe


def _probe_audio_duration(audio: str) -> float:
    exe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.check_output(
            [
                exe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio,
            ],
            text=True,
        ).strip()
        return float(out)
    except Exception:
        # fallback via ffmpeg
        r = subprocess.run(
            ["ffmpeg", "-i", audio],
            capture_output=True,
            text=True,
        )
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr or "")
        if not m:
            raise RuntimeError(f"Cannot probe audio duration: {audio}")
        h, m_, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + m_ * 60 + s


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

    # Poster decor under text (even before glyphs if we want frame always)
    if decor and palette is not None:
        with_stars = (line_index % 3 == 2) or layout == "poster_fill_v"
        draw_poster_decor(
            draw, overlay, W, H, palette,
            line_index=line_index,
            font_path=font_path,
            with_stars=with_stars,
        )

    if not shown:
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
    punch = 1.0 + 0.32 * max(0.0, 1.0 - t_local * 12)
    nchar = max(1, len(shown.replace(" ", "")))
    sc_all = size_scale_for(nchar)
    shadow_off = tuple(style.get("shadow_offset") or (18, 18))
    use_hard = palette is not None or layout.startswith("poster_fill")

    def font_for(i: int, base_size: int):
        sc = punch if i == n - 1 else 1.0
        return _font(font_path, max(24, int(base_size * sc_all * sc)))

    def put_text(d, xy, text, font, is_new=False):
        if use_hard:
            hard_block_shadow_text(
                d, xy, text, font, fill, shadow, offset=shadow_off, is_new=is_new
            )
        else:
            layered_text(d, xy, text, font, fill, shadow, accent, is_new)

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
        f = font_for(n - 1, int(min(W, H) * 0.60))
        bb = draw.textbbox((0, 0), ch, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        pad = 52
        bx0, by0 = (W - tw) // 2 - pad, (H - th) // 2 - pad
        draw.rectangle([bx0, by0, bx0 + tw + 2 * pad, by0 + th + 2 * pad], fill=box_c)
        layered_text(draw, ((W - tw) // 2, (H - th) // 2 - bb[1]), ch, f, fill, shadow, accent, True)
        if n > 1:
            trail = "".join(chunks_visible[:-1])[-6:]
            f2 = _font(font_path, 86)
            layered_text(draw, (48, H - 210), trail, f2, verse_fill, verse_shadow, verse_accent, False)

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
                fill=box_c if i == n - 1 else (8, 8, 10, 150),
            )
            layered_text(td, (x, y - bb[1]), ch, f, fill, shadow, accent, i == n - 1)
            if i == n - 1:
                tmp = tmp.rotate(-8, resample=Image.Resampling.BICUBIC, center=(x + tw / 2, y + th / 2))
            overlay = Image.alpha_composite(overlay, tmp)

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
                fill=box_c if i == n - 1 else (8, 8, 10, 155),
            )
            layered_text(draw, (x, y - bb[1]), ch, f, fill, shadow, accent, i == n - 1)
            y += th + 12

    elif layout == "right_cascade":
        base_size = 170
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            x = W - 72 - tw - i * 26
            y = 370 + i * (th + 6)
            draw.rectangle([x - 16, y - 10, x + tw + 16, y + th + 10], fill=(8, 8, 10, 175))
            layered_text(draw, (x, y - bb[1]), ch, f, fill, shadow, accent, i == n - 1)

    elif layout == "top_heavy":
        f1 = font_for(0, 250)
        head = chunks_visible[0]
        bb = draw.textbbox((0, 0), head, font=f1)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        draw.rectangle([20, 150, W - 20, 150 + th + 68], fill=box_c)
        layered_text(draw, ((W - tw) // 2, 178 - bb[1]), head, f1, fill, shadow, accent, n == 1)
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
            layered_text(
                draw, ((W - tw2) // 2, 150 + th + 100 - bb2[1]), rest, f2, fill, shadow, accent, False
            )

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
            layered_text(draw, (x, y - bb[1]), ch, f, fill, shadow, accent, i == n - 1)
            x += tw + 8

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
                fill=box_c if i == n - 1 else (8, 8, 10, 150),
            )
            layered_text(draw, (x, y - bb[1]), ch, f, fill, shadow, accent, i == n - 1)
            y += th + 12

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
    # Title uses palette B (cream paper) when available, else A
    title_pal = None
    if poster:
        pals = style.get("palettes") or []
        title_pal = pals[1] if len(pals) > 1 else palette_for(style, 1)
    hook_fill = color_tuple(style, "hook", "fill")
    hook_shadow = color_tuple(style, "hook", "shadow")
    hook_accent = color_tuple(style, "hook", "accent")
    verse_fill = color_tuple(style, "verse", "fill")
    verse_shadow = color_tuple(style, "verse", "shadow")
    verse_accent = color_tuple(style, "verse", "accent")
    shadow_off = tuple(style.get("shadow_offset") or (18, 18))

    for fi in range(frames):
        alpha = title_fade_alpha(fi, frames, fade_dur, fps)
        if poster and title_pal is not None:
            bg_rgb = tuple(title_pal.get("bg", (242, 232, 216)))
            canvas = Image.new("RGBA", (W, H), _rgba(bg_rgb, 255))
        else:
            canvas = bg_im.copy().convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
    
        if poster and title_pal is not None and style.get("decor", True):
            # fade decor with alpha by scaling palette colors via draw_poster_decor alpha
            draw_poster_decor(
                draw, overlay, W, H, title_pal,
                line_index=0,
                font_path=font_path,
                with_stars=True,
                alpha=alpha,
            )
            fill = _rgba(title_pal.get("fill", (196, 30, 58)), int(255 * alpha))
            shadow = _rgba(title_pal.get("shadow", (17, 17, 17)), int(255 * alpha))
            author_fill = fill
            author_shadow = shadow
        else:
            band_a = int(round(140 * alpha))
            draw.rectangle([0, int(H * 0.32), W, int(H * 0.68)], fill=(8, 6, 5, band_a))
            fill = _scale_rgba(hook_fill, alpha)
            shadow = _scale_rgba(hook_shadow, alpha)
            author_fill = _scale_rgba(verse_fill, alpha)
            author_shadow = _scale_rgba(verse_shadow, alpha)

        f1, bb1 = fit_font_size(draw, title or " ", font_path, 150, W - 100)
        # poster title: fill more of the screen
        if poster and title:
            f1, bb1 = _fit_poster_h_font(draw, title, font_path, W, H, shadow_off, target_w_frac=0.86)
        tw, th = bb1[2] - bb1[0], bb1[3] - bb1[1]
        x = (W - tw - (shadow_off[0] if poster else 0)) // 2
        y = int(H * (0.36 if poster else 0.40)) - bb1[1]
        if title and alpha > 0.01:
            if poster:
                hard_block_shadow_text(
                    draw, (x, y), title, f1, fill, shadow, offset=shadow_off, is_new=True
                )
            else:
                layered_text(
                    draw, (x, y), title, f1,
                    fill,
                    shadow,
                    _scale_rgba(hook_accent, alpha),
                    True,
                )
        if author and alpha > 0.01:
            f2, bb2 = fit_font_size(draw, author, font_path, 72, W - 160)
            tw2 = bb2[2] - bb2[0]
            x2 = (W - tw2) // 2
            y2 = y + th + 48
            if poster:
                hard_block_shadow_text(
                    draw, (x2, y2 - bb2[1]), author, f2,
                    author_fill, author_shadow, offset=(8, 8), is_new=False,
                )
            else:
                layered_text(
                    draw, (x2, y2 - bb2[1]), author, f2,
                    author_fill,
                    author_shadow,
                    _scale_rgba(verse_accent, alpha),
                    False,
                )
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
            decor=bool(poster and style.get("decor", True)),
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
        assign_layouts(lines, LAYOUTS)
    assign_chunk_times(lines, reveal_frac=REVEAL_FRAC, max_per_char=MAX_PER_CHAR)
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
        bg_im, bg_jpg = prepare_background(
            width=width,
            height=height,
            bg_path=bg_path,
            bg_color=bg_color,
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

    for pi, part in enumerate(parts_meta):
        if part.kind == "title":
            assert title_path is not None
            parts.append(title_path)
        elif part.kind == "gap":
            gap = part.t1 - part.t0
            gpath = clips / f"gap_{pi:03d}.mp4"
            parts.append(
                _gap_clip(bg_jpg, gpath, gap, width=width, height=height, fps=fps)
            )
        elif part.kind == "line":
            assert part.line_index is not None
            parts.append(line_paths[part.line_index])

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
