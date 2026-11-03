# -*- coding: utf-8 -*-
"""个人磁盘工作台 · 桌面启动器（pywebview 原生窗口 + 窗口看门狗）

打包后 pywebview 的窗口偶尔会以极小尺寸出现（136x38），看起来像"没打开"。
这里在 start 之后起一个看门狗，找到窗口就强制最大化并贴图标。
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

APP_TITLE = "个人磁盘工作台"

# ---------------- 精简 WebView2 内存 ----------------
# Chromium 默认会给 GPU/渲染/网络各起独立进程，一个页面能占 700MB+。
# 这里关掉 GPU、限制渲染进程数、限制 JS 堆、关掉用不到的特性。
# ⚠️ 不要用 --single-process：实测约 30 秒后渲染进程崩溃、整个应用退出。
_LEAN_ARGS = " ".join([
    "--disable-gpu",
    "--renderer-process-limit=1",
    "--process-per-site",
    "--js-flags=--max-old-space-size=128",
    "--disable-extensions",
    "--disable-sync",
    "--no-first-run",
    "--disable-background-networking",
    "--disable-features=CalculateNativeWinOcclusion,msWebOOUI,msPdfOOUI,"
    "msSmartScreenProtection,OptimizationHints,Translate",
])
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", _LEAN_ARGS)

# PyInstaller 的 --windowed 模式没有控制台，sys.stdout/stderr 是 None。
# pythonnet 初始化 .NET 时会往这些流写东西，直接抛错 → GUI 线程静默死掉。
# 先把它们替换成空设备，是 windowed 下 pythonnet 能跑起来的关键。
_devnull_out = None
if sys.stdout is None:
    _devnull_out = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _devnull_out
    sys.__stdout__ = _devnull_out
if sys.stderr is None:
    _devnull_out = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = _devnull_out
    sys.__stderr__ = _devnull_out
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    sys.__stdin__ = sys.stdin


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_window(title, min_width=40):
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _l):
        if not u.IsWindowVisible(ctypes.c_void_p(h)):
            return True
        n = u.GetWindowTextLengthW(h)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 2)
        u.GetWindowTextW(h, buf, n + 2)
        if title in buf.value:
            r = wintypes.RECT()
            u.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(r))
            if (r.right - r.left) >= min_width:
                found.append(h)
        return True

    u.EnumWindows(cb, None)
    return found[0] if found else None


def set_icon(hwnd, ico_path):
    if not hwnd or not os.path.isfile(ico_path):
        return
    u = ctypes.windll.user32
    u.LoadImageW.restype = ctypes.c_void_p
    u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    hicon = u.LoadImageW(None, ico_path, 1, 0, 0, 0x10)
    if hicon:
        u.SendMessageW(ctypes.c_void_p(hwnd), 0x80, 0, hicon)
        u.SendMessageW(ctypes.c_void_p(hwnd), 0x80, 1, hicon)


def watchdog(ico):
    """窗口一出现就纠正尺寸/图标，然后一直守着知道窗口关闭。"""
    u = ctypes.windll.user32
    hwnd = None
    for _ in range(40):
        time.sleep(0.25)
        hwnd = find_window(APP_TITLE)
        if hwnd:
            break
    if not hwnd:
        return
    u.ShowWindow(ctypes.c_void_p(hwnd), 3)        # SW_MAXIMIZE
    set_icon(hwnd, ico)
    # 有些情况最大化会被忽略，再兜一次底：直接按屏幕尺寸摆
    time.sleep(0.6)
    r = wintypes.RECT()
    u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    if (r.right - r.left) < 900:
        sw = u.GetSystemMetrics(0)
        sh = u.GetSystemMetrics(1)
        u.SetWindowPos(ctypes.c_void_p(hwnd), 0, 0, 0, sw, sh, 0x0040)


def main():
    base = app_dir()
    try:
        os.chdir(base)
    except OSError:
        pass
    if base not in sys.path:
        sys.path.insert(0, base)
    os.environ["WB_APP_ROOT"] = base

    import importlib.util
    server_py = os.path.join(base, "server.py")
    if not os.path.isfile(server_py):
        _fatal("缺少 server.py，安装包不完整。")
        return
    spec = importlib.util.spec_from_file_location("wb_server", server_py)
    server = importlib.util.module_from_spec(spec)
    sys.modules["wb_server"] = server
    spec.loader.exec_module(server)

    try:
        srv, port = server.bind_server(8733)
    except Exception:
        _fatal("没有可用端口。")
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % port

    import webview

    ico = os.path.join(base, "app.ico")
    webview.create_window(
        APP_TITLE, url,
        width=1600, height=960,
        min_size=(1180, 720),
        background_color="#f2f5fa",
        text_select=False,
        confirm_close=False,
        maximized=True,
    )
    threading.Thread(target=watchdog, args=(ico,), daemon=True).start()

    try:
        webview.start(debug=False, private_mode=False)
    except Exception:
        import traceback
        _fatal("窗口启动失败：\n\n" + traceback.format_exc())

    try:
        srv.shutdown()
    except Exception:
        pass
    os._exit(0)


def _fatal(msg):
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE + " · 启动失败", 0x10)
    except Exception:
        pass


# ---------------- 卸载（不由安装包常驻，省 23MB） ----------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _known_folder(fid):
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    g = _GUID()
    ole32.IIDFromString(ctypes.create_unicode_buffer(fid), ctypes.byref(g))
    buf = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(buf)) != 0:
        return None
    return buf.value


def do_uninstall():
    import shutil
    import subprocess
    u = ctypes.windll.user32
    base = app_dir()
    if u.MessageBoxW(None, "确定要卸载「%s」吗？\n\n安装目录：%s\n"
                           "（磁盘扫描缓存与回收站记录会一并删除）" % (APP_TITLE, base),
                     APP_TITLE + " 卸载", 0x4 | 0x20) != 6:
        return 0
    for fid in ("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",      # 桌面
                "{625B53C3-AB48-4EC1-BA1F-A1EF4146FC19}"):     # 开始菜单
        kb = _known_folder(fid)
        if not kb:
            continue
        for c in (os.path.join(kb, APP_TITLE + ".lnk"),
                  os.path.join(kb, "Programs", APP_TITLE)):
            try:
                if os.path.isdir(c):
                    shutil.rmtree(c, ignore_errors=True)
                elif os.path.isfile(c):
                    os.remove(c)
            except OSError:
                pass
    # 自己删不掉自己，交给外部命令延迟处理
    try:
        subprocess.Popen('cmd /c timeout /t 2 >nul & rmdir /s /q "%s"' % base,
                         shell=True, creationflags=0x08000000)
    except Exception:
        pass
    u.MessageBoxW(None, "已卸载。", APP_TITLE, 0x40)
    return 0


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        sys.exit(do_uninstall())
    main()
