"""Background: solid color, image fit, or OpenAI-compatible image generation."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml
from PIL import Image, ImageOps


def parse_hex_color(s: str) -> Tuple[int, int, int]:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"Invalid color '{s}', expected #RRGGBB")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def solid_bg(color: str, width: int, height: int) -> Image.Image:
    rgb = parse_hex_color(color)
    return Image.new("RGB", (width, height), rgb)


def load_fit_bg(path: str | Path, width: int, height: int) -> Image.Image:
    im = Image.open(path).convert("RGB")
    return ImageOps.fit(im, (width, height), method=Image.Resampling.LANCZOS)


def load_bg_config(path: str | Path) -> Dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data


def generate_bg(
    config: Dict[str, Any],
    width: int,
    height: int,
    save_path: Optional[str | Path] = None,
) -> Image.Image:
    """Call an OpenAI-compatible /images/generations endpoint."""
    base_url = str(config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key_env = config.get("api_key_env") or "OPENAI_API_KEY"
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise RuntimeError(
            f"Environment variable {api_key_env} is not set "
            "(needed for --bg-generate)"
        )
    model = config.get("model") or "dall-e-3"
    prompt = config.get("prompt") or "Dark vertical abstract poster background, no text"
    size = config.get("size") or f"{width}x{height}"

    url = f"{base_url}/images/generations"
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "response_format": "b64_json",
    }
    # Some compatible APIs prefer url response; we accept both
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Image generation failed ({e.code}): {detail}") from e

    data = (payload.get("data") or [None])[0] or {}
    if data.get("b64_json"):
        raw = base64.b64decode(data["b64_json"])
        tmp = Path(save_path) if save_path else Path("/tmp/dazibao_bg_gen.png")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw)
        im = Image.open(tmp).convert("RGB")
    elif data.get("url"):
        with urllib.request.urlopen(data["url"], timeout=120) as resp:
            raw = resp.read()
        tmp = Path(save_path) if save_path else Path("/tmp/dazibao_bg_gen.png")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw)
        im = Image.open(tmp).convert("RGB")
    else:
        raise RuntimeError(f"Unexpected image API response: {payload!r}")

    im = ImageOps.fit(im, (width, height), method=Image.Resampling.LANCZOS)
    if save_path:
        im.save(save_path, quality=92)
    return im


def prepare_background(
    *,
    width: int,
    height: int,
    bg_path: Optional[str] = None,
    bg_color: str = "#141210",
    bg_generate: bool = False,
    bg_config: Optional[str] = None,
    work_dir: Optional[str | Path] = None,
) -> Tuple[Image.Image, Path]:
    """Return (PIL image, saved JPEG path) for reuse in ffmpeg gaps."""
    work = Path(work_dir) if work_dir else Path(".")
    work.mkdir(parents=True, exist_ok=True)
    out_jpg = work / "dazibao_bg.jpg"

    if bg_generate:
        cfg = load_bg_config(bg_config) if bg_config else {}
        im = generate_bg(cfg, width, height, save_path=work / "dazibao_bg_gen.png")
    elif bg_path:
        im = load_fit_bg(bg_path, width, height)
    else:
        im = solid_bg(bg_color, width, height)

    im.save(out_jpg, quality=92)
    return im, out_jpg
