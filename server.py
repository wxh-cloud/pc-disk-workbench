#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人磁盘工作台 —— 本地后端服务
=================================
只监听 127.0.0.1，本机专用。

提供的能力：
  * 列出所有逻辑驱动器及容量 / 已用 / 可用
  * 后台扫描任意盘符或文件夹，实时进度
  * 结果缓存，二次打开秒出
  * 「在资源管理器中定位」 —— 推荐的安全清理方式
  * 「移到回收站」        —— 可恢复，且拒绝删除受保护路径

安全边界（重要）：
  * 本服务不提供任何形式的「永久删除」，只能移入回收站
  * 盘符根目录、Windows / Program Files / 用户配置等受保护路径一律拒绝删除
  * 每次删除都会写入 trash_log.txt 审计日志
  * 仅绑定 127.0.0.1，外部网络无法访问
"""

import ctypes
import base64
import gzip
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from collections import deque
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def _resolve_root():
    """资源/数据根目录。

    打包成 exe 时 __file__ 指向临时解压目录，缓存会随退出被删，
    所以优先用 WB_APP_ROOT（启动器会设置），其次 exe 所在目录。
    """
    env = os.environ.get("WB_APP_ROOT")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


ROOT = _resolve_root()
CACHE_DIR = os.path.join(ROOT, "cache")
LOG_FILE = os.path.join(ROOT, "trash_log.txt")
DEFAULT_PORT = 8733


# ---------------- 复制文件到 Windows 剪贴板（绕开浏览器拖拽限制） ----------------
def copy_files_to_clipboard(paths):
    """把真实文件放进剪贴板（CF_HDROP 格式）。
    浏览器里拖到微信/QQ 常常只丢一个路径文本、或要求聊天软件必须已打开并与浏览器分屏；
    走剪贴板则可以在聊天框里直接 Ctrl+V 发送文件本体，任何时候都行。
    剪贴板是系统全局单例资源，微信/输入法/截图工具都可能短暂占用，
    所以 OpenClipboard / GlobalLock 全部带重试循环（约 2 秒窗口），极大降低偶发失败。"""
    import ctypes
    import time
    from ctypes import wintypes

    files = []
    for p in paths or []:
        ap = os.path.abspath(p)
        # 文件和文件夹都允许：CF_HDROP 放文件夹路径，资源管理器 / 微信 / QQ 粘贴时
        # 会按「复制整个文件夹」处理，同样能直接发送
        if os.path.isfile(ap) or os.path.isdir(ap):
            files.append(ap)
    if not files:
        return {"ok": False, "error": "没有可复制的文件（路径不存在或已失效）"}

    CF_HDROP = 15
    GMEM_MOVEABLE = 0x0002

    class DROPFILES(ctypes.Structure):
        # 注意：pt 必须是 POINT（两个 LONG，共 8 字节）。之前用 c_void_p 在 64 位下
        # 是 8 字节 + 4 字节对齐填充，导致 fNC/fWide 整体错位 4 字节 —— 接收方把
        # fWide 读成 FALSE，按 ANSI 解析 UTF-16 路径列表，路径全是乱码，粘贴必失败。
        _fields_ = [("pFiles", wintypes.DWORD), ("pt", wintypes.POINT),
                    ("fNC", wintypes.BOOL), ("fWide", wintypes.BOOL)]

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    # 关键修复：64 位 Python 下 ctypes 默认把返回值当 32 位 int，GlobalAlloc 的
    # 句柄 / GlobalLock 的指针都会被截断 → GlobalLock 必然失败（报「剪贴板加锁失败」，
    # 重试也没用）。必须显式声明 64 位参数 / 返回类型。
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    # DROPFILES 头 + 以 \0\0 结束的 UTF-16 双零结尾路径列表
    payload = ("".join(f + "\0" for f in files) + "\0").encode("utf-16-le")
    head = DROPFILES()
    head.pFiles = ctypes.sizeof(DROPFILES)
    head.pt = wintypes.POINT(0, 0)
    head.fNC = False
    head.fWide = True
    raw = ctypes.string_at(ctypes.byref(head), ctypes.sizeof(head)) + payload

    last_err = "写入剪贴板失败"
    # 整体重试：剪贴板被别的程序占着时，等一等往往就能成功
    for attempt in range(12):
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
        if not hmem:
            last_err = "内存分配失败"
            time.sleep(0.15)
            continue
        ptr = kernel32.GlobalLock(hmem)
        if not ptr:
            kernel32.GlobalFree(hmem)
            last_err = "剪贴板加锁失败"
            time.sleep(0.15)
            continue
        ctypes.memmove(ptr, raw, len(raw))
        kernel32.GlobalUnlock(hmem)

        opened = user32.OpenClipboard(None)
        if not opened:
            kernel32.GlobalFree(hmem)
            last_err = "剪贴板被其它程序占用"
            time.sleep(0.15)
            continue
        user32.EmptyClipboard()
        ok = bool(user32.SetClipboardData(CF_HDROP, hmem))
        # 资源管理器复制文件时还会顺手写一个「Preferred DropEffect = 复制(1)」。
        # 有些接收端（聊天软件 / 网盘客户端）靠它判断这次是复制还是剪切，
        # 只给 CF_HDROP 时会被当成「来源不明」而直接忽略 —— 文件夹尤其明显。
        if ok:
            try:
                cf_eff = user32.RegisterClipboardFormatW("Preferred DropEffect")
                if cf_eff:
                    heff = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
                    if heff:
                        peff = kernel32.GlobalLock(heff)
                        if peff:
                            ctypes.memmove(peff, ctypes.byref(wintypes.DWORD(1)), 4)
                            kernel32.GlobalUnlock(heff)
                            if not user32.SetClipboardData(cf_eff, heff):
                                kernel32.GlobalFree(heff)
            except Exception:
                pass
        user32.CloseClipboard()
        if ok:
            # 写入后回读校验：确认剪贴板里确实是本次的文件数（防止看似成功、实际写坏）
            user32.GetClipboardData.restype = wintypes.HANDLE
            user32.GetClipboardData.argtypes = [wintypes.UINT]
            shell32 = ctypes.windll.shell32
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                               wintypes.LPWSTR, wintypes.UINT]
            verified = 0
            if user32.OpenClipboard(None):
                hchk = user32.GetClipboardData(CF_HDROP)
                if hchk:
                    verified = shell32.DragQueryFileW(hchk, 0xFFFFFFFF, None, 0)
                user32.CloseClipboard()
            if verified == len(files):
                return {"ok": True, "count": len(files), "verified": verified,
                        "tries": attempt + 1,
                        "hint": "已复制 " + str(len(files)) + " 项到剪贴板（已覆盖旧内容），聊天框 Ctrl+V 发送"}
            last_err = "写入校验失败（剪贴板回读到 " + str(verified) + " 项）"
        last_err = "写入剪贴板失败"
        time.sleep(0.15)
    return {"ok": False, "error": last_err + "（已自动重试约 2 秒，请稍后再试一次）"}


# 打包发送用的临时目录（放 %TEMP% 下，系统会自己回收）
PACK_DIR = os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or ".",
                        "无敌暴龙图发送")

# 这些类型本身已经是压缩格式，再 deflate 只会白烧 CPU 还慢，直接原样塞进去
_STORED_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".mp4", ".mkv",
    ".avi", ".mov", ".wmv", ".flv", ".webm", ".mp3", ".aac", ".flac", ".m4a",
    ".ogg", ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".cab", ".iso",
    ".docx", ".xlsx", ".pptx", ".apk", ".jar", ".exe", ".msi",
}


def _pack_walk(paths, should_stop=None):
    """统计待打包的文件 / 目录 / 总字节。返回 (entries, dirs, files, total)。"""
    entries = []          # [(绝对路径, 压缩包内名字, 是否目录)]
    dirs = files = total = 0
    used = set()

    def uniq(name):
        if name not in used:
            used.add(name)
            return name
        base, ext = os.path.splitext(name)
        i = 2
        while "%s(%d)%s" % (base, i, ext) in used:
            i += 1
        name = "%s(%d)%s" % (base, i, ext)
        used.add(name)
        return name

    for p in paths:
        ap = os.path.abspath(p)
        if os.path.isfile(ap):
            try:
                total += os.path.getsize(ap)
            except OSError:
                pass
            entries.append((ap, uniq(os.path.basename(ap)), False))
            files += 1
        elif os.path.isdir(ap):
            root_name = uniq(os.path.basename(ap.rstrip("\\/")) or "文件夹")
            for cur, ds, fs in os.walk(ap):
                if should_stop and should_stop():
                    return entries, dirs, files, total
                rel = os.path.relpath(cur, ap)
                arc_dir = root_name if rel == "." else os.path.join(root_name, rel)
                dirs += 1
                entries.append((cur, arc_dir, True))
                for fn in fs:
                    fp = os.path.join(cur, fn)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        continue
                    entries.append((fp, os.path.join(arc_dir, fn), False))
                    files += 1
    return entries, dirs, files, total


def send_pack_zip(paths, on_progress=None, should_stop=None):
    """把选中的文件 / 文件夹打包成一个 zip，供「复制到剪贴板发给聊天软件」用。

    聊天软件普遍不接受剪贴板里的「文件夹」（微信直接忽略），
    换成 zip 之后任何入口都能粘贴发送 —— 这是兼容性最好的一条路。

    on_progress(done_files, done_bytes, total_files, total_bytes, cur_name)
    should_stop() 返回 True 时中止并删除半成品。
    """
    t0 = time.time()
    srcs = [os.path.abspath(p) for p in (paths or [])]
    srcs = [p for p in srcs if os.path.isfile(p) or os.path.isdir(p)]
    if not srcs:
        return {"ok": False, "error": "选中的路径都不存在了，请刷新后重试"}

    entries, ndirs, nfiles, total = _pack_walk(srcs, should_stop)
    if should_stop and should_stop():
        return {"ok": False, "cancelled": True}
    if not entries:
        return {"ok": False, "error": "没有可打包的内容"}

    try:
        os.makedirs(PACK_DIR, exist_ok=True)
        # 顺手清掉 2 天前的旧包，别让临时目录一路涨
        now = time.time()
        for fn in os.listdir(PACK_DIR):
            if not fn.lower().endswith(".zip"):
                continue
            fp = os.path.join(PACK_DIR, fn)
            try:
                if now - os.path.getmtime(fp) > 2 * 86400:
                    os.remove(fp)
            except OSError:
                pass
    except OSError as e:
        return {"ok": False, "error": "临时目录不可写：%s" % e}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(srcs) == 1:
        base = os.path.basename(srcs[0].rstrip("\\/")) or "发送内容"
        fname = "%s_%s.zip" % (base, stamp)
    else:
        fname = "发送内容%s项_%s.zip" % (len(srcs), stamp)
    out = os.path.join(PACK_DIR, fname)
    # 同一个名字可能撞车（同一秒点两次），换个后缀
    if os.path.exists(out):
        out = os.path.join(PACK_DIR, "%s_%d.zip" % (fname[:-4], int(time.time() % 1000)))

    done_files = done_bytes = 0
    skipped = 0
    last_tick = 0.0
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for src, arc, isdir in entries:
                if should_stop and should_stop():
                    raise InterruptedError
                if isdir:
                    try:
                        zf.writestr(arc.replace("\\", "/") + "/", "")
                    except Exception:
                        pass
                    continue
                try:
                    ext = os.path.splitext(src)[1].lower()
                    comp = (zipfile.ZIP_STORED if ext in _STORED_EXT
                            else zipfile.ZIP_DEFLATED)
                    zf.write(src, arc.replace("\\", "/"), compress_type=comp)
                    try:
                        done_bytes += os.path.getsize(src)
                    except OSError:
                        pass
                except (OSError, PermissionError, zipfile.BadZipFile):
                    # 被系统占用 / 没权限的文件跳过，不要让整个包失败
                    skipped += 1
                done_files += 1
                if on_progress and time.time() - last_tick > 0.12:
                    last_tick = time.time()
                    on_progress(done_files, done_bytes, nfiles, total, arc)
    except InterruptedError:
        try:
            os.remove(out)
        except OSError:
            pass
        return {"ok": False, "cancelled": True}
    except Exception as e:
        try:
            os.remove(out)
        except OSError:
            pass
        return {"ok": False, "error": "打包失败：%s" % e}

    try:
        size = os.path.getsize(out)
    except OSError:
        size = 0
    if on_progress:
        on_progress(nfiles, total, nfiles, total, "")
    return {"ok": True, "zip": out, "size": size, "files": nfiles - skipped,
            "skipped": skipped, "dirs": ndirs, "src_count": len(srcs),
            "elapsed": time.time() - t0}


def _pick_share_dir():
    """分享产物目录：优先放 D 盘（用户要求别把生成物堆在 C 盘）。
    顺序：环境变量 WB_SHARE_DIR > D:\\磁盘工作台\\share > 脚本同级的 share。"""
    env = (os.environ.get("WB_SHARE_DIR") or "").strip()
    if env:
        return os.path.abspath(env)
    d = "D:\\磁盘工作台\\share"
    if os.path.isdir("D:\\") and not d.startswith(ROOT[:2]):
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            pass
    return os.path.join(ROOT, "share")


SHARE_DIR = _pick_share_dir()

LEGACY_REPORT = os.path.normpath(
    os.path.join(ROOT, "..", "folder-size-tool", "reports", "E_drive_report.html")
)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(SHARE_DIR, exist_ok=True)


def migrate_cache_to_gzip():
    """把旧的明文 *.json 缓存原地转成 *.json.gz（体积约 1/7）。
    history.json 是历史记录、单独维护，不参与转换。"""
    try:
        names = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
    except OSError:
        return
    for f in names:
        if f == "history.json":
            continue
        key = f[:-5]
        try:
            if os.path.exists(os.path.join(CACHE_DIR, key + ".json.gz")):
                os.remove(os.path.join(CACHE_DIR, f))
                continue
            with open(os.path.join(CACHE_DIR, f), encoding="utf-8") as fh:
                data = json.load(fh)
            save_cache(key, data)      # 内部会写 .json.gz 并删掉 .json
        except Exception:
            continue


# ---------------- 分享：保存文件 / 打包项目 / 拉起聊天软件 ----------------
def _safe_share_name(name):
    """只允许字母数字汉字点横线下划线，杜绝路径穿越"""
    name = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip()) or "share.html"
    if len(name) > 80:
        stem, ext = os.path.splitext(name)
        name = stem[:70] + ext
    return name


def do_share_list():
    """列出 share 目录里当前真实存在的文件（按修改时间倒序）。
    分享托盘以磁盘为准：历史上生成过、后来又被删掉的文件不再显示。"""
    items = []
    try:
        os.makedirs(SHARE_DIR, exist_ok=True)
        for nm in os.listdir(SHARE_DIR):
            p = os.path.join(SHARE_DIR, nm)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            items.append({"name": nm, "path": p, "size": st.st_size,
                          "mtime": st.st_mtime})
    except OSError:
        pass
    items.sort(key=lambda x: -x["mtime"])
    return items


def do_share_save(name, content, b64=False):
    os.makedirs(SHARE_DIR, exist_ok=True)
    name = _safe_share_name(name)
    p = os.path.join(SHARE_DIR, name)
    mode = "wb" if b64 else "w"
    data = base64.b64decode(content) if b64 else (content or "")
    kw = {} if b64 else {"encoding": "utf-8"}
    with open(p, mode, **kw) as f:
        f.write(data)
    return p


def do_share_package():
    """把整个项目打成 zip，放到桌面，拿到就能用"""
    os.makedirs(SHARE_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = os.path.join(SHARE_DIR, "磁盘工作台-分享包-%s.zip" % stamp)
    include = ["server.py", "index.html", "Start-Workbench.bat", "Scan-E-Drive.bat"]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in include:
            fp = os.path.join(ROOT, fn)
            if os.path.exists(fp):
                z.write(fp, "磁盘工作台/" + fn)
        # 附一份使用说明
        readme = (
            "【个人磁盘工作台 · 使用说明】\n\n"
            "1. 确保电脑已安装 Python 3（https://www.python.org 下载）\n"
            "2. 双击「Start-Workbench.bat」即可启动，浏览器会自动打开工作台\n"
            "3. 首次使用点「重新扫描」扫描你的磁盘；删除只进回收站，绝不直接永久删除\n"
        )
        z.writestr("磁盘工作台/使用说明.txt", readme)
    return zip_path


def open_chat_app(app):
    """尝试拉起桌面版微信 / 企业微信 / 钉钉"""
    import subprocess
    targets = {
        "weixin":  [("weixin://", None)],
        "wxwork":  [("wxwork://", None)],
        "dingtalk": [("dingtalk://", None)],
    }
    cands = targets.get(app)
    if not cands:
        return False, "未知应用：" + app
    for proto, _ in cands:
        try:
            os.startfile(proto)  # shell 打开协议，已安装则会拉起对应程序
            return True, "已尝试拉起桌面端，若未响应请手动打开后拖入文件"
        except OSError:
            continue
    return False, "未检测到该应用，请手动打开后把分享文件夹里的文件拖给好友"


# ============================================================ 扫描

class ScanJob(threading.Thread):
    """后台扫描任务。"""

    def __init__(self, path, min_node, min_file, top_files):
        super().__init__(daemon=True)
        self.path = path
        self.roots = None            # 「全部盘」模式下由外部设置为 [盘1, 盘2, ...]
        self.min_node = int(min_node)
        self.min_file = int(min_file)
        self.top_files = int(top_files)
        self.dirs = 0
        self.files = 0
        self.errors = 0
        self.t0 = time.time()
        self.state = "running"
        self.error = None
        self.result = None
        self._ext = {}
        self._ages = [0] * 5          # <30天 / <1年 / <2年 / <3年 / >=3年
        self._big = []
        self._fidx = []               # 文件搜索索引：≥1MB 的文件（按体积截断）
        self.min_idx = 1024 * 1024
        self._now = time.time()

    @staticmethod
    def is_reparse(entry):
        try:
            st = entry.stat(follow_symlinks=False)
            if getattr(st, "st_file_attributes", 0) & 0x400:
                return True
        except OSError:
            pass
        return entry.is_symlink()

    def note_file(self, size, mtime, name):
        dot = name.rfind(".")
        ext = name[dot + 1:].lower() if 0 < dot < len(name) - 1 else "(无扩展名)"
        agg = self._ext.get(ext)
        if agg is None:
            self._ext[ext] = [size, 1]
        else:
            agg[0] += size
            agg[1] += 1

        age = self._now - mtime
        if age < 30 * 86400:
            self._ages[0] += size
        elif age < 365 * 86400:
            self._ages[1] += size
        elif age < 2 * 365 * 86400:
            self._ages[2] += size
        elif age < 3 * 365 * 86400:
            self._ages[3] += size
        else:
            self._ages[4] += size

    def scan(self, path):
        self.dirs += 1
        node = {"n": os.path.basename(path) or path, "p": path,
                "s": 0, "f": 0, "d": 0, "m": 0, "c": []}
        try:
            entries = list(os.scandir(path))
        except (PermissionError, OSError):
            self.errors += 1
            node["err"] = 1
            return node

        for entry in entries:
            if self.state == "aborted":
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    if self.is_reparse(entry):
                        continue
                    child = self.scan(entry.path)
                    node["s"] += child["s"]
                    node["f"] += child["f"]
                    node["d"] += child["d"] + 1
                    if child["m"] > node["m"]:
                        node["m"] = child["m"]
                    # 边扫边裁剪：小于阈值的子树当场折算成聚合值丢掉，
                    # 否则整盘十几万个小目录的字典会一直挂在内存里（峰值可省 60%+）
                    if not self.min_node or child["s"] >= self.min_node:
                        node["c"].append(child)
                    else:
                        sm = node.get("_sm")
                        if sm is None:
                            sm = node["_sm"] = [0, 0, 0, 0, 0]
                        sm[0] += child["s"]
                        sm[1] += child["f"]
                        sm[2] += child["d"] + 1
                        if child["m"] > sm[3]:
                            sm[3] = child["m"]
                        sm[4] += 1
                else:
                    st = entry.stat(follow_symlinks=False)
                    size = st.st_size
                    node["s"] += size
                    node["f"] += 1
                    self.files += 1
                    if st.st_mtime > node["m"]:
                        node["m"] = st.st_mtime
                    if size >= self.min_file:
                        self._big.append({"s": size, "p": entry.path, "m": st.st_mtime})
                    if size >= self.min_idx:
                        self._fidx.append({"s": size, "p": entry.path, "m": st.st_mtime})
                    self.note_file(size, st.st_mtime, entry.name)
            except (PermissionError, OSError):
                self.errors += 1
        return node

    def run(self):
        try:
            roots = getattr(self, "roots", None) or [self.path]
            if len(roots) > 1:
                # 「全部盘」：每个盘各扫一棵子树，挂到一个虚拟根下合并
                kids = []
                total = 0
                for rt in roots:
                    sub = self.scan(rt)
                    if self.state == "aborted":
                        return
                    sub = prune(sub, self.min_node)
                    sub["n"] = rt.rstrip("\\")          # 盘符标签 C: / D: / E:
                    kids.append(sub)
                    total += sub["s"]
                kids.sort(key=lambda x: -x["s"])
                tree = {"n": "全部盘", "p": "", "d": 1, "s": total,
                        "m": int(time.time()), "c": kids}
            else:
                tree = self.scan(self.path)
            if self.state == "aborted":
                return
            tree = prune(tree, self.min_node)
            self._big.sort(key=lambda x: -x["s"])
            big = self._big[: self.top_files]
            self._fidx.sort(key=lambda x: -x["s"])
            fidx = self._fidx[:20000]           # 控制缓存体积，Top 2 万个 ≥1MB 文件
            ext = sorted(([k, v[0], v[1]] for k, v in self._ext.items()),
                         key=lambda x: -x[1])[:80]
            self.result = {
                "root": tree,
                "ext": ext,
                "ages": self._ages,
                "big_files": big,
                "file_index": fidx,
                "min_file": self.min_file,
                "path": self.path,
                "dirs": self.dirs,
                "files": self.files,
                "errors": self.errors,
                "elapsed": round(time.time() - self.t0, 1),
                "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total": tree["s"],
            }
            self.state = "done"
            save_cache(cache_key(getattr(self, "cache_key", None) or self.path), self.result)
            append_history(self.path, self.result["total"])
        except Exception as e:                                   # noqa
            self.error = str(e)
            self.state = "error"


def _agg_node(node, sm):
    return {"n": "（其他 %d 个小目录）" % sm[4], "p": node["p"], "s": sm[0],
            "f": sm[1], "d": sm[2], "m": sm[3], "agg": 1}


def prune(node, min_node):
    """把小于阈值的子目录合并成一个「其他」节点，控制数据量。"""
    sm = node.pop("_sm", None)
    if not node.get("c"):
        node.pop("c", None)
        if sm and sm[4]:
            node["c"] = [_agg_node(node, sm)]
        return node
    keep, small = [], []
    for ch in node["c"]:
        if ch["s"] >= min_node:
            keep.append(prune(ch, min_node))
        else:
            small.append(ch)
    keep.sort(key=lambda x: -x["s"])
    if small:
        ssize = sum(x["s"] for x in small)
        if ssize > 0:
            keep.append({
                "n": "（其他 %d 个小目录）" % len(small),
                "p": node["p"], "s": ssize,
                "f": sum(x["f"] for x in small),
                "d": sum(x["d"] + 1 for x in small),
                "m": max((x["m"] for x in small), default=0),
                "agg": 1,
            })
    if sm and sm[4]:
        keep.append(_agg_node(node, sm))
    node["c"] = keep
    return node


def cache_key(path):
    ap = os.path.abspath(path)
    drive = (ap.split(":")[0] or "disk").strip()[:2].upper()
    leaf = os.path.basename(ap.rstrip("\\/"))
    leaf = "".join(c for c in leaf if c not in '\\/:*?"<>|').strip()
    return ("%s_%s" % (drive, leaf)) if (leaf and not leaf.endswith(":")) else drive


def cache_path(key):
    """缓存文件路径：优先 gzip（体积约 1/7），兼容旧的明文 json。"""
    gz = os.path.join(CACHE_DIR, key + ".json.gz")
    if os.path.exists(gz):
        return gz, True
    return os.path.join(CACHE_DIR, key + ".json"), False


def cache_exists(key):
    return (os.path.exists(os.path.join(CACHE_DIR, key + ".json.gz"))
            or os.path.exists(os.path.join(CACHE_DIR, key + ".json")))


def save_cache(key, data):
    gz = os.path.join(CACHE_DIR, key + ".json.gz")
    try:
        with gzip.open(gz, "wt", encoding="utf-8", compresslevel=6) as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        old = os.path.join(CACHE_DIR, key + ".json")
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


def load_cache(key):
    p, is_gz = cache_path(key)
    if not os.path.exists(p):
        return None
    try:
        if is_gz:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================ 占用历史（趋势图数据源）

HISTORY_FILE = os.path.join(CACHE_DIR, "history.json")
HISTORY_MAX = 80


def hist_key(path):
    return os.path.abspath(path).rstrip("\\/").lower()


def _load_all_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def append_history(path, total):
    """每次扫描完成记录一个体积采样点，用于前端画占用变化曲线。"""
    try:
        allh = _load_all_history()
        k = hist_key(path)
        pts = list(allh.get(k) or [])
        pts.append([int(time.time()), int(total)])
        allh[k] = pts[-HISTORY_MAX:]
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(allh, f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
    except Exception:                                            # noqa
        pass


def load_history(path, limit=40):
    allh = _load_all_history()
    return (allh.get(hist_key(path)) or [])[-limit:]


jobs = {}
jobs_lock = threading.Lock()


# ============================================================ 驱动器信息

DRIVE_TYPES = {0: "未知", 1: "无效路径", 2: "可移动磁盘", 3: "本地磁盘",
               4: "网络驱动器", 5: "光盘", 6: "RAM 磁盘"}


def list_drives():
    out = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return out
    for i in range(26):
        if not (mask & (1 << i)):
            continue
        letter = chr(ord("A") + i)
        path = letter + ":\\"
        try:
            dtype = ctypes.windll.kernel32.GetDriveTypeW(path)
        except Exception:
            dtype = 0
        try:
            u = shutil.disk_usage(path)
            total, free = u.total, u.free
        except Exception:
            continue
        if total <= 0:
            continue
        used = total - free
        out.append({
            "letter": letter + ":",
            "path": path,
            "label": DRIVE_TYPES.get(dtype, "未知"),
            "total": total,
            "used": used,
            "free": free,
            "usedPct": round(used / total * 100, 1),
            "cached": cache_exists(cache_key(letter + ":\\")),
        })
    return out


def all_drives_report():
    """整台电脑所有盘的占用汇总 + 可执行的清理建议（不删除任何东西，只给建议）。"""
    drives = list_drives()
    rows, tips = [], []
    for d in drives:
        rows.append(d)
        pct = d.get("usedPct") or 0
        free_gb = d.get("free", 0) / (1024 ** 3)
        if pct >= 90:
            tips.append({"level": "high", "drive": d["letter"],
                         "title": "%s 盘已用 %.0f%%，仅剩 %.1f GB" % (d["letter"], pct, free_gb),
                         "advice": "建议：先清理下方「可清理项」列出的目录，再考虑移走视频 / 安装包等大文件。"})
        elif pct >= 75:
            tips.append({"level": "mid", "drive": d["letter"],
                         "title": "%s 盘已用 %.0f%%，剩余 %.1f GB" % (d["letter"], pct, free_gb),
                         "advice": "建议：定期清理临时文件与缓存，避免半年内写满。"})
    # 常见可清理目录（只统计体积，绝不删除）
    scan = []
    for k in ("TEMP", "TMP"):
        v = os.environ.get(k)
        if v:
            scan.append(("系统临时文件", v))
            break
    scan += [("Windows 更新缓存", "C:\\Windows\\SoftwareDistribution\\Download"),
             ("Windows 临时目录", "C:\\Windows\\Temp")]
    for d in drives:
        scan.append((d["letter"] + " 盘回收站", d["path"] + "$Recycle.Bin"))
    cands = []
    for label, p in scan:
        try:
            if not os.path.isdir(p):
                continue
        except OSError:
            continue
        size, files = 0, 0
        for dirpath, dirnames, filenames in os.walk(p, onerror=lambda e: None):
            for fn in filenames:
                try:
                    size += os.path.getsize(os.path.join(dirpath, fn))
                    files += 1
                except OSError:
                    pass
                if files > 40000:          # 防止超大目录卡住
                    break
            if files > 40000:
                break
        if size > 64 * 1024 * 1024:
            cands.append({"label": label, "path": p, "size": size, "files": files})
    cands.sort(key=lambda c: -c["size"])
    return {"drives": rows, "cleanable": cands,
            "cleanableTotal": sum(c["size"] for c in cands),
            "tips": tips,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ============================================================ 回收站 / 资源管理器

class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_NOERRORUI = 0x0400
FOF_SILENT = 0x0004

USER_HOME = os.path.expanduser("~")


# ============================== 窗口置顶 / 桌面应用
# Windows 默认会把 Explorer 等窗口丢到后台，这里用 Win32 API 把它真正拉到用户眼前。
WORKBUDDY_EXE = os.path.join(os.path.dirname(USER_HOME),
                             "AppData", "Local", "Programs", "WorkBuddy", "WorkBuddy.exe")
SW_SHOWNORMAL, SW_RESTORE = 1, 9

_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def enum_windows():
    """枚举所有可见顶层窗口，返回 [{hwnd,title,cls,pid}]。"""
    out = []

    def cb(hwnd, _lp):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        pid = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append({"hwnd": hwnd, "title": buf.value, "cls": cls.value, "pid": pid.value})
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    except Exception:                                            # noqa
        pass
    return out


def activate_window(hwnd):
    """
    把指定窗口强行拉到前台。
    SetForegroundWindow 单独调用常被系统拒绝（前台进程才有权切换焦点），
    所以这里组合了 SwitchToThisWindow + AttachThreadInput 两种绕过方式。
    """
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.ShowWindow(hwnd, SW_SHOWNORMAL)
        try:
            _user32.SwitchToThisWindow(hwnd, True)
        except Exception:                                        # noqa
            pass
        if _user32.GetForegroundWindow() != hwnd:
            cur = _user32.GetForegroundWindow()
            ctid = _user32.GetWindowThreadProcessId(cur, 0)
            ttid = _user32.GetWindowThreadProcessId(hwnd, 0)
            _user32.AttachThreadInput(ttid, ctid, True)
            _user32.SetForegroundWindow(hwnd)
            _user32.BringWindowToTop(hwnd)
            _user32.AttachThreadInput(ttid, ctid, False)
        try:
            _user32.SetFocus(hwnd)
        except Exception:                                        # noqa
            pass
        return _user32.GetForegroundWindow() == hwnd
    except Exception:                                            # noqa
        return False


def focus_window(cls=None, title_has=None, pid=None, wait_sec=6.0):
    """轮询等待目标窗口出现并置顶。title_has 为标题包含的关键字。"""
    deadline = time.time() + wait_sec
    hint = (title_has or "").lower()
    best = None
    while time.time() < deadline:
        for w in enum_windows():
            if cls and w["cls"] != cls:
                continue
            if pid and w["pid"] != pid:
                continue
            t = (w["title"] or "").lower()
            if not t:
                continue
            if hint and hint in t:
                return activate_window(w["hwnd"])
            if not hint:
                best = w
                break
        if best:
            return activate_window(best["hwnd"])
        time.sleep(0.25)
    return False


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260),
    ]


_kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


def pids_by_name(exe_name):
    """不依赖任何第三方库，用 Toolhelp32 快照找出同名进程的 PID 列表。"""
    pids = []
    try:
        snap = _kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not snap or snap == -1:
            return pids
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                pids.append(entry.th32ProcessID)
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        _kernel32.CloseHandle(snap)
    except Exception:                                            # noqa
        return []
    return pids


def _focus_app_later(exe, sec=40.0):
    """应用冷启动需要时间，后台轮询它出现后把窗口提到前台。"""
    def worker():
        deadline = time.time() + sec
        while time.time() < deadline:
            for pid in pids_by_name(os.path.basename(exe)):
                if focus_window(pid=pid, wait_sec=0.2):
                    return
            time.sleep(0.6)
    threading.Thread(target=worker, daemon=True).start()


def open_workbuddy():
    """打开本机 WorkBuddy 桌面端：已运行则直接把它的窗口提到前台，否则启动。"""
    pids = pids_by_name("WorkBuddy.exe")
    wins = enum_windows()
    for pid in pids:
        cand = [w for w in wins if w["pid"] == pid and w["title"]]
        cand.sort(key=lambda w: -len(w["title"]))
        if cand and activate_window(cand[0]["hwnd"]):
            return True, "focus"
    if not os.path.exists(WORKBUDDY_EXE):
        return False, "未找到桌面端：" + WORKBUDDY_EXE
    try:
        # close_fds：不让子进程继承本服务的监听套接字
        subprocess.Popen([WORKBUDDY_EXE], close_fds=True)
    except Exception as e:                                       # noqa
        return False, str(e)
    _focus_app_later(WORKBUDDY_EXE)
    return True, "launched"


def open_recycle_bin():
    """打开回收站（shell:RecycleBinFolder），并把窗口拉到前台。"""
    try:
        subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"], close_fds=True)
    except Exception as e:                                       # noqa
        return False, str(e)
    # 回收站窗口标题随系统语言变化，所以只按 Explorer 窗口类匹配
    threading.Thread(target=lambda: focus_window(
        cls="CabinetWClass", wait_sec=10.0), daemon=True).start()
    return True, "opened"
PERSONAL_DIRS = ("desktop", "documents", "downloads", "appdata", "pictures",
                 "videos", "music", "contacts", "favorites", "links",
                 "saved games", "searches", "onedrive",
                 "桌面", "文档", "下载", "图片", "视频", "音乐")

SYSTEM_ROOTS = [
    r"c:\windows", r"c:\program files", r"c:\program files (x86)",
    r"c:\programdata", r"c:\users", r"c:\recovery", r"c:\perflogs",
    r"c:\$recycle.bin", r"c:\system volume information",
    r"c:\documents and settings", r"c:\boot", r"c:\efi",
]


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32),
                ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


def query_recycle_bin(drive_root):
    """查询回收站配额信息，返回 (条目数, 总字节数) 或 None。"""
    try:
        info = SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
        res = ctypes.windll.shell32.SHQueryRecycleBinW(
            ctypes.c_wchar_p(drive_root), ctypes.byref(info))
        return None if res else (info.i64NumItems, info.i64Size)
    except Exception:                                            # noqa
        return None


def to_recycle_bin(path):
    """
    把文件/文件夹移入回收站。

    返回 (error, warning)：
      error   —— 操作失败的描述字符串（成功时为 None）
      warning —— 成功的额外提示（通常为 None）

    注意：实测 SHFileOperationW 在成功移入回收站后仍可能返回错误码 2
    （ERROR_FILE_NOT_FOUND），因此不能只凭返回码判断成败。这里采用双重校验：
      1) 原路径是否已不存在
      2) 回收站配额是否真的增长（确认是被回收，而不是被永久删除）
    """
    p = os.path.abspath(path)
    drive_root = os.path.splitdrive(p)[0] + "\\"
    before = query_recycle_bin(drive_root)

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = p + "\0\0"          # 必须以双 null 结尾
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))

    gone = not os.path.exists(p)
    grew = False
    if before is not None:
        after = query_recycle_bin(drive_root)
        grew = after is not None and (after[1] > before[1] or after[0] > before[0])

    if gone and grew:
        return None, None
    if gone and not grew:
        return None, ("内容已从原位置移除，但回收站体积没有增加——"
                      "请立刻打开回收站确认它是否可还原")
    if op.fAnyOperationsAborted:
        return "操作被系统中止（返回码 %d）" % rc, None
    return "删除未生效，返回码 %d（可能文件被占用或无权限）" % rc, None


def is_protected(path):
    """判断是否为受保护 / 高危路径，返回原因（字符串）或 None。"""
    ap = os.path.abspath(path)
    if re.fullmatch(r"[A-Za-z]:\\?", ap):
        return "不能删除盘符根目录"
    low = ap.lower().rstrip("\\")
    for p in SYSTEM_ROOTS:
        if low == p:
            return "系统目录不允许删除：" + ap
    home = USER_HOME.lower()
    for name in PERSONAL_DIRS:
        if low == home + "\\" + name:
            return "用户个人文件夹本身不允许删除：" + ap
    if low == ROOT.lower() or (ROOT.lower() + "\\").startswith(low + "\\"):
        return "工作台自身目录不允许删除"
    return None


def reveal_in_explorer(path):
    """在资源管理器中显示，并把该窗口真正拉到前台（而不是丢在后台）。"""
    ap = os.path.abspath(path)
    target = ap if os.path.isdir(ap) else os.path.dirname(ap)
    hint = os.path.basename(target.rstrip("\\/")) or (target[:2] if len(target) >= 2 else "")
    try:
        if os.path.isdir(ap):
            os.startfile(ap)                                     # noqa
        else:
            subprocess.Popen(["explorer.exe", "/select,", ap], close_fds=True)
    except Exception as e:                                       # noqa
        return str(e)
    # Explorer 窗口是异步弹出的，轮询等它出现后置顶
    threading.Thread(target=lambda: focus_window(
        cls="CabinetWClass", title_has=hint, wait_sec=12.0), daemon=True).start()
    return None


def log_trash(path, info):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s | %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), path, info))
    except OSError:
        pass


# ============================================================ 目录列举 / 打开 / 新建
# 扫描树只存「文件夹聚合体积」，不存具体文件清单。这里提供实时列举，
# 让「文件夹明细」能真正看到目录里的文件并打开；支持递归搜索。

def _ext_label(name):
    dot = name.rfind(".")
    if 0 < dot < len(name) - 1:
        return name[dot + 1:].lower()
    return ""


def list_dir(path, q=None, recursive=False, max_files=4000, max_dirs=6000,
             time_budget=6.0, max_entries=15000):
    """
    列举目录内容。
      recursive=False（默认）：返回该目录下的「即时子项」（文件夹 + 文件）。
      recursive=True      ：按 q 递归搜索该目录及其【所有下级目录】下的文件夹与文件。
                            每个结果带 rel（相对 base 的所在目录，""=本级），便于前端展示。
    返回 {ok, base, dirs:[{n,p,d:1,s,m,rel}], files:[...], truncated}

    性能说明：递归时除了「命中数 / 耗时」上限，还有 max_entries（已扫描条目数）上限。
    否则遇到「一个匹配都没找到」的关键字时会一路扫满整个盘直到 time_budget 耗尽，
    用户要等 6 秒才看到结果；加上条目上限后通常 1 秒内就能给出「本级 + 浅层」的结果。
    """
    ap = os.path.abspath(path)
    if not os.path.isdir(ap):
        return None
    ql = (q or "").strip().lower()
    t0 = time.time()
    dirs, files, truncated = [], [], False
    scanned = 0

    def rel_parent(p):
        """p 相对 ap 的「所在目录」，本级返回 ""。"""
        rest = p[len(ap):].lstrip("\\/")
        d = os.path.dirname(rest)
        return d.replace("/", "\\")

    def over():
        """是否已达上限（数量 / 时间 / 递归扫描条目数）。"""
        if len(files) >= max_files or len(dirs) >= max_dirs:
            return True
        if time.time() - t0 > time_budget:
            return True
        return bool(recursive and scanned >= max_entries)

    def collect():
        """广度优先：先把本级扫完，再逐层下钻。
        这样即便后面因时间 / 数量上限被截断，也保证「离当前位置近」的结果先被收录，
        不会因为先深挖某个大目录而把浅层的匹配项整个漏掉。"""
        nonlocal truncated, scanned
        queue = [(ap, 0)]
        while queue:
            base, depth = queue.pop(0)
            if over():
                truncated = True
                return
            try:
                with os.scandir(base) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                scanned += 1
                if over():
                    truncated = True
                    return
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                if is_dir:
                    if not recursive:
                        # 非递归：把子目录本身列出来（symlink/reparse 也展示但不下钻）
                        dirs.append({"n": e.name, "p": e.path,
                                     "d": 1, "s": 0, "m": st.st_mtime, "rel": ""})
                    else:
                        # 递归：命中关键字的文件夹也收进结果（本级 + 所有下级）
                        if (not ql) or (ql in e.name.lower()):
                            dirs.append({"n": e.name, "p": e.path,
                                         "d": 1, "s": 0, "m": st.st_mtime,
                                         "rel": rel_parent(e.path)})
                        if depth < 20:
                            queue.append((e.path, depth + 1))
                else:
                    if ql and ql not in e.name.lower() and ql not in _ext_label(e.name):
                        continue
                    files.append({"n": e.name, "p": e.path,
                                  "d": 0, "s": st.st_size, "m": st.st_mtime,
                                  "rel": rel_parent(e.path)})

    collect()
    return {"ok": True, "base": ap,
            "dirs": dirs, "files": files, "truncated": truncated}


def _safe_name(name):
    """清理新建文件名 / 文件夹名，禁止路径分隔与非法字符，杜绝穿越。"""
    name = (name or "").strip()
    if not name:
        return None
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[<>:"|?*]', "_", name)
    if name in (".", ".."):
        return None
    return name[:200]


def open_file(path):
    """用系统默认程序打开文件 / 文件夹（只读，不修改内容）。"""
    ap = os.path.abspath(path)
    if not os.path.exists(ap):
        return False, "路径不存在：" + path
    try:
        os.startfile(ap)
        return True, None
    except OSError as e:
        return False, str(e)


# ============================================================ 导入旧报告

def import_legacy_report():
    """把上一版静态报告的数据导入缓存，保证打开工作台就有内容。"""
    if not os.path.exists(LEGACY_REPORT):
        return
    key = cache_key("E:\\")
    if cache_exists(key):
        return
    try:
        with open(LEGACY_REPORT, encoding="utf-8") as f:
            html = f.read()
        m = re.search(r'<script id="report-data" type="application/json">(.*?)</script>',
                      html, re.S)
        if not m:
            return
        data = json.loads(m.group(1))
        root = data["root"]
        save_cache(key, {
            "root": root,
            "ext": [],
            "ages": [],
            "big_files": data.get("big_files", []),
            "path": "E:\\",
            "dirs": root.get("d", 0),
            "files": root.get("f", 0),
            "errors": 5,
            "elapsed": 0,
            "scanned_at": "导入自旧版静态报告",
            "total": root.get("s", 0),
            "legacy": 1,
        })
        print("  已导入旧版 E 盘报告数据到缓存")
    except Exception as e:                                       # noqa
        print("  旧报告导入失败（可忽略）：%s" % e)


# ============================================================ 全局搜索
# 缓存里的数据是「扫描那一刻」的快照，扫描之后新建的文件夹搜不到。
# 所以搜索 = 缓存索引 + 实时遍历各盘的名称池，两者合并去重。

SEARCH_SKIP_ANY = {"$recycle.bin", "system volume information", "config.msi",
                   "windows.old", "recovery", "perflogs", "winsxs", "installer"}
SEARCH_SKIP_TOP = SEARCH_SKIP_ANY | {
    "program files", "program files (x86)", "programdata", "windows",
    "boot", "efi", "documents and settings", "msocache", "inetpub",
}

_LIVE_TTL = 90.0            # 名称池有效期（秒），期间输入新词秒出
_live = {"items": [], "ts": 0.0, "dirs": 0, "truncated": False, "elapsed": 0.0}
_live_lock = threading.Lock()
_cache_memo = {"sig": None, "index": {}, "files": []}


def _cache_signature():
    try:
        return tuple(sorted(
            (f, int(os.path.getmtime(os.path.join(CACHE_DIR, f))))
            for f in os.listdir(CACHE_DIR) if f.endswith((".json", ".json.gz"))))
    except OSError:
        return None


def cached_index():
    """把所有扫描缓存摊平：{路径: {s,f}} + 文件列表。按文件 mtime 记忆。"""
    sig = _cache_signature()
    if sig is not None and _cache_memo["sig"] == sig:
        return _cache_memo
    idx, files = {}, []
    try:
        names = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
    except OSError:
        names = []
    for fn in names:
        try:
            with open(os.path.join(CACHE_DIR, fn), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:                                        # noqa
            continue
        stack = [data.get("root") or {}]
        while stack:
            n = stack.pop()
            if n.get("p"):
                # m 一并带上：搜索结果里的「文件夹」也要能显示具体修改时间
                idx[n["p"]] = {"s": n.get("s", 0), "f": n.get("f", 0),
                               "m": n.get("m", 0)}
            for c in (n.get("c") or []):
                stack.append(c)
        seen = set()
        for x in (data.get("file_index") or []) + (data.get("big_files") or []):
            p = x.get("p")
            if p and p not in seen:
                seen.add(p)
                files.append({"p": p, "s": x.get("s", 0), "m": x.get("m", 0)})
    _cache_memo.update(sig=sig, index=idx, files=files)
    return _cache_memo


def build_live_pool(time_budget=6.0, max_dirs=120000, max_files=80000):
    """
    实时遍历各本地盘，收集 (名称, 路径, 是否目录, 大小, 修改时间)。
    目录和文件分开计数：文件先到上限就停止记录文件，但继续遍历目录，
    否则海量小文件会早早吃掉预算，导致深层目录覆盖不到。
    """
    t0 = time.time()
    items, dirs, files, truncated = [], 0, 0, False
    # 注意：list_drives() 的 letter 已经带冒号（形如 "C:"），别再加一个
    roots = [d["letter"] + "\\" for d in list_drives() if d.get("label") == "本地磁盘"]
    dq = deque((r, 0) for r in roots)
    while dq:
        if time.time() - t0 > time_budget or dirs >= max_dirs:
            truncated = True
            break
        path, depth = dq.popleft()
        try:
            with os.scandir(path) as it:
                for e in it:
                    if time.time() - t0 > time_budget or dirs >= max_dirs:
                        truncated = True
                        break
                    try:
                        if e.is_dir(follow_symlinks=False):
                            low = e.name.lower()
                            if low in SEARCH_SKIP_ANY:
                                continue
                            if depth == 0 and low in SEARCH_SKIP_TOP:
                                continue
                            try:
                                mt = e.stat(follow_symlinks=False).st_mtime
                            except OSError:
                                mt = 0
                            items.append({"n": e.name, "p": e.path,
                                          "d": 1, "s": 0, "m": mt})
                            dirs += 1
                            if depth < 16:
                                dq.append((e.path, depth + 1))
                        elif files < max_files:
                            try:
                                st = e.stat(follow_symlinks=False)
                                sz, mt = st.st_size, st.st_mtime
                            except OSError:
                                sz, mt = 0, 0
                            items.append({"n": e.name, "p": e.path,
                                          "d": 0, "s": sz, "m": mt})
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return items, dirs, truncated, round(time.time() - t0, 1)


def _search_sort_key(sort):
    """搜索结果排序键：size（默认）/ time / type（扩展名）/ name。"""
    if sort == "time":
        return lambda x: x.get("m") or 0
    if sort == "type":
        return lambda x: os.path.splitext(x.get("n", "").rstrip("\\/"))[1].lower() or "~"
    if sort == "name":
        return lambda x: (x.get("n") or "").lower()
    return lambda x: x.get("s") or 0


def global_search(q, limit=14, force=False, sort="size", order="desc"):
    ql = (q or "").strip().lower()
    if not ql:
        return {"ok": True, "folders": [], "files": [], "meta": {"empty": 1}}
    rebuilt = False
    with _live_lock:
        fresh = bool(_live["items"]) and (time.time() - _live["ts"]) < _LIVE_TTL
        if force or not fresh:
            items, dirs, trunc, el = build_live_pool()
            _live.update(items=items, ts=time.time(), dirs=dirs,
                         truncated=trunc, elapsed=el)
            rebuilt = True
        pool = _live
    memo = cached_index()
    seen_d, seen_f = set(), set()
    fol, fil = [], []

    for it in pool["items"]:                       # 1) 实时池（含扫描后新建的）
        if ql not in it["n"].lower():
            continue
        if it["d"]:
            if it["p"] in seen_d:
                continue
            seen_d.add(it["p"])
            info = memo["index"].get(it["p"])
            fol.append({"p": it["p"], "n": it["n"], "live": 1,
                        "s": info["s"] if info else 0,
                        "f": info["f"] if info else 0, "m": it["m"]})
        else:
            if it["p"] in seen_f:
                continue
            seen_f.add(it["p"])
            fil.append({"p": it["p"], "n": it["n"], "live": 1,
                        "s": it["s"], "m": it["m"]})

    for p, info in memo["index"].items():           # 2) 缓存补体积（可排序）
        if ql not in os.path.basename(p.rstrip("\\/")).lower():
            continue
        if p in seen_d:
            continue
        seen_d.add(p)
        fol.append({"p": p, "n": os.path.basename(p.rstrip("\\/")),
                    "live": 0, "s": info["s"], "f": info["f"],
                    "m": info.get("m", 0)})

    for x in memo["files"]:
        nm = os.path.basename(x["p"].rstrip("\\/"))
        if ql not in nm.lower() or x["p"] in seen_f:
            continue
        seen_f.add(x["p"])
        fil.append({"p": x["p"], "n": nm, "live": 0, "s": x["s"], "m": x["m"]})

    fol.sort(key=_search_sort_key(sort), reverse=(order != "asc"))
    fil.sort(key=_search_sort_key(sort), reverse=(order != "asc"))
    return {
        "ok": True, "q": q, "sort": sort, "order": order,
        "folders": fol[:limit], "files": fil[:limit],
        "meta": {"dirCount": len(fol), "fileCount": len(fil),
                 "scanned": pool["dirs"], "truncated": pool["truncated"],
                 "elapsed": pool["elapsed"], "rebuilt": rebuilt,
                 "poolAge": round(time.time() - pool["ts"], 1)},
    }


# ============================================================ Everything 引擎
# voidtools Everything（https://www.voidtools.com/）有三种接入方式：
#   1) es.exe 命令行（https://github.com/voidtools/ES）—— 需要 Everything 客户端在运行；
#   2) Everything HTTP Server（工具→选项→HTTP Server，默认 80 端口）—— 返回 JSON 最稳；
#   3) Everything64.dll SDK —— 需要拖 DLL，这里不依赖。
# 本地实现优先 es.exe，其次 HTTP Server；都没有就回退内置实时遍历（global_search）。

def _es_candidates():
    """常见安装位置里的 es.exe。"""
    names = ["es.exe"]
    roots = [
        os.environ.get("LOCALAPPDATA", "") + r"\Microsoft\WindowsApps",
        r"C:\Program Files\Everything", r"C:\Program Files (x86)\Everything",
        os.environ.get("LOCALAPPDATA", "") + r"\Everything",
        os.path.dirname(os.path.abspath(__file__)),
    ]
    out = []
    for root in roots:
        if not root:
            continue
        for n in names:
            p = os.path.join(root, n)
            if os.path.isfile(p):
                out.append(p)
    # PATH 里也可能有
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and os.path.isfile(os.path.join(d, "es.exe")):
            out.append(os.path.join(d, "es.exe"))
    seen, uniq = set(), []
    for p in out:
        if p.lower() not in seen:
            seen.add(p.lower()); uniq.append(p)
    return uniq


def _everything_http_port():
    for env in ("EVERYTHING_HTTP_PORT", "WB_EVERYTHING_PORT"):
        v = os.environ.get(env)
        if v and str(v).strip().isdigit():
            return int(v)
    return 80


class IndexEngine:
    """内置极速索引引擎（Everything 的等价替代，纯标准库实现）。

    Everything 是闭源商业软件，GitHub 上没有可嵌入的源码，因此这里自研一套：
      · 后台守护线程遍历全盘，把「文件名 / 所在目录 / 大小 / 修改时间」写进 SQLite；
      · 建索引期间也能用（走实时遍历），索引建好后搜索直接查 SQL，毫秒级返回；
      · 增量刷新：只补扫上次索引之后新建/修改的条目（按 mtime 判断）；
      · 支持按目录前缀限定范围（只索引某个盘）。
    """

    SKIP_DIRS = {"$recycle.bin", "system volume information", "windows\\winsxs",
                 "$windows.~bt", "$windows.~ws", "node_modules", ".git",
                 "__pycache__", ".workbuddy", "$sys", "config.msi"}

    def __init__(self, db_path=None):
        self.db = db_path or os.path.join(CACHE_DIR, "index.db")
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = False
        self.state = {
            "status": "idle",        # idle | building | ready | error
            "scanned": 0, "files": 0, "dirs": 0,
            "roots": [], "started": 0, "finished": 0,
            "elapsed": 0, "phase": "", "last_roots": [],
        }

    # ---------- 生命周期 ----------
    def try_restore(self, ttl=12 * 3600):
        """服务重启后复用已有索引库，避免每次打开都全盘重建。
        只有库存在、里面有数据、且建成时间在 ttl（默认 12 小时）内才直接置为 ready；
        超过 ttl 则保持 idle，由页面自动触发一次后台重建。"""
        if self.state.get("status") != "idle":
            return False
        if not os.path.exists(self.db):
            return False
        try:
            mt = os.path.getmtime(self.db)
            if time.time() - mt > ttl:
                return False
            con = self._connect()
            n_f = con.execute("SELECT count(*) FROM entry WHERE dir=0").fetchone()[0]
            n_d = con.execute("SELECT count(*) FROM entry WHERE dir=1").fetchone()[0]
            con.close()
        except Exception:
            return False
        if not n_f:
            return False
        self.state.update(status="ready", files=n_f, dirs=n_d,
                          scanned=n_f + n_d, elapsed=0,
                          phase="复用上次索引（%s）" %
                          time.strftime("%m-%d %H:%M", time.localtime(mt)),
                          started=mt, finished=mt)
        return True

    def start(self, roots=None):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.state
            self.stop_flag = False
            self.thread = threading.Thread(target=self._run, args=(roots or [],),
                                           daemon=True, name="wb-index")
            self.thread.start()
            return self.state

    def status(self):
        s = dict(self.state)
        s["db"] = self.db
        s["dbSize"] = os.path.getsize(self.db) if os.path.exists(self.db) else 0
        return s

    def _update(self, **kw):
        self.state.update(kw)

    # ---------- 建库 ----------
    SCHEMA_VERSION = 3

    def _connect(self):
        """紧凑结构（v3）：
        · 目录路径只存一份（dir 表），entry 行里只存名字 + 目录 id；
        · 修改时间存整数秒；
        · 不建 ext 列（按文件名后缀 LIKE 过滤）、不建 did 索引、dir.p 不加 UNIQUE
          （id 由建库时自己分配，不需要数据库再存一份唯一索引）；
        · 旧版 v1（每行重复存 p / parent 全路径）约 230MB，本版约 60MB。
        """
        import sqlite3
        con = sqlite3.connect(self.db, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA cache_size=-8192")     # 页缓存封顶 8MB，别把内存吃满
        con.execute("PRAGMA mmap_size=0")
        ver = con.execute("PRAGMA user_version").fetchone()[0]
        if ver != IndexEngine.SCHEMA_VERSION:
            con.executescript(
                "DROP TABLE IF EXISTS entry;"
                "DROP TABLE IF EXISTS dir;"
                "DROP INDEX IF EXISTS ix_n;"
                "DROP INDEX IF EXISTS ix_ext;"
                "DROP INDEX IF EXISTS ix_par;"
                "DROP INDEX IF EXISTS ix_did;")
            con.execute("PRAGMA user_version=%d" % IndexEngine.SCHEMA_VERSION)
            con.commit()
        con.execute("CREATE TABLE IF NOT EXISTS dir(id INTEGER PRIMARY KEY, p TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS entry("
                    "n TEXT NOT NULL, did INTEGER NOT NULL, dir INTEGER NOT NULL,"
                    "s INTEGER, m INTEGER)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_n ON entry(n COLLATE NOCASE)")
        return con

    def _run(self, roots):
        t0 = time.time()
        try:
            con = self._connect()
            self._update(status="building", started=t0, phase="准备中", scanned=0)
            con.execute("DELETE FROM entry")      # 全量重建，结构简单不易出脏数据
            con.execute("DELETE FROM dir")
            con.commit()
            batch, n_file, n_dir, n_scan = [], 0, 0, 0
            dir_ids, dir_batch, next_id = {}, [], [1]

            def dir_id(path):
                """目录路径 → 数字 id（只写一次，entry 行里不再重复整条路径）"""
                i = dir_ids.get(path)
                if i is None:
                    i = next_id[0]
                    next_id[0] += 1
                    dir_ids[path] = i
                    dir_batch.append((i, path))
                return i

            def flush():
                if dir_batch:
                    con.executemany("INSERT OR IGNORE INTO dir(id,p) VALUES(?,?)",
                                    dir_batch)
                    dir_batch.clear()
                if batch:
                    con.executemany(
                        "INSERT INTO entry(n,did,dir,s,m) VALUES(?,?,?,?,?)",
                        batch)
                    con.commit()
                    batch.clear()

            for root in roots:
                if self.stop_flag:
                    break
                self._update(phase="正在索引 " + root)
                dir_id(root.rstrip("\\") or root)
                for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                            onerror=lambda e: None):
                    if self.stop_flag:
                        break
                    # 就地剪枝，跳过系统目录与体积大的无关目录
                    dirnames[:] = [d for d in dirnames
                                   if d.lower() not in IndexEngine.SKIP_DIRS
                                   and not d.startswith("~")]
                    low = dirpath.lower()
                    if any(s in low for s in ("\\$recycle.bin",
                                              "\\system volume information",
                                              "\\windows\\winsxs")):
                        dirnames[:] = []
                        continue
                    n_scan += 1
                    if n_scan % 400 == 0:
                        flush()
                        self._update(scanned=n_scan, files=n_file, dirs=n_dir,
                                     elapsed=round(time.time() - t0, 1),
                                     phase="正在索引 " + os.path.basename(dirpath or root))
                    for fn in filenames:
                        fp = os.path.join(dirpath, fn)
                        try:
                            st = os.stat(fp)
                        except OSError:
                            continue
                        batch.append((fn, dir_id(dirpath), 0, st.st_size,
                                      int(st.st_mtime)))
                        n_file += 1
                        if len(batch) >= 2000:
                            flush()
                    for dn in dirnames:
                        fp = os.path.join(dirpath, dn)
                        try:
                            st = os.stat(fp)
                        except OSError:
                            continue
                        batch.append((dn, dir_id(dirpath), 1, 0, int(st.st_mtime)))
                        n_dir += 1
            flush()
            con.commit()
            try:
                con.execute("ANALYZE")
            except sqlite_error_placeholder():
                pass
            # 全量重建后 VACUUM：回收删除留下的空洞 + 合并页，体积能再小一截
            try:
                con.execute("VACUUM")
                con.execute("PRAGMA journal_mode=DELETE")
            except sqlite_error_placeholder():
                pass
            con.close()
            self._update(status="ready", scanned=n_scan, files=n_file, dirs=n_dir,
                         roots=list(roots), elapsed=round(time.time() - t0, 1),
                         phase="索引完成", finished=time.time())
        except Exception as e:
            self._update(status="error", phase="索引失败：" + str(e)[:80])

    # ---------- 查询 ----------
    def query(self, q, kind="all", ext="", limit=2000, order="size", roots=None,
              inpath=False):
        """kind: all | dir | file；ext: 'pdf' / 'doc,docx'；inpath: 只按路径匹配（不匹配文件名）"""
        if self.state.get("status") != "ready":
            return None
        where, args = ["1=1"], []
        if q:
            if inpath:
                where.append("d.p LIKE ? COLLATE NOCASE")
                args += ["%" + q + "%"]
            else:
                where.append("(e.n LIKE ? COLLATE NOCASE OR d.p LIKE ? COLLATE NOCASE)")
                args += ["%" + q + "%", "%" + q + "%"]
        if kind == "dir":
            where.append("e.dir=1")
        elif kind == "file":
            where.append("e.dir=0")
        if ext:
            ex = [e.strip().lstrip(".").lower() for e in ext.split(",") if e.strip()]
            if ex:
                # 不再存 ext 列，直接按文件名的后缀匹配
                where.append("(" + " OR ".join(["e.n LIKE ?"] * len(ex)) + ")")
                args += ["%." + e for e in ex]
        if roots:
            rs = [r.rstrip("\\") for r in roots]
            sub = " OR ".join(["(p LIKE ? OR p = ?)"] * len(rs))
            where.append("e.did IN (SELECT id FROM dir WHERE " + sub + ")")
            for r in rs:
                args += [r + "\\%", r]
        ob = {"size": "e.s", "time": "e.m", "name": "e.n", "ext": "e.n"}[order] \
            if order in ("size", "time", "name", "ext") else "e.s"
        sql = ("SELECT e.n,d.p,e.dir,e.s,e.m FROM entry e JOIN dir d ON d.id=e.did "
               "WHERE " + " AND ".join(where) +
               " ORDER BY %s %s LIMIT ?" % (ob, "DESC" if order != "name" else "ASC"))
        args.append(limit)
        try:
            con = self._connect()
            rows = con.execute(sql, args).fetchall()
            con.close()
        except Exception:
            return None
        out = []
        for n, dp, dd, s, m in rows:
            base = dp if dp.endswith("\\") else dp + "\\"
            out.append({"n": n, "p": base + n, "d": 1 if dd else 0,
                        "s": s, "m": m})
        return out

    def stats_extra(self):
        """首页仪表盘用的聚合数据：今日增量 / 近 30 天变动 / 重复文件 / 长期未访问。

        全部基于索引库做聚合，只读，不碰任何文件。索引未就绪时返回 None。
        """
        if self.state.get("status") != "ready":
            return None
        now = time.time()
        lt = time.localtime(now)
        today0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                  0, 0, 0, 0, 0, -1)))
        d30 = now - 30 * 86400
        old = now - 365 * 86400
        out = {}
        try:
            con = self._connect()
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(s),0) FROM entry "
                "WHERE dir=0 AND m>=?", (today0,)).fetchone()
            out["today_files"], out["today_size"] = int(row[0] or 0), int(row[1] or 0)
            rows = con.execute(
                "SELECT CAST((m-?)/86400 AS INTEGER) AS d, COALESCE(SUM(s),0), "
                "COUNT(*) FROM entry WHERE dir=0 AND m>=? GROUP BY d",
                (d30, d30)).fetchall()
            by_day = {}
            for d, sz, cnt in rows:
                by_day[int(d)] = (int(sz or 0), int(cnt or 0))
            out["trend"] = [by_day.get(i, (0, 0)) for i in range(31)]
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM((c-1)*s),0) FROM ("
                "SELECT s, COUNT(*) c FROM entry WHERE dir=0 AND s>1048576 "
                "GROUP BY n, s HAVING c>1)").fetchone()
            out["dup_groups"], out["dup_size"] = int(row[0] or 0), int(row[1] or 0)
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(s),0) FROM entry "
                "WHERE dir=0 AND m>0 AND m<?", (old,)).fetchone()
            out["old_files"], out["old_size"] = int(row[0] or 0), int(row[1] or 0)
            out["at"] = now
            con.close()
        except Exception:
            return None
        return out


def sqlite_error_placeholder():
    import sqlite3
    return sqlite3.Error


INDEX = None


def get_index():
    global INDEX
    if INDEX is None:
        INDEX = IndexEngine()
    return INDEX


def default_scan_roots():
    """默认索引所有本地盘（用户要求：可以全部盘一起看）。"""
    roots = []
    for d in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        p = d + ":\\"
        if os.path.isdir(p):
            roots.append(p)
    return roots


def detect_engine():
    """探测可用搜索引擎：everything（es.exe / HTTP Server）或 builtin。"""
    cands = _es_candidates()
    for es in cands[:1]:
        try:
            r = subprocess.run([es, "-version"], capture_output=True, timeout=4)
            if r.returncode == 0:
                return {"ok": True, "engine": "everything", "via": "es.exe",
                        "es": es, "detail": "es.exe 可用（Everything 客户端需在运行）"}
        except Exception:
            pass
    # HTTP Server 探测
    port = _everything_http_port()
    try:
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/json?q=__wb_probe__&count=1" % port, timeout=1.2) as resp:
            if resp.status == 200:
                return {"ok": True, "engine": "everything", "via": "http",
                        "port": port, "detail": "Everything HTTP Server 已开启（端口 %d）" % port}
    except Exception:
        pass
    return {"ok": True, "engine": "builtin", "via": None,
            "detail": "未检测到 Everything，使用内置实时遍历"}


_ES_CACHE = {"ts": 0.0, "ok": False, "engine": "builtin", "via": None, "detail": ""}
def engine_status(force=False):
    now = time.time()
    if not force and now - _ES_CACHE["ts"] < 30 and _ES_CACHE["ts"]:
        return _ES_CACHE
    try:
        st = detect_engine()
    except Exception as e:
        st = {"ok": True, "engine": "builtin", "via": None, "detail": str(e)}
    st["ts"] = now
    _ES_CACHE.clear(); _ES_CACHE.update(st)
    return _ES_CACHE


def everything_search(q, limit, sort, order):
    """通过 Everything 搜索，输出与 global_search 相同的结构（folders/files/meta）。"""
    t0 = time.time()
    st = engine_status()
    rows = []
    via = st.get("via")
    if st.get("engine") == "everything" and via == "es.exe":
        colmap = {"size": "-sort", "time": "-sort", "name": "-sort", "type": "-sort"}
        sortname = {"size": "size", "time": "date-modified", "name": "name",
                    "type": "extension"}.get(sort, "name")
        args = [st["es"], "-json", "-n", str(max(1, min(int(limit), 5000))),
                "-size-format", "1", "-double-quote"]
        if sortname:
            args += ["-sort", sortname + ("" if order == "asc" else "-descending")]
        args += [q]
        r = subprocess.run(args, capture_output=True, timeout=15)
        if r.returncode in (0, 9):                       # 9 = 无结果
            try:
                data = json.loads((r.stdout or b"").decode("utf-8", "replace") or "[]")
            except Exception:
                data = []
            if isinstance(data, dict):
                data = data.get("results") or []
            for it in data:
                if not isinstance(it, dict):
                    continue
                p = it.get("path") or ""
                nm = it.get("name") or (p.replace("/", "\\").rsplit("\\", 1)[-1])
                if p and nm and not p.lower().endswith(nm.lower()):
                    p = p.rstrip("\\/") + "\\" + nm
                rows.append({"p": p, "n": nm, "s": int(it.get("size") or 0),
                             "m": _es_time(it.get("date_modified") or it.get("dm")),
                             "attr": it.get("attributes") or ""})
    elif st.get("engine") == "everything" and via == "http":
        import urllib.request, urllib.parse
        sortmap = {"size": "size", "time": "dm", "name": "name", "type": "ext"}
        url = ("http://127.0.0.1:%d/json?%s" % (_everything_http_port(), urllib.parse.urlencode(
            {"q": q, "count": max(1, min(int(limit), 5000)), "json": 1,
             "sort": sortmap.get(sort, "name"),
             "ascending": 1 if order == "asc" else 0})))
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        for it in (data.get("results") or []):
            rows.append({"p": (it.get("path") or "").rstrip("\\/") + "\\" + (it.get("name") or ""),
                         "n": it.get("name") or "", "s": int(it.get("size") or 0),
                         "m": _es_time(it.get("date_modified")),
                         "attr": it.get("attributes") or ""})
    else:
        return None                                       # 引擎不可用 → 调用方回退
    fol, fil = [], []
    for it in rows:
        if not it["p"]:
            continue
        is_dir = "D" in (it.get("attr") or "")
        tgt = fol if is_dir else fil
        it.pop("attr", None)
        it["live"] = 0
        if is_dir:
            it.setdefault("f", 0)
        tgt.append(it)
    return {"ok": True, "q": q, "sort": sort, "order": order,
            "folders": fol, "files": fil,
            "meta": {"dirCount": len(fol), "fileCount": len(fil), "scanned": 0,
                     "truncated": False, "elapsed": round(time.time() - t0, 2),
                     "rebuilt": False, "engine": "everything"}}


def _es_time(v):
    """Everything 的时间 → Unix 秒。支持 FILETIME（1.6e7/秒）与 ISO 字符串。"""
    try:
        if v is None:
            return 0
        if isinstance(v, (int, float)):
            f = float(v)
            return int(f / 10_000_000 - 11644473600) if f > 10**12 else int(f)
        s = str(v)
        if s.replace('.', '', 1).isdigit():
            f = float(s)
            return int(f / 10_000_000 - 11644473600) if f > 10**12 else int(f)
        from datetime import datetime, timezone
        s2 = s.replace("T", " ").replace("Z", "").split(".")[0].strip()
        for pat in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return int(datetime.strptime(s2, pat).replace(
                    tzinfo=timezone.utc).timestamp())
            except ValueError:
                continue
    except Exception:
        pass
    return 0


# ============================================================ HTTP

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:                                        # noqa
            return {}

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = {}
        if u.query:
            for kv in u.query.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    q[k] = v

        if u.path == "/api/drives":
            return self._json({"drives": list_drives()})

        if u.path == "/api/cache":
            # 支持按盘符 key（E）或按任意路径取缓存（自定义路径扫描后可直接恢复）
            if q.get("path"):
                data = load_cache(cache_key(unquote(q["path"])))
            else:
                data = load_cache(q.get("key", ""))
            return self._json({"ok": data is not None, "data": data})

        if u.path == "/api/history":
            try:
                lim = int(q.get("limit", 40))
            except (TypeError, ValueError):
                lim = 40
            pts = load_history(unquote(q.get("path", "")), lim)
            return self._json({"ok": True, "points": pts})

        if u.path == "/api/scan/status":
            with jobs_lock:
                j = jobs.get(q.get("job", ""))
            if not j:
                return self._json({"ok": False, "error": "任务不存在"}, 404)
            return self._json({
                "ok": True,
                "state": j.state,
                "error": j.error,
                "dirs": j.dirs,
                "files": j.files,
                "errors": j.errors,
                "elapsed": round(time.time() - j.t0, 1),
                "result": j.result if j.state == "done" else None,
            })

        if u.path == "/api/scan/stop":
            with jobs_lock:
                j = jobs.get(q.get("job", ""))
            if j:
                j.state = "aborted"
            return self._json({"ok": True})

        if u.path == "/api/search":
            try:
                limit = int(q.get("limit", 14))
            except ValueError:
                limit = 14
            limit = max(1, min(limit, 5000))
            sort = (q.get("sort") or "size").lower()
            order = (q.get("order") or "desc").lower()
            engine = (q.get("engine") or "builtin").lower()
            kw = unquote(q.get("q", ""))
            kind = (q.get("kind") or "all").lower()      # all | dir | file
            ext = unquote(q.get("ext") or "")            # "pdf" / "doc,docx"
            # ① 外部 Everything（若用户装了）
            if engine == "everything":
                try:
                    res = everything_search(kw, limit, sort, order)
                except Exception:
                    res = None
                if res is not None:
                    return self._json(res)
                engine = "builtin"                        # 回退
            # ② 内置极速索引（自己建的 SQLite 索引，毫秒级）
            if engine in ("fast", "index"):
                inpath = q.get("inpath") == "1"      # 只按路径匹配（文件夹全盘搜索联动用）
                rows = get_index().query(kw, kind, ext, limit, sort, inpath=inpath)
                if rows is not None:
                    fol = [r for r in rows if r["d"]]
                    fil = [r for r in rows if not r["d"]]
                    return self._json({
                        "ok": True, "q": kw, "sort": sort, "order": order,
                        "folders": fol, "files": fil,
                        "meta": {"dirCount": len(fol), "fileCount": len(fil),
                                 "scanned": get_index().state.get("scanned", 0),
                                 "truncated": False, "elapsed": 0.01,
                                 "engine": "fast-index",
                                 "indexStatus": get_index().state.get("status")},
                    })
            # ③ 实时遍历兜底
            res = global_search(kw, limit, q.get("refresh") == "1", sort, order)
            if kind != "all":
                if kind == "dir":
                    res["files"] = []
                else:
                    res["folders"] = []
            if ext:
                exs = [e.strip().lstrip(".").lower() for e in ext.split(",") if e.strip()]
                res["files"] = [f for f in res["files"]
                                if os.path.splitext(f["n"])[1].lower().lstrip(".") in exs]
            if engine == "everything":                    # 原逻辑需要标记回退
                res["fallback"] = True
                res["meta"]["engine"] = "builtin"
            return self._json(res)

        if u.path == "/api/engine":
            st = engine_status(force=q.get("refresh") == "1")
            out = {k: st.get(k) for k in ("engine", "via", "detail")}
            out["ok"] = True
            out["download"] = "https://www.voidtools.com/zh-cn/downloads/"
            out["cli"] = "https://github.com/voidtools/ES/releases"
            out["index"] = get_index().status()
            return self._json(out)

        if u.path == "/api/index/start":
            # 必须 unquote：前端会传 "D:\|" 这种 URL 编码后的盘符列表，
            # 不解码会被当成字面目录名，导致索引到 0 个文件
            raw = unquote(q.get("roots") or "")
            roots = [r.strip() for r in raw.split("|") if r.strip()]
            if not roots:
                roots = default_scan_roots()
            get_index().start(roots)
            return self._json({"ok": True, "index": get_index().status()})

        if u.path == "/api/report/all_drives":
            try:
                return self._json(all_drives_report())
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)

        if u.path == "/api/index/status":
            return self._json({"ok": True, "index": get_index().status()})

        if u.path == "/api/where":
            return self._json({"ok": True, "root": ROOT, "share": SHARE_DIR,
                               "cache": CACHE_DIR, "port": self.server.server_address[1]})
            return self._json(out)

        if u.path == "/api/download":
            # 把真实文件流出去：搜索结果 / 表格行拖拽到聊天软件时，浏览器用它落地成文件
            p = os.path.abspath(unquote(q.get("path", "")))
            if not p or not os.path.isfile(p):
                return self._json({"ok": False, "error": "文件不存在：" + p}, 404)
            size = os.path.getsize(p)
            name = os.path.basename(p)
            try:
                name.encode("ascii")
                disp = "attachment; filename=\"%s\"" % name
            except UnicodeEncodeError:
                from urllib.parse import quote
                disp = "attachment; filename*=UTF-8''" + quote(name)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", disp)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 512)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionAbortedError):
                pass
            return None

        if u.path == "/api/list_dir":
            p = unquote(q.get("path", ""))
            if not p or not os.path.isdir(p):
                return self._json({"ok": False, "error": "目录不存在：" + p})
            # 注意：do_GET 里 q 是原始 query 串，中文必须先 unquote，
            # 否则「待办」这类关键字会变成 %E5%BE%85%E5%8A%9E，永远匹配不上。
            res = list_dir(p, unquote(q.get("q", "")), q.get("recursive") == "1")
            if res is None:
                return self._json({"ok": False, "error": "目录无法访问：" + p})
            return self._json(res)

        if u.path == "/api/health":
            return self._json({"ok": True})

        if u.path == "/api/share/list":
            return self._json({"ok": True, "dir": SHARE_DIR,
                               "files": do_share_list()[:50]})

        if u.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()

        if u.path == "/api/scan/start":
            raw_path = (body.get("path") or "").strip()
            if raw_path.upper() == "ALL":
                # 「全部盘」：不指定任何盘，扫描所有本地盘并合并成一棵树
                roots = default_scan_roots()
                if not roots:
                    return self._json({"ok": False, "error": "没有找到可扫描的本地盘"}, 400)
                path = roots[0]
                merged = True
            else:
                path = os.path.abspath(raw_path or "E:\\")
                roots = [path]
                merged = False
            if not merged and not os.path.isdir(path):
                return self._json({"ok": False, "error": "目录不存在：" + path}, 400)
            key = cache_key(path)
            jid = key + "_" + datetime.now().strftime("%H%M%S")
            job = ScanJob(path,
                          body.get("min_node", 0),
                          body.get("min_file", 50 * 1024 * 1024),
                          body.get("top_files", 200))
            if merged:
                job.roots = roots
                job.cache_key = "ALL"      # 合并结果的缓存键固定为 ALL
                job.path = "ALL"           # 结果 path 也固定为 ALL，
                                           # 否则前端扫描完成会把盘符页签切到第一个盘（如 C:）
            with jobs_lock:
                jobs[jid] = job
            job.start()
            return self._json({"ok": True, "job": jid, "key": key,
                               "roots": roots, "merged": merged})

        if u.path == "/api/clipboard/files":
            try:
                return self._json(copy_files_to_clipboard(body.get("paths") or []))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)

        if u.path == "/api/share/save":
            name = body.get("name") or ""
            content = body.get("content") or ""
            try:
                p = do_share_save(name, content, b64=bool(body.get("b64")))
                return self._json({"ok": True, "path": p})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)

        if u.path == "/api/share/package":
            try:
                p = do_share_package()
                return self._json({"ok": True, "path": p})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)

        if u.path == "/api/share/open_app":
            app = (body.get("app") or "").lower()
            okd, msg = open_chat_app(app)
            return self._json({"ok": okd, "message": msg})

        if u.path == "/api/reveal":
            path = body.get("path", "")
            if not path or not os.path.exists(path):
                return self._json({"ok": False, "error": "路径不存在：" + path})
            err = reveal_in_explorer(path)
            return self._json({"ok": err is None, "error": err, "path": path})

        if u.path == "/api/open":
            app = (body.get("app") or "").lower()
            if app == "workbuddy":
                okd, how = open_workbuddy()
                if okd:
                    return self._json({"ok": True, "app": "workbuddy", "action": how})
                return self._json({"ok": False, "error": how}, 500)
            if app == "recyclebin":
                okd, how = open_recycle_bin()
                if okd:
                    return self._json({"ok": True, "app": "recyclebin", "action": how})
                return self._json({"ok": False, "error": how}, 500)
            return self._json({"ok": False, "error": "未知应用：" + app}, 400)

        if u.path == "/api/trash":
            path = body.get("path", "")
            if not path or not os.path.exists(path):
                return self._json({"ok": False, "error": "路径不存在：" + path})
            reason = is_protected(path)
            if reason:
                log_trash(path, "拒绝：" + reason)
                return self._json({"ok": False, "error": reason})
            if os.path.isdir(path):
                cnt, size = 0, 0
                for dp, _dn, fn in os.walk(path):
                    if cnt > 50000:      # 超大目录不必精确统计，够用即可
                        size = -1
                        break
                    cnt += len(fn)
                    for f in fn:
                        try:
                            size += os.path.getsize(os.path.join(dp, f))
                        except OSError:
                            pass
            else:
                cnt, size = 1, os.path.getsize(path)
            err, warn = to_recycle_bin(path)
            if err:
                log_trash(path, "失败：" + err)
                return self._json({"ok": False, "error": err})
            log_trash(path, "已移入回收站，文件数≈%s，体积≈%s%s" % (
                cnt, size, ("；警告：" + warn) if warn else ""))
            return self._json({"ok": True, "path": path, "files": cnt,
                               "size": size, "warning": warn})

        if u.path == "/api/open_file":
            path = body.get("path", "")
            okd, err = open_file(path)
            if okd:
                return self._json({"ok": True, "path": path})
            return self._json({"ok": False, "error": err})

        if u.path == "/api/mkdir":
            parent = body.get("path", "")
            name = _safe_name(body.get("name"))
            if not parent or not os.path.isdir(parent):
                return self._json({"ok": False, "error": "父目录不存在：" + parent}, 400)
            if not name:
                return self._json({"ok": False, "error": "文件夹名不合法"}, 400)
            np = os.path.join(os.path.abspath(parent), name)
            if os.path.exists(np):
                return self._json({"ok": False, "error": "已存在同名项目：" + np}, 409)
            try:
                os.makedirs(np)
            except OSError as e:
                return self._json({"ok": False, "error": str(e)}, 500)
            return self._json({"ok": True, "path": np})

        if u.path == "/api/touch":
            parent = body.get("path", "")
            name = _safe_name(body.get("name"))
            if not parent or not os.path.isdir(parent):
                return self._json({"ok": False, "error": "父目录不存在：" + parent}, 400)
            if not name:
                return self._json({"ok": False, "error": "文件名不合法"}, 400)
            np = os.path.join(os.path.abspath(parent), name)
            if os.path.exists(np):
                return self._json({"ok": False, "error": "已存在同名项目：" + np}, 409)
            try:
                open(np, "w", encoding="utf-8").close()
            except OSError as e:
                return self._json({"ok": False, "error": str(e)}, 500)
            return self._json({"ok": True, "path": np})

        return self._json({"ok": False, "error": "未知接口"}, 404)


def bind_server(start, tries=300):
    """从 start 起顺延，返回 (已绑定并 listen 的 server, 实际端口)。

    直接试真实绑定（HTTPServer 自带 SO_REUSEADDR，与最终服务完全一致），
    更早的裸 socket 探测在 Windows 上会误判保留端口，已弃用。
    """
    last = None
    try:
        migrate_cache_to_gzip()
    except Exception:
        pass
    for p in range(start, start + tries):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler), p
        except OSError as e:
            last = e
            continue
    raise last if last else OSError("no free port")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="个人磁盘工作台 本地服务")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--print-port", action="store_true",
                    help="只打印实际可用的端口号后退出（供启动脚本调用）")
    args = ap.parse_args()

    # 8733 可能落在 Windows 动态保留端口区间（netsh excludedportrange）或已被占用，
    # 顺延到下一个能真正绑定的端口，免得 WinError 10013 / 10048 直接崩掉。
    try:
        srv, port = bind_server(args.port)
    except OSError as e:
        sys.stderr.write("[错误] 从端口 %d 起连续 %d 个端口都无法绑定：%s\n"
                         % (args.port, 300, e))
        sys.exit(1)

    if args.print_port:
        srv.server_close()
        print(port)
        return

    import_legacy_report()
    # 重启后先尝试复用上次建好的索引库（12 小时内有效），避免每次打开都全盘重建
    try:
        if get_index().try_restore():
            st = get_index().status()
            print("  已复用上次索引：%s 文件 / %s 目录（%s）" %
                  (st["files"], st["dirs"], st["phase"]))
    except Exception:
        pass

    url = "http://127.0.0.1:%d/" % port
    print("=" * 62)
    print("  个人磁盘工作台  ·  PC Disk Workbench")
    print("=" * 62)
    print("  仅本机访问：%s" % url)
    if port != args.port:
        print("  [提示] 端口 %d 不可用（Windows 保留端口或已被占用），已自动改用 %d"
              % (args.port, port))
    print("  关闭此窗口即可停止服务。")
    print("  --------------------------------------------------------")
    print("  只提供「移到回收站」，不提供永久删除；")
    print("  系统目录 / 盘符根目录 / 用户个人文件夹一律拒绝删除；")
    print("  每次删除均记录在 trash_log.txt")
    print("=" * 62)

    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
