"""PIL kinetic layouts + ffmpeg encode / concat / mux."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .bg import prepare_background
from .split import split_line
from .styles import classify_line, color_tuple, load_style, resolve_font
from .timeline import (
    LAYOUTS,
    TimedLine,
    assign_chunk_times,
    assign_layouts,
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
) -> Image.Image:
    W, H = width, height
    canvas = base.copy().convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    shown = "".join(chunks_visible)
    if not shown:
        return canvas.convert("RGB")

    role = "hook" if hook else ("chorus" if chorus else "verse")
    fill = color_tuple(style, role, "fill")
    shadow = color_tuple(style, role, "shadow")
    accent = color_tuple(style, role, "accent")
    box_c = color_tuple(style, role, "box")
    verse_fill = color_tuple(style, "verse", "fill")
    verse_shadow = color_tuple(style, "verse", "shadow")
    verse_accent = color_tuple(style, "verse", "accent")
    font_path = resolve_font(style.get("font"))

    n = len(chunks_visible)
    punch = 1.0 + 0.32 * max(0.0, 1.0 - t_local * 12)
    nchar = max(1, len(shown.replace(" ", "")))
    sc_all = size_scale_for(nchar)

    def font_for(i: int, base_size: int):
        sc = punch if i == n - 1 else 1.0
        return _font(font_path, max(24, int(base_size * sc_all * sc)))

    if layout == "giant_char":
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
        y = 290
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
        base_size = 200 if len(shown) <= 6 else 145
        fonts_bbs = []
        total_h = 0
        for i, ch in enumerate(chunks_visible):
            f = font_for(i, base_size)
            bb = draw.textbbox((0, 0), ch, font=f)
            fonts_bbs.append((ch, f, bb))
            total_h += (bb[3] - bb[1]) + 12
        y = (H - total_h) // 2
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
) -> Path:
    W, H = width, height
    frames = max(4, int(round(title_dur * fps)))
    fdir = clips_dir / "f_title"
    fdir.mkdir(parents=True, exist_ok=True)
    font_path = resolve_font(style.get("font"))
    hook_fill = color_tuple(style, "hook", "fill")
    hook_shadow = color_tuple(style, "hook", "shadow")
    hook_accent = color_tuple(style, "hook", "accent")
    verse_fill = color_tuple(style, "verse", "fill")
    verse_shadow = color_tuple(style, "verse", "shadow")
    verse_accent = color_tuple(style, "verse", "accent")

    for fi in range(frames):
        canvas = bg_im.copy().convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle([0, int(H * 0.32), W, int(H * 0.68)], fill=(8, 6, 5, 140))
        f1, bb1 = fit_font_size(draw, title or " ", font_path, 150, W - 100)
        tw, th = bb1[2] - bb1[0], bb1[3] - bb1[1]
        x = (W - tw) // 2
        y = int(H * 0.40) - bb1[1]
        if title:
            layered_text(draw, (x, y), title, f1, hook_fill, hook_shadow, hook_accent, True)
        if author:
            f2, bb2 = fit_font_size(draw, author, font_path, 72, W - 160)
            tw2 = bb2[2] - bb2[0]
            x2 = (W - tw2) // 2
            y2 = y + th + 48
            layered_text(
                draw, (x2, y2 - bb2[1]), author, f2, verse_fill, verse_shadow, verse_accent, False
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

    for fi in range(frames):
        t = t0 + fi / fps
        n_show = sum(1 for ct in line.chunk_times if t >= ct)
        n_show = max(1, min(n_show, len(line.chunks)))
        t_local = t - line.chunk_times[n_show - 1]
        im = draw_layout(
            bg_im,
            line.chunks[:n_show],
            line.text,
            line.layout,
            t_local,
            style,
            chorus=line.chorus,
            hook=line.hook,
            width=width,
            height=height,
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
            L.chunks = split_line(L.text, max_chars=max_chars) or [L.text]
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
    title_dur: float = 2.0,
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

    line_paths: List[Path] = []
    for i, line in enumerate(lines):
        print(f"[{i+1}/{len(lines)}] {line.t0:.1f}-{line.t1:.1f} {line.text}", flush=True)
        line_paths.append(
            build_line_clip(
                i, line, bg_im, clips, style, width=width, height=height, fps=fps
            )
        )

    parts_meta = build_concat_list(
        lines, title_dur=title_dur if title else 0.0, audio_dur=audio_dur, fps=fps
    )

    parts: List[Path] = []
    title_path = None
    if title:
        title_path = build_title_clip(
            bg_im, clips,
            title=title, author=author or "", style=style,
            title_dur=title_dur, width=width, height=height, fps=fps,
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
            # ffmpeg concat demuxer: escape single quotes
            sp = str(p).replace("'", "'\\''")
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
