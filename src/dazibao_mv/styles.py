"""Load builtin and custom style YAML files."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_FALLBACK_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def resolve_font(preferred: Optional[str] = None) -> str:
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(_FALLBACK_FONTS)
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    # last resort: let PIL use default bitmap font (render will handle)
    return preferred or _FALLBACK_FONTS[-1]


def _styles_dir() -> Path:
    try:
        ref = resources.files("dazibao_mv").joinpath("styles")
        return Path(str(ref))
    except Exception:
        return Path(__file__).resolve().parent / "styles"


def list_builtin_styles() -> List[str]:
    d = _styles_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


def _normalize_style(data: Dict[str, Any], source: str) -> Dict[str, Any]:
    style = dict(data)
    style["name"] = style.get("name") or Path(source).stem
    style["font"] = resolve_font(style.get("font"))
    for key in ("verse", "chorus", "hook"):
        block = style.get(key) or {}
        for ch in ("fill", "shadow", "accent", "box"):
            if ch in block:
                block[ch] = tuple(int(x) for x in block[ch])
        style[key] = block
    style.setdefault("hook_keywords", [])
    style.setdefault("chorus_keywords", [])
    style["_source"] = source
    return style


def load_style(name: Optional[str] = None, style_file: Optional[str] = None) -> Dict[str, Any]:
    """Load a builtin style by name, or a custom YAML file."""
    if style_file:
        path = Path(style_file)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return _normalize_style(data, str(path))

    name = name or "dazibao-ivory"
    path = _styles_dir() / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(list_builtin_styles()) or "(none)"
        raise FileNotFoundError(f"Unknown style '{name}'. Builtins: {available}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _normalize_style(data, str(path))


def classify_line(text: str, style: Dict[str, Any]) -> Tuple[bool, bool]:
    """Return (hook, chorus) flags from style keywords."""
    compact = (
        text.replace(" ", "")
        .replace("…", "")
        .replace("。", "")
        .replace("，", "")
    )
    hook = False
    chorus = False
    for kw in style.get("hook_keywords") or []:
        if kw and kw.replace(" ", "") in compact:
            hook = True
            break
    for kw in style.get("chorus_keywords") or []:
        if kw and kw.replace(" ", "") in compact:
            chorus = True
            break
    if hook:
        chorus = True
    return hook, chorus


def color_tuple(style: Dict[str, Any], role: str, channel: str) -> Tuple[int, int, int, int]:
    block = style.get(role) or style.get("verse") or {}
    val = block.get(channel)
    if not val:
        return (255, 255, 255, 255)
    t = tuple(int(x) for x in val)
    if len(t) == 3:
        return (*t, 255)  # type: ignore[return-value]
    return t  # type: ignore[return-value]
