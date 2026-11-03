# -*- mode: python ; coding: utf-8 -*-
# 原生界面版打包配置（Win32 + GDI，无 WebView2 / 无 pythonnet）
#
# 用法（在仓库根目录执行）：
#     pyinstaller build/Native.spec
# 产物：build/dist_native/NativeDiskWorkbench/
import os

_ROOT = os.path.dirname(SPECPATH)          # SPECPATH = 本文件所在目录（build/）
_ICON = os.path.join(SPECPATH, 'app.ico')

a = Analysis(
    [os.path.join(_ROOT, 'app_win.py')],
    pathex=[_ROOT],
    binaries=[],
    datas=[(_ICON, '.')],
    hiddenimports=['server'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc', 'doctest', 'xmlrpc', 'pdb',
              'lib2to3', 'test', 'distutils', 'setuptools', 'pip',
              'PIL', 'numpy', 'webview', 'clr'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NativeDiskWorkbench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='NativeDiskWorkbench',
)
