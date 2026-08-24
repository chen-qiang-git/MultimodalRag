# -*- coding: utf-8 -*-
"""启动本地 Android 联调后端（热重载默认开启）。

用法：
  python run.py              # 使用 .env 的 OMNICART_HOST / OMNICART_PORT
  python run.py 8010         # 指定端口
  python run.py --no-reload  # 关闭热重载
"""

import sys

import uvicorn

from app.core.config import HOST, PORT


def main():
    args = sys.argv[1:]
    reload_enabled = "--no-reload" not in args
    port = PORT
    for a in args:
        if a.isdigit():
            port = int(a)
    uvicorn.run(
        "app.api.main:app",
        host=HOST,
        port=port,
        reload=reload_enabled,
    )


if __name__ == "__main__":
    main()
