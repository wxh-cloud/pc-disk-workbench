# -*- coding: utf-8 -*-
"""把打包产物组装成可分发的应用目录 release/app。"""
import os
import shutil

BUILD = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BUILD)          # 仓库根目录（server.py / app.ico 所在处）
SRC = os.path.join(BUILD, "dist18", "DiskWorkbench")
OUT = os.path.join(BUILD, "release9", "app")

EXE_SRC = os.path.join(SRC, "DiskWorkbench.exe")
EXE_DST = os.path.join(OUT, "个人磁盘工作台.exe")


def main():
    if not os.path.isfile(EXE_SRC):
        raise SystemExit("找不到打包产物: " + EXE_SRC)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    # PyInstaller 产物
    for name in os.listdir(SRC):
        s = os.path.join(SRC, name)
        d = os.path.join(OUT, name)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    os.replace(os.path.join(OUT, "DiskWorkbench.exe"), EXE_DST)

    # 明文资源（方便以后直接改页面/后端，不用重新打包）
    for f in ("server.py", "index.html", "trash_log.txt"):
        src = os.path.join(ROOT, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUT, f))

    shutil.copy2(os.path.join(BUILD, "app.ico"), os.path.join(OUT, "app.ico"))
    os.makedirs(os.path.join(OUT, "cache"), exist_ok=True)

    total = 0
    count = 0
    for base, _dirs, files in os.walk(OUT):
        for f in files:
            total += os.path.getsize(os.path.join(base, f))
            count += 1
    print("输出目录:", OUT)
    print("文件数: %d，合计 %.1f MB" % (count, total / 1024 / 1024))
    for f in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, f)
        size = "%.1f MB" % (os.path.getsize(p) / 1024 / 1024) if os.path.isfile(p) else "<dir>"
        print("  %-24s %s" % (f, size))


if __name__ == "__main__":
    main()
