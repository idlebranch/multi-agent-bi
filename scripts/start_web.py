"""Start the local BI API and open its browser UI automatically."""

from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from api import api  # noqa: E402


URL = "http://127.0.0.1:8000"


def main() -> None:
    opener = threading.Timer(1.2, webbrowser.open, args=(URL,))
    opener.daemon = True
    opener.start()
    print(f"正在启动 BI Agent：{URL}")
    print("停止服务请在此窗口按 Ctrl+C。")
    uvicorn.run(api, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
