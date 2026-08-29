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


@pytest.mark.parametrize("fmt,ext", [("mp3", ".mp3"), ("m4a", ".m4a")])
def test_audio_only_formats(source, tmp_path, fmt, ext):
    out = media.render(
        [media.Cut(source, 0.0, 2.0), media.Cut(source, 4.0, 6.0)],
        tmp_path / f"out{ext}",
        fmt=fmt,
    )
    info = media.probe(out)
    assert info.has_audio and not info.has_video
    assert info.duration == pytest.approx(4.0, abs=0.4)


@pytest.mark.parametrize("fmt,ext", [("mp4", ".mp4"), ("mp4_hevc", ".mp4"), ("webm", ".webm")])
def test_video_formats(source, tmp_path, fmt, ext):
    out = media.render([media.Cut(source, 1.0, 3.0)], tmp_path / f"out-{fmt}{ext}", fmt=fmt)
    info = media.probe(out)
    assert info.has_video and info.has_audio
    assert info.duration == pytest.approx(2.0, abs=0.4)


def test_gif_has_no_audio_and_is_downscaled(source, tmp_path):
    out = media.render([media.Cut(source, 0.0, 2.0)], tmp_path / "out.gif", fmt="gif")
    info = media.probe(out)
    assert info.has_video and not info.has_audio
    assert info.width == 360  # fast 단계 기본 폭


def test_gif_rejects_long_output(source, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "MAX_GIF_SECONDS", 1)
    with pytest.raises(media.MediaError, match="GIF"):
        media.render([media.Cut(source, 0.0, 5.0)], tmp_path / "long.gif", fmt="gif")


def test_unknown_format_is_rejected(source, tmp_path):
    with pytest.raises(media.MediaError, match="지원하지 않는"):
        media.render([media.Cut(source, 0.0, 1.0)], tmp_path / "x.avi", fmt="avi")


def test_empty_cut_list_raises(tmp_path):
    with pytest.raises(media.MediaError):
        media.render([], tmp_path / "none.mp4")


def test_zero_length_cuts_are_dropped(source, tmp_path):
    with pytest.raises(media.MediaError):
        media.render([media.Cut(source, 3.0, 3.0)], tmp_path / "none.mp4")


def test_thumbnail(source, tmp_path):
    shot = media.make_thumbnail(source, tmp_path / "thumb.jpg", at=3.0)
    assert shot is not None and shot.stat().st_size > 0


def test_probe_handles_korean_path(tmp_path):
    """한글 경로. ffmpeg 출력은 UTF-8인데 한글 윈도우 기본 인코딩은 cp949라 죽던 자리."""
    korean = tmp_path / "한글 제목 영상.mp4"
    _make_source(korean, seconds=3)
    assert media.probe(korean).duration == pytest.approx(3, abs=0.4)


def test_render_handles_korean_path(tmp_path):
    korean = tmp_path / "한글 소스.mp4"
    _make_source(korean, seconds=6)
    out = media.render([media.Cut(korean, 1.0, 3.0)], tmp_path / "한글 결과.mp4")
    assert media.probe(out).duration == pytest.approx(2.0, abs=0.3)


# --- 구간 제목 자막 ----------------------------------------------------------

def _brightness(path, at):
    """그 시각 한 프레임의 평균 밝기. 글자가 얹히면 달라진다."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
         "-vf", "crop=iw:ih/6:0:0,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    return sum(out) / max(1, len(out))


def test_titles_are_drawn_only_during_their_own_segment(tmp_path):
    """제목은 그 구간이 나오는 동안에만 화면 위쪽에 보여야 한다."""
    if media.find_font() is None:
        pytest.skip("이 컴퓨터에 쓸 글꼴이 없다")

    source = _make_source(tmp_path / "src.mp4", seconds=12, size="640x360")
    cuts = [
        media.Cut(source, 0.0, 3.0, title="첫 번째 구간 제목"),
        media.Cut(source, 5.0, 8.0, title=""),  # 제목 없는 구간
    ]
    plain = media.render(cuts, tmp_path / "plain.mp4")
    titled = media.render(cuts, tmp_path / "titled.mp4", titles=True)

    assert media.probe(titled).duration == pytest.approx(6.0, abs=0.3)
    # 제목이 있는 앞 구간은 위쪽 띠가 달라지고, 제목 없는 뒤 구간은 그대로여야 한다
    assert abs(_brightness(titled, 1.5) - _brightness(plain, 1.5)) > 1.0
    assert abs(_brightness(titled, 4.5) - _brightness(plain, 4.5)) < 0.5


def test_titles_off_leaves_the_video_untouched(tmp_path):
    source = _make_source(tmp_path / "src2.mp4", seconds=6, size="320x240")
    cuts = [media.Cut(source, 0.0, 3.0, title="제목 있음")]
    plain = media.render(cuts, tmp_path / "off.mp4", titles=False)
    assert media.probe(plain).duration == pytest.approx(3.0, abs=0.3)
    assert not list(tmp_path.glob(".titles-*"))


def test_titles_survive_a_missing_font(tmp_path, monkeypatch):
    """글꼴이 없다고 완성본을 못 받으면 곤란하다. 제목만 빼고 만든다."""
    monkeypatch.setattr(media, "find_font", lambda: None)
    warnings = []
    source = _make_source(tmp_path / "src3.mp4", seconds=6, size="320x240")
    out = media.render(
        [media.Cut(source, 0.0, 3.0, title="제목")],
        tmp_path / "nofont.mp4",
        titles=True,
        warn=warnings.append,
    )
    assert media.probe(out).duration == pytest.approx(3.0, abs=0.3)
    assert warnings and "글꼴" in warnings[0]


def test_title_workdir_is_cleaned_up(tmp_path):
    if media.find_font() is None:
        pytest.skip("이 컴퓨터에 쓸 글꼴이 없다")
    source = _make_source(tmp_path / "src4.mp4", seconds=6, size="320x240")
    media.render([media.Cut(source, 0.0, 3.0, title="제목")], tmp_path / "clean.mp4", titles=True)
    assert not list(tmp_path.glob(".titles-*"))


def test_special_characters_in_a_title_do_not_break_the_filtergraph(tmp_path):
    """제목은 사용자가 붙여넣은 아무 문장이다. 콜론·쉼표·따옴표가 들어와도 돌아야 한다."""
    if media.find_font() is None:
        pytest.skip("이 컴퓨터에 쓸 글꼴이 없다")
    source = _make_source(tmp_path / "src5.mp4", seconds=6, size="320x240")
    nasty = r"AI: '행위자(Agent)'다, 그리고 [주석] 50% \ 끝"
    out = media.render(
        [media.Cut(source, 0.0, 3.0, title=nasty)], tmp_path / "nasty.mp4", titles=True
    )
    assert media.probe(out).duration == pytest.approx(3.0, abs=0.3)
