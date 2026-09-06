"""Audio duration probe — Chrome WebM without container duration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from dazibao_mv.render import (
    _duration_from_packet_pts,
    _finite_duration,
    _parse_ffmpeg_duration_line,
    _probe_audio_duration,
)

SONG_WEBM = Path("/workspace/dazibao-samples/out/baiyueguang/song.webm")
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def test_finite_duration_rejects_na_and_inf():
    assert _finite_duration("N/A") is None
    assert _finite_duration("inf") is None
    assert _finite_duration("-1") is None
    assert _finite_duration("0") is None
    assert _finite_duration("1.5") == 1.5


def test_parse_ffmpeg_duration_skips_na():
    assert _parse_ffmpeg_duration_line("Duration: N/A, start: 0.000000") is None
    assert _parse_ffmpeg_duration_line("  Duration: 00:06:25.02, start:") == pytest.approx(
        6 * 60 + 25.02
    )


def _make_opus_webm(path: Path, seconds: float = 1.5) -> Path:
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=d={seconds}",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe required")
def test_probe_generated_opus_webm(tmp_path):
    webm = _make_opus_webm(tmp_path / "sine.webm", 1.5)
    dur = _probe_audio_duration(str(webm))
    assert dur > 1.0
    assert dur < 3.0

    pts = _duration_from_packet_pts(str(webm), pad=0.0)
    assert pts is not None
    assert pts > 1.0

    real_check = subprocess.check_output
    real_run = subprocess.run

    def fake_check_output(cmd, *a, **kw):
        joined = " ".join(str(x) for x in cmd)
        if "packet=" in joined:
            return real_check(cmd, *a, **kw)
        if "format=duration" in joined or "stream=duration" in joined:
            return "N/A\n"
        return real_check(cmd, *a, **kw)

    def fake_run(cmd, *a, **kw):
        joined = " ".join(str(x) for x in cmd)
        # ffmpeg -i only (no -f null) → Duration N/A
        if joined.startswith("ffmpeg") or "/ffmpeg" in joined.split()[0]:
            if "-f" not in cmd and "null" not in cmd:
                class R:
                    stderr = "Input #0\n  Duration: N/A, start: 0.000000, bitrate: N/A\n"
                    stdout = ""

                return R()
        return real_run(cmd, *a, **kw)

    with (
        patch("subprocess.check_output", side_effect=fake_check_output),
        patch("subprocess.run", side_effect=fake_run),
    ):
        dur2 = _probe_audio_duration(str(webm))
    assert dur2 > 1.0
    assert dur2 < 3.5


@pytest.mark.skipif(
    not SONG_WEBM.is_file() or not HAVE_FFMPEG, reason="sample song.webm missing"
)
def test_probe_chrome_song_webm():
    """Real Chrome MediaRecorder WebM: format duration is N/A."""
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(SONG_WEBM),
        ],
        text=True,
    ).strip()
    assert out.upper() == "N/A" or _finite_duration(out) is None

    dur = _probe_audio_duration(str(SONG_WEBM))
    assert 380.0 < dur < 400.0


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe required")
def test_duration_from_packet_pts_unit(tmp_path):
    webm = _make_opus_webm(tmp_path / "sine2.webm", 1.2)
    pts = _duration_from_packet_pts(str(webm), pad=0.02)
    assert pts is not None
    assert 1.0 < pts < 2.5
