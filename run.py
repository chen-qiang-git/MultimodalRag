# -*- coding: utf-8 -*-
"""启动 Web 测试台（热重载默认开启）。

用法：
  python run.py              # 127.0.0.1:8007，改代码自动重启
  python run.py 8010         # 指定端口
  python run.py --no-reload  # 关闭热重载
"""

import sys

import uvicorn


def main():
    args = sys.argv[1:]
    reload_enabled = "--no-reload" not in args
    port = 8007
    for a in args:
        if a.isdigit():
            port = int(a)
    uvicorn.run(
        "app.api.main:app",
        host="127.0.0.1",
        port=port,
        reload=reload_enabled,
    )


if __name__ == "__main__":
    main()
