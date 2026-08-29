"""묶어서 배포할 때의 진입점.

app/desktop.py를 PyInstaller에 직접 넘기면 그 파일이 __main__으로 실행되어
`from .main import app` 같은 상대 import가 깨진다("attempted relative import with
no known parent package"). 그래서 패키지째 import 하는 얇은 껍데기를 하나 둔다.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # 묶인 앱에서 자식 프로세스가 앱을 통째로 다시 띄우는 것을 막는다
    multiprocessing.freeze_support()

    from app.desktop import main

    sys.exit(main())
