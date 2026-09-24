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


# --- 하드웨어 인코더 ----------------------------------------------------------

@pytest.mark.parametrize(
    "name,expect",
    [
        ("h264_nvenc", "-cq"),
        ("h264_qsv", "-global_quality"),
        ("h264_amf", "-qp_i"),
        ("h264_videotoolbox", "-q:v"),
    ],
)
def test_hardware_quality_args_use_each_encoder_own_knob(name, expect):
    """하드웨어 인코더는 -crf를 모른다. 종류마다 다른 이름을 써야 한다."""
    args = media._hw_quality_args(name, "fast")
    assert expect in args
    assert "-crf" not in args


def test_hardware_quality_follows_the_quality_step():
    fast = media._hw_quality_args("h264_nvenc", "fast")
    best = media._hw_quality_args("h264_nvenc", "quality")
    assert fast[fast.index("-cq") + 1] > best[best.index("-cq") + 1]  # 숫자가 작을수록 고화질


def test_detection_skips_encoders_not_in_the_build(monkeypatch):
    monkeypatch.setattr(media, "_hw_cache", {})
    monkeypatch.setattr(media, "_hw_attempts", {})
    monkeypatch.setattr(media, "_compiled_encoders", lambda: {"libx264"})
    tried = []
    monkeypatch.setattr(media, "_encoder_works",
                        lambda name: (tried.append(name), (False, "없음"))[1])
    assert media.detect_hardware_encoder("libx264") is None
    assert tried == [], "빌드에 없는 인코더를 굳이 돌려봤다"


def test_detection_tries_candidates_and_caches(monkeypatch):
    monkeypatch.setattr(media, "_hw_cache", {})
    monkeypatch.setattr(media, "_hw_attempts", {})
    monkeypatch.setattr(media, "_compiled_encoders", lambda: set())
    calls = []

    def works(name):
        calls.append(name)
        return (True, "") if name == "h264_qsv" else (False, "이 기계에 없음")

    monkeypatch.setattr(media, "_encoder_works", works)
    assert media.detect_hardware_encoder("libx264") == ("h264_qsv", "Intel")
    assert calls == ["h264_nvenc", "h264_qsv"], "되는 것을 찾으면 멈춰야 한다"
    media.detect_hardware_encoder("libx264")
    assert len(calls) == 2, "두 번째 조회는 조사하지 않고 기억한 값을 써야 한다"


def test_hardware_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(media, "_hw_cache", {})
    monkeypatch.setattr(media, "_hw_attempts", {})
    monkeypatch.setattr(media.config, "HARDWARE", "off")
    monkeypatch.setattr(media, "_encoder_works", lambda name: (True, ""))
    assert media.detect_hardware_encoder("libx264") is None


def test_encode_args_use_hardware_encoder_when_given():
    args = media._encode_args("mp4", "fast", True, True, hw="h264_nvenc")
    assert "h264_nvenc" in args and "libx264" not in args


def test_render_falls_back_to_cpu_when_hardware_fails(source, tmp_path, monkeypatch):
    """조사에서는 됐는데 실제 인코딩에서 실패할 수 있다. 그래도 완성본은 나와야 한다."""
    monkeypatch.setattr(media, "detect_hardware_encoder", lambda vcodec: ("h264_없는것", "테스트"))
    warnings = []
    out = media.render([media.Cut(source, 1.0, 3.0)], tmp_path / "fallback.mp4",
                       warn=warnings.append)
    info = media.probe(out)
    assert info.duration == pytest.approx(2.0, abs=0.4)
    assert any("CPU로 다시" in text for text in warnings), "되돌렸다는 사실을 알려야 한다"


def test_render_reports_the_finalizing_phase(source, tmp_path):
    """인코딩이 끝난 뒤 파일을 마무리하는 시간에는 진행률이 나오지 않는다."""
    phases = []
    media.render([media.Cut(source, 0.0, 2.0)], tmp_path / "phase.mp4",
                 on_phase=phases.append)
    assert "finalizing" in phases
    assert phases.count("finalizing") == 1, "한 번만 알려야 한다"


# --- 진행률 파싱 --------------------------------------------------------------

def _fake_ffmpeg(lines):
    """ffmpeg의 -progress 출력을 그대로 흉내 내는 명령."""
    import sys
    body = "\n".join(f"print({line!r})" for line in lines)
    return [sys.executable, "-c", body]


def test_progress_ignores_out_time_ms_which_is_actually_microseconds():
    """ffmpeg의 out_time_ms 는 이름과 달리 값이 마이크로초다.

    밀리초로 읽으면 진행률이 1000배가 되어 시작하자마자 100%가 된다.
    실제로 그 버그 때문에 화면이 늘 99%에 붙어 있었다.
    """
    seen = []
    media._run_with_progress(
        _fake_ffmpeg([
            "out_time_us=5000000",   # 5초
            "out_time_ms=5000000",   # 같은 5초를 ms 이름으로 또 보낸다
            "progress=continue",
        ]),
        total_seconds=100.0, on_progress=seen.append, cancel=None,
    )
    assert seen == [pytest.approx(0.05)], f"5/100 = 0.05 여야 하는데 {seen}"


def test_progress_handles_na_at_startup():
    seen = []
    media._run_with_progress(
        _fake_ffmpeg(["out_time_us=N/A", "out_time_us=2500000", "progress=continue"]),
        total_seconds=10.0, on_progress=seen.append, cancel=None,
    )
    assert seen == [pytest.approx(0.25)]


def test_finalizing_fires_only_when_encoding_ends():
    """진행률이 100%에 닿았다고 끝난 게 아니다. progress=end 만 믿는다."""
    phases = []
    media._run_with_progress(
        _fake_ffmpeg([
            "out_time_us=10000000", "progress=continue",   # 100% 지만 아직 진행 중
            "out_time_us=10000000", "progress=end",
        ]),
        total_seconds=10.0, on_progress=lambda f: None, cancel=None,
        on_phase=phases.append,
    )
    assert phases == ["finalizing"]


def test_failed_attempts_are_recorded_with_a_reason(monkeypatch):
    """CPU로 떨어졌을 때 왜 그랬는지 말할 수 있어야 한다."""
    monkeypatch.setattr(media, "_hw_cache", {})
    monkeypatch.setattr(media, "_hw_attempts", {})
    monkeypatch.setattr(media, "_compiled_encoders", lambda: {"h264_amf"})
    monkeypatch.setattr(media, "_encoder_works", lambda name: (False, "드라이버 없음"))

    assert media.detect_hardware_encoder("libx264") is None
    attempts = media.hardware_attempts("libx264")
    by_name = {a["encoder"]: a for a in attempts}
    assert by_name["h264_amf"]["reason"] == "드라이버 없음"
    assert by_name["h264_nvenc"]["reason"] == "이 ffmpeg 빌드에 없습니다"
    assert not any(a["ok"] for a in attempts)
