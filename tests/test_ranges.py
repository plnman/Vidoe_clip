"""받을 범위를 묶는 규칙과, 그것을 사람에게 설명하는 말.

여유분이 붙고 붙어 있는 구간끼리 묶이므로, 화면에 뜨는 범위는 사용자가 적어 넣은
시각과 다를 수밖에 없다. 실제로 "내가 넣지 않은 시간대를 받고 있다"는 오해를 샀다.
그래서 어느 구간을 받는 중인지 함께 말하도록 했고, 그 말을 여기서 고정한다.
"""

import pytest

from app.projects import merge_needs


def needs(spans, pad=10.0, duration=2403.0):
    return merge_needs(((s, e, i) for i, (s, e) in enumerate(spans, 1)), pad, duration)


# --- 묶는 규칙 --------------------------------------------------------------

def test_pad_widens_each_side():
    [need] = needs([(195.0, 252.0)])
    assert (need.start, need.end) == (185.0, 262.0)
    assert need.cuts == [1]


def test_touching_ranges_merge_into_one_download():
    """3:15-4:12 와 4:30-7:05. 여유분을 붙이면 4:22와 4:20이 겹친다 → 한 번에 받는다."""
    result = needs([(195.0, 252.0), (270.0, 425.0)])
    assert len(result) == 1
    assert (result[0].start, result[0].end) == (185.0, 435.0)
    assert result[0].cuts == [1, 2]


def test_far_apart_ranges_stay_separate():
    result = needs([(195.0, 252.0), (1000.0, 1100.0)])
    assert len(result) == 2
    assert [n.cuts for n in result] == [[1], [2]]


def test_pad_is_clamped_to_the_video():
    [need] = needs([(2.0, 20.0)], pad=30.0, duration=100.0)
    assert (need.start, need.end) == (0.0, 50.0)


def test_pad_zero_keeps_exact_bounds():
    result = needs([(100.0, 110.0), (120.0, 130.0)], pad=0.0)
    assert len(result) == 2


# --- 사람에게 하는 말 --------------------------------------------------------

def test_label_names_the_segments_not_just_the_times():
    """'3:05~7:15 받는 중'만 보여주면 넣은 적 없는 시각이라 당황한다."""
    [need] = needs([(195.0, 252.0), (270.0, 425.0)])
    label = need.label()
    assert label.startswith("구간 1–2")
    assert "여유분 포함" in label
    assert "3:05" in label and "7:15" in label


def test_label_for_a_single_segment():
    [need] = needs([(463.0, 631.0)])
    assert need.label().startswith("구간 1 ")
    assert "7:33" in need.label() and "10:41" in need.label()


# --- 받을 양 ----------------------------------------------------------------

def test_span_is_larger_than_the_sum_of_segments():
    """여유분 때문에 실제로 받는 양은 구간 합계보다 크다. 화면이 이걸 알려줘야 한다."""
    spans = [(195.0, 252.0), (1000.0, 1100.0)]
    total_wanted = sum(e - s for s, e in spans)
    total_fetched = sum(n.length for n in needs(spans))
    assert total_fetched == pytest.approx(total_wanted + 4 * 10.0)


def test_merging_does_not_double_count_the_overlap():
    result = needs([(195.0, 252.0), (270.0, 425.0)])
    assert sum(n.length for n in result) == pytest.approx(250.0)


def test_nothing_to_do_when_there_are_no_segments():
    assert needs([]) == []
