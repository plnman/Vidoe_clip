"""모든 테스트에 공통으로 걸리는 것."""

import pytest

from app import config, media


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path, monkeypatch):
    """사용자 데이터 폴더를 테스트마다 새로 준다.

    인코더 선택 결과는 재는 데 몇 초가 들어 파일에 적어둔다. 그 파일이 테스트끼리
    새면 앞 테스트의 답이 뒤 테스트에 딸려와 엉뚱한 결과가 나온다. 더 나쁜 것은
    테스트를 돌렸을 뿐인데 진짜 내 `%LOCALAPPDATA%` 가 더럽혀지는 것이다.
    """
    home = tmp_path / "userdata"
    monkeypatch.setattr(config, "user_data_dir", lambda: home)
    monkeypatch.setattr(media.config, "user_data_dir", lambda: home)
    # 메모리 캐시도 비워야 앞 테스트가 고른 인코더가 남지 않는다
    monkeypatch.setattr(media, "_hw_cache", {})
    monkeypatch.setattr(media, "_hw_attempts", {})
