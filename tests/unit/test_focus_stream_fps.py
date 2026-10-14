"""host_tools.focus_stream: the --fps cap reaches the board's start_stream settings (QE #107)."""

from host_tools import focus_stream as fs


def test_fps_reaches_settings_only_when_set():
    assert fs.stream_settings("VGA", 70, 3600, 10.0)["fps"] == 10.0
    assert "fps" not in fs.stream_settings("VGA", 70, 3600, 0.0)   # old behaviour unchanged
    assert fs.stream_settings("HD", 50, 60, 0) == {"framesize": "HD", "jpeg_quality": 50,
                                                    "max_seconds": 60}


def test_cli_parses_fps(monkeypatch):
    seen = {}
    monkeypatch.setattr(fs, "run", lambda **kw: seen.update(kw))
    assert fs.main(["--serial", "x", "--fps", "10"]) == 0
    assert seen["fps"] == 10.0
