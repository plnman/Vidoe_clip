import json

import pytest

from app.segments import (
    Segment,
    format_timecode,
    merge_overlaps,
    parse_segments,
    parse_timecode,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("83", 83.0),
        ("1:23", 83.0),
        ("01:23", 83.0),
        ("00:01:23", 83.0),
        ("1:00:00", 3600.0),
        ("1:23.5", 83.5),
        ("1:23,500", 83.5),
        (90, 90.0),
        (90.5, 90.5),
    ],
)
def test_parse_timecode(raw, expected):
    assert parse_timecode(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "abc", "1:2:3:4", "-5", "12:34:56.7890"])
def test_parse_timecode_rejects(raw):
    with pytest.raises(ValueError):
        parse_timecode(raw)


def test_format_timecode():
    assert format_timecode(83) == "1:23"
    assert format_timecode(3723) == "1:02:03"
    assert format_timecode(0) == "0:00"


def test_plain_ranges_with_various_separators():
    text = """
    00:01:23 - 00:02:45 인트로
    3:10~4:05 핵심 요약
    5:12 ~ 6:00
    7:00 -> 7:30 마지막
    """
    result = parse_segments(text)
    assert [(s.start, s.end) for s in result.segments] == [
        (83, 165),
        (190, 245),
        (312, 360),
        (420, 450),
    ]
    assert result.segments[0].title == "인트로"
    assert result.segments[2].title == ""
    assert not result.errors


def test_list_markers_and_brackets_are_stripped_from_title():
    text = "1. [00:01:23 - 00:02:45] 인트로\n- 3:10~4:05 · 요약"
    result = parse_segments(text)
    assert [s.title for s in result.segments] == ["인트로", "요약"]


def test_title_before_range():
    result = parse_segments("인트로 (00:01:23-00:02:45)")
    assert result.segments[0].title == "인트로"


@pytest.mark.parametrize(
    "line",
    [
        "8:00 - 9:15 반론과 재반박",
        "8:00-9:15 반론과 재반박",
        "8:00 ~ 9:15 반론과 재반박",
        "| 8:00 | 9:15 | 반론과 재반박 |",
        "| 8:00 - 9:15 | 반론과 재반박 |",
        "8:00부터 9:15까지 반론과 재반박",
        "- 8:00 -> 9:15 반론과 재반박",
    ],
)
def test_all_spellings_of_a_range_mean_the_same_thing(line):
    """구분자가 무엇이든( - | ~ 부터/까지 ) 결과는 같아야 한다."""
    segment = parse_segments(line).segments[0]
    assert (segment.start, segment.end, segment.title) == (480, 555, "반론과 재반박")


def test_markdown_table():
    text = """
    | 시작 | 종료 | 내용 |
    |---|---|---|
    | 00:01:23 | 00:02:45 | 인트로 |
    | 3:10 | 4:05 | 핵심 요약 |
    """
    result = parse_segments(text)
    assert [(s.start, s.end, s.title) for s in result.segments] == [
        (83, 165, "인트로"),
        (190, 245, "핵심 요약"),
    ]


def test_markdown_table_with_range_in_one_cell():
    result = parse_segments("| 00:01:23 - 00:02:45 | 인트로 |")
    assert (result.segments[0].start, result.segments[0].end) == (83, 165)
    assert result.segments[0].title == "인트로"


def test_json_list():
    text = json.dumps(
        [
            {"start": "1:23", "end": "2:45", "title": "인트로"},
            {"start": 190, "end": 245, "label": "요약"},
        ],
        ensure_ascii=False,
    )
    result = parse_segments(text)
    assert [(s.start, s.end, s.title) for s in result.segments] == [
        (83, 165, "인트로"),
        (190, 245, "요약"),
    ]


def test_json_wrapped_in_object():
    text = '{"segments": [{"from": "0:10", "to": "0:20", "제목": "가"}]}'
    result = parse_segments(text)
    assert (result.segments[0].start, result.segments[0].end) == (10, 20)
    assert result.segments[0].title == "가"


def test_chapter_style_open_ends_close_at_next_start():
    text = "00:00 오프닝\n1:30 본론\n5:00 마무리"
    result = parse_segments(text, duration=400)
    assert [(s.start, s.end) for s in result.segments] == [(0, 90), (90, 300), (300, 400)]


def test_last_open_end_without_duration_is_reported():
    result = parse_segments("0:10 하나\n1:00 둘")
    assert [(s.start, s.end) for s in result.segments] == [(10, 60)]
    assert result.errors and "끝 시간" in result.errors[0].reason


def test_prose_lines_are_ignored_but_timecode_lines_error():
    result = parse_segments("이 영상의 요약입니다\n중요 포인트 3가지\n12:34 - 어어")
    assert result.segments == []
    assert [e.reason for e in result.errors] == ["끝 시간을 알 수 없습니다"]


def test_backwards_range_is_an_error():
    result = parse_segments("2:45 - 1:23 거꾸로")
    assert result.segments == []
    assert "끝이 시작보다" in result.errors[0].reason


def test_duration_clamps_and_drops():
    result = parse_segments("0:10-9:00 넘침\n20:00-21:00 아예밖", duration=300)
    assert [(s.start, s.end) for s in result.segments] == [(10, 300)]
    assert result.warnings
    assert any("벗어난" in e.reason for e in result.errors)


def test_overlap_produces_warning_not_error():
    result = parse_segments("0:10-1:00\n0:50-2:00")
    assert len(result.segments) == 2
    assert any("겹치는" in w for w in result.warnings)


def test_merge_overlaps():
    merged = merge_overlaps(
        [Segment(10, 60, "가"), Segment(50, 120, "나"), Segment(200, 240, "다")]
    )
    assert [(s.start, s.end) for s in merged] == [(10, 120), (200, 240)]
    assert merged[0].title == "가 / 나"


def test_empty_input():
    assert parse_segments("").to_dict()["segments"] == []


def test_segment_cap():
    text = "\n".join(f"{i}:00-{i}:30" for i in range(1, 80))
    result = parse_segments(text)
    assert len(result.segments) == 60
    assert any("60개만" in w for w in result.warnings)
