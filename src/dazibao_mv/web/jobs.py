"""In-memory job store + background worker for web renders."""

from __future__ import annotations

import json
import os
import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..align import save_aligned, save_srt
from ..render import render_mv
from ..styles import load_style, resolve_font
from .lyrics import prepare_aligned_from_lyrics


def data_root() -> Path:
    env = os.environ.get("DAZIBAO_DATA")
    if env:
        root = Path(env).expanduser()
    else:
        root = Path.home() / ".cache" / "dazibao-mv"
    jobs = root / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs


def _rgba_list(val: Any) -> Optional[List[int]]:
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip().lstrip("#")
        if len(s) == 6:
            return [int(s[i : i + 2], 16) for i in (0, 2, 4)] + [255]
        if len(s) == 8:
            return [int(s[i : i + 2], 16) for i in (0, 2, 4, 6)]
        return None
    if isinstance(val, (list, tuple)):
        nums = [int(x) for x in val]
        if len(nums) == 3:
            nums.append(255)
        if len(nums) >= 4:
            return nums[:4]
    return None


def _rgb_list(val: Any) -> Optional[List[int]]:
    rgba = _rgba_list(val)
    return rgba[:3] if rgba else None


def apply_style_overrides(style: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Mutate a copy of style with color / palette / font overrides from the UI."""
    out = dict(style)
    if not overrides:
        return out

    if overrides.get("font"):
        out["font"] = resolve_font(str(overrides["font"]))

    for role in ("verse", "chorus", "hook"):
        block = dict(out.get(role) or {})
        ov = overrides.get(role) or {}
        if isinstance(ov, dict):
            for ch in ("fill", "shadow", "accent", "box"):
                if ch in ov:
                    parsed = _rgba_list(ov[ch])
                    if parsed:
                        block[ch] = tuple(parsed)
        out[role] = block

    # Poster-wall palettes (optional list of {bg,fill,shadow,...})
    pals = overrides.get("palettes")
    if isinstance(pals, list) and pals:
        new_pals: List[Dict[str, Any]] = []
        for p in pals:
            if not isinstance(p, dict):
                continue
            np = dict(p)
            for key in ("bg", "fill", "shadow", "accent", "border", "rule", "stamp_bg", "stamp_fg", "label"):
                if key in np:
                    rgb = _rgb_list(np[key])
                    if rgb:
                        np[key] = tuple(rgb)
            new_pals.append(np)
        if new_pals:
            out["palettes"] = new_pals

    return out


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued|running|done|error
    message: str = ""
    align_skipped: bool = False
    lyrics_kind: str = "plain"
    error: Optional[str] = None
    work_dir: Path = field(default_factory=Path)
    options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        master = self.work_dir / "master.mp4"
        lite = self.work_dir / "master-lite.mp4"
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "align_skipped": self.align_skipped,
            "lyrics_kind": self.lyrics_kind,
            "error": self.error,
            "has_master": master.is_file(),
            "has_lite": lite.is_file(),
        }


class JobManager:
    def __init__(self, max_workers: int = 1) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dazibao-job")

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def create(
        self,
        *,
        audio_bytes: bytes,
        audio_name: str,
        lyrics_text: str,
        options: Dict[str, Any],
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        work = data_root() / job_id
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        # persist uploads
        suffix = Path(audio_name or "audio.mp3").suffix or ".mp3"
        audio_path = work / f"audio{suffix}"
        audio_path.write_bytes(audio_bytes)
        (work / "lyrics.txt").write_text(lyrics_text, encoding="utf-8")
        (work / "options.json").write_text(
            json.dumps(options, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        job = Job(id=job_id, work_dir=work, options=options, message="queued")
        with self._lock:
            self._jobs[job_id] = job
        self._pool.submit(self._run, job_id)
        return job

    def _update(self, job: Job, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(job, k, v)

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return
        self._update(job, status="running", message="preparing")
        try:
            opts = job.options or {}
            work = job.work_dir
            audio_files = list(work.glob("audio.*"))
            if not audio_files:
                raise FileNotFoundError("audio file missing")
            audio = str(audio_files[0])
            lyrics_text = (work / "lyrics.txt").read_text(encoding="utf-8")

            style_name = opts.get("style") or "dazibao-ivory"
            style = load_style(style_name)
            style = apply_style_overrides(style, opts.get("style_overrides") or opts.get("colors"))

            max_chars = int(opts.get("max_chars") or 9)
            whisper_model = str(opts.get("whisper_model") or "medium")
            initial_prompt = opts.get("initial_prompt")
            max_line_sec = float(opts.get("max_line_sec") or 5.5)

            self._update(job, message="aligning lyrics")
            aligned, skipped, kind = prepare_aligned_from_lyrics(
                lyrics_text,
                audio=audio,
                max_chars=max_chars,
                whisper_model=whisper_model,
                initial_prompt=initial_prompt,
                max_line_sec=max_line_sec,
            )
            self._update(job, align_skipped=skipped, lyrics_kind=kind)
            save_aligned(aligned, work / "aligned.json")
            save_srt(aligned, work / "aligned.srt")

            out = work / "master.mp4"
            self._update(job, message="rendering video")
            render_mv(
                audio=audio,
                aligned=aligned,
                out=str(out),
                style=style,
                bg_path=opts.get("bg"),
                bg_color=str(opts.get("bg_color") or "#141210"),
                title=str(opts.get("title") or ""),
                author=str(opts.get("author") or ""),
                title_dur=opts.get("title_dur"),
                title_before_lyric=float(opts.get("title_before_lyric") or 1.0),
                title_fade=float(opts.get("title_fade") or 0.8),
                lead=float(opts.get("lead") or 0.12),
                max_chars=max_chars,
                lite=bool(opts.get("lite")),
                width=int(opts.get("width") or 1080),
                height=int(opts.get("height") or 1920),
                fps=int(opts.get("fps") or 24),
                gap_mode=str(opts.get("gap_mode") or "hold"),
            )
            self._update(job, status="done", message="done")
        except Exception as e:
            self._update(
                job,
                status="error",
                message="error",
                error=f"{e}\n{traceback.format_exc()}",
            )


# process-wide singleton
manager = JobManager(max_workers=1)
