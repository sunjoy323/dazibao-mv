"""Web API tests — styles, timestamp skip detection, job validation."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from dazibao_mv.web.app import create_app
from dazibao_mv.web.audio_ext import resolve_audio_suffix
from dazibao_mv.web.lyrics import detect_lyrics_kind, parse_lrc, prepare_aligned_from_lyrics


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DAZIBAO_DATA", str(tmp_path / "data"))
    # Fresh manager pointing at temp data — recreate app after env set
    from dazibao_mv.web import jobs as jobs_mod

    jobs_mod.manager = jobs_mod.JobManager(max_workers=1)
    app = create_app()
    return TestClient(app)


def test_styles_endpoint(client):
    r = client.get("/api/styles")
    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data["styles"]]
    assert "dazibao-ivory" in names
    assert "poster-wall" in names
    ivory = next(s for s in data["styles"] if s["name"] == "dazibao-ivory")
    assert "fill" in ivory["verse"]
    wall = next(s for s in data["styles"] if s["name"] == "poster-wall")
    assert wall["poster_fill"] is True
    assert len(wall["palettes"]) >= 2


def test_fonts_endpoint(client):
    r = client.get("/api/fonts")
    assert r.status_code == 200
    fonts = r.json()["fonts"]
    assert isinstance(fonts, list)
    # At least something discoverable on typical Linux CI images
    assert any(f["path"].endswith((".ttf", ".otf", ".ttc")) for f in fonts) or fonts == []


def test_detect_lyrics_kind_helpers():
    assert detect_lyrics_kind("一行歌词\n另一行") == "plain"
    lrc = "[00:12.50]后海不是海\n[00:16.00]八零后的老登"
    assert detect_lyrics_kind(lrc) == "lrc"
    srt = (
        "1\n00:00:01,000 --> 00:00:03,000\n你好\n\n"
        "2\n00:00:03,500 --> 00:00:05,000\n世界\n"
    )
    assert detect_lyrics_kind(srt) == "srt"


def test_parse_lrc_aligned():
    text = "[00:01.00]第一句\n[00:03.50]第二句\n[00:05.00]第三句"
    cues = parse_lrc(text)
    assert len(cues) == 3
    assert cues[0]["start"] == 1.0
    assert cues[0]["end"] == 3.5
    assert cues[1]["text"] == "第二句"


def test_prepare_aligned_skips_whisper_for_lrc():
    text = "[00:01.00]第一句很长很长\n[00:04.00]第二句"
    aligned, skipped, kind = prepare_aligned_from_lyrics(text, audio=None, max_chars=9)
    assert skipped is True
    assert kind == "lrc"
    assert aligned
    assert aligned[0]["start"] == 1.0


def test_detect_lyrics_api(client):
    r = client.post(
        "/api/detect-lyrics",
        data={"lyrics_text": "[00:10.00]带时间轴的歌词"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "lrc"
    assert body["align_skipped"] is True


def test_job_create_requires_lyrics(client):
    audio = ("song.mp3", io.BytesIO(b"ID3fakeaudio"), "audio/mpeg")
    r = client.post("/api/jobs", files={"audio": audio})
    assert r.status_code == 400


def test_job_create_requires_audio_bytes(client):
    r = client.post(
        "/api/jobs",
        files={"audio": ("song.mp3", io.BytesIO(b""), "audio/mpeg")},
        data={"lyrics_text": "一行"},
    )
    assert r.status_code == 400


def test_job_create_invalid_options(client):
    audio = ("song.mp3", io.BytesIO(b"ID3fake"), "audio/mpeg")
    r = client.post(
        "/api/jobs",
        files={"audio": audio},
        data={"lyrics_text": "一行", "options": "not-json{"},
    )
    assert r.status_code == 400


def test_job_create_with_lrc_mocks_render(client, monkeypatch):
    """Create job with LRC → align skipped; mock heavy render."""
    from dazibao_mv.web import jobs as jobs_mod

    calls = {}

    def fake_render_mv(**kwargs):
        calls["kwargs"] = kwargs
        out = kwargs["out"]
        from pathlib import Path

        Path(out).write_bytes(b"fake-mp4")
        return Path(out)

    monkeypatch.setattr(jobs_mod, "render_mv", fake_render_mv)

    audio = ("song.mp3", io.BytesIO(b"ID3fakeaudio-bytes"), "audio/mpeg")
    options = json.dumps(
        {
            "style": "dazibao-ivory",
            "title": "测试",
            "lite": False,
            "whisper_model": "tiny",
        }
    )
    r = client.post(
        "/api/jobs",
        files={"audio": audio},
        data={
            "lyrics_text": "[00:01.00]第一句\n[00:03.00]第二句",
            "options": options,
        },
    )
    assert r.status_code == 200
    job_id = r.json()["id"]

    # Wait for background thread
    import time

    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        st = client.get(f"/api/jobs/{job_id}")
        assert st.status_code == 200
        status = st.json()
        if status["status"] in ("done", "error"):
            break
        time.sleep(0.05)

    assert status is not None
    assert status["status"] == "done", status
    assert status["align_skipped"] is True
    assert status["lyrics_kind"] == "lrc"
    assert "kwargs" in calls
    assert calls["kwargs"]["title"] == "测试"

    dl = client.get(f"/api/jobs/{job_id}/download")
    assert dl.status_code == 200
    assert dl.content == b"fake-mp4"


def test_static_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "大字报" in r.text or "dazibao" in r.text.lower()


def test_resolve_audio_suffix_webm_filename():
    assert resolve_audio_suffix("rec.webm") == ".webm"


def test_resolve_audio_suffix_webm_mime():
    assert resolve_audio_suffix("blob", "audio/webm") == ".webm"
    assert resolve_audio_suffix("blob", "video/webm") == ".webm"


def test_resolve_audio_suffix_webm_magic():
    # EBML header used by WebM / Matroska
    data = b"\x1a\x45\xdf\xa3" + b"\x00" * 16
    assert resolve_audio_suffix("blob", None, data=data) == ".webm"


def test_job_create_webm_writes_audio_webm(client, monkeypatch):
    """POST clip.webm + lyrics creates job and persists audio.webm under work dir."""
    from dazibao_mv.web import app as app_mod
    from dazibao_mv.web import jobs as jobs_mod

    # App holds its own manager reference; stop the worker before create runs.
    monkeypatch.setattr(app_mod.manager._pool, "submit", lambda *a, **k: None)

    webm_bytes = b"\x1a\x45\xdf\xa3" + b"fake-webm-opus"
    audio = ("clip.webm", io.BytesIO(webm_bytes), "audio/webm")
    r = client.post(
        "/api/jobs",
        files={"audio": audio},
        data={"lyrics_text": "[00:01.00]一句\n[00:03.00]两句"},
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    audio_path = jobs_mod.data_root() / job_id / "audio.webm"
    assert audio_path.is_file()
    assert audio_path.read_bytes() == webm_bytes
