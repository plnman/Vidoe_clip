"""앱 패키지.

어느 진입점으로 들어오든(app.main / app.desktop / app.doctor) 반드시 지나는 자리라,
'무엇보다 먼저 해야 하는 두 가지'를 여기에 둔다.

1. yt-dlp 갱신본이 있으면 그것을 먼저 보게 한다 — yt_dlp를 import 하기 전이어야 한다
2. 묶어 온 bin/을 PATH 앞에 둔다 — ffmpeg을 찾는 코드가 우리 것만 있는 게 아니다
"""

from .config import use_bundled_bin as _use_bundled_bin
from .updater import bootstrap as _bootstrap

_bootstrap()
_use_bundled_bin()
