#!/usr/bin/env python3
"""兼容入口：运行本文件会调用纯 Python 主渲染器生成 20 秒 MP4。"""

from __future__ import annotations

import sys

from python_ascii_tree import main


if __name__ == "__main__":
    if "--render" not in sys.argv and "--preview" not in sys.argv:
        sys.argv.append("--render")
    raise SystemExit(main())
