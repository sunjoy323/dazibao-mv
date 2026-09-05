"""FastAPI application: styles/fonts/jobs + static SPA."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..styles import is_poster_fill, list_builtin_styles, load_style
from .fonts import discover_fonts
from .jobs import manager
from .lyrics import detect_lyrics_kind


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, tuple):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items() if not str(k).startswith("_")}
    return obj


def style_public(name: str) -> Dict[str, Any]:
    style = load_style(name)
    data = {
        "name": style.get("name") or name,
        "description": style.get("description") or "",
        "font": style.get("font"),
        "mode": style.get("mode") or "",
        "poster_fill": is_poster_fill(style),
        "decor": bool(style.get("decor")),
        "shadow_offset": list(style.get("shadow_offset") or (18, 18)),
        "verse": _jsonable(style.get("verse") or {}),
        "chorus": _jsonable(style.get("chorus") or {}),
        "hook": _jsonable(style.get("hook") or {}),
        "palettes": _jsonable(style.get("palettes") or []),
        "hook_keywords": list(style.get("hook_keywords") or []),
        "chorus_keywords": list(style.get("chorus_keywords") or []),
    }
    return data


def create_app() -> FastAPI:
    app = FastAPI(title="dazibao-mv", version="0.1.0")
    static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/api/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/styles")
    def api_styles() -> Dict[str, Any]:
        names = list_builtin_styles()
        styles = [style_public(n) for n in names]
        return {"styles": styles}

    @app.get("/api/fonts")
    def api_fonts() -> Dict[str, Any]:
        return {"fonts": discover_fonts()}

    @app.post("/api/detect-lyrics")
    async def api_detect_lyrics(
        lyrics_text: Optional[str] = Form(None),
        lyrics_file: Optional[UploadFile] = File(None),
    ) -> Dict[str, Any]:
        text = lyrics_text or ""
        if lyrics_file is not None:
            raw = await lyrics_file.read()
            text = raw.decode("utf-8", errors="replace")
        kind = detect_lyrics_kind(text)
        return {
            "kind": kind,
            "align_skipped": kind in ("srt", "lrc"),
            "preview_chars": len(text),
        }

    @app.post("/api/jobs")
    async def create_job(
        audio: UploadFile = File(...),
        lyrics_file: Optional[UploadFile] = File(None),
        lyrics_text: Optional[str] = Form(None),
        options: Optional[str] = Form(None),
    ) -> Dict[str, Any]:
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="audio file is empty")

        text = (lyrics_text or "").strip()
        if lyrics_file is not None:
            raw = await lyrics_file.read()
            file_text = raw.decode("utf-8", errors="replace").strip()
            if file_text:
                text = file_text
        if not text:
            raise HTTPException(
                status_code=400,
                detail="lyrics required (lyrics_file or lyrics_text)",
            )

        opts: Dict[str, Any] = {}
        if options:
            try:
                opts = json.loads(options)
            except json.JSONDecodeError as e:
                raise HTTPException(status_code=400, detail=f"invalid options JSON: {e}") from e
            if not isinstance(opts, dict):
                raise HTTPException(status_code=400, detail="options must be a JSON object")

        job = manager.create(
            audio_bytes=audio_bytes,
            audio_name=audio.filename or "audio.mp3",
            lyrics_text=text,
            options=opts,
        )
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> Dict[str, Any]:
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/download")
    def download_job(job_id: str, lite: int = 0) -> FileResponse:
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status != "done":
            raise HTTPException(status_code=409, detail=f"job status is {job.status}")
        path = job.work_dir / ("master-lite.mp4" if lite else "master.mp4")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="video not found")
        filename = f"dazibao-{job_id}{'-lite' if lite else ''}.mp4"
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=filename,
        )

    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
