"""CLI entrypoint for dazibao-mv."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .align import align, save_aligned, save_srt
from .render import render_mv
from .styles import list_builtin_styles, load_style


def _cmd_styles(_args: argparse.Namespace) -> int:
    names = list_builtin_styles()
    if not names:
        print("No builtin styles found.", file=sys.stderr)
        return 1
    for name in names:
        style = load_style(name)
        desc = style.get("description") or ""
        print(f"{name:20s} {desc}")
    return 0


def _cmd_align(args: argparse.Namespace) -> int:
    aligned = align(
        audio=args.audio,
        lyrics_path=args.lyrics,
        srt=args.srt,
        max_chars=args.max_chars,
        whisper_model=args.whisper_model,
        initial_prompt=args.initial_prompt,
        max_line_sec=args.max_line_sec,
    )
    save_aligned(aligned, args.out)
    srt_out = Path(args.out).with_suffix(".srt")
    save_srt(aligned, srt_out)
    print(f"Wrote {len(aligned)} lines → {args.out}")
    print(f"Wrote SRT → {srt_out}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    style = load_style(args.style, style_file=args.style_file)

    aligned = align(
        audio=args.audio,
        lyrics_path=args.lyrics,
        srt=args.srt,
        max_chars=args.max_chars,
        whisper_model=getattr(args, "whisper_model", "medium"),
        initial_prompt=getattr(args, "initial_prompt", None),
        max_line_sec=getattr(args, "max_line_sec", 5.5),
    )
    # cache aligned next to out
    out_path = Path(args.out)
    cache = out_path.with_suffix(".aligned.json")
    save_aligned(aligned, cache)

    bg_modes = sum(bool(x) for x in (args.bg, args.bg_generate))
    if bg_modes > 1:
        print("Use only one of --bg / --bg-generate (or neither for --bg-color).", file=sys.stderr)
        return 2

    render_mv(
        audio=args.audio,
        aligned=aligned,
        out=args.out,
        style=style,
        bg_path=args.bg,
        bg_color=args.bg_color,
        bg_generate=bool(args.bg_generate),
        bg_config=args.bg_config,
        title=args.title or "",
        author=args.author or "",
        title_dur=args.title_dur,
        lead=args.lead,
        max_chars=args.max_chars,
        lite=bool(args.lite),
        width=args.width,
        height=args.height,
        fps=args.fps,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dazibao-mv",
        description="Vertical kinetic dazibao lyric MV CLI",
    )
    p.add_argument("--version", action="version", version=f"dazibao-mv {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # styles
    sp = sub.add_parser("styles", help="List builtin styles")
    sp.set_defaults(func=_cmd_styles)

    # align
    ap = sub.add_parser("align", help="Align lyrics to audio/SRT → JSON")
    ap.add_argument("--audio", required=False, help="Audio file (for whisper if no --srt)")
    ap.add_argument("--lyrics", required=True, help="Lyrics text file")
    ap.add_argument("--out", required=True, help="Output aligned.json")
    ap.add_argument("--srt", default=None, help="Skip whisper; use this SRT")
    ap.add_argument("--max-chars", type=int, default=9)
    ap.add_argument("--whisper-model", default="medium", help="faster-whisper model size")
    ap.add_argument("--initial-prompt", default=None, help="Optional ASR prompt (song title/hooks)")
    ap.add_argument("--max-line-sec", type=float, default=5.5, help="Cap single-line ASR span seconds")
    ap.set_defaults(func=_cmd_align)

    # render
    rp = sub.add_parser("render", help="Render kinetic dazibao MV")
    rp.add_argument("--audio", required=True, help="Audio file (mp3/wav/…)")
    rp.add_argument("--lyrics", required=True, help="Lyrics text file")
    rp.add_argument("--out", required=True, help="Output mp4 path")
    rp.add_argument("--bg", default=None, help="Background image path")
    rp.add_argument("--bg-color", default="#141210", help="Solid background #RRGGBB")
    rp.add_argument("--bg-generate", action="store_true", help="Generate BG via image API")
    rp.add_argument("--bg-config", default=None, help="YAML config for image gen provider")
    rp.add_argument("--style", default="dazibao-ivory", help="Builtin style name")
    rp.add_argument("--style-file", default=None, help="Custom style YAML")
    rp.add_argument("--title", default="", help="Title card text")
    rp.add_argument("--author", default="", help="Author on title card")
    rp.add_argument("--title-dur", type=float, default=2.0, help="Title duration seconds")
    rp.add_argument("--srt", default=None, help="SRT timings (skip whisper)")
    rp.add_argument("--max-chars", type=int, default=9)
    rp.add_argument("--whisper-model", default="medium", help="faster-whisper model when no --srt")
    rp.add_argument("--initial-prompt", default=None, help="Optional ASR prompt")
    rp.add_argument("--max-line-sec", type=float, default=5.5, help="Cap single-line ASR span seconds")
    rp.add_argument("--lead", type=float, default=0.12, help="LEAD early punch (seconds)")
    rp.add_argument("--lite", action="store_true", help="Also write lite mp4")
    rp.add_argument("--width", type=int, default=1080)
    rp.add_argument("--height", type=int, default=1920)
    rp.add_argument("--fps", type=int, default=24)
    rp.set_defaults(func=_cmd_render)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    # align needs audio or srt
    if args.command == "align" and not args.srt and not args.audio:
        parser.error("align requires --audio and/or --srt")
    code = args.func(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
