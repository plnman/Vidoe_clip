"""합성 영상으로 컷·이어붙이기 파이프라인을 검증한다(유튜브 접속 없이)."""

import subprocess

import pytest

from app import media


def _make_source(path, seconds=12, size="320x240", fps=25, with_audio=True):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", str(seconds)]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    return _make_source(tmp_path_factory.mktemp("src") / "source.mp4")


def test_probe_reads_streams(source):
    info = media.probe(source)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (320, 240)
    assert info.duration == pytest.approx(12, abs=0.5)


def test_probe_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(media.MediaError):
        media.probe(bad)


def test_render_single_cut(source, tmp_path):
    out = media.render([media.Cut(source, 2.0, 5.0)], tmp_path / "out.mp4")
    assert media.probe(out).duration == pytest.approx(3.0, abs=0.3)


def test_render_concatenates_multiple_cuts_in_order(source, tmp_path):
    cuts = [media.Cut(source, 8.0, 10.0), media.Cut(source, 1.0, 3.0), media.Cut(source, 5.0, 5.5)]
    out = media.render(cuts, tmp_path / "multi.mp4")
    info = media.probe(out)
    assert info.duration == pytest.approx(4.5, abs=0.4)
    assert info.has_video and info.has_audio


def test_render_reports_progress(source, tmp_path):
    seen = []
    media.render([media.Cut(source, 0.0, 6.0)], tmp_path / "p.mp4", on_progress=seen.append)
    assert seen and 0.0 <= min(seen) <= max(seen) <= 1.0
    assert max(seen) > 0.5


def test_render_mixes_sources_of_different_size(source, tmp_path):
    other = _make_source(tmp_path / "small.mp4", seconds=6, size="160x120", fps=25)
    out = media.render(
        [media.Cut(source, 0.0, 2.0), media.Cut(other, 0.0, 2.0)], tmp_path / "mixed.mp4"
    )
    info = media.probe(out)
    assert (info.width, info.height) == (320, 240)
    assert info.duration == pytest.approx(4.0, abs=0.4)


def test_render_fills_silence_for_video_without_audio(source, tmp_path):
    mute = _make_source(tmp_path / "mute.mp4", seconds=6, with_audio=False)
    out = media.render(
        [media.Cut(source, 0.0, 2.0), media.Cut(mute, 0.0, 2.0)], tmp_path / "silence.mp4"
    )
    info = media.probe(out)
    assert info.has_audio
    assert info.duration == pytest.approx(4.0, abs=0.4)


def test_audio_only_output(source, tmp_path):
    out = media.render(
        [media.Cut(source, 0.0, 2.0), media.Cut(source, 4.0, 6.0)],
        tmp_path / "out.mp3",
        audio_only=True,
    )
    info = media.probe(out)
    assert info.has_audio and not info.has_video
    assert info.duration == pytest.approx(4.0, abs=0.4)


def test_empty_cut_list_raises(tmp_path):
    with pytest.raises(media.MediaError):
        media.render([], tmp_path / "none.mp4")


def test_zero_length_cuts_are_dropped(source, tmp_path):
    with pytest.raises(media.MediaError):
        media.render([media.Cut(source, 3.0, 3.0)], tmp_path / "none.mp4")


def test_thumbnail(source, tmp_path):
    shot = media.make_thumbnail(source, tmp_path / "thumb.jpg", at=3.0)
    assert shot is not None and shot.stat().st_size > 0
