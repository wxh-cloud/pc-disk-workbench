# -*- coding: utf-8 -*-
"""
个人磁盘工作台 —— 原生界面版（Win32 + GDI 自绘，零第三方 UI 依赖）

设计目标：内存占用相比 WebView2 版本（私有 376MB）降低一个数量级。
所有绘制走 GDI 双缓冲，颜色/字体统一由 Theme 管理。

复用 server.py 的全部后端能力：盘符/扫描/缓存/索引/目录列举/回收站/文件剪贴板。
"""
import ctypes
import math
import os
import sys
import threading
import time
from ctypes import wintypes as w

# ------------------------------------------------------------------ 后端
_HERE = os.path.dirname(os.path.abspath(__file__))
_DUMP_HOT = bool(os.environ.get("WB_DUMP"))


def _dbg_path(name):
    """调试文件落点：源码运行放 build/，打包后放 exe 同级。"""
    d = os.path.join(_HERE, "build")
    if not os.path.isdir(d):
        d = _HERE
    return os.path.join(d, name)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import server as S  # noqa: E402

# ------------------------------------------------------------------ Win32 声明
u32 = ctypes.windll.user32
gdi = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", w.HDC), ("fErase", w.BOOL), ("rcPaint", w.RECT),
                ("fRestore", w.BOOL), ("fIncUpdate", w.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, ctypes.c_uint, ctypes.c_size_t, LRESULT)

u32.DefWindowProcW.argtypes = [w.HWND, ctypes.c_uint, ctypes.c_size_t, LRESULT]
u32.DefWindowProcW.restype = LRESULT
u32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND,
                                w.HMENU, w.HINSTANCE, ctypes.c_void_p]
u32.CreateWindowExW.restype = w.HWND
u32.RegisterClassW.argtypes = [ctypes.c_void_p]
u32.RegisterClassW.restype = w.ATOM
u32.LoadCursorW.restype = ctypes.c_void_p
u32.LoadCursorW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
u32.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long
u32.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]
u32.SetWindowLongW.restype = ctypes.c_long
u32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
u32.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
u32.MonitorFromWindow.restype = ctypes.c_void_p
u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.CreatePopupMenu.restype = ctypes.c_void_p
u32.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, w.LPCWSTR]
u32.TrackPopupMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, ctypes.c_void_p]
u32.TrackPopupMenu.restype = ctypes.c_int
u32.DestroyMenu.argtypes = [ctypes.c_void_p]
u32.ClientToScreen.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
u32.ScreenToClient.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
u32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
u32.OpenClipboard.argtypes = [w.HWND]
u32.EmptyClipboard.restype = ctypes.c_int
u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
u32.CloseClipboard.restype = ctypes.c_int
k32.GetModuleHandleW.restype = w.HINSTANCE
k32.GetModuleHandleW.argtypes = [w.LPCWSTR]
k32.GlobalAlloc.restype = ctypes.c_void_p
k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalLock.argtypes = [ctypes.c_void_p]
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
gdi.GetDeviceCaps.argtypes = [w.HDC, ctypes.c_int]
gdi.GetStockObject.argtypes = [ctypes.c_int]
gdi.GetStockObject.restype = ctypes.c_void_p
u32.FillRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT), ctypes.c_void_p]
u32.FillRect.restype = ctypes.c_int
u32.BeginPaint.argtypes = [w.HWND, ctypes.POINTER(PAINTSTRUCT)]
u32.BeginPaint.restype = w.HDC
u32.EndPaint.argtypes = [w.HWND, ctypes.POINTER(PAINTSTRUCT)]
u32.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
u32.GetDC.argtypes = [w.HWND]
u32.GetDC.restype = w.HDC
u32.ReleaseDC.argtypes = [w.HWND, w.HDC]
u32.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.RECT), w.UINT]
u32.DrawTextW.restype = ctypes.c_int
gdi.GetTextExtentPoint32W.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int,
                                      ctypes.POINTER(w.SIZE)]
u32.InvalidateRect.argtypes = [w.HWND, ctypes.c_void_p, ctypes.c_int]
u32.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, ctypes.c_void_p]
u32.KillTimer.argtypes = [w.HWND, ctypes.c_size_t]
u32.SetCapture.argtypes = [w.HWND]
u32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int,
                           ctypes.c_int, w.UINT]
u32.LoadImageW.restype = ctypes.c_void_p
u32.SendMessageW.argtypes = [w.HWND, ctypes.c_uint, ctypes.c_size_t, LRESULT]

gdi.CreateCompatibleDC.argtypes = [w.HDC]
gdi.CreateCompatibleDC.restype = w.HDC
gdi.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi.SelectObject.argtypes = [w.HDC, ctypes.c_void_p]
gdi.SelectObject.restype = ctypes.c_void_p
gdi.DeleteObject.argtypes = [ctypes.c_void_p]
gdi.DeleteDC.argtypes = [w.HDC]
gdi.SetTextColor.argtypes = [w.HDC, w.COLORREF]
gdi.SetBkMode.argtypes = [w.HDC, ctypes.c_int]
gdi.SetDCBrushColor.argtypes = [w.HDC, w.COLORREF]
gdi.SetDCBrushColor.restype = w.COLORREF
gdi.SetDCPenColor.argtypes = [w.HDC, w.COLORREF]
gdi.SetDCPenColor.restype = w.COLORREF
gdi.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
gdi.CreateFontW.argtypes = [ctypes.c_int] * 5 + [w.DWORD] * 8 + [w.LPCWSTR]
gdi.CreateFontW.restype = ctypes.c_void_p
gdi.RoundRect.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi.Ellipse.argtypes = [w.HDC] + [ctypes.c_int] * 4
gdi.Rectangle.argtypes = [w.HDC] + [ctypes.c_int] * 4
gdi.Pie.argtypes = [w.HDC] + [ctypes.c_int] * 8
gdi.Polygon.argtypes = [w.HDC, ctypes.POINTER(w.POINT), ctypes.c_int]
gdi.MoveToEx.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi.LineTo.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
gdi.SaveDC.argtypes = [w.HDC]
gdi.RestoreDC.argtypes = [w.HDC, ctypes.c_int]
gdi.IntersectClipRect.argtypes = [w.HDC] + [ctypes.c_int] * 4

DC_BRUSH = 18
DC_PEN = 19
NULL_PEN = 8
NULL_BRUSH = 5


def rgb(s):
    """'#2f6fed' 或 '2f6fed' -> COLORREF"""
    s = s.lstrip("#")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return (b << 16) | (g << 8) | r


def mix(a, b, t):
    """两色线性混合，t=0 取 a，1 取 b"""
    ar, ag, ab = (a & 0xFF), (a >> 8) & 0xFF, (a >> 16) & 0xFF
    br, bg, bb = (b & 0xFF), (b >> 8) & 0xFF, (b >> 16) & 0xFF
    r = int(ar + (br - ar) * t)
    g = int(ag + (bg - ag) * t)
    bl = int(ab + (bb - ab) * t)
    return (bl << 16) | (g << 8) | r


class _GdiPlus:
    """GDI+ 扁平 API 的薄封装：只用来把圆角/圆弧画得抗锯齿。

    GDI 的 RoundRect / Ellipse / Pie 是硬边，在 1.25 倍缩放下看着全是台阶，
    这就是页面「发皱」的根源。这里只用 GDI+ 画形状，文字仍然走 GDI（ClearType 更清晰）。
    """

    def __init__(self):
        self.ok = False
        self.token = ctypes.c_size_t()
        try:
            d = ctypes.windll.gdiplus
        except Exception:
            return

        class SI(ctypes.Structure):
            _fields_ = [("v", ctypes.c_uint32), ("cb", ctypes.c_void_p),
                        ("bg", ctypes.c_int), ("codecs", ctypes.c_int)]

        si = SI()
        si.v = 1
        try:
            if d.GdiplusStartup(ctypes.byref(self.token), ctypes.byref(si), None) != 0:
                return
        except Exception:
            return

        P = ctypes.c_void_p
        F = ctypes.c_float

        class POINTF(ctypes.Structure):
            _fields_ = [("x", F), ("y", F)]

        class RECTF(ctypes.Structure):
            _fields_ = [("x", F), ("y", F), ("w", F), ("h", F)]

        self.POINTF, self.RECTF = POINTF, RECTF
        d.GdipCreateFromHDC.argtypes = [w.HDC, ctypes.POINTER(P)]
        d.GdipDeleteGraphics.argtypes = [P]
        d.GdipFlush.argtypes = [P, ctypes.c_int]
        d.GdipSetSmoothingMode.argtypes = [P, ctypes.c_int]
        d.GdipSetPixelOffsetMode.argtypes = [P, ctypes.c_int]
        d.GdipSetCompositingQuality.argtypes = [P, ctypes.c_int]
        d.GdipSetInterpolationMode.argtypes = [P, ctypes.c_int]
        d.GdipCreateSolidFill.argtypes = [ctypes.c_uint32, ctypes.POINTER(P)]
        d.GdipDeleteBrush.argtypes = [P]
        d.GdipCreatePen1.argtypes = [ctypes.c_uint32, F, ctypes.c_int, ctypes.POINTER(P)]
        d.GdipDeletePen.argtypes = [P]
        try:
            d.GdipSetPenAlignment.argtypes = [P, ctypes.c_int]
        except Exception:
            pass          # 部分 gdiplus 版本没导出这个
        d.GdipSetPenLineCap197819.argtypes = [P, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        d.GdipCreatePath.argtypes = [ctypes.c_int, ctypes.POINTER(P)]
        d.GdipDeletePath.argtypes = [P]
        d.GdipAddPathArc.argtypes = [P, F, F, F, F, F, F]
        d.GdipAddPathLine.argtypes = [P, F, F, F, F]
        d.GdipAddPathEllipse.argtypes = [P, F, F, F, F]
        d.GdipStartPathFigure.argtypes = [P]
        d.GdipClosePathFigure.argtypes = [P]
        d.GdipFillPath.argtypes = [P, P, P]
        d.GdipDrawPath.argtypes = [P, P, P]
        d.GdipDrawArc.argtypes = [P, P, F, F, F, F, F, F]
        d.GdipFillEllipse.argtypes = [P, P, F, F, F, F]
        d.GdipFillRectangle.argtypes = [P, P, F, F, F, F]
        d.GdipDrawLine.argtypes = [P, P, F, F, F, F]
        d.GdipCreateLineBrushFromRectWithAngle.argtypes = [
            ctypes.POINTER(RECTF), ctypes.c_uint32, ctypes.c_uint32, F, ctypes.c_int,
            ctypes.c_int, ctypes.POINTER(P)]
        self.dll = d
        self.ok = True

    # ---------------------------------------------------------------- 基础对象
    def graphics(self, hdc):
        if not self.ok:
            return None
        g = ctypes.c_void_p()
        if self.dll.GdipCreateFromHDC(hdc, ctypes.byref(g)) != 0:
            return None
        self.dll.GdipSetSmoothingMode(g, 4)        # AntiAlias
        self.dll.GdipSetPixelOffsetMode(g, 2)      # HighQuality
        self.dll.GdipSetCompositingQuality(g, 2)   # HighQuality
        return g

    def free(self, g):
        if g and self.ok:
            try:
                self.dll.GdipFlush(g, 1)
                self.dll.GdipDeleteGraphics(g)
            except Exception:
                pass

    def brush(self, color):
        b = ctypes.c_void_p()
        if self.dll.GdipCreateSolidFill(argb(color), ctypes.byref(b)) != 0:
            return None
        return b

    def grad_brush(self, l, t, r, b, c1, c2, angle=0.0):
        rc = self.RECTF(float(l), float(t), float(max(1, r - l)), float(max(1, b - t)))
        br = ctypes.c_void_p()
        # wrapMode 必须用 0(Tile)：传 4(Clamp) 对线性渐变画刷是非法参数，
        # 会导致创建失败、静默退化成纯色（以前所有"渐变"其实都是单色）
        if self.dll.GdipCreateLineBrushFromRectWithAngle(
                ctypes.byref(rc), argb(c1), argb(c2), ctypes.c_float(angle),
                1, 0, ctypes.byref(br)) != 0:
            return None
        return br

    def pen(self, color, width=1.0, cap=0, align=0):
        p = ctypes.c_void_p()
        if self.dll.GdipCreatePen1(argb(color), ctypes.c_float(width), 0,
                                   ctypes.byref(p)) != 0:
            return None
        if align and hasattr(self.dll, "GdipSetPenAlignment"):
            self.dll.GdipSetPenAlignment(p, align)
        if cap:
            self.dll.GdipSetPenLineCap197819(p, cap, cap, 0)
        return p

    def round_path(self, l, t, r, b, rad):
        rad = max(0.0, min(rad, (r - l) / 2.0, (b - t) / 2.0))
        p = ctypes.c_void_p()
        if self.dll.GdipCreatePath(0, ctypes.byref(p)) != 0:
            return None
        f = ctypes.c_float
        self.dll.GdipStartPathFigure(p)
        self.dll.GdipAddPathArc(p, f(l), f(t), f(rad * 2), f(rad * 2), f(180), f(90))
        self.dll.GdipAddPathArc(p, f(r - rad * 2), f(t), f(rad * 2), f(rad * 2), f(270), f(90))
        self.dll.GdipAddPathArc(p, f(r - rad * 2), f(b - rad * 2), f(rad * 2), f(rad * 2), f(0), f(90))
        self.dll.GdipAddPathArc(p, f(l), f(b - rad * 2), f(rad * 2), f(rad * 2), f(90), f(90))
        self.dll.GdipClosePathFigure(p)
        return p

    def top_round_path(self, l, t, r, b, rad):
        """只有上面两个圆角的路径（treemap 标题条用）"""
        rad = max(0.0, min(rad, (r - l) / 2.0, (b - t)))
        p = ctypes.c_void_p()
        if self.dll.GdipCreatePath(0, ctypes.byref(p)) != 0:
            return None
        f = ctypes.c_float
        self.dll.GdipStartPathFigure(p)
        self.dll.GdipAddPathArc(p, f(l), f(t), f(rad * 2), f(rad * 2), f(180), f(90))
        self.dll.GdipAddPathLine(p, f(l + rad), f(t), f(r - rad), f(t))
        self.dll.GdipAddPathArc(p, f(r - rad * 2), f(t), f(rad * 2), f(rad * 2), f(270), f(90))
        self.dll.GdipAddPathLine(p, f(r), f(t + rad), f(r), f(b))
        self.dll.GdipAddPathLine(p, f(r), f(b), f(l), f(b))
        self.dll.GdipAddPathLine(p, f(l), f(b), f(l), f(t + rad))
        self.dll.GdipClosePathFigure(p)
        return p

    def del_path(self, p):
        if p:
            self.dll.GdipDeletePath(p)
    def del_brush(self, b):
        if b:
            self.dll.GdipDeleteBrush(b)

    def del_pen(self, p):
        if p:
            self.dll.GdipDeletePen(p)


GP = _GdiPlus()


def argb(c, a=255):
    """COLORREF -> GDI+ ARGB"""
    r = c & 0xFF
    g = (c >> 8) & 0xFF
    b = (c >> 16) & 0xFF
    return (a << 24) | (r << 16) | (g << 8) | b


class Theme:
    bg = rgb("f3f5f9")
    card = rgb("ffffff")
    line = rgb("e5e9f1")
    line_soft = rgb("eef2f8")
    text = rgb("1c2330")
    ink2 = rgb("475467")
    muted = rgb("697180")
    faint = rgb("8a94a6")
    accent = rgb("2f6fed")
    accent_soft = rgb("ecf2ff")
    accent_dark = rgb("1f56c9")
    green = rgb("0f9d6e")
    amber = rgb("c2740a")
    danger = rgb("d92d20")
    purple = rgb("7c5cf0")
    hover = rgb("f3f7ff")
    head = rgb("f8fafc")
    zebra = rgb("f8fafd")
    field = rgb("f5f7fb")


# 首页表格斑马纹：比 Theme.zebra 略明显一档，隔行看得清但不抢内容
ZEBRA = rgb("f5f8fe")
# 环形图的蓝色梯度（由深到浅），保证同一色系不花
BLUE_SCALE = ["3f78e8", "5b93f0", "7aabf5", "9cc2fb", "bedbfd", "d9e9fe",
              "8ea6c8"]


PALETTE = ["2f6fed", "e0567a", "f2a33c", "0f9d6e", "7c5cf0", "0e9aa7",
           "3f7ce0", "c94f4f", "37a169", "8a6cd6", "4fa3d1", "d9822b"]
AGE_COLORS = ["3f6fd0", "5c8ae0", "7aa3ea", "96b9f0", "b2cbf5"]
AGE_LABELS = ["近30天", "1年内", "1-2年", "2-3年", "3年以上"]

# 文件类型：图标字 + 颜色 + 扩展名
_KIND_DEF = [
    ("pdf", "P", "d92d20", "pdf"),
    ("video", "视", "7c5cf0", "mp4 mkv avi mov wmv flv rmvb webm m4v mpg mpeg ts m2ts "
                              "3gp f4v rm asf"),
    ("image", "图", "0f9d6e", "jpg jpeg png gif bmp webp svg ico tif tiff heic raw psd "
                              "ai eps jfif"),
    ("audio", "音", "e0567a", "mp3 wav flac aac ogg m4a wma ape mid midi amr aiff"),
    ("doc", "文", "2f6fed", "doc docx xls xlsx ppt pptx txt md csv rtf wps et dps odt "
                            "ods odp tsv"),
    ("zip", "压", "c2740a", "zip rar 7z tar gz bz2 xz iso cab jar war apk gzip tgz"),
    ("prog", "程", "475467", "exe msi dll sys bat cmd ps1 lnk com scr ocx swf"),
    ("code", "码", "0e9aa7", "py js ts jsx tsx html htm css scss less json xml java c "
                             "cpp h hpp cs go rs sql sh bash yml yaml php rb vue lua "
                             "toml ini conf log ipynb"),
]

KIND_MAP = {}
for _k, _g, _c, _exts in _KIND_DEF:
    for _e in _exts.split():
        KIND_MAP[_e] = (_g, rgb(_c))

TYPE_OPTS = [("all", "全部类型"), ("video", "视频"), ("image", "图片"),
             ("doc", "文档"), ("audio", "音频"), ("zip", "压缩包"),
             ("prog", "程序"), ("code", "代码"), ("pdf", "PDF"), ("other", "其他")]

TYPE_EXT = {}
for _k, _g, _c, _exts in _KIND_DEF:
    TYPE_EXT[_k] = ",".join(_exts.split())

# 首页「空间分布」用的大类名称与配色（与图标体系一致）
KIND_LABELS = {"doc": "文档", "image": "图片", "video": "视频", "audio": "音频",
               "zip": "压缩包", "prog": "程序", "code": "代码", "pdf": "PDF"}
KIND_COLORS = {_k: rgb(_c) for _k, _g, _c, _exts in _KIND_DEF}
KIND_COLORS["其他"] = rgb("c3ccdb")
EXT2KIND = {}
for _k, _g, _c, _exts in _KIND_DEF:
    for _e in _exts.split():
        EXT2KIND[_e] = _k

QUICKS = [("only_drive", "仅本盘"), ("recent", "近一年"),
          ("big", "≥100MB"), ("dirs_only", "仅文件夹")]


def kind_of(ext):
    """扩展名 -> (图标字, 颜色)。未知类型用首字母，避免出现空白方块。"""
    e = (ext or "").lower()
    v = KIND_MAP.get(e)
    if v:
        return v
    if e:
        return (e[:1].upper(), rgb("9aa4b5"))
    return ("·", rgb("9aa4b5"))

# ------------------------------------------------------------------ 工具函数
FL = 0x00  # DrawText 标志
DT_LEFT = 0x0000
DT_CENTER = 0x0001
DT_RIGHT = 0x0002
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_END_ELLIPSIS = 0x8000
DT_NOCLIP = 0x0100
DT_PATH_ELLIPSIS = 0x4000


def fmt(b):
    b = float(b or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or u == "TB":
            return ("%.0f %s" % (b, u)) if u in ("B", "KB") else ("%.1f %s" % (b, u))
        b /= 1024.0
    return "%.1f TB" % b


def fmt_t(t):
    try:
        t = int(t or 0)
        if t <= 0:
            return "—"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except Exception:
        return "—"


def fmt_n(n):
    try:
        return "{:,}".format(int(n))
    except Exception:
        return str(n)


def now_str():
    return time.strftime("%H:%M:%S")


class App:
    # -------------------------------------------------------------- 生命周期
    def __init__(self):
        self.scale = 1.0
        self.hwnd = None
        self.fonts = {}
        self._buf = None
        self._bufsize = None
        self.hot = []
        self.hover_key = None
        self.press_key = None
        self.sheet = "overview"
        self.drives = []
        self.active = None
        self.data = None
        self.tree = None
        self.tm_path = []
        self.pwd = ""
        self.dir_cache = None
        self.dir_busy = False
        self.scroll = {"dirs": 0, "big": 0, "drv": 0}
        self.sel = set()
        self.job = None
        self.report = None
        self.toast_msg = None
        self.toast_until = 0
        # 「复制到聊天软件」：选中项里含文件夹时先问一句，再决定打包还是直传
        self._copy_pending = None
        self._pack_stop = False
        self._pack_res = None
        self._pack_prog = {}
        # 居中弹窗（提示 / 进度）：kind=info|progress|ok|error
        self.dlg = None
        self.dlg_drag = None        # 拖动中：(dx, dy)
        self.dlg_hover = None
        self.dlg_next = None        # 进度弹窗期间来的提示，等进度框关掉再显示
        self._booting = True        # 启动阶段不弹加载框（首屏自己会很快）
        self._dlg_seq = 0
        self.dirs_q = ""
        self.dirs_recursive = False
        self.gq = ""
        self.grows = []
        self.gtype = "all"          # 搜索页的文件类型过滤
        self.gtype_open = False
        self.drv_open = False       # 标题栏盘符下拉是否展开
        self._fixing = False
        self.drv_btn_rect = None
        self.gquick = set()         # 搜索页快捷过滤：only_drive / recent / big / files_only
        self.datas = {}
        self._sizemaps = {}
        self.insights = None          # 首页仪表盘聚合数据（今日增量 / 趋势 / 重复 / 长期未访问）
        self._insight_busy = False
        self._size_extra = {}
        self._treemap = None
        self._treemap_data = None
        self.checked = {"dirs": set(), "big": set(), "search": set()}
        self.editing = None      # 'dirs' 时表示输入框焦点
        self.path_buf = ""       # 顶栏路径框正在输入的内容
        self.path_fresh = False
        self.caret_t = 0
        self.fullscreen = False
        self.saved_style = None
        self.sort = {"dirs": ["n", 0], "big": ["s", -1], "search": ["n", 0]}
        self.tm_rect_cache = []
        self.repaint_pending = False
        self.cursor_cur = 0
        self._g = None

    # -------------------------------------------------------------- 绘图原语
    def f(self, px, bold=False, face=None):
        """按逻辑像素取字体句柄（缓存）"""
        size = max(9, int(round(px * self.scale)))
        key = (size, bold, face)
        h = self.fonts.get(key)
        if h:
            return h
        face = face or "Microsoft YaHei UI"
        h = gdi.CreateFontW(-size, 0, 0, 0, 700 if bold else 400,
                            0, 0, 0, 1, 0, 0, 5, 0, face)
        self.fonts[key] = h
        return h

    def use_font(self, hdc, px, bold=False, face=None):
        return gdi.SelectObject(hdc, ctypes.c_void_p(self.f(px, bold, face)))

    def fill(self, hdc, l, t, r, b, color):
        if r <= l or b <= t:
            return
        gdi.SetDCBrushColor(hdc, color)
        rc = w.RECT(int(l), int(t), int(r), int(b))
        u32.FillRect(hdc, ctypes.byref(rc), ctypes.c_void_p(gdi.GetStockObject(DC_BRUSH)))

    def rrect(self, hdc, l, t, r, b, rad, fill_color, stroke=None, sw=1):
        l, t, r, b = int(round(l)), int(round(t)), int(round(r)), int(round(b))
        if r <= l or b <= t:
            return
        rad = max(0, min(rad, (r - l) / 2.0, (b - t) / 2.0))
        g = self._g
        if g is not None and rad > 0.5:
            p = GP.round_path(l, t, r, b, rad)
            br = GP.brush(fill_color)
            if p and br:
                GP.dll.GdipFillPath(g, br, p)
            GP.del_brush(br)
            if stroke is not None:
                pn = GP.pen(stroke, sw * self.scale, 0, 1)
                if p and pn:
                    GP.dll.GdipDrawPath(g, pn, p)
                GP.del_pen(pn)
            GP.del_path(p)
            return
        gdi.SetDCBrushColor(hdc, fill_color)
        gdi.SelectObject(hdc, ctypes.c_void_p(gdi.GetStockObject(DC_BRUSH)))
        if stroke is None:
            gdi.SelectObject(hdc, ctypes.c_void_p(gdi.GetStockObject(NULL_PEN)))
        else:
            gdi.SelectObject(hdc, ctypes.c_void_p(gdi.GetStockObject(DC_PEN)))
            gdi.SetDCPenColor(hdc, stroke)
        gdi.RoundRect(hdc, int(l), int(t), int(r), int(b), int(rad * 2), int(rad * 2))

    def rrect_a(self, hdc, l, t, r, b, rad, color, alpha):
        """半透明圆角矩形（投影用，必须走 alpha，不能拿不透明色冒充阴影）"""
        l, t, r, b = int(round(l)), int(round(t)), int(round(r)), int(round(b))
        if r <= l or b <= t or alpha <= 0:
            return
        rad = max(0, min(rad, (r - l) / 2.0, (b - t) / 2.0))
        g = self._g
        a = max(0, min(255, int(round(alpha))))
        if g is None or rad <= 0.5:
            self.fill(hdc, l, t, r, b, mix(Theme.bg, color, a / 255.0))
            return
        p = GP.round_path(l, t, r, b, rad)
        br = ctypes.c_void_p()
        if GP.dll.GdipCreateSolidFill(argb(color, a), ctypes.byref(br)) == 0 and br:
            if p:
                GP.dll.GdipFillPath(g, br, p)
            GP.del_brush(br)
        GP.del_path(p)

    def rrect_grad(self, hdc, l, t, r, b, rad, c1, c2, horizontal=True):
        """圆角矩形 + 线性渐变填充（进度条用）"""
        l, t, r, b = int(round(l)), int(round(t)), int(round(r)), int(round(b))
        if r <= l or b <= t:
            return
        rad = max(0, min(rad, (r - l) / 2.0, (b - t) / 2.0))
        g = self._g
        if g is None:
            self.rrect(hdc, l, t, r, b, rad, c1)
            return
        p = GP.round_path(l, t, r, b, rad)
        br = GP.grad_brush(l, t, r, b, c1, c2, 0.0 if horizontal else 90.0)
        if not br:
            br = GP.brush(c1)
        if p and br:
            GP.dll.GdipFillPath(g, br, p)
        GP.del_brush(br)
        GP.del_path(p)

    def rect_top(self, hdc, l, t, r, b, rad, color):
        g = self._g
        if g is None:
            self.fill(hdc, l, t, r, b, color)
            return
        p = GP.top_round_path(l, t, r, b, rad)
        br = GP.brush(color)
        if p and br:
            GP.dll.GdipFillPath(g, br, p)
        GP.del_brush(br)
        GP.del_path(p)

    def circle(self, hdc, cx, cy, rad, fill_color, stroke=None):
        g = self._g
        if g is not None:
            br = GP.brush(fill_color)
            if br:
                GP.dll.GdipFillEllipse(g, br, ctypes.c_float(cx - rad),
                                       ctypes.c_float(cy - rad),
                                       ctypes.c_float(rad * 2),
                                       ctypes.c_float(rad * 2))
            GP.del_brush(br)
            if stroke is not None:
                pn = GP.pen(stroke, self.scale, 0, 1)
                if pn:
                    GP.dll.GdipDrawArc(g, pn, ctypes.c_float(cx - rad),
                                       ctypes.c_float(cy - rad),
                                       ctypes.c_float(rad * 2), ctypes.c_float(rad * 2),
                                       ctypes.c_float(0), ctypes.c_float(360))
                GP.del_pen(pn)
            return
        gdi.SetDCBrushColor(hdc, fill_color)
        gdi.SelectObject(hdc, ctypes.c_void_p(gdi.GetStockObject(DC_BRUSH)))
        if stroke is None:
            gdi.SelectObject(hdc, ctypes.c_void_p(gdi.GetStockObject(NULL_PEN)))
        else:
            gdi.SelectObject(hdc, ctypes.c_void_p(gdi.GetStockObject(DC_PEN)))
            gdi.SetDCPenColor(hdc, stroke)
        gdi.Ellipse(hdc, int(cx - rad), int(cy - rad), int(cx + rad), int(cy + rad))

    def arc(self, hdc, cx, cy, rad, thick, start, sweep, color, rounded=False,
            cap=None, ry=None):
        """粗圆弧（甜甜圈的一段），GDI+ 抗锯齿

        cap: 0=平头(截止在端点) 1=方头(向外延伸 th/2 后切平) 2=圆头
        ry:  纵半径，给了就是椭圆弧（rad 变横半径），角度按椭圆参数角算
        """
        g = self._g
        if g is None:
            return
        c = cap if cap is not None else (2 if rounded else 0)
        rx = rad
        ry = rad if ry is None else ry
        pn = GP.pen(color, thick, c, 0)
        if not pn:
            return
        GP.dll.GdipDrawArc(g, pn, ctypes.c_float(cx - rx), ctypes.c_float(cy - ry),
                           ctypes.c_float(rx * 2), ctypes.c_float(ry * 2),
                           ctypes.c_float(start), ctypes.c_float(sweep))
        GP.del_pen(pn)

    def line(self, hdc, x1, y1, x2, y2, color, width=1):
        gdi.SelectObject(hdc, ctypes.c_void_p(gdi.GetStockObject(DC_PEN)))
        gdi.SetDCPenColor(hdc, color)
        gdi.MoveToEx(hdc, int(x1), int(y1), None)
        gdi.LineTo(hdc, int(x2), int(y2))

    def hline(self, hdc, l, r, y, color):
        self.fill(hdc, l, y, r, y + max(1, int(self.scale)), color)

    def seg(self, hdc, x1, y1, x2, y2, color, width=1.0):
        """抗锯齿直线（标题栏图标用）"""
        g = self._g
        if g is not None:
            pn = GP.pen(color, max(1.0, width * self.scale), 0, 1)
            if pn:
                GP.dll.GdipDrawLine(g, pn, ctypes.c_float(x1), ctypes.c_float(y1),
                                    ctypes.c_float(x2), ctypes.c_float(y2))
                GP.del_pen(pn)
                return
        self.line(hdc, x1, y1, x2, y2, color)

    def seg_box(self, hdc, l, t, r, b, color, width=1.0):
        self.seg(hdc, l, t, r, t, color, width)
        self.seg(hdc, r, t, r, b, color, width)
        self.seg(hdc, r, b, l, b, color, width)
        self.seg(hdc, l, b, l, t, color, width)

    def circle_line(self, hdc, cx, cy, rad, color, width=1.0):
        g = self._g
        if g is not None:
            pn = GP.pen(color, max(1.0, width * self.scale), 0, 1)
            if pn:
                GP.dll.GdipDrawArc(g, pn, ctypes.c_float(cx - rad),
                                   ctypes.c_float(cy - rad),
                                   ctypes.c_float(rad * 2), ctypes.c_float(rad * 2),
                                   ctypes.c_float(0), ctypes.c_float(360))
                GP.del_pen(pn)
                return
        self.line(hdc, cx - rad, cy, cx + rad, cy, color)

    def text(self, hdc, s, l, t, r, b, px=12.5, color=None, bold=False,
             align=DT_LEFT, face=None, ellipsis=True, vcenter=True):
        if s is None:
            s = ""
        old = self.use_font(hdc, px, bold, face)
        gdi.SetBkMode(hdc, 1)
        gdi.SetTextColor(hdc, color if color is not None else Theme.text)
        flags = DT_SINGLELINE | (DT_VCENTER if vcenter else 0)
        flags |= {DT_LEFT: DT_LEFT, DT_CENTER: DT_CENTER, DT_RIGHT: DT_RIGHT}.get(align, DT_LEFT)
        if ellipsis:
            flags |= DT_END_ELLIPSIS
        rc = w.RECT(int(l), int(t), int(r), int(b))
        u32.DrawTextW(hdc, str(s), -1, ctypes.byref(rc), flags)
        gdi.SelectObject(hdc, ctypes.c_void_p(old))

    def tw(self, hdc, s, px=12.5, bold=False, face=None):
        old = self.use_font(hdc, px, bold, face)
        sz = w.SIZE()
        gdi.GetTextExtentPoint32W(hdc, str(s), len(str(s)), ctypes.byref(sz))
        gdi.SelectObject(hdc, ctypes.c_void_p(old))
        return sz.cx

    def clip(self, hdc, l, t, r, b):
        return gdi.IntersectClipRect(hdc, int(l), int(t), int(r), int(b))

    def save(self, hdc):
        return gdi.SaveDC(hdc)

    def restore(self, hdc, n):
        gdi.RestoreDC(hdc, n)

    # -------------------------------------------------------------- 热点
    def add_hot(self, l, t, r, b, act, arg=None, tip=None, cur=None, key=None):
        k = key or ("%s|%s|%s" % (act, arg, l))
        self.hot.append((int(l), int(t), int(r), int(b), act, arg, tip, cur, k))
        return k

    def hit(self, x, y):
        for h in reversed(self.hot):
            if h[0] <= x <= h[2] and h[1] <= y <= h[3]:
                return h
        return None

    # -------------------------------------------------------------- 尺寸
    def client(self):
        rc = w.RECT()
        u32.GetClientRect(self.hwnd, ctypes.byref(rc))
        return rc.right, rc.bottom

    def sc(self, v):
        return v * self.scale

    # -------------------------------------------------------------- 数据
    def refresh_drives(self):
        try:
            self.drives = S.list_drives()
        except Exception:
            self.drives = []

    def chips(self):
        total = sum(d.get("total", 0) for d in self.drives)
        used = sum(d.get("used", 0) for d in self.drives)
        pct = int(used / total * 100) if total else 0
        out = [{"letter": "ALL", "pct": pct, "total": total, "used": used,
                "free": total - used, "label": "全部盘符"}]
        for d in self.drives:
            out.append(d)
        return out

    def boot(self):
        self.refresh_drives()
        # 盘符热插拔：2 秒轮询一次位图（一次系统调用，开销可忽略）
        self._drv_mask = self._logical_mask()
        u32.SetTimer(self.hwnd, 3, 2000, None)
        pick = None
        for d in self.drives:
            if d["letter"] == "C:":
                pick = "C:"
        for d in self.drives:
            if d.get("cached"):
                pick = d["letter"]
                break
        self.active = pick or (self.drives[0]["letter"] if self.drives else "C:")
        self.load_drive(self.active, scan_if_empty=True)
        threading.Thread(target=self._load_report, daemon=True).start()
        threading.Thread(target=self._load_insights, daemon=True).start()
        threading.Thread(target=self._auto_index, daemon=True).start()

    def _auto_index(self):
        """首次启动静默建立全盘文件名索引（约几秒延迟，避免和首屏抢 IO）。"""
        time.sleep(8)
        try:
            st = self.index_state()
            if st.get("status") == "ready":
                # 复用了上次的索引库 → 直接把仪表盘数据补上
                self._load_insights()
                return
            if st.get("status") != "idle":
                return
            S.get_index().start(S.default_scan_roots())
            self.dlg_show("正在建立文件索引",
                          "首次运行会扫一遍全盘文件名，之后按类型 / 后缀搜索就是毫秒级。"
                          "索引期间照常使用，点「后台运行」可以收起这个窗口。",
                          kind="progress", pct=None, sub="正在准备…", tag="index",
                          action="index_hide", soft=True)
            u32.SetTimer(self.hwnd, 2, 800, None)
        except Exception:
            pass

    def _load_report(self):
        try:
            self.report = S.all_drives_report()
        except Exception:
            self.report = None
        self.invalidate()

    def _load_insights(self):
        """后台算首页仪表盘的聚合数据（走索引，几十万行聚合约 0.5 秒）。"""
        if self._insight_busy:
            return
        self._insight_busy = True
        try:
            self.index_state()      # 先触发索引复用检测，ready 之后才有数据
            self.insights = S.get_index().stats_extra()
        except Exception:
            self.insights = None
        self._insight_busy = False
        self.invalidate()

    def busy_show(self, title, msg):
        """同步耗时操作前先落一帧带进度条的弹窗，避免界面像卡死"""
        self.dlg_show(title, msg, kind="progress", pct=None, sub="", tag="busy")
        try:
            u32.UpdateWindow(self.hwnd)
        except Exception:
            pass

    def busy_done(self):
        self.dlg_close("busy")

    def load_drive(self, letter, scan_if_empty=False):
        self.active = letter
        self.editing = None
        self.dir_cache = None
        self.scroll["dirs"] = 0
        self.scroll["big"] = 0
        if letter == "ALL":
            self.pwd = ""
            data = self._merge_all()
            self.data = data
            self.tree = (data or {}).get("root")
            self.tm_path = [self.tree] if self.tree else []
        else:
            self.pwd = letter + "\\"
            data = None
            need_read = (letter not in self.datas)
            big = False
            if need_read:
                try:
                    cp = S.cache_path(S.cache_key(letter + "\\"))
                    if isinstance(cp, (tuple, list)):
                        cp = cp[0]
                    big = os.path.exists(cp) and os.path.getsize(cp) > 6 * 1024 * 1024
                except Exception:
                    big = False
            if big and not self._booting:
                self.busy_show("正在加载 %s 盘缓存" % letter,
                               "读取上一次扫描的结果，第一次切到这个盘会慢一点。")
            try:
                data = S.load_cache(S.cache_key(letter + "\\"))
            except Exception:
                data = None
            finally:
                if big and not self._booting:
                    self.busy_done()
            self.datas[letter] = data
            if data:
                self.data = data
                self.tree = data.get("root")
                self.tm_path = [self.tree] if self.tree else []
            else:
                self.data = None
                self.tree = None
                self.tm_path = []
                if scan_if_empty:
                    self.start_scan(letter)
        self.reload_dir()
        self.invalidate()

    def _merge_all(self):
        """把所有已缓存的盘合成一份数据，「全部」视图用。"""
        ext, big, roots = {}, [], []
        ages = [0, 0, 0, 0, 0]
        files = dirs = 0
        mn = 0
        missing = 0
        for d in self.drives:
            letter = d["letter"]
            data = self.datas.get(letter)
            if data is None:
                try:
                    data = S.load_cache(S.cache_key(letter + "\\"))
                except Exception:
                    data = None
                self.datas[letter] = data
            if not data:
                missing += 1
                roots.append({"n": d["letter"] + "（未扫描）", "p": d["path"],
                              "s": d.get("used", 0), "f": 0, "d": 0, "m": 0,
                              "unscanned": d["letter"]})
                continue
            files += data.get("files", 0)
            dirs += data.get("dirs", 0)
            for e in (data.get("ext") or []):
                ext[e[0]] = ext.get(e[0], 0) + e[1]
            ag = data.get("ages") or []
            for i in range(min(5, len(ag))):
                ages[i] += ag[i]
            big.extend(data.get("big_files") or [])
            r = data.get("root")
            if r:
                roots.append(r)
            mn = mn or data.get("min_file") or 0
        big.sort(key=lambda f: -f.get("s", 0))
        out = {"files": files, "dirs": dirs, "min_file": mn, "ages": ages,
               "big_files": big[:4000], "missing": missing,
               "ext": sorted(ext.items(), key=lambda kv: -kv[1])}
        if roots:
            out["root"] = {"n": "全部", "p": "", "m": max([r.get("m", 0) for r in roots] or [0]),
                           "s": sum(r.get("s", 0) for r in roots),
                           "f": sum(r.get("f", 0) for r in roots),
                           "d": sum(r.get("d", 0) for r in roots) + len(roots),
                           "c": roots}
        return out


    # -------------------------------------------------------------- 路径 / 范围
    def _tree_map(self):
        """路径 -> 节点 的扁平索引（按 data 对象缓存，避免每行都 DFS）。"""
        d = self.data
        if self._treemap is not None and self._treemap_data is d:
            return self._treemap
        mp = {}
        root = (d or {}).get("root")
        if root:
            stack = [root]
            while stack:
                nd = stack.pop()
                p = nd.get("p") or ""
                if p and not nd.get("agg"):
                    mp[p.rstrip("\\").upper()] = nd
                stack.extend(nd.get("c") or [])
        self._treemap = mp
        self._treemap_data = d
        return mp

    def _node_by_path(self, p):
        """在已缓存的扫描树里按绝对路径找节点（找不到返回 None）。"""
        if not p:
            return (self.data or {}).get("root")
        return self._tree_map().get(os.path.abspath(p).rstrip("\\").upper())

    def _scope_chain(self, p):
        """从根往下钻到 p，返回面包屑节点链。"""
        root = (self.data or {}).get("root")
        if not root:
            return []
        if not p:
            return [root]
        tgt = os.path.abspath(p).rstrip("\\").upper()
        chain, nd = [], root
        while nd is not None:
            chain.append(nd)
            if (nd.get("p") or "").rstrip("\\").upper() == tgt:
                return chain
            nxt = None
            for ch in (nd.get("c") or []):
                cp = (ch.get("p") or "").rstrip("\\").upper()
                if cp and (cp == tgt or tgt.startswith(cp + "\\")):
                    nxt = ch
                    break
            nd = nxt
        return [root]

    def _size_map(self, letter):
        """某盘的 目录路径 -> 占用 轻量映射（不保留整棵树）。"""
        mp = self._sizemaps.get(letter)
        if mp is not None:
            return mp
        data = self.datas.get(letter)
        if not data:
            try:
                data = S.load_cache(S.cache_key(letter + "\\"))
            except Exception:
                data = None
        out = {}
        root = (data or {}).get("root")
        if root:
            stack = [root]
            while stack:
                nd = stack.pop()
                p = nd.get("p") or ""
                if p and not nd.get("agg"):
                    out[p.rstrip("\\").upper()] = nd.get("s", 0)
                stack.extend(nd.get("c") or [])
        self._sizemaps[letter] = out
        return out

    def _dir_size(self, p):
        """目录占用：扫描树 -> 后台补算结果 -> 该盘轻量映射；未知返回 None。"""
        nd = self._node_by_path(p)
        if nd is not None:
            return nd.get("s", 0) or 0
        key = os.path.abspath(p).rstrip("\\").upper()
        v = self._size_extra.get(key)
        if v is not None:
            return v
        letter = (p or "")[:2].upper()
        mp = self._size_map(letter) if letter.endswith(":") else {}
        return mp.get(key)

    def _fill_sizes_bg(self, paths):
        """后台补齐缓存里被裁剪掉的小目录大小（不阻塞绘制）。"""
        budget = [260000, time.time() + 5.0]
        got = {}
        for p in paths:
            if time.time() > budget[1] or budget[0] <= 0:
                break
            v = self._walk_size(p, budget)
            if v is not None:
                got[os.path.abspath(p).rstrip("\\").upper()] = v
        if got:
            self._size_extra.update(got)
            self.invalidate()

    def _walk_size(self, path, budget):
        total = 0
        stack = [path]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        budget[0] -= 1
                        if budget[0] <= 0:
                            return None
                        try:
                            if e.is_dir(follow_symlinks=False):
                                st = e.stat(follow_symlinks=False)
                                if getattr(st, "st_file_attributes", 0) & 0x400:
                                    continue
                                stack.append(e.path)
                            else:
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        return total

    def scope(self):
        """当前路径范围 -> (node, size, files, dirs)。"""
        d = self.data or {}
        if not self.pwd:
            r = d.get("root")
            return (r, (r or {}).get("s", 0) or d.get("total", 0),
                    d.get("files", 0), d.get("dirs", 0))
        nd = self._node_by_path(self.pwd)
        if nd is not None:
            return nd, nd.get("s", 0) or 0, nd.get("f", 0) or 0, nd.get("d", 0) or 0
        return None, 0, 0, 0

    def set_scope(self, txt):
        """顶栏路径框：解析用户输入，切换盘 / 目录，所有页面跟着过滤。"""
        t = (txt or "").strip().strip('"').strip()
        if not t or t.upper() in ("ALL", "*", "全部", "全部盘符", "全部盘"):
            self.load_drive("ALL")
            self.toast("已切换到全部盘符")
            return
        t = t.replace("/", "\\")
        if len(t) == 2 and t[1] == ":":
            t += "\\"
        if not os.path.isdir(t):
            self.toast("路径不存在：%s" % t)
            return
        ap = os.path.abspath(t)
        letter = ap[:2].upper()
        if letter != self.active:
            data = self.datas.get(letter)
            if data is None:
                try:
                    data = S.load_cache(S.cache_key(letter + "\\"))
                except Exception:
                    data = None
                self.datas[letter] = data
            self.active = letter
            self.data = data
            self.tree = (data or {}).get("root")
            self.tm_path = [self.tree] if self.tree else []
            if not data:
                self.toast("%s 还没有扫描缓存，先点右上角「扫描」" % letter)
        self.pwd = ap if ap.endswith("\\") else ap + "\\"
        self.dirs_q = ""
        self.dirs_recursive = False
        self.editing = None
        self.scroll["dirs"] = 0
        self.scroll["big"] = 0
        chain = self._scope_chain(self.pwd)
        if chain:
            self.tm_path = chain
        self.reload_dir()
        self.invalidate()

    def start_scan(self, letter=None):
        letter = letter or self.active
        if letter == "ALL":
            pend = [d["letter"] for d in self.drives if not self.datas.get(d["letter"])]
            if not pend:
                self.toast("所有盘都有扫描缓存了")
                return
            letter = pend[0]
        if self.job and self.job.state == "running":
            self.toast("已有扫描在进行中")
            return
        try:
            job = S.ScanJob(letter + "\\", 24 * 1024 * 1024, 50 * 1024 * 1024, 300)
            job.cache_key = letter + "\\"   # 交给 cache_key() 规范化成 "D"
            job.start()
            self.job = job
            u32.SetTimer(self.hwnd, 1, 300, None)
            self.dlg_show("正在扫描 %s 盘" % letter,
                          "正在遍历文件与目录。过程中可以点「后台运行」继续浏览，"
                          "图表与统计会在扫完之后一起刷新。",
                          kind="progress", pct=0.0, sub="正在准备…", tag="scan",
                          action="scan_cancel")
        except Exception as e:
            self.toast("扫描失败：%s" % e, kind="error", title="扫描启动失败",
                       auto=0)
        self.invalidate()

    def _index_dlg_sync(self, st):
        """把建索引进度同步到弹窗（目录数没有总数，所以用流动条 + 实数）"""
        if not self.dlg_alive("index"):
            return
        el = st.get("elapsed")
        if not el:
            el = time.time() - (st.get("started") or time.time())
        el = int(el)
        dur = ("%d 秒" % el) if el < 60 else ("%d 分 %d 秒" % (el // 60, el % 60))
        sub = "已扫 %s 个目录 · 收录 %s 个文件 · 已用 %s" % (
            fmt_n(st.get("scanned") or 0), fmt_n(st.get("files") or 0), dur)
        self.dlg_update("index", pct=None, sub=sub,
                        msg=str(st.get("phase") or "正在遍历全盘文件名…"))

    def _scan_dlg_sync(self, job):
        """把扫描进度同步到弹窗（体积估算百分比 + 文件/目录/用时）"""
        if not self.dlg_alive("scan"):
            return
        done = 0
        try:
            for v in (getattr(job, "_ext", None) or {}).values():
                done += v[0]
        except Exception:
            done = 0
        ltr = (getattr(job, "cache_key", "") or job.path or "C:")[:2].upper()
        used = 0
        for d in self.drives:
            if d["letter"].upper() == ltr:
                used = d.get("used") or 0
                break
        pct = None
        if used > 0:
            pct = max(0.0, min(0.99, done / float(used)))
        el = time.time() - job.t0
        if el < 60:
            dur = "%d 秒" % el
        else:
            dur = "%d 分 %d 秒" % (el // 60, el % 60)
        sub = "已扫描 %s 个文件 · %s 个目录 · 已用 %s" % (
            fmt_n(job.files), fmt_n(job.dirs), dur)
        self.dlg_update("scan", pct=pct, sub=sub,
                        msg="正在读取 %s 盘的文件清单…" % ltr)

    def reload_dir(self):
        if self.active == "ALL":
            rows = []
            for d in self.drives:
                rows.append({"n": d["letter"], "p": d["path"], "d": 1,
                             "s": d.get("used", 0), "m": 0,
                             "rel": d.get("label", ""), "drv": d["letter"]})
            self.dir_cache = {"dirs": rows, "files": [], "truncated": False}
            self.scroll["dirs"] = 0
            return
        if not self.pwd:
            return
        self.dir_busy = True
        try:
            res = S.list_dir(self.pwd, self.dirs_q, self.dirs_recursive)
            self.dir_cache = res
        except Exception:
            self.dir_cache = None
        self.dir_busy = False
        self.scroll["dirs"] = 0
        self._kick_size_fill()

    def _kick_size_fill(self):
        """列目录后，为「缓存里没有的」子目录起一个后台补算任务。"""
        if self.dir_busy or not self.dir_cache:
            return
        pend = []
        for d in (self.dir_cache.get("dirs") or []):
            if d.get("drv"):
                continue
            key = os.path.abspath(d["p"]).rstrip("\\").upper()
            if self._node_by_path(d["p"]) is None and key not in self._size_extra:
                pend.append(d["p"])
            if len(pend) >= 200:
                break
        if not pend:
            return

        def run(paths):
            time.sleep(0.15)
            try:
                self._fill_sizes_bg(paths)
            except Exception:
                pass

        threading.Thread(target=run, args=(pend,), daemon=True).start()

    # -------------------------------------------------------------- 居中弹窗
    def dlg_show(self, title, msg, kind="info", pct=None, sub="", auto=0.0,
                 tag=None, action=None, soft=False, alt=None, label=None):
        """打开居中弹窗（替代原来底部一闪而过的黑条）。

        kind   info | progress | ok | error
        pct    0~1；None 表示进度未知，画流动条
        auto   自动关闭秒数（0 = 常驻，等用户点掉）
        tag    同一 tag 的弹窗会被后续更新复用（扫描 / 索引进度）
        action 主按钮动作：scan_cancel / pack_cancel / copy_pack / None(=知道了)
        soft   True 时不阻隔壁后的点击（自动建索引那种后台任务用）
        alt    次级按钮 (文字, 动作)，如 ("直接复制文件夹", "copy_raw")
        label  主按钮文字（默认「知道了」）
        """
        cur = self.dlg
        if (cur and cur.get("kind") == "progress" and kind != "progress"
                and tag is None):
            # 正在跑进度时别被零散提示顶掉，先存着，等进度框收起来再弹
            self.dlg_next = {"title": title, "msg": msg, "kind": kind,
                             "auto": auto or 2.8}
            return None
        self._dlg_seq += 1
        self.dlg = {"id": self._dlg_seq, "title": title, "msg": msg, "sub": sub,
                    "kind": kind, "pct": pct, "auto": auto, "tag": tag,
                    "action": action, "soft": bool(soft), "alt": alt,
                    "label": label,
                    "x": None, "y": None, "t0": time.time(), "_g": None}
        self.dlg_drag = None
        # 动画 / 到点自动关闭都需要重绘：进度框转圈要快一点，静态提示放慢
        u32.SetTimer(self.hwnd, 4, 160 if kind == "progress" else 280, None)
        self.invalidate()
        return self.dlg

    def dlg_update(self, tag=None, **kw):
        d = self.dlg
        if not d or (tag is not None and d.get("tag") != tag):
            return False
        d.update(kw)
        d["t0"] = time.time()      # 自动关闭从这次更新重新计时，否则结束态会一闪而过
        u32.SetTimer(self.hwnd, 4, 160 if d.get("kind") == "progress" else 280, None)
        self.invalidate()
        return True

    def dlg_close(self, tag=None):
        d = self.dlg
        if not d:
            return
        if tag is not None and d.get("tag") != tag:
            return
        self.dlg = None
        self.dlg_drag = None
        u32.KillTimer(self.hwnd, 4)
        self.invalidate()
        self._dlg_flush_next()

    def _dlg_flush_next(self):
        """把排队中的提示弹出来"""
        nx = self.dlg_next
        if nx:
            self.dlg_next = None
            self.dlg_show(**nx)

    def dlg_alive(self, tag=None):
        d = self.dlg
        return bool(d) and (tag is None or d.get("tag") == tag)

    def _dlg_action(self, what):
        if what in ("know", "hide", None):
            self.dlg_close()
        elif what == "scan_cancel":
            job = self.job
            if job and job.state == "running":
                job.state = "aborted"
                self.dlg_update("scan", msg="正在取消扫描…", sub="", pct=None)
            else:
                self.dlg_close()
        elif what == "index_hide":
            self.dlg_close()
        elif what in ("copy_pack", "copy_raw"):
            ps = list(self._copy_pending or [])
            self.dlg_close()
            if ps:
                self._copy_pack(ps) if what == "copy_pack" else self._copy_now(ps)
        elif what == "pack_cancel":
            self._pack_stop = True
            self.dlg_update("pack", msg="正在取消打包，临时文件会一起清理…",
                            pct=None, sub="")

    _NO_LINE_START = "，。、；：？！）】》」』”’%,.;:?!)]}…"

    def _wrap(self, hdc, s, px, maxw, maxlines=3):
        """按像素宽度折行（含中文禁则：标点不落在行首）"""
        out, line = [], ""
        for ch in str(s or ""):
            if ch == "\n":
                out.append(line)
                line = ""
            elif line and self.tw(hdc, line + ch, px) > maxw:
                if ch in self._NO_LINE_START and len(line) > 1:
                    # 标点不能落在行首：往前找到最后一个非标点字符，
                    # 连它一起带到下一行（否则「），」这种连续标点会把
                    # 右括号单独顶到下一行行首，看着更难受）
                    k = len(line) - 1
                    while k > 0 and line[k] in self._NO_LINE_START:
                        k -= 1
                    if k >= 1:
                        out.append(line[:k])
                        line = line[k:] + ch
                    else:
                        out.append(line)
                        line = ch
                else:
                    out.append(line)
                    line = ch
            else:
                line += ch
            if len(out) >= maxlines:
                return out
        if line and len(out) < maxlines:
            out.append(line)
        return out

    def _dlg_metrics(self, hdc, cw, ch):
        """弹窗尺寸与位置（每帧重算；拖动过就用用户给的位置）"""
        s = self.scale
        d = self.dlg
        prog = (d["kind"] == "progress")
        pad = int(22 * s)
        want = int((470 if prog else 430) * s)
        w = int(max(int(300 * s), min(want, cw - int(60 * s))))
        inner = w - pad * 2
        lines = self._wrap(hdc, d.get("msg"), 11.5, inner, 3)
        sublines = self._wrap(hdc, d.get("sub"), 10.5, inner, 2)
        head = int(34 * s)
        lh = int(20 * s)
        barh = int(26 * s) if prog else 0
        btnh = int(32 * s)
        h = (int(18 * s) + head + len(lines) * lh + barh
             + len(sublines) * int(16 * s) + int(18 * s) + btnh + int(18 * s))
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            x = int((cw - w) / 2.0)
            y = int((ch - h) / 2.0) - int(26 * s)
        x = max(int(6 * s), min(int(x), cw - w - int(6 * s)))
        y = max(int(6 * s), min(int(y), ch - h - int(6 * s)))
        return {"x": x, "y": y, "w": w, "h": h, "pad": pad, "head": head,
                "lh": lh, "barh": barh, "btnh": btnh,
                "lines": lines, "sublines": sublines}

    def veil(self, hdc, l, t, r, b, color, alpha=60):
        """半透明遮罩（GDI+ alpha 填充；没有 GDI+ 时退化成一个混色实填）"""
        g = self._g
        br = None
        if g is not None:
            br = ctypes.c_void_p()
            if GP.dll.GdipCreateSolidFill(argb(color, alpha), ctypes.byref(br)) != 0:
                br = None
        if g is not None and br:
            GP.dll.GdipFillRectangle(g, br, ctypes.c_float(l), ctypes.c_float(t),
                                     ctypes.c_float(max(1, r - l)),
                                     ctypes.c_float(max(1, b - t)))
            GP.del_brush(br)
        else:
            self.fill(hdc, l, t, r, b, mix(Theme.bg, color, alpha / 255.0))

    def _dlg_icon(self, hdc, cx, cy, r, kind):
        """弹窗左侧的小图标：进度=转动圆环，成功=勾，失败=感叹，提示=i"""
        s = self.scale
        col = {"progress": Theme.accent, "ok": Theme.green,
               "error": Theme.danger}.get(kind, Theme.accent)
        bg = {"progress": Theme.accent_soft, "ok": rgb("e8f7f1"),
              "error": rgb("fdecea")}.get(kind, Theme.accent_soft)
        self.circle(hdc, cx, cy, r, bg)
        if kind == "progress":
            a = (time.time() * 380.0) % 360.0
            self.arc(hdc, cx, cy, r - int(4 * s), max(2.0, 2.6 * s), a, 260, col,
                     cap=2)
        elif kind == "ok":
            self.seg(hdc, cx - r * 0.44, cy + r * 0.02, cx - r * 0.12, cy + r * 0.36,
                     col, 1.9 * s)
            self.seg(hdc, cx - r * 0.12, cy + r * 0.36, cx + r * 0.46, cy - r * 0.36,
                     col, 1.9 * s)
        elif kind == "error":
            self.seg(hdc, cx, cy - r * 0.48, cx, cy + r * 0.16, col, 2.0 * s)
            self.circle(hdc, cx, cy + r * 0.5, max(1.2, 1.6 * s), col)
        else:
            self.circle(hdc, cx, cy - r * 0.44, max(1.2, 1.5 * s), col)
            self.seg(hdc, cx, cy - r * 0.06, cx, cy + r * 0.48, col, 1.9 * s)

    def _dlg_bar(self, hdc, l, t, r, b, pct):
        """进度条：已知百分比画实心渐变，未知百分比画流动光带"""
        s = self.scale
        rad = (b - t) / 2.0
        self.rrect(hdc, l, t, r, b, rad, rgb("eaeff8"))
        W = float(r - l)
        if pct is None:
            seg_w = W * 0.34
            ph = (time.time() * 0.62) % 1.0
            x0 = l + (W + seg_w) * ph - seg_w
            x1 = x0 + seg_w
            x0 = max(l, x0)
            x1 = min(r, x1)
            if x1 - x0 > 2:
                self.rrect_grad(hdc, x0, t, x1, b, rad, rgb("9dc3fb"),
                                rgb("3f7ce8"), horizontal=True)
        else:
            p = max(0.0, min(1.0, float(pct)))
            if p > 0.001:
                self.rrect_grad(hdc, l, t, l + max(b - t, W * p), b, rad,
                                rgb("5b93f0"), rgb("2f6fed"), horizontal=True)

    def paint_dlg(self, hdc, cw, ch):
        d = self.dlg
        if not d:
            return
        s = self.scale
        # 自动关闭（正在拖动时先别关）
        if d.get("auto") and not self.dlg_drag and time.time() > d["t0"] + d["auto"]:
            self.dlg = None
            u32.KillTimer(self.hwnd, 4)
            self._dlg_flush_next()
            return
        M = self._dlg_metrics(hdc, cw, ch)
        x, y, w, h, pad = M["x"], M["y"], M["w"], M["h"], M["pad"]
        d["_g"] = (x, y, w, h)
        kind = d["kind"]
        # 背景压暗：让弹窗从内容里浮出来
        self.veil(hdc, 0, 0, cw, ch, rgb("0e1a30"), 124 if not d.get("soft") else 62)
        # 柔和投影：必须用半透明多层叠加。以前用三层不透明的浅灰蓝色，
        # 压在压暗的背景上会浮出一圈亮框，看起来像叠了两张卡片。
        N = 10
        E = int(round(14 * s))
        dy = int(round(4 * s))
        for k in range(N):                       # k=0 最外 → 最内
            f = k / float(N - 1)
            e = int(round(E * (1.0 - f)))
            a = 2.0 + 5.0 * f
            self.rrect_a(hdc, x - e, y - e + dy, x + w + e, y + h + e + dy,
                         int(16 * s) + e, rgb("16233a"), a)
        # 卡片本体：白底 + 极浅描边（描边只负责收边，不能比投影亮）
        self.rrect(hdc, x, y, x + w, y + h, int(16 * s), Theme.card,
                   rgb("e6ebf4"))
        # 整卡兜底：先垫一层，防止点到背后内容（必须最先加，否则会盖住关闭按钮）
        self.add_hot(x, y, x + w, y + h, "dlg_noop", None, None, None, "dlg_body")
        # ---------------- 头部：图标 + 标题 + 关闭
        ic = int(30 * s)
        cx0 = x + pad + ic / 2.0
        cy0 = y + int(18 * s) + ic / 2.0
        self._dlg_icon(hdc, cx0, cy0, ic / 2.0, kind)
        self.text(hdc, d.get("title"), x + pad + ic + int(11 * s), y + int(18 * s),
                  x + w - pad - int(30 * s), y + int(18 * s) + ic, 13,
                  Theme.text, True, DT_LEFT)
        # 关闭按钮
        cw2 = int(26 * s)
        cl, ct = x + w - pad - cw2 + int(4 * s), y + int(16 * s)
        hov = (self.dlg_hover == "dlg_close")
        if hov:
            self.rrect(hdc, cl, ct, cl + cw2, ct + cw2, int(7 * s), rgb("eef2f9"))
        e = 4.6 * s
        cc = Theme.ink2 if hov else Theme.faint
        ccx, ccy = cl + cw2 / 2.0, ct + cw2 / 2.0
        self.seg(hdc, ccx - e, ccy - e, ccx + e, ccy + e, cc, 1.3 * s)
        self.seg(hdc, ccx + e, ccy - e, ccx - e, ccy + e, cc, 1.3 * s)
        self.add_hot(cl - int(4 * s), ct - int(4 * s), cl + cw2, ct + cw2,
                     "dlg_close", None, "关闭", "hand", "dlg_close")
        # 整卡兜底 hot 已在画卡片时加过（顺序不能挪，否则会盖住按钮）
        # 标题栏拖动区
        self.add_hot(x + int(2 * s), y + int(2 * s), cl - int(6 * s),
                     y + int(18 * s) + ic + int(2 * s), "dlg_drag", None,
                     "按住可拖动", "hand", "dlg_drag")
        # ---------------- 正文
        yb = y + int(18 * s) + M["head"]
        lh = M["lh"]
        for i, ln in enumerate(M["lines"]):
            self.text(hdc, ln, x + pad, yb + i * lh, x + w - pad, yb + (i + 1) * lh,
                      11.5, Theme.ink2, False, DT_LEFT)
        yy = yb + len(M["lines"]) * lh
        # ---------------- 进度条
        if M["barh"]:
            bt = yy + int(8 * s)
            self._dlg_bar(hdc, x + pad, bt, x + w - pad, bt + int(9 * s),
                          d.get("pct"))
            yy += M["barh"]
        # ---------------- 明细行（文件数 / 用时；进度百分比用强调色单列）
        spct = None
        if kind == "progress" and d.get("pct") is not None:
            spct = "%d%%" % (max(0.0, min(1.0, d["pct"])) * 100)
        lh2 = int(16 * s)
        sx = x + pad
        if spct:
            self.text(hdc, spct, x + pad, yy, x + pad + int(46 * s), yy + lh2,
                      10.5, Theme.accent, True, DT_LEFT)
            sx = x + pad + self.tw(hdc, spct, 10.5, True) + int(10 * s)
        for i, ln in enumerate(M["sublines"]):
            xx = sx if i == 0 else x + pad
            self.text(hdc, ln, xx, yy + i * lh2, x + w - pad, yy + (i + 1) * lh2,
                      10.5, Theme.faint, False, DT_LEFT)
        # ---------------- 底部按钮
        by = y + h - int(18 * s) - M["btnh"]
        bh = M["btnh"]
        main_label = d.get("label") or "知道了"
        if kind == "progress":
            if d.get("action") == "pack_cancel":
                main_label = "取消打包"
            elif d.get("action") == "scan_cancel":
                main_label = "取消"
            else:
                main_label = "关闭"
        danger = d.get("action") in ("scan_cancel", "pack_cancel")
        prim = (kind != "progress") and not danger
        bw2 = int(max(88 * s, self.tw(hdc, main_label, 11, True) + 30 * s))
        bx = x + w - pad - bw2
        hov = (self.dlg_hover == "dlg_btn")
        if danger:
            self.rrect(hdc, bx, by, bx + bw2, by + bh, int(7 * s),
                       rgb("fff6f5") if not hov else rgb("fdeceb"), rgb("f2c9c4"))
            self.text(hdc, main_label, bx, by, bx + bw2, by + bh, 11,
                      Theme.danger, True, DT_CENTER)
        elif prim:
            self.rrect_grad(hdc, bx, by, bx + bw2, by + bh, int(7 * s),
                            rgb("4b86f0"), rgb("2f6fed"), horizontal=True)
            self.text(hdc, main_label, bx, by, bx + bw2, by + bh, 11,
                      rgb("ffffff"), True, DT_CENTER)
        else:
            self.rrect(hdc, bx, by, bx + bw2, by + bh, int(7 * s),
                       rgb("f7f9fc") if not hov else rgb("eef2f9"), Theme.line)
            self.text(hdc, main_label, bx, by, bx + bw2, by + bh, 11,
                      Theme.ink2, True, DT_CENTER)
        self.add_hot(bx, by, bx + bw2, by + bh, "dlg_btn", d.get("action"),
                     None, "hand", "dlg_btn")
        # 次级按钮：进度框是「后台运行」，询问框是 alt（如「直接复制文件夹」）
        alt = d.get("alt")
        if kind == "progress":
            bx2 = bx - int(8 * s) - int(96 * s)
            hov2 = (self.dlg_hover == "dlg_hide")
            self.rrect(hdc, bx2, by, bx2 + int(96 * s), by + bh, int(7 * s),
                       rgb("f7f9fc") if not hov2 else rgb("eef2f9"), Theme.line)
            self.text(hdc, "后台运行", bx2, by, bx2 + int(96 * s), by + bh, 11,
                      Theme.ink2, False, DT_CENTER)
            self.add_hot(bx2, by, bx2 + int(96 * s), by + bh, "dlg_hide", None,
                         "隐藏这个进度框，任务继续跑", "hand", "dlg_hide")
        elif alt:
            aw = int(max(96 * s, self.tw(hdc, alt[0], 11) + 26 * s))
            ax2 = bx - int(8 * s) - aw
            hov2 = (self.dlg_hover == "dlg_alt")
            self.rrect(hdc, ax2, by, ax2 + aw, by + bh, int(7 * s),
                       rgb("f7f9fc") if not hov2 else rgb("eef2f9"), Theme.line)
            self.text(hdc, alt[0], ax2, by, ax2 + aw, by + bh, 11,
                      Theme.ink2, False, DT_CENTER)
            self.add_hot(ax2, by, ax2 + aw, by + bh, "dlg_alt", alt[1], None,
                         "hand", "dlg_alt")

    def toast(self, msg, kind="info", title=None, auto=2.8):
        """轻提示 → 改成居中弹窗（可拖动、可关闭），默认几秒后自动收起"""
        self.dlg_show(title or ("提示" if kind == "info" else "出错了"), msg,
                      kind=kind, auto=auto)

    def invalidate(self):
        if self.hwnd:
            u32.InvalidateRect(self.hwnd, None, 0)

    # -------------------------------------------------------------- 绘制主流程
    def on_paint(self):
        ps = PAINTSTRUCT()
        hdc = u32.BeginPaint(self.hwnd, ctypes.byref(ps))
        cw, ch = self.client()
        if cw <= 0 or ch <= 0:
            u32.EndPaint(self.hwnd, ctypes.byref(ps))
            return
        if self._bufsize != (cw, ch):
            if self._buf:
                gdi.DeleteObject(self._buf[1])
                gdi.DeleteDC(self._buf[0])
            mem = gdi.CreateCompatibleDC(hdc)
            bmp = gdi.CreateCompatibleBitmap(hdc, cw, ch)
            gdi.SelectObject(mem, bmp)
            self._buf = (mem, bmp)
            self._bufsize = (cw, ch)
        mem = self._buf[0]
        gdi.SelectObject(mem, ctypes.c_void_p(gdi.GetStockObject(DC_BRUSH)))
        gdi.SelectObject(mem, ctypes.c_void_p(gdi.GetStockObject(DC_PEN)))
        self.hot = []
        self._g = GP.graphics(mem)
        try:
            self.paint_all(mem, cw, ch)
        except Exception as e:
            self.fill(mem, 0, 0, cw, ch, Theme.bg)
            self.text(mem, "绘制异常：%s" % e, 20, 20, cw - 20, 50, 13, Theme.danger)
        GP.free(self._g)
        self._g = None
        gdi.BitBlt(hdc, 0, 0, cw, ch, mem, 0, 0, 0x00CC0020)
        u32.EndPaint(self.hwnd, ctypes.byref(ps))
        if _DUMP_HOT:
            try:
                import json
                with open(_dbg_path("hot.json"), "w", encoding="utf-8") as fh:
                    json.dump({"scale": self.scale, "client": [cw, ch],
                               "sheet": self.sheet,
                               "hot": [{"r": h[:4], "act": h[4],
                                        "arg": h[5] if isinstance(h[5], str) else None,
                                        "key": h[8]} for h in self.hot]}, fh)
            except Exception:
                pass

    def paint_all(self, hdc, cw, ch):
        s = self.scale
        self.fill(hdc, 0, 0, cw, ch, Theme.bg)
        self.paint_titlebar(hdc, cw)
        tabs_h = int(34 * s)
        self.paint_tabs(hdc, cw, self.topbar_h(), tabs_h)
        top = self.topbar_h() + tabs_h
        content = (int(16 * s), top + int(9 * s), cw - int(16 * s), ch - int(10 * s))
        if self.sheet == "overview":
            self.paint_overview(hdc, content)
        elif self.sheet == "map":
            self.paint_map(hdc, content)
        elif self.sheet == "dirs":
            self.paint_dirs(hdc, content)
        elif self.sheet == "big":
            self.paint_big(hdc, content)
        else:
            self.paint_search(hdc, content)
        # 盘符下拉浮层（最后画，保证在内容之上）
        if self.drv_open and self.drv_btn_rect:
            bw = int(214 * s)
            bx = max(int(8 * s), self.drv_btn_rect[2] - bw)
            self.drv_panel(hdc, bx, self.drv_btn_rect[3] + int(5 * s))
        # 居中弹窗（提示 / 进度）永远画在最上层
        self.paint_dlg(hdc, cw, ch)

    def topbar_h(self):
        return int(46 * self.scale)

    # -------------------------------------------------------------- 标题栏
    def paint_titlebar(self, hdc, cw):
        """自绘标题栏：路径框 + 盘符下拉 + 扫描/回收站图标 + 最小化/最大化/关闭"""
        s = self.scale
        h = self.topbar_h()
        self.fill(hdc, 0, 0, cw, h, Theme.card)
        self.hline(hdc, 0, cw, h - max(1, int(s)), Theme.line)

        # ---------------- 右侧窗口按钮（从右往左：关闭 / 最大化 / 最小化）
        wb = int(42 * s)
        zoomed = bool(u32.IsZoomed(self.hwnd))
        x = cw
        for act in ("win_close", "win_max", "win_min"):
            x0 = x - wb
            hov = (self.hover_key == act)
            if hov:
                self.fill(hdc, x0, 0, x, h, rgb("e5484d") if act == "win_close"
                          else rgb("edf1f8"))
            fg = rgb("ffffff") if (hov and act == "win_close") else Theme.ink2
            cx, cy = (x0 + x) / 2.0, h / 2.0
            u = 5.0 * s
            if act == "win_min":
                self.seg(hdc, cx - u, cy, cx + u, cy, fg, 1.3)
            elif act == "win_max":
                if zoomed:
                    self.seg_box(hdc, cx - u + 1.8 * s, cy - u, cx + u, cy + u - 1.8 * s,
                                 fg, 1.0)
                    self.seg_box(hdc, cx - u, cy - u + 1.8 * s, cx + u - 1.8 * s, cy + u,
                                 fg, 1.0)
                else:
                    self.seg_box(hdc, cx - u, cy - u, cx + u, cy + u, fg, 1.0)
            else:
                self.seg(hdc, cx - u, cy - u, cx + u, cy + u, fg, 1.1)
                self.seg(hdc, cx + u, cy - u, cx - u, cy + u, fg, 1.1)
            self.add_hot(x0, 0, x, h, act, None,
                         {"win_min": "最小化", "win_max": "最大化 / 还原",
                          "win_close": "关闭"}[act], "hand", act)
            x = x0

        x -= int(10 * s)

        # ---------------- 图标按钮：回收站 / 扫描（与最小化完全同款：整格高、直角、仅悬停铺浅灰）
        bw = int(34 * s)
        for act, tip in (("recycle", "打开回收站"), ("scan", "重新扫描当前盘")):
            x0 = x - bw
            hov = (self.hover_key == act)
            if hov:
                self.fill(hdc, x0, 0, x, h, rgb("edf1f8"))
            c = Theme.ink2
            cx, cy = (x0 + x) / 2.0, h / 2.0
            if act == "scan":
                self.circle_line(hdc, cx - 1.0 * s, cy - 1.0 * s, 4.6 * s, c, 1.4)
                self.seg(hdc, cx + 2.3 * s, cy + 2.3 * s, cx + 5.6 * s, cy + 5.6 * s, c, 1.6)
            else:
                self.seg(hdc, cx - 5.2 * s, cy - 4.2 * s, cx + 5.2 * s, cy - 4.2 * s, c, 1.3)
                self.seg(hdc, cx - 2.0 * s, cy - 6.4 * s, cx + 2.0 * s, cy - 6.4 * s, c, 1.3)
                self.seg(hdc, cx - 4.0 * s, cy - 3.2 * s, cx - 3.1 * s, cy + 6.0 * s, c, 1.3)
                self.seg(hdc, cx + 4.0 * s, cy - 3.2 * s, cx + 3.1 * s, cy + 6.0 * s, c, 1.3)
                self.seg(hdc, cx - 3.1 * s, cy + 6.0 * s, cx + 3.1 * s, cy + 6.0 * s, c, 1.3)
            self.add_hot(x0, 0, x, h, act, None, tip, "hand", act)
            x = x0

        x -= int(10 * s)

        # ---------------- 左上角：应用图标 + 品牌标题
        ix = int(12 * s)
        iw = int(20 * s)
        iy = int((h - iw) / 2)
        if not _draw_hicon(hdc, ix, iy, iw):
            self.rrect(hdc, ix, iy, ix + iw, iy + iw, int(5 * s), Theme.accent_soft)
        tx0 = ix + iw + int(8 * s)
        title = "无敌暴龙图"
        twt = int(self.tw(hdc, title, 12.5, True))
        self.text(hdc, title, tx0, 0, tx0 + twt + int(6 * s), h, 12.5, Theme.text, True)

        # ---------------- 路径框：左侧内嵌盘符段（点击展开盘符下拉）+ 右侧路径输入
        label = "全部盘符" if self.active == "ALL" else (self.active or "")
        lw = int(self.tw(hdc, label, 11))
        seg_w = max(int(56 * s), lw + int(31 * s))
        px = tx0 + twt + int(16 * s)
        pr = min(x - int(2 * s), px + int(700 * s))
        if pr <= px + seg_w + int(70 * s):
            return
        ph = int(28 * s)
        py = int((h - ph) / 2)
        focused = (self.editing == "path")
        self.rrect(hdc, px, py, pr, py + ph, int(8 * s),
                   rgb("ffffff") if focused else Theme.field,
                   Theme.accent if focused else Theme.line)
        # 盘符段：文字 + ▾，悬停 / 展开时变蓝，右侧一条细分隔线
        dhov = (self.hover_key == "drv_menu") or self.drv_open
        dc = Theme.accent if dhov else Theme.ink2
        segx = px + seg_w
        self.text(hdc, label, px + int(10 * s), py, segx - int(14 * s), py + ph, 11,
                  dc, dhov, DT_LEFT)
        ctx = px + int(10 * s) + lw + int(3 * s)
        # 小箭头用两段线画，避免 ▾ 字形垂直偏移
        acx = ctx + int(5 * s)
        acy = py + ph / 2.0
        cc = Theme.accent if dhov else Theme.faint
        self.seg(hdc, acx - 3.6 * s, acy - 1.4 * s, acx, acy + 1.8 * s, cc, 1.2)
        self.seg(hdc, acx, acy + 1.8 * s, acx + 3.6 * s, acy - 1.4 * s, cc, 1.2)
        self.fill(hdc, segx, py + int(6 * s), segx + 1, py + ph - int(6 * s), Theme.line)
        self.add_hot(px, py, segx, py + ph, "drv_menu", None,
                     "切换盘符：全部盘符 / 单个磁盘", "hand", "drv_menu")
        self.drv_btn_rect = (px, py, segx, py + ph)

        # 路径文本
        txt = self.path_buf if focused else (self.pwd or "")
        txl = segx + int(11 * s)
        txr = pr - int(10 * s)
        if txt:
            self.text(hdc, txt, txl, py, txr, py + ph, 11.5,
                      Theme.text if focused else Theme.ink2, False, DT_LEFT, "Consolas")
        else:
            self.text(hdc, "输入路径后回车，如 D:\\ 或 全部盘符", txl, py, txr, py + ph,
                      10.5, Theme.faint)
        if focused:
            cx_ = txl + (self.tw(hdc, txt, 11.5, False, "Consolas") if txt else 0)
            cx_ = min(cx_, txr - int(2 * s))
            self.fill(hdc, cx_ + 1, py + int(7 * s), cx_ + 2, py + int(ph - 7 * s),
                      Theme.accent)
        self.add_hot(segx + int(1 * s), py, pr, py + ph, "focus_path", None,
                     "可直接输入任意路径，回车后总览 / 地图 / 明细 / 大文件 / 搜索都按它过滤",
                     "ibeam", "pathbox")

    def drv_panel(self, hdc, bx, by):
        """盘符快捷下拉（自绘面板，不用系统菜单）"""
        s = self.scale
        rows = self.chips()
        w1 = int(214 * s)
        ih = int(30 * s)
        h1 = ih * len(rows) + int(10 * s)
        self.fill(hdc, bx + int(2 * s), by + int(3 * s), bx + w1 + int(2 * s),
                  by + h1 + int(3 * s), rgb("e4e9f2"))
        self.rrect(hdc, bx, by, bx + w1, by + h1, int(8 * s), Theme.card, Theme.line)
        yy = by + int(5 * s)
        for d in rows:
            letter = d["letter"]
            on = (letter == self.active)
            if on:
                self.rrect(hdc, bx + int(4 * s), yy, bx + w1 - int(4 * s), yy + ih,
                           int(5 * s), Theme.accent_soft)
            label = "全部盘符" if letter == "ALL" else letter
            self.text(hdc, label, bx + int(12 * s), yy, bx + int(92 * s), yy + ih, 11.5,
                      Theme.accent if on else Theme.text, on)
            sub = ("%d 个盘符" % len(self.drives)) if letter == "ALL" else (d.get("label") or "")
            self.text(hdc, sub, bx + int(86 * s), yy, bx + int(156 * s), yy + ih, 10,
                      Theme.faint)
            pct = d.get("pct") if letter == "ALL" else d.get("usedPct")
            self.text(hdc, "%d%%" % int(pct or 0), bx + int(152 * s), yy,
                      bx + w1 - int(12 * s), yy + ih, 10.5, Theme.muted, False, DT_RIGHT)
            self.add_hot(bx + int(4 * s), yy, bx + w1 - int(4 * s), yy + ih,
                         "drv_pick", letter, None, "hand", "dv_" + letter)
            yy += ih

    def paint_tabs(self, hdc, cw, top, height):
        s = self.scale
        self.fill(hdc, 0, top, cw, top + height, Theme.card)
        tabs = [("overview", "总览"), ("map", "空间地图"), ("dirs", "文件夹明细"),
                ("big", "大文件"), ("search", "搜索")]
        x = int(12 * s)
        pad = int(12 * s)
        ty = top + int(6 * s)
        tb = top + height - int(6 * s)
        for key, label in tabs:
            on = (self.sheet == key)
            tw_ = int(self.tw(hdc, label, 11.5, on) + pad * 2)
            hov = (self.hover_key == "tab" + key)
            if on:
                self.rrect(hdc, x, ty, x + tw_, tb, int(5 * s), Theme.accent_soft)
            elif hov:
                self.rrect(hdc, x, ty, x + tw_, tb, int(5 * s), rgb("f4f7fc"))
            col = Theme.accent if on else (Theme.text if hov else Theme.muted)
            self.text(hdc, label, x, ty, x + tw_, tb, 11.5, col, on, DT_CENTER)
            self.add_hot(x, top, x + tw_, top + height, "sheet", key, None, "hand",
                         "tab" + key)
            x += tw_ + int(2 * s)
        self.hline(hdc, 0, cw, top + height - max(1, int(s)), Theme.line)

    # -------------------------------------------------------------- 通用卡片
    def card(self, hdc, l, t, r, b, pad=True):
        s = self.scale
        rad = int(10 * s)
        # 统一轻投影，让所有卡片有同样的浮起感
        self.rrect(hdc, l + int(1 * s), t + int(2 * s), r + int(1 * s),
                   b + int(2 * s), rad, rgb("edf1f7"))
        self.rrect(hdc, l, t, r, b, rad, Theme.card, rgb("e8edf6"))
        return (l + int(14 * self.scale) if pad else l,
                t + int(12 * self.scale) if pad else t,
                r - int(14 * self.scale) if pad else r,
                b - int(12 * self.scale) if pad else b)

    def card_title(self, hdc, txt, l, t, r, sub=None):
        s = self.scale
        self.text(hdc, txt, l, t, r, t + int(18 * s), 12.5, Theme.text, True)
        if sub:
            self.text(hdc, sub, l, t, r, t + int(18 * s), 10.5, Theme.faint, False, DT_RIGHT)

    # -------------------------------------------------------------- 环形图
    def donut(self, hdc, cx, cy, rad, thick, items, total, center_top, center_sub,
              bg=None, center_px=17):
        s = self.scale
        total = float(total or 0)
        ring = rad - thick / 2.0
        self.arc(hdc, cx, cy, ring, thick, 0, 360, bg if bg is not None else rgb("eef2f8"))
        if total > 0:
            ang = -90.0
            live = [(c, v) for c, v in items if v > 0]
            gap = 0.9 if len(live) > 1 else 0.0
            for color, val in live:
                sweep = 360.0 * val / total
                sw = max(0.6, sweep - gap)
                self.arc(hdc, cx, cy, ring, thick, ang + gap / 2.0, sw, color,
                         rounded=(len(live) == 1))
                ang += sweep
        if center_top:
            self.text(hdc, center_top, cx - rad, cy - int(center_px * 0.9 * s),
                      cx + rad, cy + int(center_px * 0.42 * s), center_px,
                      Theme.text, True, DT_CENTER)
        if center_sub:
            self.text(hdc, center_sub, cx - rad, cy + int(5 * s), cx + rad,
                      cy + int(25 * s), 10.5, Theme.faint, False, DT_CENTER)

    # -------------------------------------------------------------- 总览
    def paint_overview(self, hdc, rc):
        """总览：蓝色主卡 + 三张小指标卡 / 三张图表卡 / 大文件与清理 + 智能洞察。"""
        s = self.scale
        l, t, r, b = rc
        W = r - l
        gap = int(12 * s)
        kh = int(98 * s)

        # ---------------- 第一行：蓝色主卡 + 文件总数 / 今日增量 / 建议可清理
        hero_w = int(W * 0.315)
        self.hero_card(hdc, l, t, l + hero_w, t + kh)
        _nd, _ssz, sf, sd = self.scope()
        ins = self.insights or {}
        tp = ins.get("trend") or []
        tsz = [p[0] for p in tp]
        tcnt = [p[1] for p in tp]
        cum, acc = [], 0
        for c in tcnt:
            acc += c
            cum.append(acc)
        series = [cum, tsz, tcnt]
        stats = [
            ("文件总数", fmt_n(sf), "文件夹 %s 个" % fmt_n(sd), 3, 0),
            ("今日增量", fmt(ins.get("today_size", 0)),
             "%s 个文件有变动" % fmt_n(ins.get("today_files", 0)), 1, 1),
            ("建议可清理", fmt(self.cleanable_total()), "临时 / 回收站 / 更新缓存", 2, 2),
        ]
        sw = (W - hero_w - gap * 3) / 3.0
        for i, (lab, val, sub, gi, si) in enumerate(stats):
            x0 = l + hero_w + gap + i * (sw + gap)
            self.mini_stat(hdc, x0, t, x0 + sw, t + kh, lab, val, sub, gi,
                           series[si], si)

        # ---------------- 第二行：空间分布 / 文件类型分布 / 近 30 天趋势
        cy = t + kh + gap
        chh = int(236 * s)
        cw3 = (W - gap * 2) / 3.0
        self.chart_space(hdc, l, cy, l + cw3, cy + chh)
        self.chart_ext_bars(hdc, l + cw3 + gap, cy, l + cw3 * 2 + gap, cy + chh)
        self.chart_trend(hdc, l + cw3 * 2 + gap * 2, cy, r, cy + chh)

        # ---------------- 第三行：大文件与清理建议 + 智能洞察
        ty = cy + chh + gap
        if b - ty > int(90 * s):
            mid = l + int((r - l) * 0.665)
            self.big_clean_table(hdc, l, ty, mid - int(6 * s), b)
            self.insight_card(hdc, mid + int(6 * s), ty, r, b)

    def disk_figures(self):
        """主卡用：总容量 / 已用 / 可用 / 占用比例 / 说明文字。"""
        scoped = self.deep_scope()
        if scoped:
            d = next((x for x in self.drives if x["letter"] == self.active), None)
            total = d["total"] if d else 0
            used = self.scope()[1]
            tail = os.path.basename((self.pwd or "").rstrip("\\")) or (self.active or "")
        elif self.active == "ALL":
            total = sum(d.get("total", 0) for d in self.drives)
            used = sum(d.get("used", 0) for d in self.drives)
            tail = "全部 %d 个盘符" % len(self.drives)
        else:
            d = next((x for x in self.drives if x["letter"] == self.active), None)
            total = d.get("total", 0) if d else 0
            used = d.get("used", 0) if d else 0
            tail = "%s · %s" % (self.active or "", (d or {}).get("label", ""))
        total = max(total or 0, used)
        free = max(0, total - used)
        pct = (used / float(total)) if total else 0.0
        return total, used, free, pct, tail

    def sparkline(self, hdc, l, t, r, b, series, col_top, col_bottom, ymin=0,
                  col_line=None):
        """迷你趋势：整块渐变面积 + 顶部折线，数据来自真实近 30 天变动。"""
        if not series or len(series) < 2 or r - l < 8:
            return
        lo = float(ymin or 0)
        span = (max(series) - lo) or 1.0
        n = len(series)
        wpx = max(2, int(r - l))

        def _y(px):
            fx = px / float(max(1, wpx - 1)) * (n - 1)
            i0 = int(fx)
            i1 = min(n - 1, i0 + 1)
            fr = fx - i0
            v = series[i0] * (1.0 - fr) + series[i1] * fr
            rt = min(1.0, max(0.0, (v - lo) / span))
            return b - (b - t) * rt

        # 面积用「一整块渐变路径」填充：逐列混色在陡坡处会出现竖条纹
        done = False
        g = self._g
        if g is not None:
            pts = [(l + px, _y(px)) for px in range(0, wpx, 2)]
            if pts[-1][0] < l + wpx - 1:
                pts.append((l + wpx - 1, _y(wpx - 1)))
            p = ctypes.c_void_p()
            if GP.dll.GdipCreatePath(0, ctypes.byref(p)) == 0:
                f = ctypes.c_float
                GP.dll.GdipStartPathFigure(p)
                arr = (f * (len(pts) * 2))()
                for i, (xx, yy) in enumerate(pts):
                    arr[i * 2] = f(xx)
                    arr[i * 2 + 1] = f(yy)
                GP.dll.GdipAddPathLine2(p, arr, len(pts))
                GP.dll.GdipAddPathLine(p, f(pts[-1][0]), f(pts[-1][1]),
                                       f(pts[-1][0]), f(b))
                GP.dll.GdipAddPathLine(p, f(pts[-1][0]), f(b), f(pts[0][0]), f(b))
                GP.dll.GdipClosePathFigure(p)
                br = GP.grad_brush(l, t, r, b, col_top, col_bottom, 90.0)
                if not br:
                    br = GP.brush(col_top)
                if br:
                    GP.dll.GdipFillPath(g, br, p)
                    done = True
                GP.del_brush(br)
                GP.del_path(p)
        if not done:
            for px in range(wpx):
                y = _y(px)
                self.fill(hdc, l + px, y, l + px + 1, b,
                          mix(col_bottom, col_top,
                              (b - y) / float(max(1, b - t))))
        cl = col_line if col_line is not None else col_top
        prev = None
        for px in range(wpx):
            y = _y(px)
            if prev is not None:
                self.seg(hdc, prev[0], prev[1], l + px, y, cl, 1.0)
            prev = (l + px, y)

    def hero_card(self, hdc, l, t, r, b):
        """蓝色主卡：总容量 / 已用 / 可用 + 半圆占用仪表。"""
        s = self.scale
        rad = int(13 * s)
        # 蓝色渐变：上方更蓝（饱和）→ 下方转淡
        self.rrect_grad(hdc, l, t, r, b, rad, rgb("2f68e6"), rgb("8ab6fb"),
                        horizontal=False)
        total, _used, free, pct, _tail = self.disk_figures()
        pl = l + int(17 * s)
        pr = r - int(17 * s)
        w1 = (pr - pl) / 3.0
        cx = pl + w1 * 1.5                 # 中列中心 = 仪表中心
        y = t + int(16 * s)
        # 仪表几何先算出来：左右两列的大数字按仪表的垂直区间居中
        cy = t + int(76 * s)
        rx = int(40 * s)                   # 横半径（比原来略宽）
        ry = int(38 * s)                   # 纵半径（略扁，但不至于太扁）
        th = int(9 * s)                    # 环更细
        band_t = cy - ry - th / 2.0        # 仪表上沿
        band_b = cy + th / 2.0             # 仪表下沿
        # 三列内容各自在列内水平居中（「已用」整体左移一点，视觉上更贴仪表）
        for i, lab in enumerate(("总容量", "已用", "可用")):
            x0 = pl + w1 * i + (int(-6 * s) if i == 1 else 0)
            self.text(hdc, lab, x0, y, x0 + w1, y + int(15 * s), 10.5,
                      rgb("dce8ff"), False, DT_CENTER)
        vb = int(30 * s)
        vy = (band_t + band_b) / 2.0 - vb / 2.0
        self.text(hdc, fmt(total), pl, vy, pl + w1, vy + vb, 21,
                  rgb("ffffff"), True, DT_CENTER)
        self.text(hdc, fmt(free), pl + w1 * 2, vy, pl + w1 * 3, vy + vb,
                  21, rgb("ffffff"), True, DT_CENTER)
        # 中间半圆仪表：椭圆弧 + 方头切平（cap=1）
        self.arc(hdc, cx, cy, rx, th, 180, 180, rgb("c3d8fb"), cap=1, ry=ry)
        if pct > 0.004:
            self.arc(hdc, cx, cy, rx, th, 180, 180 * min(1.0, pct),
                     rgb("ffffff"), cap=1, ry=ry)
        self.text(hdc, "%.0f%%" % (pct * 100), cx - int(40 * s),
                  cy - int(26 * s), cx + int(40 * s), cy - int(1 * s), 15,
                  rgb("ffffff"), True, DT_CENTER)

    def mini_stat(self, hdc, l, t, r, b, label, value, sub, gi, trend, sidx):
        """第一行小指标卡：标签 / 大数值 / 说明 / 右下角迷你趋势。"""
        s = self.scale
        rad = int(13 * s)
        self.rrect(hdc, l + int(1 * s), t + int(2 * s), r + int(1 * s), b + int(2 * s),
                   rad, rgb("edf1f7"))
        self.rrect(hdc, l, t, r, b, rad, Theme.card, rgb("e8edf6"))
        pl = l + int(15 * s)
        pr = r - int(15 * s)
        self.text(hdc, label, pl, t + int(12 * s), pr - int(24 * s),
                  t + int(26 * s), 10.5, Theme.muted)
        self.text(hdc, value, pl, t + int(29 * s), pr, t + int(58 * s), 21,
                  Theme.text, True)
        self.text(hdc, sub, pl, t + int(59 * s), pr, t + int(74 * s), 9.5,
                  Theme.faint)
        # 右上角小图标（灰阶，与主色系一致）
        bs = int(22 * s)
        self._kpi_glyph(hdc, gi, r - int(13 * s) - bs / 2.0, t + int(21 * s),
                        s * 0.62, rgb("b9c2d2"))
        # 右下迷你趋势（真实近 30 天变动，纯视觉）
        if trend:
            c1 = (rgb("8fb4f8"), rgb("c9dcfb"), rgb("f6c48a"))[sidx % 3]
            c0 = (rgb("eef4ff"), rgb("eef4ff"), rgb("fdf3e6"))[sidx % 3]
            self.sparkline(hdc, l + (r - l) * 0.46, b - int(30 * s),
                           pr, b - int(10 * s), trend, c1, c0)

    def _kind_totals(self):
        """按大类聚合体积，返回 [(中文名, kind, 大小)] 按大小倒序。

        优先用大文件索引（≥1MB 的文件，覆盖绝大部分体积），这样每个大类都不会
        因为「扩展名 Top N 被截断」而整类消失；没有索引时退回扩展名统计。
        """
        agg = {}
        pre = (self.pwd or "").lower() if self.deep_scope() else ""
        for f in (self.data or {}).get("file_index") or []:
            p = (f.get("p") or "").lower()
            if pre and not p.startswith(pre):
                continue
            k = EXT2KIND.get(S._ext_label(f.get("p", "")), "其他")
            agg[k] = agg.get(k, 0) + (f.get("s") or 0)
        if agg:
            rows = sorted(agg.items(), key=lambda kv: -kv[1])
            return [(KIND_LABELS.get(k, "其他"), k, v) for k, v in rows]
        ext = list((self.data or {}).get("ext") or [])
        other = 0
        for e in ext:
            nm = str(e[0]).lower().lstrip(".")
            sz = e[1] or 0
            k = EXT2KIND.get(nm)
            if k:
                agg[k] = agg.get(k, 0) + sz
            else:
                other += sz
        if other:
            agg["其他"] = agg.get("其他", 0) + other
        rows = sorted(agg.items(), key=lambda kv: -kv[1])
        return [(KIND_LABELS.get(k, "其他"), k, v) for k, v in rows]

    def chart_space(self, hdc, l, t, r, b):
        """空间分布：按文件大类聚合的环形图 + 右侧图例。"""
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        rows = self._kind_totals()
        other_sz = sum(v for _n, k, v in rows if k == "其他")
        main = [r for r in rows if r[1] != "其他"]
        if len(main) > 5:
            other_sz += sum(v for _n, _k, v in main[5:])
            main = main[:5]
        rows = main + ([("其他", "其他", other_sz)] if other_sz else [])
        self.card_title(hdc, "空间分布", inner[0], inner[1], inner[2],
                        "当前路径" if self.deep_scope() else "整盘")
        if not rows:
            self.text(hdc, "扫描后显示", inner[0], inner[1] + int(64 * s), inner[2],
                      b, 11, Theme.faint, False, DT_CENTER)
            return
        total = sum(v for _n, _k, v in rows) or 1
        rad = int(68 * s)
        cx = inner[0] + rad + int(6 * s)
        cy = inner[1] + int(36 * s) + rad
        # 统一蓝色梯度：体积越大颜色越深；「其他」用中性灰蓝，避免看起来像背景
        cols = [rgb("9aa8bf") if k == "其他"
                else rgb(BLUE_SCALE[min(i, len(BLUE_SCALE) - 1)])
                for i, (_n, k, _v) in enumerate(rows)]
        items = [(c, v) for c, (_n, _k, v) in zip(cols, rows)]
        self.donut(hdc, cx, cy, rad, int(13 * s), items, total, fmt(total), "总占用",
                   center_px=16)
        lx = cx + rad + int(18 * s)
        nmw = max([int(self.tw(hdc, nm, 10)) for nm, _k, _v in rows] or [0])
        szx = lx + int(12 * s) + nmw + int(12 * s)
        yy = inner[1] + int(50 * s)
        for i, (nm, k, v) in enumerate(rows):
            if yy + int(18 * s) > b - int(12 * s):
                break
            self.circle(hdc, lx + int(3.5 * s), yy + int(8 * s), int(3.5 * s),
                        cols[i])
            self.text(hdc, nm, lx + int(12 * s), yy, szx - int(4 * s),
                      yy + int(17 * s), 10, Theme.ink2)
            self.text(hdc, fmt(v), szx, yy, inner[2], yy + int(17 * s), 10,
                      Theme.text)
            yy += int(24 * s)

    def chart_ext_bars(self, hdc, l, t, r, b):
        """文件类型分布：Top 扩展名的渐变柱状图，柱顶标百分比。"""
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        ext = list((self.data or {}).get("ext") or [])[:6]
        self.card_title(hdc, "文件类型分布", inner[0], inner[1], inner[2],
                        ("整盘 · Top %d 扩展名" % len(ext)) if ext else None)
        if not ext:
            self.text(hdc, "扫描后显示", inner[0], inner[1] + int(64 * s), inner[2],
                      b, 11, Theme.faint, False, DT_CENTER)
            return
        total = sum(e[1] for e in ext) or 1
        base = b - int(30 * s)
        top = inner[1] + int(54 * s)
        n = len(ext)
        slot = (inner[2] - inner[0]) / float(n)
        bw = min(int(20 * s), int(slot * 0.26))
        mx = max(e[1] for e in ext) or 1
        for i, e in enumerate(ext):
            cx = inner[0] + slot * (i + 0.5)
            x = cx - bw / 2.0
            hgt = max(int(4 * s), (base - top) * (e[1] / float(mx)))
            y0 = base - hgt
            # 统一蓝色渐变柱（细柱 + 更饱和的蓝，圆角只在顶部微收）
            self.rrect_grad(hdc, x, y0, x + bw, base, int(min(4 * s, bw / 3.0)),
                            rgb("8dbaff"), rgb("3468dc"), horizontal=False)
            pct = e[1] / float(total) * 100
            self.text(hdc, "%.0f%%" % pct, cx - slot / 2.0, y0 - int(16 * s),
                      cx + slot / 2.0, y0 - int(2 * s), 9.5, Theme.ink2, True,
                      DT_CENTER)
            lab = str(e[0]) or "无扩展名"
            if len(lab) > 6:
                lab = lab[:5] + "…"
            self.text(hdc, lab, cx - slot / 2.0, base + int(5 * s),
                      cx + slot / 2.0, base + int(21 * s), 9, Theme.text,
                      False, DT_CENTER)
            self.add_hot(cx - slot / 2.0, top, cx + slot / 2.0, base + int(22 * s),
                         "noop", None, "%s：%s（%.1f%%）" % (e[0], fmt(e[1]), pct),
                         None, "xb%d" % i)

    def chart_trend(self, hdc, l, t, r, b):
        """近 30 天大小趋势：把每天的变动量累加成「体积随时间变化」的曲线。"""
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        ins = self.insights or {}
        self.card_title(hdc, "近 30 天大小趋势", inner[0], inner[1], inner[2],
                        "按文件修改时间" if ins else "索引建好后显示")
        if not ins:
            self.text(hdc, "建立索引后，这里会显示最近 30 天的体积变化趋势",
                      inner[0], inner[1] + int(56 * s), inner[2], b, 10.5,
                      Theme.faint, False, DT_CENTER)
            return
        daily = [p[0] for p in (ins.get("trend") or [])]
        if not daily:
            self.text(hdc, "最近 30 天没有体积变化", inner[0],
                      inner[1] + int(56 * s), inner[2], b, 10.5, Theme.faint,
                      False, DT_CENTER)
            return
        grow = sum(daily)
        # 期末体积对齐当前层级实际大小，往前逐日回推得到每天收盘体积
        now = self.scope()[1] or grow
        start = max(0, now - grow)
        acc, cum = 0, []
        for v in daily:
            acc += v
            cum.append(start + acc)
        head = fmt(cum[-1])
        self.text(hdc, head, inner[0], inner[1] + int(20 * s), inner[2],
                  inner[1] + int(44 * s), 16, Theme.text, True)
        hw = int(self.tw(hdc, head, 16, True))
        self.text(hdc, "当前体积", inner[0] + hw + int(8 * s),
                  inner[1] + int(22 * s), inner[2], inner[1] + int(44 * s), 9.5,
                  Theme.faint)
        self.text(hdc, "+%s / 30 天" % fmt(grow), inner[0], inner[1] + int(42 * s),
                  inner[2], inner[1] + int(58 * s), 9.5, rgb("0f9d6e"),
                  False, DT_RIGHT)
        gx0, gx1 = inner[0] + int(4 * s), inner[2] - int(4 * s)
        gy0, gy1 = inner[1] + int(64 * s), b - int(34 * s)
        for i in range(3):
            yy = gy0 + (gy1 - gy0) * i / 2.0
            self.hline(hdc, gx0, gx1, yy, Theme.line_soft)
        lo = min(cum)
        hi = max(cum)
        ymin = lo - (hi - lo) * 0.35 if hi > lo else lo * 0.98
        self.sparkline(hdc, gx0, gy0, gx1, gy1, cum, rgb("a4c8f8"),
                       rgb("f3f8ff"), ymin=ymin, col_line=rgb("5b93f0"))
        # 气泡标出单日最大增量（哪一天长得最多），比标末端更有信息量
        mx = max(daily)
        if mx > 0 and len(daily) > 1:
            idx = daily.index(mx)
            fx = gx0 + (gx1 - gx0) * idx / float(len(daily) - 1)
            span = (hi - ymin) or 1
            fy = gy1 - (gy1 - gy0) * ((cum[idx] - ymin) / span)
            fy = max(gy0 + int(4 * s), min(gy1, fy))
            self.circle(hdc, fx, fy, int(3 * s), rgb("ffffff"), rgb("4a86f2"))
            lab = "+" + fmt(mx)
            w1 = int(self.tw(hdc, lab, 9.5, True) + 12 * s)
            bx = min(max(gx0, fx - w1 / 2.0), gx1 - w1)
            by0 = fy - int(26 * s)
            if by0 < gy0:                      # 峰值贴顶时气泡改放点的下方
                by0 = fy + int(7 * s)
            self.rrect(hdc, bx, by0, bx + w1, by0 + int(16 * s),
                       int(4 * s), rgb("2b3a4a"))
            self.text(hdc, lab, bx, by0, bx + w1, by0 + int(16 * s),
                      9.5, rgb("ffffff"), True, DT_CENTER)
        for i, lab in enumerate(("30 天前", "15 天前", "今天")):
            if i == 0:
                self.text(hdc, lab, gx0, gy1 + int(7 * s), gx0 + int(60 * s),
                          gy1 + int(23 * s), 9, Theme.text, False, DT_LEFT)
            elif i == 2:
                self.text(hdc, lab, gx1 - int(60 * s), gy1 + int(7 * s), gx1,
                          gy1 + int(23 * s), 9, Theme.text, False, DT_RIGHT)
            else:
                xm = (gx0 + gx1) / 2.0
                self.text(hdc, lab, xm - int(30 * s), gy1 + int(7 * s),
                          xm + int(30 * s), gy1 + int(23 * s), 9, Theme.text,
                          False, DT_CENTER)

    def big_clean_table(self, hdc, l, t, r, b):
        """大文件与清理建议：当前层级最大的文件，带「查看 / 清理」按钮（都不删除）。"""
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        raw = list((self.data or {}).get("big_files") or [])
        pre = (self.pwd or "").lower()
        if pre:
            raw = [f for f in raw if (f.get("p") or "").lower().startswith(pre)]
        raw.sort(key=lambda f: -f.get("s", 0))
        rows = raw[:16]
        cl = self.cleanable()
        tip = ("可清理 %s · 点击定位，不会删除任何文件" % fmt(sum(
            c.get("size", 0) for c in cl))) if cl else ("点击行在资源管理器定位")
        self.card_title(hdc, "大文件与清理建议", inner[0], inner[1], inner[2], tip)
        W = max(240, inner[2] - inner[0])
        c_name = inner[0]
        c_type = inner[0] + int(W * 0.42)
        c_size = inner[0] + int(W * 0.53)
        c_time = inner[0] + int(W * 0.68)
        c_act = inner[2] - int(54 * s)
        hy = inner[1] + int(26 * s)
        self.fill(hdc, inner[0], hy, inner[2], hy + int(24 * s), Theme.head)
        for txt, x0, x1, al in (("文件名", c_name, c_type, DT_LEFT),
                                ("类型", c_type, c_size, DT_LEFT),
                                ("大小", c_size, c_time, DT_LEFT),
                                ("修改时间", c_time, c_act, DT_LEFT),
                                ("操作", c_act, inner[2], DT_CENTER)):
            self.text(hdc, txt, x0, hy, x1, hy + int(24 * s), 10, Theme.muted,
                      True, al)
        ry = hy + int(24 * s)
        rowh = int(26 * s)
        if not rows:
            self.text(hdc, "该层级没有容积较大的文件" if self.data else "扫描后显示",
                      inner[0], ry, inner[2], ry + int(40 * s), 10.5, Theme.faint,
                      False, DT_CENTER)
            return
        for i, f in enumerate(rows):
            if ry + rowh > b - int(6 * s):
                break
            path = f.get("p", "")
            k = "bigp_" + path
            # 斑马隔行底色（悬停时再压一层更明显的色）
            if i % 2 == 1:
                self.fill(hdc, inner[0], ry, inner[2], ry + rowh, ZEBRA)
            if self.hover_key == k:
                self.fill(hdc, inner[0], ry, inner[2], ry + rowh, Theme.hover)
            nm = os.path.basename(path) or path
            e2 = S._ext_label(path) or ""
            self.icon(hdc, c_name, ry + int(6 * s), 14, None, e2 or None)
            self.text(hdc, nm, c_name + int(20 * s), ry, c_type - int(6 * s),
                      ry + rowh, 10.5, Theme.text)
            self.text(hdc, e2 or "文件", c_type, ry, c_size - int(4 * s),
                      ry + rowh, 10, Theme.muted)
            self.text(hdc, fmt(f.get("s", 0)), c_size, ry, c_time - int(4 * s),
                      ry + rowh, 10.5, Theme.text, True)
            self.text(hdc, fmt_t(f.get("m")), c_time, ry, c_act - int(4 * s),
                      ry + rowh, 9.5, Theme.faint)
            # 只保留「查看」：打开所在目录并定位，不做任何删除动作
            w1 = int(44 * s)
            bx = c_act + int((inner[2] - c_act - w1) / 2)
            hk = k + "_view"
            hov = (self.hover_key == hk)
            self.rrect(hdc, bx, ry + int(4 * s), bx + w1, ry + rowh - int(4 * s),
                       int(9 * s),
                       mix(Theme.accent_soft, Theme.accent, 0.18) if hov
                       else Theme.accent_soft, Theme.accent)
            self.text(hdc, "查看", bx, ry + int(4 * s), bx + w1,
                      ry + rowh - int(4 * s), 9.5, Theme.accent, False, DT_CENTER)
            self.add_hot(bx, ry + int(4 * s), bx + w1, ry + rowh - int(4 * s),
                         "reveal", path, "在资源管理器查看该文件", "hand", hk)
            self.add_hot(inner[0], ry, c_act - int(4 * s), ry + rowh, "reveal", path,
                         "在资源管理器定位：%s" % nm, "hand", k)
            ry += rowh

    def insight_card(self, hdc, l, t, r, b):
        """智能洞察：重复文件 / 临时文件 / 长期未访问 + 合计可回收进度。"""
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        ins = self.insights or {}
        self.card_title(hdc, "智能洞察", inner[0], inner[1], inner[2],
                        "基于索引" if ins else "索引建好后显示")
        rows = [
            ("重复文件", ins.get("dup_size", 0),
             "%s 组同名同大小" % fmt_n(ins.get("dup_groups", 0)) if ins else "待建索引",
             Theme.purple),
            ("临时文件", self.cleanable_total(), "临时目录 / 回收站 / 更新缓存",
             Theme.amber),
            ("长期未访问", ins.get("old_size", 0),
             "%s 个文件超 1 年没改动" % fmt_n(ins.get("old_files", 0)) if ins
             else "待建索引", Theme.green),
        ]
        bigs = [f for f in ((self.data or {}).get("big_files") or [])
                if (f.get("s") or 0) >= 1024 ** 3]
        rows.append(("超大文件", sum(f.get("s", 0) for f in bigs),
                     "%d 个文件超过 1 GB" % len(bigs), rgb("2f6fed")))
        yy = inner[1] + int(24 * s)
        # 行高按可用高度自适应，卡片变矮时自动收紧，避免最后一行撞到合计行
        avail = (b - int(44 * s)) - yy
        rowh = max(int(34 * s), min(int(64 * s), avail / float(max(1, len(rows)))))
        tot = 0
        for i, (nm, sz, sub, col) in enumerate(rows):
            if yy + rowh > b - int(44 * s):
                break
            tot += sz or 0
            oy = yy + max(0.0, (rowh - int(33 * s)) / 2.0)   # 行内两行文字垂直居中
            self.rrect(hdc, inner[0], oy + int(2 * s), inner[0] + int(4 * s),
                       oy + int(29 * s), int(2 * s), col)
            self.text(hdc, nm, inner[0] + int(12 * s), oy, inner[2] - int(88 * s),
                      oy + int(18 * s), 10.5, Theme.ink2)
            self.text(hdc, fmt(sz or 0), inner[2] - int(88 * s), oy, inner[2],
                      oy + int(19 * s), 12.5, Theme.text, True, DT_RIGHT)
            self.text(hdc, sub, inner[0] + int(12 * s), oy + int(18 * s), inner[2],
                      oy + int(33 * s), 9, Theme.faint)
            if i < len(rows) - 1:
                self.hline(hdc, inner[0] + int(12 * s), inner[2],
                           yy + rowh - int(2 * s), Theme.line_soft)
            yy += rowh
        # 底部：合计可回收（重叠项不换算百分比，避免出现 >100% 的异常读数）
        used = self.disk_figures()[1] or 1
        pct = min(1.0, tot / float(used))
        bar_y = b - int(24 * s)
        self.text(hdc, "合计可回收 %s" % fmt(tot),
                  inner[0], bar_y - int(17 * s), inner[2], bar_y - int(3 * s),
                  9.5, Theme.faint)
        self.rrect(hdc, inner[0], bar_y, inner[2], bar_y + int(7 * s), int(4 * s),
                   rgb("eaeff7"))
        if pct > 0.001:
            w1 = max(int(8 * s), (inner[2] - inner[0]) * pct)
            self.rrect_grad(hdc, inner[0], bar_y, inner[0] + w1,
                            bar_y + int(7 * s), int(4 * s), rgb("5fc9a8"),
                            rgb("0f9d6e"))

    def _kpi_glyph(self, hdc, kind, cx, cy, u, c):
        """KPI 卡片右侧的小线稿图标（纯灰阶，四卡统一笔画）。"""
        if kind == 0:                                   # 磁盘
            self.seg_box(hdc, cx - 8 * u, cy - 5.4 * u, cx + 8 * u, cy + 5.4 * u, c, 1.2)
            self.seg(hdc, cx + 3.4 * u, cy - 5.4 * u, cx + 3.4 * u, cy + 5.4 * u, c, 1.2)
            self.circle(hdc, cx + 5.9 * u, cy, max(1.0, 1.05 * u), c)
        elif kind == 1:                                 # 占用柱状
            base = cy + 6.2 * u
            for dx, hh in ((-5.6, 4.0), (0, 8.0), (5.6, 12.0)):
                self.seg(hdc, cx + dx * u, base, cx + dx * u, base - hh * u, c, 1.9)
        elif kind == 2:                                 # 清理星芒
            d = 6.4 * u
            self.seg(hdc, cx, cy - d, cx, cy + d, c, 1.2)
            self.seg(hdc, cx - d, cy, cx + d, cy, c, 1.2)
            self.seg(hdc, cx - d * 0.52, cy - d * 0.52, cx - d * 0.24, cy - d * 0.24, c, 1.1)
            self.seg(hdc, cx + d * 0.24, cy + d * 0.24, cx + d * 0.52, cy + d * 0.52, c, 1.1)
        else:                                           # 文件夹
            self.rrect(hdc, cx - 7 * u, cy - 3.2 * u, cx + 7 * u, cy + 5.4 * u,
                       2 * u, Theme.card, c, 1.2)
            self.seg(hdc, cx - 7 * u, cy - 6.0 * u, cx - 2.2 * u, cy - 6.0 * u, c, 1.2)
            self.seg(hdc, cx - 2.2 * u, cy - 6.0 * u, cx - 0.4 * u, cy - 3.2 * u, c, 1.2)

    def kpi_card(self, hdc, l, t, r, b, label, value, sub, idx):
        """总览 KPI 卡：统一白卡 + 轻投影 + 黑色加粗数值 + 右侧灰阶图标。"""
        s = self.scale
        rad = int(12 * s)
        # 轻投影（下方 2px 极浅灰，制造浮起感）
        self.rrect(hdc, l + int(1 * s), t + int(2 * s), r + int(1 * s),
                   b + int(2 * s), rad, rgb("edf1f7"))
        self.rrect(hdc, l, t, r, b, rad, Theme.card, rgb("e8edf6"))
        pl = l + int(16 * s)
        pt = t + int(12 * s)
        pr = r - int(16 * s)
        self.text(hdc, label, pl, pt, pr, pt + int(14 * s), 10.5, Theme.muted)
        self.text(hdc, value, pl, pt + int(14 * s), pr, pt + int(50 * s), 23,
                  Theme.text, True)
        self.text(hdc, sub, pl, pt + int(48 * s), pr, pt + int(66 * s), 10.5,
                  Theme.faint)
        # 右侧图标徽章：四卡同色同尺寸，收紧右边空白
        bs = int(34 * s)
        bx = r - int(14 * s) - bs
        by = t + (b - t - bs) / 2.0
        if bx > pl + int(120 * s):
            self.rrect(hdc, bx, by, bx + bs, by + bs, int(10 * s), rgb("f3f6fb"))
            self._kpi_glyph(hdc, idx, bx + bs / 2.0, by + bs / 2.0, s * 0.98,
                            Theme.ink2)

    def ov_node(self):
        """总览页空间地图当前层级的节点（跟随路径过滤）。"""
        if self.deep_scope():
            nd = self._node_by_path(self.pwd)
            if nd is not None:
                return nd
        return self.tree

    def minimap(self, hdc, l, t, r, b):
        """总览页内嵌的空间地图（可点块进地图页）"""
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        node = self.ov_node()
        self.text(hdc, "空间地图", inner[0], inner[1], inner[2] - int(90 * s),
                  inner[1] + int(18 * s), 12.5, Theme.text, True)
        lw = int(self.tw(hdc, "完整地图 ›", 10) + 4 * s)
        hov = (self.hover_key == "ovmap_link")
        self.text(hdc, "完整地图 ›", inner[2] - lw, inner[1], inner[2],
                  inner[1] + int(18 * s), 10, Theme.accent if hov else Theme.faint,
                  hov, DT_RIGHT)
        self.add_hot(inner[2] - lw, inner[1], inner[2], inner[1] + int(18 * s),
                     "open_map", None, "打开完整空间地图", "hand", "ovmap_link")
        if not node:
            self.text(hdc, "先到「总览」扫描一次，这里会显示空间分布",
                      inner[0], inner[1] + int(30 * s), inner[2],
                      inner[1] + int(64 * s), 11, Theme.faint, False, DT_CENTER)
            return
        self.text(hdc, "%s · 合计 %s" % ((node.get("n") or "全部盘符"),
                                         fmt(node.get("s"))),
                  inner[0], inner[1] + int(19 * s), inner[2],
                  inner[1] + int(36 * s), 10, Theme.faint)
        area = (inner[0], inner[1] + int(40 * s), inner[2], b - int(4 * s))
        kids = [c for c in (node.get("c") or []) if c.get("s", 0) > 0]
        if not kids or area[3] - area[1] < int(40 * s):
            self.text(hdc, "该层级没有可展示的子项", area[0],
                      (area[1] + area[3]) / 2 - int(10 * s), area[2],
                      (area[1] + area[3]) / 2 + int(10 * s), 11, Theme.faint, False,
                      DT_CENTER)
            return
        kids.sort(key=lambda c: -c.get("s", 0))
        kids = kids[:40]
        rects = squarify([(kids[i].get("s", 0), i) for i in range(len(kids))],
                         area[0], area[1], area[2] - area[0], area[3] - area[1])
        gapp = int(2 * s)
        for (rr, idx) in rects:
            k = kids[idx]
            x1, y1, x2, y2 = rr[0] + gapp, rr[1] + gapp, rr[2] - gapp, rr[3] - gapp
            if x2 - x1 < 3 or y2 - y1 < 3:
                continue
            hk = "ovtm%s" % (k.get("p") or k.get("n"))
            on = (self.hover_key == hk)
            self.rrect(hdc, x1, y1, x2, y2, int(4 * s),
                       Theme.accent_soft if on else rgb("ffffff"),
                       Theme.accent if on else rgb("e3e9f3"))
            if x2 - x1 > int(48 * s) and y2 - y1 > int(22 * s):
                self.text(hdc, k.get("n") or "", x1 + int(7 * s), y1 + int(2 * s),
                          x2 - int(7 * s), y1 + int(19 * s), 10,
                          Theme.accent if on else Theme.text, True)
                if y2 - y1 > int(40 * s):
                    self.text(hdc, fmt(k.get("s", 0)), x1 + int(7 * s),
                              y1 + int(19 * s), x2 - int(7 * s), y1 + int(34 * s), 9.5,
                              Theme.muted)
            self.add_hot(x1, y1, x2, y2, "ov_tm", k,
                         "%s\n%s · 点击查看完整地图" % (k.get("n", ""), fmt(k.get("s"))),
                         "hand", hk)

    def deep_scope(self):
        """当前路径是否比盘符根更深（用于判断是否处于「路径过滤」状态）。"""
        p = (self.pwd or "").rstrip("\\")
        return len(p) > 2

    def kpi_data(self):
        nd, ssz, sf, sd = self.scope()
        if not self.deep_scope():
            if self.active == "ALL":
                total = sum(d.get("total", 0) for d in self.drives)
                used = sum(d.get("used", 0) for d in self.drives)
                pct = (used / total * 100) if total else 0
                return [
                    ("全部盘总容量", fmt(total),
                     "%d 个盘符 · 已用 %.0f%%" % (len(self.drives), pct), Theme.text),
                    ("已使用", fmt(used), "剩余 %s" % fmt(total - used), Theme.accent),
                    ("可清理", fmt(self.cleanable_total()), "临时文件 / 回收站 / 更新缓存",
                     Theme.green),
                    ("文件总数", fmt_n(sf), "文件夹 %s 个" % fmt_n(sd), Theme.purple),
                ]
            d = next((x for x in self.drives if x["letter"] == self.active), None)
            total = d.get("total", 0) if d else 0
            used = d.get("used", 0) if d else 0
            pct = (used / total * 100) if total else 0
            return [
                ("%s 盘容量" % (self.active or "").rstrip(":"), fmt(total),
                 "已用 %.0f%% · 剩余 %s" % (pct, fmt(total - used)), Theme.text),
                ("已使用", fmt(used),
                 "%s · %s" % (self.active or "", d.get("label", "") if d else ""),
                 Theme.accent),
                ("可清理", fmt(self.cleanable_total()), "本盘临时文件 / 回收站", Theme.green),
                ("本盘文件", fmt_n(sf), "文件夹 %s 个" % fmt_n(sd), Theme.purple),
            ]
        d = next((x for x in self.drives if x["letter"] == self.active), None)
        total = d.get("total", 0) if d else 0
        pct = (ssz / total * 100) if total else 0
        tail = os.path.basename((self.pwd or "").rstrip("\\")) or (self.active or "")
        return [
            ("%s 占用" % tail, fmt(ssz), "占本盘 %.0f%%" % pct, Theme.text),
            ("本盘已用", fmt(d.get("used", 0) if d else 0),
             "%s 剩余 %s" % (self.active or "", fmt(total - (d.get("used", 0) if d else 0))),
             Theme.accent),
            ("可清理", fmt(self.cleanable_total()), "临时文件 / 回收站 / 更新缓存",
             Theme.green),
            ("文件夹", fmt_n(sd), "该路径 · 文件 %s 个" % fmt_n(sf), Theme.purple),
        ]

    def cleanable(self):
        cands = list((self.report or {}).get("cleanable") or [])
        if self.active == "ALL":
            return cands
        pre = (self.active or "").upper()
        return [c for c in cands if str(c.get("path", "")).upper().startswith(pre)]

    def cleanable_total(self):
        if not self.report:
            return 0
        return sum(c.get("size", 0) for c in self.cleanable())

    def chart_disk(self, hdc, l, t, r, b):
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        scoped = self.deep_scope()
        tail = os.path.basename((self.pwd or "").rstrip("\\"))
        self.card_title(hdc, "磁盘空间", inner[0], inner[1], inner[2],
                        tail or ("全部盘" if self.active == "ALL" else self.active))
        if self.active == "ALL" and not scoped:
            total = sum(d.get("total", 0) for d in self.drives)
            used = sum(d.get("used", 0) for d in self.drives)
        else:
            d = next((x for x in self.drives if x["letter"] == self.active), None)
            if not d and not scoped:
                self.text(hdc, "暂无数据", inner[0], inner[1] + int(40 * s), inner[2],
                          b, 11, Theme.faint, False, DT_CENTER)
                return
            total = d["total"] if d else 0
            _nd, ssz, _f, _dd = self.scope()
            used = ssz if scoped else (d["used"] if d else 0)
        total = max(total or 0, used)
        free = max(0, total - used)
        rad = int(56 * s)
        cx = inner[0] + rad + int(6 * s)
        cy = inner[1] + int(46 * s) + rad
        self.donut(hdc, cx, cy, rad, int(14 * s),
                   [(Theme.accent, used), (rgb("dbe3ef"), free)], total,
                   fmt(used) if scoped else fmt(total),
                   "该路径占用" if scoped else "总容量")
        lx = cx + rad + int(24 * s)
        yy = inner[1] + int(46 * s)
        pct_u = (used / total * 100) if total else 0
        rows = [("该路径" if scoped else "已用", used, Theme.accent, "%.0f%%" % pct_u),
                ("其他" if scoped else "可用", free, rgb("dbe3ef"),
                 "%.0f%%" % (100 - pct_u if total else 0))]
        for label, val, col, pctxt in rows:
            self.rrect(hdc, lx, yy + int(5 * s), lx + int(9 * s), yy + int(14 * s),
                       int(2 * s), col)
            self.text(hdc, label, lx + int(15 * s), yy, lx + int(80 * s),
                      yy + int(19 * s), 11, Theme.muted)
            self.text(hdc, fmt(val), lx + int(15 * s), yy + int(19 * s),
                      lx + int(15 * s) + int(90 * s), yy + int(41 * s), 13.5,
                      Theme.text, True)
            # 比例与数值同字体同字号（用户要求：格式、字体保持一致）
            self.text(hdc, pctxt, lx + int(15 * s) + int(92 * s), yy + int(19 * s),
                      inner[2], yy + int(41 * s), 13.5, Theme.faint, True, DT_LEFT)
            yy += int(52 * s)

    def chart_ext(self, hdc, l, t, r, b):
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        self.card_title(hdc, "文件类型分布", inner[0], inner[1], inner[2],
                        "全盘" if self.deep_scope() else
                        ("全部盘" if self.active == "ALL" else "本盘"))
        ext = list((self.data or {}).get("ext") or [])[:6]
        if not ext:
            self.text(hdc, "扫描后显示", inner[0], inner[1] + int(60 * s), inner[2], b,
                      11, Theme.faint, False, DT_CENTER)
            return
        total = sum(e[1] for e in ext) or 1
        rad = int(52 * s)
        cx = inner[0] + rad + int(4 * s)
        cy = inner[1] + int(50 * s) + rad
        items = [(rgb(PALETTE[i % len(PALETTE)]), e[1]) for i, e in enumerate(ext)]
        allsz = sum(e[1] for e in ext)
        self.donut(hdc, cx, cy, rad, int(12 * s), items, total,
                   fmt(allsz), "Top %d 合计" % len(ext))
        # ---------------- 图例：色块 + 名称 + 大小 同一行靠近，整块贴右
        rowh = int(21 * s)
        dot = int(8 * s)
        gap = int(7 * s)
        rows = [(rgb(PALETTE[i % len(PALETTE)]), str(e[0]), fmt(e[1]))
                for i, e in enumerate(ext)]
        nmw = max([int(self.tw(hdc, n, 10.5)) for _c, n, _z in rows] or [0])
        szw = max([int(self.tw(hdc, z, 10.5)) for _c, _n, z in rows] or [0])
        szx = inner[2] - szw - int(2 * s)          # 大小列左边界（右对齐）
        nmx = szx - int(11 * s)                    # 名称紧贴大小，间距固定
        lim = cx + rad + int(12 * s)
        nmw = max(int(20 * s), min(nmw, nmx - lim))
        yy = inner[1] + int(44 * s)
        for col, nm, sz in rows:
            if yy + rowh > b - int(12 * s):
                break
            nw = min(int(self.tw(hdc, nm, 10.5)), nmw)
            dx = nmx - nw - gap - dot
            self.rrect(hdc, dx, yy + int(5 * s), dx + dot, yy + int(5 * s) + dot,
                       int(2 * s), col)
            self.text(hdc, nm, dx + dot + gap, yy, nmx, yy + int(18 * s), 10.5,
                      Theme.ink2)
            self.text(hdc, sz, szx, yy, inner[2], yy + int(18 * s), 10.5,
                      Theme.faint, False, DT_RIGHT)
            yy += rowh

    def chart_age(self, hdc, l, t, r, b):
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        self.card_title(hdc, "文件年代分布", inner[0], inner[1], inner[2],
                        "全盘" if self.deep_scope() else None)
        ages = (self.data or {}).get("ages") or [0] * 5
        total = sum(ages) or 1
        if not sum(ages):
            self.text(hdc, "扫描后显示", inner[0], inner[1] + int(70 * s), inner[2], b,
                      11, Theme.faint, False, DT_CENTER)
            return
        base = b - int(36 * s)
        top = inner[1] + int(44 * s)
        n = 5
        slot = (inner[2] - inner[0]) / float(n)
        bw = min(int(26 * s), int(slot * 0.46))
        mx = max(ages) or 1
        for i in range(n):
            cx = inner[0] + slot * (i + 0.5)
            x = cx - bw / 2.0
            hgt = max(int(3 * s), (base - top - int(22 * s)) * (ages[i] / float(mx)))
            y0 = base - hgt
            col = rgb(AGE_COLORS[i])
            self.rrect(hdc, x, y0, x + bw, base, int(5 * s), col)
            pct = ages[i] / total * 100
            self.text(hdc, "%.0f%%" % pct, cx - int(30 * s), y0 - int(17 * s),
                      cx + int(30 * s), y0 - int(1 * s), 10, Theme.ink2, True, DT_CENTER)
            self.text(hdc, AGE_LABELS[i], cx - int(slot / 2), base + int(6 * s),
                      cx + int(slot / 2), base + int(22 * s), 10, Theme.faint, False,
                      DT_CENTER)
            self.add_hot(cx - slot / 2, top, cx + slot / 2, base + int(22 * s), "noop",
                         None, "%s：%s（%.1f%%）" % (AGE_LABELS[i], fmt(ages[i]), pct),
                         None, "age%d" % i)

    def drive_table(self, hdc, l, t, r, b):
        s = self.scale
        inner = self.card(hdc, l, t, r, b)
        drives = self.drives if self.active == "ALL" else [
            d for d in self.drives if d["letter"] == self.active]
        self.card_title(hdc, "磁盘一览", inner[0], inner[1], inner[2],
                        ("共 %d 个盘符" % len(self.drives)) if self.active == "ALL"
                        else ("仅显示 %s" % self.active))
        W = max(200, inner[2] - inner[0])
        c0 = inner[0] + int(W * 0.04)
        c1 = inner[0] + int(W * 0.11)
        c2 = inner[0] + int(W * 0.27)
        c3 = inner[0] + int(W * 0.59)
        c4 = inner[0] + int(W * 0.86)
        c5 = inner[2] - int(W * 0.02)
        hy = inner[1] + int(24 * s)
        ry_h = int(30 * s)
        self.fill(hdc, inner[0], hy, inner[2], hy + int(26 * s), Theme.head)
        self.text(hdc, "盘符", c0, hy, c1, hy + ry_h, 10.5, Theme.muted, True)
        self.text(hdc, "类型", c1, hy, c2, hy + ry_h, 10.5, Theme.muted, True)
        self.text(hdc, "已用 / 总容量", c2, hy, c3, hy + ry_h, 10.5, Theme.muted, True)
        self.text(hdc, "占用条", c3, hy, c4, hy + ry_h, 10.5, Theme.muted, True)
        self.text(hdc, "使用率", c4, hy, c5, hy + ry_h, 10.5, Theme.muted, True, DT_RIGHT)
        ry = hy + int(26 * s)
        for d in drives:
            if ry + ry_h > b - int(4 * s):
                break
            on = d["letter"] == self.active
            if on:
                self.fill(hdc, inner[0], ry, inner[2], ry + ry_h, Theme.accent_soft)
            self.text(hdc, d["letter"], c0, ry, c1, ry + ry_h, 12, Theme.text, True)
            self.text(hdc, d.get("label", ""), c1, ry, c2, ry + ry_h, 11, Theme.muted)
            self.text(hdc, "%s / %s" % (fmt(d["used"]), fmt(d["total"])),
                      c2, ry, c3, ry + ry_h, 11, Theme.ink2)
            barx = c3 + int(2 * s)
            bare = c4 - int(10 * s)
            bw2 = max(int(24 * s), bare - barx)
            by1, by2 = ry + int(11 * s), ry + int(19 * s)
            self.rrect(hdc, barx, by1, barx + bw2, by2, int(4 * s), rgb("eaeff7"))
            pct = max(0.0, min(1.0, d.get("usedPct", 0) / 100.0))
            fw = max(int(6 * s), bw2 * pct)
            self.rrect_grad(hdc, barx, by1, barx + fw, by2, int(4 * s),
                            rgb("8fb2f7"), rgb("2f6fed"))
            self.text(hdc, "%.0f%%" % d.get("usedPct", 0), c4, ry,
                      c5, ry + ry_h, 11.5, Theme.accent, True, DT_RIGHT)
            ry += ry_h

        # ---------------- 大文件排行（当前层级，点击在资源管理器定位）
        cy = ry + int(6 * s)
        if cy + int(20 * s) >= b - int(4 * s):
            return
        self.hline(hdc, c0, c5, cy, Theme.line_soft)
        cy += int(9 * s)
        raw = list((self.data or {}).get("big_files") or [])
        pre = (self.pwd or "").lower()
        if pre:
            raw = [f for f in raw if (f.get("p") or "").lower().startswith(pre)]
        raw.sort(key=lambda f: -f.get("s", 0))
        rows2 = raw[:12]
        self.text(hdc, "大文件排行", c0, cy, c0 + int(74 * s), cy + int(18 * s), 11,
                  Theme.text, True)
        if not self.data:
            hint = "扫描后显示"
        elif not rows2:
            hint = "该层级没有容积较大的文件"
        else:
            hint = "当前层级 · 共 %s · 点击定位" % fmt(sum(f.get("s", 0) for f in rows2))
        self.text(hdc, hint, c0 + int(70 * s), cy, c5, cy + int(18 * s), 10,
                  Theme.faint)
        cy += int(22 * s)
        nr = c0
        n1 = inner[0] + int(W * 0.44)
        p1 = inner[0] + int(W * 0.82)
        rowh = int(24 * s)
        for i, f in enumerate(rows2):
            if cy + rowh > b - int(4 * s):
                break
            path = f.get("p", "")
            k = "bigp_" + path
            if self.hover_key == k:
                self.fill(hdc, inner[0], cy, inner[2], cy + rowh, Theme.zebra)
            self.text(hdc, "%d" % (i + 1), nr, cy, c0 + int(16 * s), cy + rowh, 10,
                      Theme.faint)
            nm = os.path.basename(path) or path
            self.text(hdc, nm, c0 + int(18 * s), cy, n1, cy + rowh, 10.5, Theme.text)
            self.text(hdc, os.path.dirname(path), n1 + int(8 * s), cy,
                      p1 - int(8 * s), cy + rowh, 9.5, Theme.faint)
            self.text(hdc, fmt(f.get("s", 0)), p1, cy, c5, cy + rowh, 11,
                      Theme.text, True, DT_RIGHT)
            self.add_hot(inner[0], cy, inner[2], cy + rowh, "reveal", path,
                         "在资源管理器定位：%s" % nm, "hand", k)
            cy += rowh

    def paint_map(self, hdc, rc):
        s = self.scale
        l, t, r, b = rc
        cw, chh = self.client()
        # 整页白色画布（用户要求：空间地图用白色）
        self.fill(hdc, 0, self.topbar_h() + int(34 * s), cw, chh, Theme.card)
        # 面包屑工具条
        bar = t
        crumb_h = int(26 * s)
        x = l
        chain = self.tm_path or []
        for i, node in enumerate(chain):
            name = node.get("n") or "(根)"
            if i > 0:
                self.text(hdc, "›", x, bar, x + int(12 * s), bar + crumb_h, 12, Theme.faint,
                          False, DT_CENTER)
                x += int(12 * s)
            on_last = (i == len(chain) - 1)
            wdt = int(self.tw(hdc, name, 11.5, on_last) + 14 * s)
            self.text(hdc, name, x, bar, x + wdt, bar + crumb_h, 11.5,
                      Theme.text if on_last else Theme.accent, on_last)
            if not on_last:
                self.add_hot(x, bar, x + wdt, bar + crumb_h, "tm_up", i, None, "hand",
                             "crumb%d" % i)
            x += wdt + int(4 * s)
        node = chain[-1] if chain else self.tree
        if node:
            self.text(hdc, "合计 %s · 文件 %s 个" % (fmt(node.get("s")), fmt_n(node.get("f"))),
                      l, bar, r, bar + crumb_h, 10.5, Theme.faint, False, DT_RIGHT)
        bar_b = bar + crumb_h + int(8 * s)
        self.hline(hdc, l, r, bar_b, Theme.line)
        top = bar_b + int(12 * s)
        area = (l, top, r, b)
        if not node or not node.get("c"):
            self.text(hdc, "该层级没有可展示的子项（或尚未扫描）", area[0], area[1],
                      area[2], (area[1] + area[3]) / 2, 12, Theme.faint, False, DT_CENTER)
            return
        kids = [c for c in node["c"] if c.get("s", 0) > 0]
        kids.sort(key=lambda c: -c.get("s", 0))
        kids = kids[:60]
        rects = squarify([(kids[i].get("s", 0), i) for i in range(len(kids))],
                         area[0], area[1], area[2] - area[0], area[3] - area[1])
        self.tm_rect_cache = []
        gapp = int(2 * s)
        for (rr, idx) in rects:
            k = kids[idx]
            x1, y1, x2, y2 = rr[0] + gapp, rr[1] + gapp, rr[2] - gapp, rr[3] - gapp
            if x2 - x1 < 3 or y2 - y1 < 3:
                continue
            hk = "tm%s" % (k.get("p") or k.get("n"))
            hov = (self.hover_key == hk)
            # 全部纯白底 + 细描边（用户要求：空间地图全部使用白色）
            self.rrect(hdc, x1, y1, x2, y2, int(4 * s),
                       Theme.accent_soft if hov else rgb("ffffff"),
                       Theme.accent if hov else rgb("e3e9f3"))
            if x2 - x1 > int(46 * s) and y2 - y1 > int(26 * s):
                name = k.get("n") or ""
                head = int(21 * s)
                self.text(hdc, name, x1 + int(8 * s), y1 + int(2 * s), x2 - int(8 * s),
                          y1 + head, 10.5, Theme.accent if hov else Theme.text, True)
                if y2 - y1 > int(46 * s):
                    self.text(hdc, fmt(k.get("s", 0)), x1 + int(8 * s),
                              y1 + head, x2 - int(8 * s), y1 + head + int(16 * s), 10,
                              Theme.muted)
            if k.get("unscanned"):
                self.add_hot(x1, y1, x2, y2, "tm_scan", k["unscanned"],
                             "%s 还没有扫描数据，点击这里开始扫描" % k["unscanned"],
                             "hand", hk)
                continue
            if k.get("agg"):
                self.add_hot(x1, y1, x2, y2, "noop", None,
                             "%s 个小目录合计 %s（单个都不到 24MB，已合并）"
                             % (k.get("d"), fmt(k.get("s"))), "hand", hk)
                continue
            tip = "%s\n%s · 占本层 %.1f%%\n文件 %s 个 · 文件夹 %s 个" % (
                k.get("n", ""), fmt(k.get("s")), k.get("s", 0) * 100.0 / (node.get("s") or 1),
                fmt_n(k.get("f")), fmt_n(k.get("d")))
            self.add_hot(x1, y1, x2, y2, "tm_enter", k, tip, "hand", hk)
            self.tm_rect_cache.append((k, rr))

    # -------------------------------------------------------------- 表格通用
    def check_box(self, hdc, x, y, on=False, size=14):
        s = self.scale
        sz = size * s
        if on:
            self.rrect(hdc, x, y, x + sz, y + sz, 3.5 * s, Theme.accent)
            pn = GP.pen(rgb("ffffff"), max(1.2, 1.7 * s), 1, 0)
            g = self._g
            if g and pn:
                GP.dll.GdipDrawLine(g, pn, x + sz * 0.26, y + sz * 0.53,
                                    x + sz * 0.44, y + sz * 0.71)
                GP.dll.GdipDrawLine(g, pn, x + sz * 0.44, y + sz * 0.71,
                                    x + sz * 0.76, y + sz * 0.30)
            GP.del_pen(pn)
        else:
            self.rrect(hdc, x, y, x + sz, y + sz, 3.5 * s, rgb("ffffff"),
                       rgb("ccd6e5"), 1)

    def icon(self, hdc, x, y, size=15, kind=None, ext=None):
        """文件类型图标：文件夹画形状，文件是彩色圆角块 + 类型字。"""
        s = self.scale
        sz = size * s
        if kind == "dir":
            self.rrect(hdc, x + sz * 0.03, y + sz * 0.10, x + sz * 0.48, y + sz * 0.44,
                       2 * s, rgb("e2a010"))
            self.rrect(hdc, x, y + sz * 0.24, x + sz, y + sz * 0.94, 2.6 * s,
                       rgb("f7b731"))
            return
        glyph, col = kind_of(ext)
        self.rrect(hdc, x, y, x + sz, y + sz, 3.8 * s, col)
        self.text(hdc, glyph, x, y - s, x + sz, y + sz + s, size * 0.56, rgb("ffffff"),
                  True, DT_CENTER)

    _SORTMAP = {"n": "rn", "s": "rs", "m": "rt", "t": "re", "rel": "rdir",
                "dir": "rdir"}

    def _apply_sort(self, rows, skey):
        st = self.sort.get(skey) or [None, 0]
        if not st[1] or not st[0]:
            return rows
        k = self._SORTMAP.get(st[0])
        if not k:
            return rows
        try:
            return sorted(rows, key=lambda x: x.get(k) or 0, reverse=(st[1] < 0))
        except TypeError:
            return rows

    def table(self, hdc, rect, cols, rows, skey, empty_text, on_row_dbl="noop"):
        """cols: [(标题, 权重, 对齐, 字段)]；行高紧凑、带勾选列与类型图标。"""
        s = self.scale
        l, t, r, b = rect
        hh = int(27 * s)
        rh = int(22 * s)
        boxw = int(28 * s)
        chk = self.checked.setdefault(skey, set())
        paths = [x.get("p") for x in rows if x.get("p")]
        allon = bool(paths) and all(p in chk for p in paths)
        self.fill(hdc, l, t, r, t + hh, Theme.head)
        hline_y = t + hh
        self.hline(hdc, l, r, hline_y, Theme.line)
        xs = [l + boxw]
        avail = (r - l) - boxw - int(6 * s)
        tot = sum(c[1] for c in cols) or 1
        for c in cols:
            xs.append(xs[-1] + avail * c[1] / tot)

        self.check_box(hdc, l + int(8 * s), t + int((hh - 12 * s) / 2), allon, 12)
        self.add_hot(l, t, l + boxw, t + hh, "chk_all", skey, "全选 / 取消全选", "hand",
                     "chkall_" + skey)
        st = self.sort.get(skey) or [None, 0]
        for i, c in enumerate(cols):
            cl = xs[i] + (int(9 * s) if i else int(6 * s))
            cr = xs[i + 1] - int(8 * s)
            self.text(hdc, c[0], cl, t, cr, t + hh, 10.5, Theme.muted, True, c[2])
            if st[0] == c[3] and st[1]:
                arrow = "↓" if st[1] < 0 else "↑"
                if c[2] == DT_RIGHT:
                    self.text(hdc, arrow, cr - int(2 * s), t, cr + int(12 * s), t + hh,
                              9, Theme.accent, True)
                else:
                    tw = self.tw(hdc, c[0], 10.5, True)
                    self.text(hdc, arrow, cl + tw + int(3 * s), t, cl + tw + int(18 * s),
                              t + hh, 9, Theme.accent, True)
            self.add_hot(xs[i], t, xs[i + 1], t + hh, "sort", (skey, c[3]),
                         "点击切换 正序 / 倒序 / 取消排序", "hand",
                         "hdr_%s_%s" % (skey, c[3]))

        body_t = t + hh
        lvl = self.save(hdc)
        self.clip(hdc, l, body_t, r, b)
        off = self.scroll.get(skey, 0)
        vis = max(1, int((b - body_t) / rh))
        if off > max(0, len(rows) - vis):
            off = max(0, len(rows) - vis)
            self.scroll[skey] = off
        self.scroll[skey + "_max"] = max(0, len(rows) - vis)
        if not rows:
            self.text(hdc, empty_text, l, body_t + int(12 * s), r,
                      body_t + int(52 * s), 11.5, Theme.faint, False, DT_CENTER)
        for i in range(off, min(len(rows), off + vis + 1)):
            row = rows[i]
            y = body_t + (i - off) * rh
            if y > b:
                break
            sel = row.get("p") in chk
            hov = (self.hover_key == "row_%s_%d" % (skey, i))
            if i % 2:                      # 斑马底色
                self.fill(hdc, l, y, r, y + rh, Theme.zebra)
            if sel:
                self.fill(hdc, l, y, r, y + rh, Theme.accent_soft)
            elif hov:
                self.fill(hdc, l, y, r, y + rh, Theme.hover)
            self.check_box(hdc, l + int(8 * s), y + int((rh - 12 * s) / 2), sel, 12)
            for ci, c in enumerate(cols):
                val = row.get(c[3], "")
                cl = xs[ci] + (int(9 * s) if ci else int(6 * s))
                cr = xs[ci + 1] - int(8 * s)
                if ci == 0:
                    self.icon(hdc, xs[0] + int(5 * s), y + int((rh - 13 * s) / 2), 13,
                              row.get("k"), row.get("e"))
                    cl = xs[0] + int(5 * s) + int(19 * s)
                colc = Theme.text if ci == 0 else Theme.ink2
                if c[3] in ("t", "rel", "dir"):
                    colc = Theme.faint
                self.text(hdc, val, cl, y, cr, y + rh, 10.5 if ci == 0 else 10, colc,
                          ci == 0, c[2])
            self.hline(hdc, l, r, y + rh - 1, Theme.line_soft)
            self.add_hot(l, y, r, y + rh, on_row_dbl, row, None, "hand",
                         "row_%s_%d" % (skey, i))
            self.add_hot(l, y, l + boxw, y + rh, "chk", (skey, row.get("p")), None,
                         "hand", "chkrow_%s_%d" % (skey, i))
        self.restore(hdc, lvl)

    def sel_bar(self, hdc, l, t, r, key):
        """已选提示条"""
        s = self.scale
        chk = self.checked.get(key) or set()
        if not chk:
            return
        total = 0
        for row in getattr(self, "_rowcache_" + key, []) or []:
            if row.get("p") in chk:
                total += row.get("sz") or 0
        txt = "已选 %d 项 · 合计 %s" % (len(chk), fmt(total))
        w1 = int(self.tw(hdc, txt, 10.5) + 18 * s)
        bh = int(22 * s)
        cw = int(46 * s)                    # 清除
        kw = int(52 * s)                    # 复制到剪贴板
        x_clear0 = r - cw
        x_copy1 = x_clear0 - int(6 * s)
        x_copy0 = x_copy1 - kw
        bx = x_copy0 - int(6 * s) - w1
        self.rrect(hdc, bx, t, bx + w1, t + bh, int(5 * s), Theme.accent_soft)
        self.text(hdc, txt, bx, t, bx + w1, t + bh, 10.5, Theme.accent, True,
                  DT_CENTER)
        # 复制：把选中项（文件 / 文件夹都行）以文件形式放进剪贴板，
        # 在微信 / QQ / 钉钉聊天框直接 Ctrl+V 就是发送文件本体
        self.rrect(hdc, x_copy0, t, x_copy1, t + bh, int(5 * s), Theme.accent)
        self.text(hdc, "复制", x_copy0, t, x_copy1, t + bh, 10.5, rgb("ffffff"),
                  True, DT_CENTER)
        self.add_hot(x_copy0, t, x_copy1, t + bh, "sel_copy", key, None, "hand",
                     "selcopy_" + key)
        self.rrect(hdc, x_clear0, t, r, t + bh, int(5 * s), rgb("f7f9fc"),
                   Theme.line)
        self.text(hdc, "清除", x_clear0, t, r, t + bh, 10.5, Theme.muted, False,
                  DT_CENTER)
        self.add_hot(x_clear0, t, r, t + bh, "sel_clear", key, None, "hand",
                     "selclr_" + key)

    # -------------------------------------------------------------- 文件夹明细
    def paint_dirs(self, hdc, rc):
        s = self.scale
        l, t, r, b = rc
        inner = self.card(hdc, l, t, r, b)
        y = inner[1]
        up = os.path.dirname(self.pwd.rstrip("\\"))
        can_up = bool(up) and len(self.pwd.rstrip("\\")) > 2
        bw = int(58 * s)
        self.rrect(hdc, inner[0], y, inner[0] + bw, y + int(26 * s), int(6 * s),
                   rgb("f2f5fa") if can_up else rgb("f8fafc"), Theme.line)
        self.text(hdc, "↑ 上级", inner[0], y, inner[0] + bw, y + int(26 * s), 11,
                  Theme.ink2 if can_up else Theme.faint, False, DT_CENTER)
        if can_up:
            self.add_hot(inner[0], y, inner[0] + bw, y + int(26 * s), "dir_up", None,
                         None, "hand", "dirup")
        px = inner[0] + bw + int(8 * s)
        srch_w = int(210 * s)
        path_txt = self.pwd or ("全部盘符" if self.active == "ALL" else "")
        self.text(hdc, path_txt, px, y, inner[2] - srch_w - int(10 * s),
                  y + int(26 * s), 11.5, Theme.ink2, False, DT_LEFT, "Consolas")
        self.add_hot(px, y, inner[2] - srch_w - int(10 * s), y + int(26 * s), "focus_path",
                     None, "点这里可直接改路径", "ibeam", "dirpath")
        sx = inner[2] - srch_w
        focused = (self.editing == "dirs")
        self.rrect(hdc, sx, y, inner[2], y + int(26 * s), int(6 * s),
                   rgb("ffffff"), Theme.accent if focused else Theme.line)
        q = self.dirs_q or ""
        show = q if q else "搜索当前文件夹…"
        self.text(hdc, show, sx + int(9 * s), y, inner[2] - int(9 * s), y + int(26 * s),
                  11, Theme.text if q else Theme.faint)
        if focused:
            tx = sx + int(9 * s) + (self.tw(hdc, q, 11) if q else 0)
            self.fill(hdc, tx + 1, y + int(6 * s), tx + 2, y + int(20 * s), Theme.accent)
        self.add_hot(sx, y, inner[2], y + int(26 * s), "focus_search", None, None, "ibeam",
                     "dirsearch")

        ry = y + int(32 * s)
        on = self.dirs_recursive
        tw_ = int(88 * s)
        self.rrect(hdc, inner[0], ry, inner[0] + tw_, ry + int(22 * s), int(5 * s),
                   Theme.accent_soft if on else rgb("f7f9fc"),
                   Theme.accent if on else Theme.line)
        self.text(hdc, "含子文件夹" if on else "仅当前层", inner[0], ry, inner[0] + tw_,
                  ry + int(22 * s), 10.5, Theme.accent if on else Theme.muted, on,
                  DT_CENTER)
        self.add_hot(inner[0], ry, inner[0] + tw_, ry + int(22 * s), "dir_rec", None,
                     "包含所有下级目录一起搜索", "hand", "dirrec")
        cnt = 0
        tr = ""
        if self.dir_cache:
            cnt = len(self.dir_cache.get("dirs", [])) + len(self.dir_cache.get("files", []))
            if self.dir_cache.get("truncated"):
                tr = " · 结果已截断"
        self.text(hdc, "共 %s 项%s" % (fmt_n(cnt), tr), inner[0] + tw_ + int(12 * s), ry,
                  inner[2] - int(130 * s), ry + int(22 * s), 10.5, Theme.faint)
        self.sel_bar(hdc, inner[0], ry, inner[2], "dirs")

        tt = ry + int(28 * s)
        rows = self.dir_rows()
        cols = [("名称", 40, DT_LEFT, "n"), ("类型", 11, DT_LEFT, "t"),
                ("大小", 11, DT_RIGHT, "s"), ("修改时间", 17, DT_LEFT, "m"),
                ("所在位置", 21, DT_LEFT, "rel")]
        self.table(hdc, (inner[0], tt, inner[2], b - int(2 * s)), cols, rows, "dirs",
                   "空文件夹" if not self.dirs_q else "没有匹配项",
                   on_row_dbl="dir_row")

    def _loc_text(self, loc):
        """所在位置显示文本：盘符根保留 "C:\\"，其余去掉尾部反斜杠。"""
        if len(loc) > 3:
            return loc.rstrip("\\")
        return loc

    def dir_rows(self):
        if not self.dir_cache:
            return []
        q = (self.dirs_q or "").strip().lower()
        base = self.pwd or ""
        out = []
        for d in self.dir_cache.get("dirs", []):
            if q and q not in d["n"].lower():
                continue
            if d.get("drv"):
                out.append({"n": d["n"], "t": d.get("rel") or "本地磁盘",
                            "s": fmt(d.get("s", 0)),
                            "m": "—", "rel": "", "p": d["p"], "d": 1,
                            "sz": d.get("s", 0), "k": "dir", "e": None,
                            "rn": d["n"].lower(), "rs": d.get("s", 0), "rt": 0,
                            "re": d.get("rel") or "本地磁盘", "rdir": "",
                            "drv": d["drv"]})
                continue
            sz = self._dir_size(d["p"])
            rl = d.get("rel") or ""
            loc = (base + rl) if rl else base
            out.append({"n": d["n"], "t": "文件夹",
                        "s": fmt(sz) if sz is not None else "—",
                        "m": fmt_t(d["m"]), "rel": self._loc_text(loc),
                        "p": d["p"], "d": 1, "sz": sz or 0,
                        "k": "dir", "e": None, "rn": d["n"].lower(), "rs": sz or 0,
                        "rt": d.get("m") or 0, "re": "文件夹",
                        "rdir": self._loc_text(loc)})
        for f in self.dir_cache.get("files", []):
            ext = S._ext_label(f["n"])
            if q and q not in f["n"].lower() and q not in ext:
                continue
            rl = f.get("rel") or ""
            loc = (base + rl) if rl else base
            out.append({"n": f["n"], "t": ext or "文件", "s": fmt(f["s"]),
                        "m": fmt_t(f["m"]), "rel": self._loc_text(loc), "p": f["p"],
                        "d": 0, "sz": f["s"], "k": None, "e": ext,
                        "rn": f["n"].lower(), "rs": f["s"], "rt": f.get("m") or 0,
                        "re": ext, "rdir": self._loc_text(loc)})
        self._rowcache_dirs = out
        return self._apply_sort(out, "dirs")

    # -------------------------------------------------------------- 大文件
    def paint_big(self, hdc, rc):
        s = self.scale
        l, t, r, b = rc
        inner = self.card(hdc, l, t, r, b)
        files = self.big_rows()
        total = sum(f["sz"] for f in files)
        has_sel = bool(self.checked.get("big"))
        self.card_title(hdc, "大文件（≥ %s）" % fmt((self.data or {}).get("min_file", 0)),
                        inner[0], inner[1], inner[2],
                        None if has_sel else
                        "共 %s 个 · 合计 %s" % (fmt_n(len(files)), fmt(total)))
        self.sel_bar(hdc, inner[0], inner[1], inner[2], "big")
        tt = inner[1] + int(26 * s)
        cols = [("名称", 40, DT_LEFT, "n"), ("类型", 9, DT_LEFT, "t"),
                ("大小", 11, DT_RIGHT, "s"), ("所在目录", 33, DT_LEFT, "dir"),
                ("修改时间", 15, DT_LEFT, "m")]
        self.table(hdc, (inner[0], tt, inner[2], b - int(2 * s)), cols, files, "big",
                   "该路径下暂无大文件" if self.pwd else "暂无大文件数据，先到「总览」扫描一次")

    def big_rows(self):
        raw = list((self.data or {}).get("big_files") or [])
        pre = (self.pwd or "").lower()
        if pre:
            raw = [f for f in raw if (f.get("p") or "").lower().startswith(pre)]
        out = []
        for f in raw:
            p = f.get("p", "")
            n = os.path.basename(p) or p
            ext = S._ext_label(n)
            out.append({"n": n, "p": p, "sz": f.get("s", 0), "m": f.get("m", 0),
                        "dir": os.path.dirname(p), "t": ext or "文件",
                        "s": fmt(f.get("s", 0)), "m2": fmt_t(f.get("m")),
                        "k": None, "e": ext, "rn": n.lower(), "rs": f.get("s", 0),
                        "rt": f.get("m") or 0, "re": ext,
                        "rdir": os.path.dirname(p)})
        self._rowcache_big = out
        out = self._apply_sort(out, "big")
        for x in out:
            x["m"] = x["m2"]
        return out

    # -------------------------------------------------------------- 全局搜索
    def index_state(self):
        try:
            eng = S.get_index()
            if eng.state.get("status") == "idle":
                eng.try_restore(ttl=7 * 24 * 3600)
            return eng.status()
        except Exception:
            return {}

    def gtype_panel(self, hdc, bx, by):
        """类型过滤下拉面板（自绘，不用系统菜单，避免模态阻塞）"""
        s = self.scale
        w1 = int(150 * s)
        ih = int(26 * s)
        h1 = ih * len(TYPE_OPTS) + int(10 * s)
        self.fill(hdc, bx + int(2 * s), by + int(3 * s), bx + w1 + int(2 * s),
                  by + h1 + int(3 * s), rgb("e9edf5"))
        self.rrect(hdc, bx, by, bx + w1, by + h1, int(8 * s), Theme.card, Theme.line)
        yy = by + int(5 * s)
        for k, label in TYPE_OPTS:
            on = (k == self.gtype)
            if on:
                self.rrect(hdc, bx + int(4 * s), yy, bx + w1 - int(4 * s), yy + ih,
                           int(5 * s), Theme.accent_soft)
            self.text(hdc, label, bx + int(12 * s), yy, bx + w1 - int(10 * s),
                      yy + ih, 11, Theme.accent if on else Theme.ink2, on)
            self.add_hot(bx + int(4 * s), yy, bx + w1 - int(4 * s), yy + ih,
                         "gtype_pick", k, None, "hand", "gt_" + k)
            yy += ih

    def paint_search(self, hdc, rc):
        s = self.scale
        l, t, r, b = rc
        inner = self.card(hdc, l, t, r, b)
        y = inner[1]
        focused = (self.editing == "search")
        bw = int(320 * s)
        self.rrect(hdc, inner[0], y, inner[0] + bw, y + int(26 * s), int(6 * s),
                   rgb("ffffff"), Theme.accent if focused else Theme.line)
        q = self.gq or ""
        self.text(hdc, q if q else "全盘搜索文件名 / 路径…", inner[0] + int(10 * s), y,
                  inner[0] + bw - int(10 * s), y + int(26 * s), 11,
                  Theme.text if q else Theme.faint)
        if focused:
            tx = inner[0] + int(10 * s) + (self.tw(hdc, q, 11) if q else 0)
            self.fill(hdc, tx + 1, y + int(6 * s), tx + 2, y + int(20 * s), Theme.accent)
        self.add_hot(inner[0], y, inner[0] + bw, y + int(26 * s), "focus_search2", None,
                     "输入后按全盘索引实时匹配", "ibeam", "gsearch")

        # 文件类型过滤
        tlabel = dict(TYPE_OPTS).get(self.gtype, "全部类型")
        tx0 = inner[0] + bw + int(8 * s)
        tw_ = int(self.tw(hdc, tlabel, 10.5) + 30 * s)
        self.rrect(hdc, tx0, y, tx0 + tw_, y + int(26 * s), int(6 * s), rgb("f7f9fc"),
                   Theme.line)
        self.text(hdc, tlabel, tx0 + int(9 * s), y, tx0 + tw_ - int(14 * s),
                  y + int(26 * s), 10.5, Theme.ink2)
        self.text(hdc, "▾", tx0, y, tx0 + tw_ - int(8 * s), y + int(26 * s), 9.5,
                  Theme.faint, False, DT_RIGHT)
        self.add_hot(tx0, y, tx0 + tw_, y + int(26 * s), "gtype", None,
                     "按文件类型过滤", "hand", "gtype")

        # 索引状态 / 建库按钮
        st = self.index_state()
        ready = (st.get("status") == "ready")
        tip = st.get("status") or "idle"
        scanned = st.get("scanned") or 0
        stx = tx0 + tw_ + int(12 * s)
        if ready:
            self.text(hdc, "索引已就绪 · %s 个条目" % fmt_n(scanned), stx, y,
                      inner[2] - int(96 * s), y + int(26 * s), 10.5, Theme.faint)
        elif tip in ("building", "running"):
            self.text(hdc, "正在建立索引 · 已扫 %s 个目录" % fmt_n(scanned), stx, y,
                      inner[2] - int(96 * s), y + int(26 * s), 10.5, Theme.accent)
        else:
            self.text(hdc, "尚未建立索引", stx, y, inner[2] - int(96 * s),
                      y + int(26 * s), 10.5, Theme.faint)
        btn = int(88 * s)
        bx = inner[2] - btn
        self.rrect(hdc, bx, y, inner[2], y + int(26 * s), int(6 * s),
                   Theme.accent_soft if not ready else rgb("f5f7fb"), Theme.line)
        self.text(hdc, "建立索引" if not ready else "重建索引", bx, y, inner[2],
                  y + int(26 * s), 10.5, Theme.accent if not ready else Theme.muted, True,
                  DT_CENTER)
        self.add_hot(bx, y, inner[2], y + int(26 * s), "idx_build", None,
                     "扫描全部分区建立文件名索引（一次性，之后毫秒级）", "hand", "idxbuild")

        # 快捷筛选
        qy = y + int(33 * s)
        cx = inner[0]
        self.text(hdc, "快捷筛选", cx, qy, cx + int(56 * s), qy + int(21 * s), 10,
                  Theme.muted)
        cx += int(56 * s)
        for key, label in QUICKS:
            on = key in self.gquick
            w1 = int(self.tw(hdc, label, 10, on) + 18 * s)
            self.rrect(hdc, cx, qy, cx + w1, qy + int(21 * s), int(5 * s),
                       Theme.accent_soft if on else rgb("f7f9fc"),
                       Theme.accent if on else Theme.line)
            self.text(hdc, label, cx, qy, cx + w1, qy + int(21 * s), 10,
                      Theme.accent if on else Theme.muted, on, DT_CENTER)
            self.add_hot(cx, qy, cx + w1, qy + int(21 * s), "gquick", key, None, "hand",
                         "q_" + key)
            cx += w1 + int(6 * s)
        if self.grows:
            self.text(hdc, "%s 条结果" % fmt_n(len(self.grows)), cx + int(8 * s), qy,
                      inner[2], qy + int(21 * s), 10, Theme.faint)
        self.sel_bar(hdc, inner[0], qy, inner[2], "search")

        tt = qy + int(26 * s)
        cols = [("名称", 40, DT_LEFT, "n"), ("类型", 9, DT_LEFT, "t"),
                ("大小", 10, DT_RIGHT, "s"), ("所在目录", 29, DT_LEFT, "dir"),
                ("修改时间", 14, DT_LEFT, "m")]
        if not ready:
            self.text(hdc, "先点右上角「建立索引」，之后这里就能毫秒级全盘搜索",
                      inner[0], tt, inner[2], tt + int(60 * s), 12, Theme.faint,
                      False, DT_CENTER)
        else:
            empty = ("输入关键字搜索，或直接选上方文件类型列出全部"
                     if not (self.gtype != "all" or self.gquick)
                     else "没有符合条件的结果")
            self.table(hdc, (inner[0], tt, inner[2], b - int(2 * s)), cols, self.grows,
                       "search", empty, on_row_dbl="s_row")
        if self.gtype_open:
            self.gtype_panel(hdc, tx0, y + int(30 * s))

    def refresh_search(self):
        q = (self.gq or "").strip()
        # 只选了文件类型（或开了快捷筛选）时，空关键词也直接列举，
        # 这样「不记得名字、只想看所有视频/PDF」也能过滤出来
        filtering = (self.gtype and self.gtype != "all") or bool(self.gquick)
        if not q and not filtering:
            self.grows = []
            self._rowcache_search = []
            self.checked["search"] = set()
            return
        kind = "dir" if "dirs_only" in self.gquick else "all"
        roots = None
        if self.deep_scope():
            roots = [self.pwd]              # 路径过滤到某个具体目录时只搜这里
        elif "only_drive" in self.gquick and self.active and self.active != "ALL":
            roots = [self.active + "\\"]
        ext = TYPE_EXT.get(self.gtype, "")
        try:
            if kind == "dir":
                rows = S.get_index().query(q, "dir", ext, 1200, "size", roots)
            else:
                # 分成两次查：文件夹的 s=0 会被「按大小倒序 + LIMIT」整批挤掉，
                # 单独查一次才能保证搜索里能看到文件夹
                rows = (S.get_index().query(q, "dir", ext, 300, "size", roots) or []) \
                    + (S.get_index().query(q, "file", ext, 1200, "size", roots) or [])
        except Exception:
            rows = None
        if not rows:
            self.grows = []
            self._rowcache_search = []
            return
        ql = q.lower()
        cutoff = time.time() - 365 * 86400 if "recent" in self.gquick else 0
        minsz = 100 * 1024 * 1024 if "big" in self.gquick else 0
        out = []
        for r in rows:
            p = r.get("p", "")
            n = r.get("n", "")
            d = r.get("d")
            sz = r.get("s", 0) or 0
            if cutoff and (r.get("m") or 0) < cutoff:
                continue
            if minsz and sz < minsz:
                continue
            e2 = "" if d else S._ext_label(p)
            known = KIND_MAP.get(e2) is not None
            if self.gtype == "other" and not d and known:
                continue
            if self.gtype not in ("all", "other") and not d and not known:
                continue
            ln = n.lower()
            score = 0 if ln == ql else (1 if ln.startswith(ql) else
                                        (2 if ql in ln else 3))
            dd = os.path.dirname(p)
            dsz = self._dir_size(p) if d else sz
            out.append({"n": n, "t": "文件夹" if d else (e2 or "文件"),
                        "s": (fmt(dsz) if dsz is not None else "—") if d else fmt(sz),
                        "dir": dd,
                        "m": fmt_t(r.get("m")), "p": p, "d": 1 if d else 0, "sz": dsz or 0,
                        "k": "dir" if d else None, "e": e2, "rn": ln, "rs": dsz or 0,
                        "rt": r.get("m") or 0, "re": e2, "rdir": dd, "_sc": score})
        out.sort(key=lambda x: (x["_sc"], -x["sz"]))
        self._rowcache_search = out
        self.grows = self._apply_sort(out, "search") if self.sort["search"][1] else out
        self.scroll["search"] = 0
    # -------------------------------------------------------------- 交互
    def on_click(self, x, y, dbl=False):
        h = self.hit(x, y)
        # 弹窗在前：模态时点外面不穿透；soft 弹窗只挡自己那块
        if self.dlg:
            inside = bool(h) and str(h[8]).startswith("dlg")
            if not inside and not self.dlg.get("soft"):
                return
        if self.drv_open and not (h and h[4] in ("drv_pick", "drv_menu")):
            self.drv_open = False
            self.invalidate()
        if self.gtype_open and not (h and h[4] in ("gtype_pick", "gtype")):
            self.gtype_open = False
            self.invalidate()
        if not h:
            self.editing = None
            self.invalidate()
            return
        act, arg = h[4], h[5]
        if act == "noop":
            return
        if act == "dlg_noop":
            return
        if act == "dlg_drag":
            g = (self.dlg or {}).get("_g")
            if g:
                self.dlg["x"], self.dlg["y"] = g[0], g[1]
                self.dlg_drag = (x - g[0], y - g[1])
                self.dlg["auto"] = 0          # 拖过之后就别自动消失了
                u32.SetCapture(self.hwnd)
            return
        if act == "dlg_close":
            self._dlg_action("know")
            return
        if act == "dlg_hide":
            self._dlg_action("hide")
            return
        if act == "dlg_btn":
            self._dlg_action(arg)
            return
        if act == "dlg_alt":
            self._dlg_action(arg)
            return
        if act == "win_min":
            u32.ShowWindow(self.hwnd, 6)
            return
        if act == "win_max":
            u32.ShowWindow(self.hwnd, 9 if u32.IsZoomed(self.hwnd) else 3)
            self.invalidate()
            return
        if act == "win_close":
            u32.PostMessageW(self.hwnd, 0x0010, 0, 0)
            return
        if act == "sheet":
            self.sheet = arg
            self.invalidate()
        elif act == "drive":
            self.editing = None
            self.load_drive(arg)
        elif act == "scan":
            self.start_scan()
        elif act == "full":
            self.toggle_full()
        elif act == "recycle":
            threading.Thread(target=lambda: S.open_recycle_bin(), daemon=True).start()
            self.toast("已打开回收站")
        elif act == "tm_enter":
            self.tm_path = self.tm_path[:] + [arg]
            p = arg.get("p") if isinstance(arg, dict) else None
            if p and not arg.get("agg") and os.path.isdir(p):
                self.pwd = p if p.endswith("\\") else p + "\\"
                self.dirs_q = ""
                self.scroll["dirs"] = 0
                self.scroll["big"] = 0
                self.reload_dir()
            self.invalidate()
        elif act == "tm_up":
            self.tm_path = self.tm_path[:arg + 1]
            nd = self.tm_path[-1] if self.tm_path else None
            p = nd.get("p") if isinstance(nd, dict) else None
            if p and os.path.isdir(p):
                self.pwd = p if p.endswith("\\") else p + "\\"
                self.dirs_q = ""
                self.scroll["dirs"] = 0
                self.scroll["big"] = 0
                self.reload_dir()
            self.invalidate()
        elif act == "tm_scan":
            self.load_drive(arg)
            self.start_scan(arg)
        elif act == "dir_up":
            up = os.path.dirname(self.pwd.rstrip("\\"))
            if up and len(up) >= 2:
                self.pwd = up if up.endswith("\\") else up + "\\"
                self.dirs_q = ""
                self.scroll["dirs"] = 0
                self.scroll["big"] = 0
                chain = self._scope_chain(self.pwd)
                if chain:
                    self.tm_path = chain
                self.reload_dir()
                self.invalidate()
        elif act == "dir_rec":
            self.dirs_recursive = not self.dirs_recursive
            self.reload_dir()
            self.invalidate()
        elif act == "focus_path":
            self.editing = "path"
            self.path_buf = self.pwd or ""
            self.path_fresh = True      # 首次输入直接替换整段路径
            self.invalidate()
        elif act == "path_all":
            self.load_drive("ALL")
            self.toast("已切换到全部盘符")
        elif act == "drv_menu":
            self.drv_open = not self.drv_open
            self.invalidate()
        elif act == "drv_pick":
            self.drv_open = False
            self.load_drive(arg)
        elif act == "open_map":
            self.sheet = "map"
            if self.deep_scope():
                chain = self._scope_chain(self.pwd)
                if chain:
                    self.tm_path = chain
            self.invalidate()
        elif act == "ov_tm":
            self.sheet = "map"
            base = list(self.tm_path or [])
            if isinstance(arg, dict) and arg not in base:
                base.append(arg)
            self.tm_path = base
            self.invalidate()
        elif act == "focus_search":
            self.editing = "dirs"
            self.invalidate()
        elif act == "focus_search2":
            self.editing = "search"
            self.invalidate()
        elif act == "idx_build":
            try:
                roots = S.default_scan_roots()
                S.get_index().start(roots)
                self.dlg_show("正在建立文件索引",
                              "正在遍历全盘文件名并写入索引库，完成后搜索是毫秒级。"
                              "过程中可以点「后台运行」继续用其他页面。",
                              kind="progress", pct=None, sub="正在准备…", tag="index",
                              action="index_hide")
                u32.SetTimer(self.hwnd, 2, 600, None)
            except Exception as e:
                self.toast("建立索引失败：%s" % e, kind="error",
                           title="建立索引失败", auto=0)
        elif act == "s_row":
            if dbl:
                self.open_path(arg)
        elif act == "sort":
            skey, key = arg
            cur = self.sort.get(skey) or [None, 0]
            if cur[0] != key:
                self.sort[skey] = [key, 1]
            elif cur[1] == 1:
                self.sort[skey] = [key, -1]
            elif cur[1] == -1:
                self.sort[skey] = [key, 0]
            else:
                self.sort[skey] = [key, 1]
            if skey == "search" and self.sort[skey][1]:
                self.refresh_search()
            if skey == "dirs":
                self.scroll["dirs"] = 0
            if skey == "big":
                self.scroll["big"] = 0
            self.invalidate()
        elif act == "chk":
            skey, p = arg
            if p:
                s = self.checked.setdefault(skey, set())
                if p in s:
                    s.discard(p)
                else:
                    s.add(p)
                self.invalidate()
        elif act == "chk_all":
            skey = arg
            rows = getattr(self, "_rowcache_" + skey, None)
            if rows is None:
                rows = {"dirs": self.dir_rows(), "big": self.big_rows(),
                        "search": self.grows}.get(skey, [])
            ps = [r.get("p") for r in rows if r.get("p")]
            s = self.checked.setdefault(skey, set())
            if ps and all(p in s for p in ps):
                s.clear()
            else:
                s.update(ps)
            self.invalidate()
        elif act == "sel_clear":
            self.checked[arg] = set()
            self.invalidate()
        elif act == "sel_copy":
            self.copy_checked(arg)
        elif act == "gtype":
            self.gtype_open = not getattr(self, "gtype_open", False)
            self.invalidate()
        elif act == "gtype_pick":
            self.gtype = arg
            self.gtype_open = False
            self.refresh_search()
            self.invalidate()
        elif act == "gquick":
            k = arg
            if k in self.gquick:
                self.gquick.discard(k)
            else:
                self.gquick.add(k)
            self.refresh_search()
            self.invalidate()
        elif act in ("cleanable", "reveal"):
            S.reveal_in_explorer(arg)
            self.toast("已在资源管理器打开")
        elif act == "dir_row":
            if dbl:
                self.open_path(arg, is_dir_row=True)
            else:
                self.hover_key = h[8]
                self.invalidate()
        elif act == "row_big_0":
            pass
        elif act.startswith("row_"):
            if dbl:
                self.open_path(arg)
            else:
                self.hover_key = h[8]
                self.invalidate()

    def open_path(self, row, is_dir_row=False):
        p = row.get("p")
        if not p:
            return
        if row.get("drv"):
            self.load_drive(row["drv"])
            return
        if row.get("d"):
            self.pwd = p if p.endswith("\\") else p + "\\"
            self.dirs_q = ""
            self.scroll["dirs"] = 0
            self.scroll["big"] = 0
            chain = self._scope_chain(self.pwd)
            if chain:
                self.tm_path = chain
            self.reload_dir()
            self.invalidate()
        else:
            ok, err = S.open_file(p)
            self.toast("已打开：%s" % os.path.basename(p) if ok else "打开失败：%s" % err)

    def on_dbl(self, x, y):
        self.on_click(x, y, dbl=True)

    def on_wheel(self, x, y, delta):
        if self.dlg:
            return
        # 判断指针在哪个表上
        h = self.hit(x, y)
        skey = None
        if h and h[8].startswith("row_"):
            skey = h[8].split("_")[1]
        else:
            if self.sheet == "dirs":
                skey = "dirs"
            elif self.sheet == "big":
                skey = "big"
        if not skey and self.sheet == "search":
            skey = "search"
        if not skey:
            return
        step = -3 if delta > 0 else 3
        cur = self.scroll.get(skey, 0)
        mx = self.scroll.get(skey + "_max", 0)
        self.scroll[skey] = max(0, min(mx, cur + step))
        self.invalidate()

    def on_right(self, x, y):
        if self.dlg:
            return
        h = self.hit(x, y)
        if not h:
            return
        row = h[5] if isinstance(h[5], dict) else None
        if not row or not row.get("p"):
            return
        p = row["p"]
        # 右键点在「已勾选」的行上时，按整批勾选项处理（方便一次发给别人）
        sel = sorted(self.checked.get(self.sheet) or [])
        targets = sel if (len(sel) > 1 and p in sel) else [p]
        menu = u32.CreatePopupMenu()
        u32.AppendMenuW(menu, 0x0000, 1001, "打开")
        u32.AppendMenuW(menu, 0x0000, 1002, "在资源管理器中定位")
        u32.AppendMenuW(menu, 0x0000, 1003, "复制路径")
        u32.AppendMenuW(menu, 0x0000, 1004,
                        "复制选中的 %d 项" % len(targets)
                        if len(targets) > 1 else
                        ("复制文件夹" if os.path.isdir(p) else "复制文件"))
        u32.AppendMenuW(menu, 0x0800, 0, None)
        u32.AppendMenuW(menu, 0x0000, 1005, "移入回收站")
        pt = w.POINT(x, y)
        u32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        u32.SetForegroundWindow(self.hwnd)      # 否则菜单点外面不会消失
        cmd = u32.TrackPopupMenu(menu, 0x0100, pt.x, pt.y, 0, self.hwnd, None)
        u32.DestroyMenu(menu)
        if cmd == 1001:
            self.open_path(row)
        elif cmd == 1002:
            S.reveal_in_explorer(p)
            self.toast("已在资源管理器定位")
        elif cmd == 1003:
            self.set_clip(p)
            self.toast("路径已复制")
        elif cmd == 1004:
            self._copy_pending = list(targets)
            ndir = 0
            for q in targets:
                try:
                    if os.path.isdir(q):
                        ndir += 1
                except OSError:
                    pass
            if ndir:
                self.dlg_show(
                    "复制文件夹",
                    "选中内容里有 %d 个文件夹。微信不接受直接粘贴文件夹"
                    "（粘了会没反应），打成压缩包最稳；对方用 QQ 也可以直接复制。"
                    % ndir,
                    kind="info", auto=0, label="打包成 ZIP 再复制",
                    action="copy_pack", alt=("直接复制文件夹", "copy_raw"))
            else:
                self._copy_now(targets)
        elif cmd == 1005:
            reason = S.is_protected(p)
            if reason:
                self.toast("不允许删除：%s" % reason)
            else:
                err, warn = S.to_recycle_bin(p)
                self.toast("已移入回收站" if not err else "失败：%s" % err)
                self.reload_dir()

    def copy_checked(self, key=None):
        """把勾选项复制进剪贴板（可直接粘到聊天软件）。

        文件直接进剪贴板；只要选中里有文件夹就多问一句 —— 微信这类软件
        不收剪贴板里的文件夹，打成 zip 才发得出去。
        """
        key = key or self.sheet
        ps = sorted(self.checked.get(key) or [])
        if not ps:
            self.toast("先勾选要复制的文件或文件夹")
            return
        self._copy_pending = ps
        ndir = 0
        for p in ps:
            try:
                if os.path.isdir(p):
                    ndir += 1
            except OSError:
                pass
        if ndir:
            self.dlg_show(
                "复制文件夹",
                "选中 %d 项，其中 %d 个是文件夹。微信不接受直接粘贴文件夹"
                "（粘了会没反应），打成压缩包最稳；对方用 QQ 也可以直接复制。"
                % (len(ps), ndir),
                kind="info", auto=0, label="打包成 ZIP 再复制",
                action="copy_pack", alt=("直接复制文件夹", "copy_raw"))
            return
        self._copy_now(ps)

    def _copy_now(self, ps):
        r = S.copy_files_to_clipboard(ps)
        if not r.get("ok"):
            self.toast("复制失败：%s" % r.get("error"), kind="error",
                       title="复制失败")
            return
        ndir = 0
        for p in ps:
            try:
                if os.path.isdir(p):
                    ndir += 1
            except OSError:
                pass
        if ndir:
            self.toast("已复制 %d 项（其中 %d 个文件夹）。QQ 等可以直接 Ctrl+V；"
                       "微信粘了没反应的话，改用「打包成 ZIP 再复制」"
                       % (len(ps), ndir))
        else:
            self.toast("已复制 %d 项，可直接粘贴到聊天软件" % len(ps))

    # ------------------------------------------------------------ 打包成 zip
    def _copy_pack(self, ps):
        """把选中项打成 zip 再复制 —— 文件夹也能被任何聊天软件接受。"""
        self._pack_stop = False
        self._pack_res = None
        self._pack_prog = {"done": 0, "bytes": 0, "files": 0, "total": 0,
                           "name": ""}
        self.dlg_show("打包成附件",
                      "先把选中的内容压成一个压缩包，完成后会自动放进剪贴板。",
                      kind="progress", pct=None, action="pack_cancel",
                      tag="pack", sub="正在统计文件大小…")
        u32.SetTimer(self.hwnd, 5, 150, None)
        threading.Thread(target=self._pack_worker, args=(list(ps),),
                         daemon=True).start()

    def _pack_worker(self, ps):
        def prog(done, done_bytes, nfiles, total, name):
            self._pack_prog.update(done=done, bytes=done_bytes, files=nfiles,
                                   total=total, name=name)
        try:
            r = S.send_pack_zip(ps, on_progress=prog,
                                should_stop=lambda: self._pack_stop)
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        self._pack_res = r

    def _pack_sync(self):
        r = self._pack_res
        if r is None:
            p = self._pack_prog
            total = p.get("total") or 0
            done = p.get("bytes") or 0
            pct = (min(0.995, done / float(total)) if total and done else None)
            sub = "已收进 %s 个文件 · %s" % (fmt_n(p.get("done") or 0),
                                            fmt(done))
            cur = p.get("name") or ""
            if cur:
                sub += " · " + os.path.basename(cur)
            self.dlg_update("pack", pct=pct, sub=sub,
                            msg="正在压缩，文件多的时候要等一会儿…")
            return
        u32.KillTimer(self.hwnd, 5)
        if r.get("ok"):
            z = S.copy_files_to_clipboard([r["zip"]])
            name = os.path.basename(r["zip"])
            if z.get("ok"):
                sub = "%s（%s，%s 个文件）" % (name, fmt(r.get("size")),
                                            fmt_n(r.get("files") or 0))
                if r.get("skipped"):
                    sub += " · 跳过 %d 个被占用的文件" % r["skipped"]
                self._pack_notify("ok", "已打包并复制",
                                  "到聊天软件里按 Ctrl+V，即可发送这个压缩包",
                                  sub, auto=7.0)
            else:
                self._pack_notify("error", "压缩包好了，但复制失败",
                                  "可以直接从下面这个位置把压缩包拖进聊天窗口",
                                  r["zip"])
        elif r.get("cancelled"):
            self._pack_notify("info", "已取消打包",
                              "临时文件已清理，原文件没有被改动", "", auto=2.4)
        else:
            self._pack_notify("error", "打包失败",
                              "可以点「复制」时选「直接复制文件夹」再试一次",
                              str(r.get("error") or ""))

    def _pack_notify(self, kind, title, msg, sub, auto=0.0):
        """结束态：进度框还在就就地升级；用户点过「后台运行」就补一条提示。"""
        if self.dlg_update("pack", kind=kind, pct=(1.0 if kind == "ok" else None),
                           action=None, label=None, auto=auto, title=title,
                           msg=msg, sub=sub):
            return
        self.toast(msg, kind=("error" if kind == "error" else "info"),
                   title=title, auto=max(auto, 3.4))

    def set_clip(self, txt):
        u32.OpenClipboard(self.hwnd)
        u32.EmptyClipboard()
        data = ctypes.create_unicode_buffer(txt)
        GMEM_MOVEABLE = 0x0002
        h = k32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(data))
        ptr = k32.GlobalLock(h)
        ctypes.memmove(ptr, data, ctypes.sizeof(data))
        k32.GlobalUnlock(h)
        u32.SetClipboardData(13, h)
        u32.CloseClipboard()

    def _clip_text(self):
        """读取剪贴板纯文本（给输入框粘贴用）。"""
        u32.OpenClipboard(self.hwnd)
        try:
            h = u32.GetClipboardData(13)
            if not h:
                return ""
            ptr = k32.GlobalLock(h)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                k32.GlobalUnlock(h)
        except Exception:
            return ""
        finally:
            u32.CloseClipboard()

    def paste_input(self):
        t = (self._clip_text() or "").strip()
        if not t:
            return
        t = t.strip('"')
        if self.editing == "path":
            self.path_buf = t
        elif self.editing == "search":
            self.gq += t
            self.refresh_search()
        elif self.editing == "dirs":
            self.dirs_q += t
            self.reload_dir()
        else:
            return
        self.invalidate()

    def on_char(self, ch):
        if self.editing == "path":
            if ch == "\x08":
                self.path_buf = self.path_buf[:-1]
                self.path_fresh = False
            elif ch == "\r":
                self.set_scope(self.path_buf)
                return
            elif 32 <= ord(ch) < 0x10000:
                if self.path_fresh:
                    self.path_buf = ""
                    self.path_fresh = False
                self.path_buf += ch
            else:
                return
            self.invalidate()
            return
        if self.editing == "search":
            if ch == "\x08":
                self.gq = self.gq[:-1]
            elif ch == "\r":
                self.editing = None
            elif 32 <= ord(ch) < 0x10000:
                self.gq += ch
            else:
                return
            self.refresh_search()
            self.invalidate()
            return
        if self.editing != "dirs":
            return
        if ch == "\x08":
            self.dirs_q = self.dirs_q[:-1]
        elif ch == "\r":
            self.reload_dir()
            self.editing = None
        elif 32 <= ord(ch) < 0x10000:
            self.dirs_q += ch
        else:
            return
        self.reload_dir()
        self.invalidate()

    def nchit(self, lp):
        """无边框窗口的命中测试：边缘缩放 + 标题栏拖动。"""
        x = ctypes.c_short(lp & 0xFFFF).value
        y = ctypes.c_short((lp >> 16) & 0xFFFF).value
        pt = w.POINT(x, y)
        u32.ScreenToClient(self.hwnd, ctypes.byref(pt))
        cx, cy = pt.x, pt.y
        cw, ch = self.client()
        b = max(3, int(6 * self.scale))
        zoomed = bool(u32.IsZoomed(self.hwnd))
        if not zoomed and not self.fullscreen:
            cl, cr = cx < b, cx >= cw - b
            ct, cb = cy < b, cy >= ch - b
            if ct and cl:
                return 13
            if ct and cr:
                return 14
            if cb and cl:
                return 16
            if cb and cr:
                return 17
            if cl:
                return 10
            if cr:
                return 11
            if ct:
                return 12
            if cb:
                return 15
        if cy < self.topbar_h():
            return 2 if self.hit(cx, cy) is None else 1
        return 1

    def on_getminmaxinfo(self, lp):
        """无边框窗口最大化时正好铺满工作区（不盖住任务栏）。"""
        try:
            class MINMAXINFO(ctypes.Structure):
                _fields_ = [("ptReserved", w.POINT), ("ptMaxSize", w.POINT),
                            ("ptMaxPosition", w.POINT), ("ptMinTrackSize", w.POINT),
                            ("ptMaxTrackSize", w.POINT)]

            class MI(ctypes.Structure):
                _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                            ("rcWork", w.RECT), ("dwFlags", w.DWORD)]
            mmi = ctypes.cast(ctypes.c_void_p(lp), ctypes.POINTER(MINMAXINFO)).contents
            mon = u32.MonitorFromWindow(self.hwnd, 2)
            mi = MI()
            mi.cbSize = ctypes.sizeof(mi)
            u32.GetMonitorInfoW(mon, ctypes.byref(mi))
            # 最大化尺寸交给 WM_SIZE 校正（去掉非客户区后系统仍会多留 9px 边框）
            mmi.ptMinTrackSize.x = int(900 * self.scale)
            mmi.ptMinTrackSize.y = int(620 * self.scale)
        except Exception:
            pass

    def work_area(self):
        class MI(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                        ("rcWork", w.RECT), ("dwFlags", w.DWORD)]
        mi = MI()
        mi.cbSize = ctypes.sizeof(mi)
        u32.GetMonitorInfoW(u32.MonitorFromWindow(self.hwnd, 2), ctypes.byref(mi))
        return mi.rcWork

    def fix_client_rect(self, lp):
        """无边框窗口最大化时，系统会额外给 9px 边框；这里把客户区夹到工作区，
        内容正好铺满屏幕又不被裁掉。"""
        try:
            class WINDOWPOS(ctypes.Structure):
                _fields_ = [("hwnd", w.HWND), ("hwndInsertAfter", w.HWND),
                            ("x", ctypes.c_int), ("y", ctypes.c_int),
                            ("cx", ctypes.c_int), ("cy", ctypes.c_int),
                            ("flags", w.UINT)]

            class NCCSP(ctypes.Structure):
                _fields_ = [("rgrc", w.RECT * 3), ("lppos", ctypes.c_void_p)]

            p = ctypes.cast(ctypes.c_void_p(lp), ctypes.POINTER(NCCSP)).contents
            r = p.rgrc[0]
            mon = u32.MonitorFromRect(ctypes.byref(r), 2)
            class MI(ctypes.Structure):
                _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                            ("rcWork", w.RECT), ("dwFlags", w.DWORD)]
            mi = MI()
            mi.cbSize = ctypes.sizeof(mi)
            if not u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                return
            wk = mi.rcWork
            if (r.left >= wk.left and r.top >= wk.top and
                    r.right <= wk.right and r.bottom <= wk.bottom):
                return
            p.rgrc[0].left = max(r.left, wk.left)
            p.rgrc[0].top = max(r.top, wk.top)
            p.rgrc[0].right = min(r.right, wk.right)
            p.rgrc[0].bottom = min(r.bottom, wk.bottom)
        except Exception:
            pass

    def toggle_full(self):
        GWL_STYLE = -16
        WS_OVERLAPPEDWINDOW = 0x00CF0000
        if not self.fullscreen:
            self.saved_style = u32.GetWindowLongW(self.hwnd, GWL_STYLE)
            mon = u32.MonitorFromWindow(self.hwnd, 2)
            class MI(ctypes.Structure):
                _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                            ("rcWork", w.RECT), ("dwFlags", w.DWORD)]
            mi = MI()
            mi.cbSize = ctypes.sizeof(mi)
            u32.GetMonitorInfoW(mon, ctypes.byref(mi))
            m = mi.rcMonitor
            u32.SetWindowLongW(self.hwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW & ~0x00C00000)
            u32.SetWindowPos(self.hwnd, None, m.left, m.top, m.right - m.left,
                             m.bottom - m.top, 0x0040)
            self.fullscreen = True
        else:
            u32.SetWindowLongW(self.hwnd, GWL_STYLE, self.saved_style or WS_OVERLAPPEDWINDOW)
            u32.ShowWindow(self.hwnd, 3)
            self.fullscreen = False
        self.invalidate()

    # -------------------------------------------------------------- 定时器
    def _logical_mask(self):
        """当前已分配盘符的位图，用来发现 U 盘 / 移动硬盘的插拔。"""
        try:
            return ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            return 0

    def _watch_drives(self):
        """轮询盘符变化：插上/拔掉 U 盘后自动刷新列表，不用重启应用。"""
        m = self._logical_mask()
        if m == getattr(self, "_drv_mask", None):
            return
        self._drv_mask = m
        before = set(d["letter"] for d in self.drives)
        self.refresh_drives()
        after = set(d["letter"] for d in self.drives)
        added, removed = after - before, before - after
        if self.active not in after and self.active != "ALL":
            nxt = "C:" if "C:" in after else (sorted(after)[0] if after else "C:")
            self.load_drive(nxt, scan_if_empty=True)
        if added:
            names = []
            for d in self.drives:
                if d["letter"] in added:
                    names.append("%s（%s %s）" % (d["letter"], d.get("label", ""),
                                                 fmt(d.get("total", 0))))
            self.toast("发现新磁盘：" + "、".join(names))
        elif removed:
            self.toast("磁盘已移除：" + "、".join(sorted(removed)))
        self.invalidate()

    def on_timer(self, tid):
        if tid == 3:
            self._watch_drives()
            return
        if tid == 4:            # 弹窗动画 / 到点自动收起
            if self.dlg:
                self.invalidate()
            else:
                u32.KillTimer(self.hwnd, 4)
            return
        if tid == 5:            # 打包成 zip 的进度
            self._pack_sync()
            return
        if tid == 2:
            st = self.index_state()
            stat = st.get("status")
            if stat == "building":
                self._index_dlg_sync(st)
                self.invalidate()
                return
            u32.KillTimer(self.hwnd, 2)
            if stat == "ready":
                if self.dlg_alive("index"):
                    self.dlg_update("index", kind="ok", pct=1.0, action=None,
                                    auto=3.0, title="索引已就绪",
                                    msg="现在可以按名称 / 类型毫秒级全盘搜索",
                                    sub="%s 个条目 · 用时 %s 秒" % (
                                        fmt_n(st.get("scanned") or 0),
                                        int(st.get("elapsed") or 0)))
                else:      # 用户把进度框收起来了，结束时还是给个提示
                    self.toast("现在可以按名称 / 类型毫秒级全盘搜索了",
                               title="索引已就绪")
                self.refresh_search()
                threading.Thread(target=self._load_insights, daemon=True).start()
            elif stat == "error":
                if self.dlg_alive("index"):
                    self.dlg_update("index", kind="error", pct=None, action=None,
                                    auto=0, title="索引建立失败",
                                    msg="可以到「搜索」页点「建立索引」重试",
                                    sub=str(st.get("error") or ""))
                else:
                    self.toast("索引没有建成，可以到搜索页重试",
                               kind="error", title="索引建立失败", auto=0)
            self.invalidate()
            return
        if tid != 1:
            return
        job = self.job
        if not job:
            u32.KillTimer(self.hwnd, 1)
            return
        if job.state == "running":
            self._scan_dlg_sync(job)
            self.invalidate()
            return
        u32.KillTimer(self.hwnd, 1)
        if job.state == "done" and job.result:
            ltr = (getattr(job, "cache_key", "") or "")[:2]
            if ltr:
                self.datas[ltr] = job.result
            if self.active == "ALL":
                self.load_drive("ALL")
            else:
                self.data = job.result
                self.tree = job.result.get("root")
                self.tm_path = [self.tree] if self.tree else []
                self.dir_cache = None
            el = job.result.get("elapsed") or 0
            sub = "%s 个文件 · %s 个目录 · 共 %s · 用时 %.0f 秒" % (
                fmt_n(job.result.get("files", 0)),
                fmt_n(job.result.get("dirs", 0)),
                fmt(job.result.get("total", 0)), el)
            if not self.dlg_update("scan", kind="ok", pct=1.0, action=None,
                                   auto=3.2, title="扫描完成",
                                   msg="图表与统计已更新，可以继续查看各页",
                                   sub=sub):
                self.toast(sub, title="扫描完成")     # 进度框已被收起
        elif job.state == "error":
            if not self.dlg_update("scan", kind="error", pct=None, action=None,
                                   auto=0, title="扫描失败",
                                   msg="这次没能扫完，可以再试一次",
                                   sub=str(job.error or "未知错误")):
                self.toast(str(job.error or "未知错误"), kind="error",
                           title="扫描失败", auto=0)
        else:   # aborted
            self.dlg_update("scan", kind="info", pct=None, action=None, auto=2.4,
                            title="已取消扫描",
                            msg="没有写入缓存，原有数据保持不变", sub="")
        self.job = None
        self._sizemaps.clear()
        self.refresh_drives()
        self.reload_dir()
        self.invalidate()

    def paint_scan_hint(self, hdc, cw, ch):
        pass


# ---------------------------------------------------------------- 布局算法
def squarify(items, x, y, w, h):
    """items: [(value,payload)]，返回 [((l,t,r,b), payload)]"""
    if not items:
        return []
    items = [(v, p) for v, p in items if v > 0]
    if not items:
        return []
    total = sum(v for v, _ in items)
    area = w * h
    scaled = [(v * area / total, p) for v, p in items]
    out = []

    def worst(row, side):
        ssum = sum(r[0] for r in row)
        if ssum <= 0:
            return float("inf")
        mx = max(r[0] for r in row)
        mn = min(r[0] for r in row)
        return max((side * side * mx) / (ssum * ssum), (ssum * ssum) / (side * side * mn))

    def layout_row(row, rect):
        l, t, r_, b_ = rect
        ssum = sum(i[0] for i in row)
        if ssum <= 0:
            return rect, []
        if (r_ - l) >= (b_ - t):
            rw = ssum / (b_ - t) if (b_ - t) else 0
            res, cy = [], t
            for val, p in row:
                hh = val / rw if rw else 0
                res.append(((l, cy, l + rw, cy + hh), p))
                cy += hh
            return (l + rw, t, r_, b_), res
        rh = ssum / (r_ - l) if (r_ - l) else 0
        res, cx = [], l
        for val, p in row:
            ww = val / rh if rh else 0
            res.append(((cx, t, cx + ww, t + rh), p))
            cx += ww
        return (l, t + rh, r_, b_), res

    rect = (x, y, x + w, y + h)
    row = []
    i = 0
    while i < len(scaled):
        side = min(rect[2] - rect[0], rect[3] - rect[1])
        if not row:
            row = [scaled[i]]
            i += 1
            continue
        if worst(row, side) >= worst(row + [scaled[i]], side):
            row.append(scaled[i])
            i += 1
        else:
            rect, res = layout_row(row, rect)
            out.extend(res)
            row = []
    if row:
        _, res = layout_row(row, rect)
        out.extend(res)
    return out


# ---------------------------------------------------------------- 窗口图标
def _icon_file():
    """找 app.ico：源码目录 / 打包后 _internal / exe 同级都试一遍。"""
    bases = [_HERE, os.path.dirname(_HERE),
             os.path.dirname(os.path.abspath(sys.executable)),
             getattr(sys, "_MEIPASS", "") or ""]
    for b in bases:
        if not b:
            continue
        p = os.path.join(b, "app.ico")
        if os.path.isfile(p):
            return p
    return None


APP_ICON_SMALL = None


def _draw_hicon(hdc, x, y, size):
    """把应用图标（与桌面快捷方式一致）画到指定位置；没有图标时返回 False。"""
    if not APP_ICON_SMALL:
        return False
    try:
        # DrawIconEx 在 user32，不在 gdi32
        u32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        u32.DrawIconEx.restype = ctypes.c_bool
        return bool(u32.DrawIconEx(hdc, int(x), int(y), APP_ICON_SMALL,
                                   int(size), int(size), 0, None, 0x0003))
    except Exception:
        return False


def _app_icons():
    """返回 (大图标, 小图标) HICON，保证窗口和任务栏都用羊图标。"""
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
    big = small = None
    try:
        cx = u32.GetSystemMetrics(11) or 32
        cy = u32.GetSystemMetrics(12) or 32
        sx = u32.GetSystemMetrics(49) or 16
        sy = u32.GetSystemMetrics(50) or 16
    except Exception:
        cx, cy, sx, sy = 32, 32, 16, 16
    p = _icon_file()
    if p:
        big = u32.LoadImageW(None, p, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        small = u32.LoadImageW(None, p, IMAGE_ICON, sx, sy, LR_LOADFROMFILE)
    if not big:      # 兜底：直接取 exe 内嵌图标（打包时已写入 app.ico）
        try:
            big = u32.ExtractIconW(None, sys.executable, 0)
            small = small or u32.ExtractIconW(None, sys.executable, 0)
        except Exception:
            pass
    return big, small


# ---------------------------------------------------------------- 窗口过程
def run():
    app = App()

    # DPI
    try:
        dc = u32.GetDC(None)
        dpi = gdi.GetDeviceCaps(dc, 90)  # LOGPIXELSX
        u32.ReleaseDC(None, dc)
        app.scale = max(1.0, dpi / 96.0)
    except Exception:
        app.scale = 1.0

    hinst = k32.GetModuleHandleW(None)
    cls_name = "DiskWorkbenchNative"

    @WNDPROC
    def wndproc(hwnd, msg, wp_, lp):
        try:
            if msg == 0x000F:
                app.on_paint()
                return 0
            if msg == 0x0001:  # WM_CREATE
                app.hwnd = hwnd
                return 0
            if msg == 0x0024:  # WM_GETMINMAXINFO
                app.on_getminmaxinfo(lp)
                return 0
            if msg == 0x0083:  # WM_NCCALCSIZE：去掉系统标题栏，客户区铺满整窗
                if wp_:
                    app.fix_client_rect(lp)
                    return 0
                return u32.DefWindowProcW(hwnd, msg, wp_, lp)
            if msg == 0x0084:  # WM_NCHITTEST：自绘标题栏拖动 / 边缘缩放
                return app.nchit(lp)
            if msg == 0x0005:  # WM_SIZE
                app.invalidate()
                return 0
            if msg == 0x0003:  # WM_MOVE
                return 0
            if msg == 0x0014:  # WM_ERASEBKGND
                return 1
            if msg == 0x0113:  # WM_TIMER
                app.on_timer(int(wp_))
                return 0
            if msg == 0x0114:  # WM_HSCROLL / 忽略
                return 0
            if msg == 0x0200:  # WM_MOUSEMOVE
                x = ctypes.c_short(lp & 0xFFFF).value
                y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                if app.dlg_drag and app.dlg:
                    app.dlg["x"] = x - app.dlg_drag[0]
                    app.dlg["y"] = y - app.dlg_drag[1]
                    app.invalidate()
                    return 0
                h = app.hit(x, y)
                key = h[8] if h else None
                if app.dlg:
                    app.dlg_hover = key if (key or "").startswith("dlg") else None
                if key != app.hover_key:
                    app.hover_key = key
                    app.invalidate()
                return 0
            if msg == 0x0202:  # LBUTTONUP：结束拖动
                if app.dlg_drag:
                    app.dlg_drag = None
                    try:
                        u32.ReleaseCapture()
                    except Exception:
                        pass
                    app.invalidate()
                    return 0
            if msg == 0x0201:  # LBUTTONDOWN
                x = ctypes.c_short(lp & 0xFFFF).value
                y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                if _DUMP_HOT:
                    try:
                        with open(_dbg_path("msg.log"), "a", encoding="utf-8") as fh:
                            fh.write("click %s,%s hot=%s\n" % (x, y, app.hit(x, y)))
                    except Exception:
                        pass
                app.on_click(x, y)
                return 0
            if msg == 0x0203:  # LBUTTONDBLCLK
                x = ctypes.c_short(lp & 0xFFFF).value
                y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                app.on_dbl(x, y)
                return 0
            if msg == 0x0204:  # RBUTTONUP
                x = ctypes.c_short(lp & 0xFFFF).value
                y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                app.on_right(x, y)
                return 0
            if msg == 0x020A:  # MOUSEWHEEL
                delta = ctypes.c_short((wp_ >> 16) & 0xFFFF).value
                pt = w.POINT()
                u32.GetCursorPos(ctypes.byref(pt))
                u32.ScreenToClient(hwnd, ctypes.byref(pt))
                app.on_wheel(pt.x, pt.y, delta)
                return 0
            if msg == 0x0100:  # KEYDOWN
                if wp_ == 0x1B:
                    if app.dlg:
                        app.dlg_close()
                        return 0
                    if app.gtype_open:
                        app.gtype_open = False
                        app.invalidate()
                    elif app.editing:
                        app.editing = None
                        app.invalidate()
                    elif app.fullscreen:
                        app.toggle_full()
                    return 0
                if wp_ == 0x74:  # F5
                    app.start_scan()
                    return 0
                if wp_ == 0x7A:  # F11 全屏（按钮已去掉，保留快捷键）
                    app.toggle_full()
                    return 0
                if wp_ == 0x56 and (u32.GetKeyState(0x11) & 0x8000) and app.editing:
                    app.paste_input()      # Ctrl+V
                    return 0
                if wp_ == 0x43 and (u32.GetKeyState(0x11) & 0x8000) and not app.editing:
                    app.copy_checked()     # Ctrl+C：复制勾选的文件给聊天软件
                    return 0
                if wp_ == 0x08 and app.editing:
                    app.on_char("\x08")
                    return 0
                return 0
            if msg == 0x0102:  # CHAR
                if app.editing in ("dirs", "search", "path"):
                    ch = chr(wp_)
                    if ch == "\r" or ch == "\x08" or wp_ >= 32:
                        app.on_char(ch)
                    return 0
                return 0
            if msg == 0x0002:
                u32.PostQuitMessage(0)
                return 0
        except Exception as e:
            try:
                import traceback
                with open(os.path.join(_HERE, "app_error.log"), "a", encoding="utf-8") as fp:
                    fp.write("\n[%s] msg=%s %s\n" % (now_str(), msg, e))
                    traceback.print_exc(file=fp)
            except Exception:
                pass
            return 0
        return u32.DefWindowProcW(hwnd, msg, wp_, lp)

    wc = WNDCLASSW = None
    rc_style = 0x0008 | 0x0002 | 0x0001  # CS_DBLCLKS | CS_VREDRAW | CS_HREDRAW

    class WC(ctypes.Structure):
        _fields_ = [("style", w.UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                    ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                    ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR),
                    ("hIconSm", w.HICON)]

    wc = WC()
    wc.style = rc_style
    wc.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
    wc.lpszClassName = cls_name
    wc.hbrBackground = None
    wc.hCursor = u32.LoadCursorW(None, ctypes.c_void_p(32512))
    big_ico, small_ico = _app_icons()
    global APP_ICON_SMALL
    APP_ICON_SMALL = small_ico or big_ico
    try:
        _p = _icon_file()
        if _p:
            _sz = int(24 * app.scale)
            _h = u32.LoadImageW(None, _p, 1, _sz, _sz, 0x0010)
            if _h:
                APP_ICON_SMALL = _h
    except Exception:
        pass
    if big_ico:
        wc.hIcon = big_ico
    if small_ico:
        wc.hIconSm = small_ico
    u32.RegisterClassW(ctypes.byref(wc))

    WS_OVERLAPPEDWINDOW = 0x00CF0000
    hwnd = u32.CreateWindowExW(0, cls_name, "无敌暴龙图", WS_OVERLAPPEDWINDOW,
                               60, 40, 1500, 900, None, None, hinst, None)
    app.hwnd = hwnd
    # 强制把图标贴到窗口上（任务栏 / 标题栏 / Alt+Tab 都用它）
    WM_SETICON = 0x0080
    if big_ico:
        u32.SendMessageW(hwnd, WM_SETICON, 0, ctypes.c_ssize_t(big_ico))
    if small_ico:
        u32.SendMessageW(hwnd, WM_SETICON, 1, ctypes.c_ssize_t(small_ico))
    u32.ShowWindow(hwnd, 3)
    u32.UpdateWindow(hwnd)
    app.boot()
    app._booting = False
    _sheet = os.environ.get("WB_SHEET")
    if _sheet in ("overview", "map", "dirs", "big", "search"):
        app.sheet = _sheet
        app.invalidate()

    msg = w.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    run()
