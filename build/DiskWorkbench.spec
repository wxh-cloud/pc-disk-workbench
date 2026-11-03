# -*- mode: python ; coding: utf-8 -*-
# WebView2 窗口版打包配置（pywebview 承载 index.html）
#
# 用法（在仓库根目录执行）：
#     pyinstaller build/DiskWorkbench.spec
# 产物：build/dist18/DiskWorkbench/（用 build/assemble.py 组装成可分发目录）
#
# 注意：server.py 故意不打进包（excludes 里有 'server'），而是由 launcher_native.py
# 在运行时从应用目录 importlib 加载。这样以后改后端不必重新打包。
import os

from PyInstaller.utils.hooks import collect_all

_ROOT = os.path.dirname(SPECPATH)          # SPECPATH = 本文件所在目录（build/）
_ICON = os.path.join(SPECPATH, 'app.ico')

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [os.path.join(SPECPATH, 'launcher_native.py')],
    pathex=[_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['server'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DiskWorkbench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_ICON],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DiskWorkbench',
)
