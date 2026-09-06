"""Resolve audio file suffixes from filename, MIME type, or magic bytes."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

ALLOWED_AUDIO_SUFFIXES = {
    "mp3",
    "wav",
    "m4a",
    "flac",
    "ogg",
    "opus",
    "webm",
    "aac",
    "wma",
}

MIME_TO_SUFFIX = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/aac": ".aac",
    "audio/x-ms-wma": ".wma",
}


def assert_audio_allowed(suffix: str) -> str:
    """Normalize and validate a suffix like ``.webm`` or ``webm``."""
    s = (suffix or "").strip().lower()
    if s.startswith("."):
        s = s[1:]
    if s not in ALLOWED_AUDIO_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_AUDIO_SUFFIXES))
        raise ValueError(f"unsupported audio type '.{s or '?'}'; allowed: {allowed}")
    return f".{s}"


def _sniff_magic(data: bytes) -> Optional[str]:
    if not data:
        return None
    # WebM / Matroska EBML header
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    # ID3 tag (MP3)
    if data[:3] == b"ID3":
        return ".mp3"
    # MPEG frame sync
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return ".mp3"
    # RIFF WAVE
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
        return ".wav"
    # Ogg container
    if data[:4] == b"OggS":
        return ".ogg"
    # FLAC
    if data[:4] == b"fLaC":
        return ".flac"
    # ISO BMFF (m4a/mp4) — ftyp box
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return ".m4a"
    return None


def resolve_audio_suffix(
    filename: str,
    content_type: Optional[str] = None,
    data: Optional[bytes] = None,
) -> str:
    """Pick a file suffix for an uploaded audio blob.

    Prefer a real allowed extension from ``filename``, else map ``content_type``,
    else sniff magic bytes when ``data`` is provided, else fall back to ``.mp3``.
    """
    name = filename or ""
    suffix = Path(name).suffix.lower()
    if suffix.startswith("."):
        ext = suffix[1:]
        if ext in ALLOWED_AUDIO_SUFFIXES:
            return f".{ext}"

    if content_type:
        # strip "; charset=..." etc.
        mime = content_type.split(";", 1)[0].strip().lower()
        mapped = MIME_TO_SUFFIX.get(mime)
        if mapped:
            return mapped

    if data:
        sniffed = _sniff_magic(data)
        if sniffed:
            return sniffed

    return ".mp3"
