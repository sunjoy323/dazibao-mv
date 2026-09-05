"""Discover system TTF/OTF/TTC fonts useful for CJK / dazibao rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

_FONT_EXTS = {".ttf", ".otf", ".ttc"}

_SEARCH_DIRS = [
    Path("/usr/share/fonts/opentype/noto"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/opentype"),
    Path("/usr/share/fonts/truetype"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
]


def _label(path: Path) -> str:
    return path.stem


def discover_fonts(extra_dirs: List[str] | None = None) -> List[Dict[str, Any]]:
    """Return unique fonts as [{path, name, family_hint}, ...], Noto/DejaVu first."""
    dirs = list(_SEARCH_DIRS)
    if extra_dirs:
        dirs.extend(Path(d) for d in extra_dirs)

    seen: set[str] = set()
    found: List[Dict[str, Any]] = []
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            candidates = sorted(d.rglob("*"))
        except OSError:
            continue
        for p in candidates:
            if not p.is_file():
                continue
            if p.suffix.lower() not in _FONT_EXTS:
                continue
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            name = _label(p)
            lower = name.lower()
            hint = "other"
            if "noto" in lower and "cjk" in lower:
                hint = "noto-cjk"
            elif "noto" in lower:
                hint = "noto"
            elif "dejavu" in lower:
                hint = "dejavu"
            found.append({"path": str(p), "name": name, "family_hint": hint})

    priority = {"noto-cjk": 0, "noto": 1, "dejavu": 2, "other": 3}
    found.sort(key=lambda f: (priority.get(f["family_hint"], 9), f["name"].lower()))
    return found
