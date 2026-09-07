"""Lite re-encode: args + skip when not smaller than master."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from dazibao_mv.render import LITE_AUDIO_BITRATE, LITE_CRF, LITE_MAXRATE, lite_ffmpeg_cmd, _write_lite_mp4


def test_lite_ffmpeg_cmd_uses_crf_and_maxrate():
    cmd = lite_ffmpeg_cmd(Path("master.mp4"), Path("master-lite.mp4"))
    assert cmd[:4] == ["ffmpeg", "-y", "-i", "master.mp4"]
    assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == LITE_CRF
    assert "-maxrate" in cmd and cmd[cmd.index("-maxrate") + 1] == LITE_MAXRATE
    assert "-b:v" not in cmd  # old fixed bitrate must not return
    assert "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == LITE_AUDIO_BITRATE
    assert cmd[-1] == "master-lite.mp4"


def test_write_lite_keeps_file_when_smaller(tmp_path: Path):
    master = tmp_path / "out.mp4"
    master.write_bytes(b"m" * 1000)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"l" * 400)

    with patch("dazibao_mv.render.subprocess.run", side_effect=fake_run) as run:
        got = _write_lite_mp4(master)
    assert got is not None
    assert got.name == "out-lite.mp4"
    assert got.is_file()
    assert got.stat().st_size < master.stat().st_size
    assert run.called
    assert "-crf" in run.call_args[0][0]


def test_write_lite_skips_when_not_smaller(tmp_path: Path, capsys):
    master = tmp_path / "out.mp4"
    master.write_bytes(b"m" * 500)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"L" * 800)

    with patch("dazibao_mv.render.subprocess.run", side_effect=fake_run):
        got = _write_lite_mp4(master)
    assert got is None
    assert not (tmp_path / "out-lite.mp4").exists()
    out = capsys.readouterr().out
    assert "skipped" in out
    assert "already small enough" in out
