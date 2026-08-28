"""브라우저로 화면 전체 흐름을 점검한다(선택 도구).

    pip install playwright && playwright install chromium
    python tests/fake_server.py 8765 &
    python tests/browser_smoke.py
"""

from __future__ import annotations

import os
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
# 설치된 크로미움 경로를 직접 줄 수 있게 한다(playwright 버전이 어긋날 때).
CHROME = os.environ.get("CLIPPER_CHROME", "")
SEGMENTS = "0:30-0:45 인트로\n2:00 - 2:20 본론\n| 4:00 | 4:10 | 마무리 |"


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME or None)
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        page.goto(BASE)
        expect(page.locator("h1")).to_have_text("유튜브 구간 편집기")

        page.fill("#url", "https://youtu.be/dQw4w9WgXcQ")
        page.click("#loadBtn")
        expect(page.locator("#videoTitle")).to_have_text("합성 테스트 영상")

        page.fill("#segText", SEGMENTS)
        expect(page.locator("#parseSummary")).to_contain_text("3개")
        expect(page.locator("#parseSummary")).to_contain_text("45초")

        page.click("#prepareBtn")
        expect(page.locator("#editCard")).to_be_visible(timeout=60_000)
        expect(page.locator(".cut")).to_have_count(3)
        expect(page.locator("#editSummary")).to_contain_text("45초")
        page.screenshot(path="/tmp/clipper-edit.png", full_page=True)

        # 시작을 1초 당겨도 여유분 안이라 다시 받지 않아야 한다
        first = page.locator(".cut").first
        first.locator('.nudge[data-edge="start"][data-delta="-1"]').click()
        expect(first.locator(".start")).to_have_value("0:29")
        expect(page.locator("#editSummary")).to_contain_text("46초")
        expect(page.locator("#staleNotice")).to_be_hidden()

        # 세 번째 구간 제외
        page.locator(".cut").nth(2).locator(".on").uncheck()
        expect(page.locator("#editSummary")).to_contain_text("36초")

        # 저장 형식 목록이 서버에서 채워졌는지
        expect(page.locator("#format option")).to_have_count(6)
        page.click("#renderBtn")
        expect(page.locator("#resultBox")).to_be_visible(timeout=120_000)
        expect(page.locator("#resultMarkers button")).to_have_count(2)
        # 완성 후에도 화면이 방금 한 편집을 그대로 유지해야 한다
        expect(page.locator("#editSummary")).to_contain_text("36초")
        expect(page.locator(".cut").nth(2).locator(".on")).not_to_be_checked()
        assert "/download" in page.locator("#downloadLink").get_attribute("href")
        expect(page.locator("#resultPreview video")).to_be_visible()
        expect(page.locator("#resultInfo")).to_contain_text("MB")
        page.screenshot(path="/tmp/clipper-result.png", full_page=True)

        # 형식을 GIF로 바꿔 다시 만들면 미리보기가 이미지로 바뀐다
        page.select_option("#format", "gif")
        page.click("#renderBtn")
        expect(page.locator("#resultPreview img")).to_be_visible(timeout=120_000)
        expect(page.locator("#downloadLink")).to_have_attribute("download", re.compile(r"\.gif$"))

        # 결과물이 실제로 받아지는지 (재생 자체는 확인하지 않는다 —
        # 오픈소스 크로미움에는 H.264 디코더가 없어 미리보기가 비어 보인다)
        size = page.evaluate(
            "async () => (await fetch(document.getElementById('downloadLink').href))"
            ".headers.get('content-length')"
        )
        assert size and int(size) > 10_000, f"결과 파일이 이상합니다: {size}"

        browser.close()
        if errors:
            print("콘솔 오류:", *errors, sep="\n  ")
            return 1
    print(f"통과 — 결과 파일 {int(size) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
