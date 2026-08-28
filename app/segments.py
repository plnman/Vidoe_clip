"""제미나이 등이 뱉은 자유 형식 구간 목록을 구조화된 세그먼트로 파싱한다.

받아주는 형태:
    00:01:23 - 00:02:45 인트로
    1. 1:23~2:45 핵심 요약
    | 00:01:23 | 00:02:45 | 인트로 |      (마크다운 표)
    [{"start": "1:23", "end": "2:45", "title": "인트로"}]   (JSON)
    00:01:23 인트로                        (시작만 — 다음 항목 시작에서 끊김)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_SEGMENTS = 60

# 83 / 1:23 / 01:02:03 / 1:23.500 / 1:23,500
_TC = r"\d{1,3}(?::\d{1,2}){0,2}(?:[.,]\d{1,3})?"
_TC_FULL = re.compile(rf"^{_TC}$")
# 콜론이 든 타임코드. "이 줄은 시간을 적으려 한 줄"인지 판정하는 데만 쓴다.
_HAS_CLOCK = re.compile(r"\d{1,3}:\d{1,2}")
_SEP = r"\s*(?:->|=>|~|〜|–|—|→|-{1,2}|부터|to|until)\s*"
_RANGE_RE = re.compile(rf"(?P<start>{_TC}){_SEP}(?P<end>{_TC})")
_SINGLE_RE = re.compile(rf"(?<![\d:.,]){_TC}(?![\d:.,])")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•·▪]|\(?\d{1,3}[.)])\s+")
_TABLE_SEP_RE = re.compile(r"^[\s|:\-–—=+]+$")
_TRIM = " \t\r\n-–—:|[](){}\"'`·•*#>,.…"
_TAIL_KO = ("까지", "부터")
# "8:00부터 9:15까지 제목" 처럼 쓰면 범위를 떼고 난 자리에 조사가 남는다
_LEAD_KO_RE = re.compile(r"^(?:까지|부터)(?=\s|$)\s*")


@dataclass
class Segment:
    """소스 영상 기준 초 단위 구간."""

    start: float
    end: float | None = None
    title: str = ""

    @property
    def duration(self) -> float:
        return 0.0 if self.end is None else max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": None if self.end is None else round(self.end, 3),
            "title": self.title,
            "duration": round(self.duration, 3),
        }


@dataclass
class ParseIssue:
    line_no: int
    text: str
    reason: str

    def to_dict(self) -> dict:
        return {"line": self.line_no, "text": self.text, "reason": self.reason}


@dataclass
class ParseResult:
    segments: list[Segment] = field(default_factory=list)
    errors: list[ParseIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "errors": [e.to_dict() for e in self.errors],
            "warnings": self.warnings,
            "total": round(sum(s.duration for s in self.segments), 3),
        }


def parse_timecode(value) -> float:
    """'1:23.5' / 83 / '00:01:23' -> 초. 형식이 아니면 ValueError."""
    if isinstance(value, bool):
        raise ValueError("시간 값이 아닙니다")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("시간이 음수입니다")
        return float(value)
    s = str(value).strip()
    if not _TC_FULL.match(s):
        raise ValueError(f"시간 형식을 알 수 없습니다: {value!r}")
    total = 0.0
    for part in s.replace(",", ".").split(":"):
        total = total * 60 + float(part)
    return total


def format_timecode(seconds: float) -> str:
    """초 -> 'H:MM:SS' 또는 'M:SS'."""
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _clean_title(*chunks: str) -> str:
    parts = []
    for chunk in chunks:
        t = _LEAD_KO_RE.sub("", chunk.strip().strip(_TRIM).strip())
        for tail in _TAIL_KO:
            if t.endswith(tail):
                t = t[: -len(tail)].strip(_TRIM).strip()
        if t:
            parts.append(t)
    return re.sub(r"\s+", " ", " ".join(parts))


def _from_json(text: str) -> ParseResult | None:
    """JSON으로 읽히면 그걸로 파싱한다. 아니면 None."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        for key in ("segments", "clips", "sections", "items", "chapters"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return None

    result = ParseResult()
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            result.errors.append(ParseIssue(i, str(item), "객체가 아닙니다"))
            continue
        low = {str(k).lower(): v for k, v in item.items()}
        raw_start = _first(low, "start", "start_time", "from", "begin", "시작")
        raw_end = _first(low, "end", "end_time", "to", "stop", "finish", "종료", "끝")
        title = _first(low, "title", "label", "name", "text", "description", "제목", "내용")
        if raw_start is None:
            result.errors.append(ParseIssue(i, json.dumps(item, ensure_ascii=False), "시작 시간이 없습니다"))
            continue
        try:
            start = parse_timecode(raw_start)
            end = None if raw_end is None else parse_timecode(raw_end)
        except ValueError as exc:
            result.errors.append(ParseIssue(i, json.dumps(item, ensure_ascii=False), str(exc)))
            continue
        result.segments.append(Segment(start, end, _clean_title(str(title or ""))))
    return result


def _first(mapping: dict, *keys):
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def _parse_table_row(line: str) -> tuple[float, float | None, str] | None:
    """'| 00:01:23 | 00:02:45 | 인트로 |' 같은 표 한 줄."""
    if "|" not in line:
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    time_idx = [i for i, c in enumerate(cells) if _TC_FULL.match(c)]
    if len(time_idx) < 2:
        return None
    si, ei = time_idx[0], time_idx[1]
    title = _clean_title(*[c for i, c in enumerate(cells) if i not in (si, ei)])
    return parse_timecode(cells[si]), parse_timecode(cells[ei]), title


def parse_segments(text: str, duration: float | None = None) -> ParseResult:
    """자유 형식 텍스트 -> 세그먼트 목록.

    duration을 주면 끝 시간이 비어 있는 마지막 항목을 영상 길이로 채우고,
    영상 길이를 넘는 구간을 잘라낸다.
    """
    text = (text or "").strip()
    if not text:
        return ParseResult()

    result = _from_json(text)
    if result is None:
        result = _parse_lines(text)

    _resolve_open_ends(result, duration)
    _validate(result, duration)
    return result


def _parse_lines(text: str) -> ParseResult:
    result = ParseResult()
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or _TABLE_SEP_RE.match(line):
            continue

        table = _parse_table_row(line)
        if table:
            start, end, title = table
            result.segments.append(Segment(start, end, title))
            continue

        body = _LIST_MARKER_RE.sub("", line)
        if "|" in body:  # 표인데 시간 칸이 안 잡힌 경우 — 칸 구분자를 공백으로
            body = " ".join(c.strip() for c in body.strip("|").split("|"))

        match = _RANGE_RE.search(body)
        if match:
            try:
                start = parse_timecode(match.group("start"))
                end = parse_timecode(match.group("end"))
            except ValueError as exc:
                result.errors.append(ParseIssue(line_no, line, str(exc)))
                continue
            title = _clean_title(body[: match.start()], body[match.end() :])
            result.segments.append(Segment(start, end, title))
            continue

        singles = list(_SINGLE_RE.finditer(body))
        if singles and _HAS_CLOCK.search(body):
            hit = singles[0]
            start = parse_timecode(hit.group(0))
            title = _clean_title(body[: hit.start()], body[hit.end() :])
            result.segments.append(Segment(start, None, title))
            continue

        if _HAS_CLOCK.search(line):
            result.errors.append(ParseIssue(line_no, line, "구간을 읽지 못했습니다"))
    return result


def _resolve_open_ends(result: ParseResult, duration: float | None) -> None:
    """끝이 비어 있는 항목은 다음 항목 시작으로 닫는다(챕터 목록 형태)."""
    segs = result.segments
    for i, seg in enumerate(segs):
        if seg.end is not None:
            continue
        nxt = next((s.start for s in segs[i + 1 :] if s.start > seg.start), None)
        if nxt is not None:
            seg.end = nxt
        elif duration is not None and duration > seg.start:
            seg.end = duration

    unresolved = [s for s in segs if s.end is None]
    if unresolved:
        for seg in unresolved:
            result.errors.append(
                ParseIssue(0, f"{format_timecode(seg.start)} {seg.title}".strip(), "끝 시간을 알 수 없습니다")
            )
        result.segments = [s for s in segs if s.end is not None]


def _validate(result: ParseResult, duration: float | None) -> None:
    kept: list[Segment] = []
    for seg in result.segments:
        label = f"{format_timecode(seg.start)}~{format_timecode(seg.end or 0)} {seg.title}".strip()
        if seg.end is None or seg.end <= seg.start:
            result.errors.append(ParseIssue(0, label, "끝이 시작보다 빠르거나 같습니다"))
            continue
        if duration is not None:
            if seg.start >= duration:
                result.errors.append(ParseIssue(0, label, "영상 길이를 벗어난 구간입니다"))
                continue
            if seg.end > duration:
                result.warnings.append(f"{label} — 영상 끝({format_timecode(duration)})으로 줄였습니다")
                seg.end = duration
        kept.append(seg)

    if len(kept) > MAX_SEGMENTS:
        result.warnings.append(f"구간이 {len(kept)}개라 앞에서 {MAX_SEGMENTS}개만 남겼습니다")
        kept = kept[:MAX_SEGMENTS]
    result.segments = kept

    for a, b in zip(kept, kept[1:]):
        if b.start < a.end:
            result.warnings.append("겹치는 구간이 있습니다 — 그대로 이어붙입니다")
            break


def merge_overlaps(segments: list[Segment]) -> list[Segment]:
    """시간순 정렬 후 겹치거나 맞닿은 구간을 합친다."""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: (s.start, s.end or 0))
    merged = [Segment(ordered[0].start, ordered[0].end, ordered[0].title)]
    for seg in ordered[1:]:
        last = merged[-1]
        if seg.start <= (last.end or 0):
            last.end = max(last.end or 0, seg.end or 0)
            if seg.title and seg.title not in last.title:
                last.title = f"{last.title} / {seg.title}".strip(" /")
        else:
            merged.append(Segment(seg.start, seg.end, seg.title))
    return merged
