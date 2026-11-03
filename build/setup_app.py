# -*- coding: utf-8 -*-
"""个人磁盘工作台 · 安装程序

双击即可把应用安装到当前用户目录，并创建桌面快捷方式；
带 --uninstall 参数则卸载。
"""
import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import (POINTER, byref, c_bool, c_long, c_ubyte, c_ulong,
                    c_ushort, c_void_p, c_wchar_p, create_unicode_buffer)

APP_NAME = "个人磁盘工作台"
APP_DIR_NAME = "PersonalDiskWorkbench"
EXE_NAME = "个人磁盘工作台.exe"

user32 = ctypes.windll.user32
ole32 = ctypes.windll.ole32
shell32 = ctypes.windll.shell32

MB_OK = 0x0
MB_YESNO = 0x4
MB_ICONQUESTION = 0x20
MB_ICONINFORMATION = 0x40
MB_ICONERROR = 0x10
IDYES = 6

# --silent：无人值守安装（自动化测试 / 命令行安装用）
SILENT = "--silent" in sys.argv


def msg(text, title=APP_NAME, flags=MB_OK | MB_ICONINFORMATION):
    if SILENT:
        return IDYES
    return user32.MessageBoxW(None, text, title, flags)


# ---------------- 路径 ----------------
def target_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.environ.get("USERPROFILE", ""), "AppData", "Local")
    return os.path.join(base, "Programs", APP_DIR_NAME)


def payload_dir():
    """安装包内的源文件目录。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        for cand in (os.path.join(meipass, "app"),
                     os.path.join(os.path.dirname(sys.executable), "app")):
            if os.path.isdir(cand):
                return cand
        return meipass
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "app"),
                 os.path.join(here, "release10", "app"),
                 os.path.join(here, "release6", "app"),
                 os.path.join(here, "dist", "DiskWorkbench")):
        if os.path.isdir(cand):
            return cand
    return here


# ---------------- 快捷方式 ----------------
class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort),
                ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def mk_guid(s):
    g = GUID()
    ole32.IIDFromString(create_unicode_buffer(s), byref(g))
    return g


def known_folder(fid):
    buf = c_wchar_p()
    g = mk_guid(fid)
    if shell32.SHGetKnownFolderPath(byref(g), 0, None, byref(buf)) != 0 or not buf.value:
        return None
    return buf.value


def vtable(ptr):
    return ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]


def make_shortcut(target, lnk_path, workdir="", desc="", icon="", args=""):
    target = os.path.normpath(target)
    if workdir:
        workdir = os.path.normpath(workdir)
    ole32.CoInitialize(None)
    clsid = mk_guid("{00021401-0000-0000-C000-000000000046}")
    iid_sl = mk_guid("{000214F9-0000-0000-C000-000000000046}")
    iid_pf = mk_guid("{0000010b-0000-0000-C000-000000000046}")

    p = c_void_p()
    hr = ole32.CoCreateInstance(byref(clsid), None, 1, byref(iid_sl), byref(p))
    if hr != 0 or not p:
        return "CoCreateInstance failed hr=%s" % hr

    vt = vtable(p)
    SetPath = ctypes.WINFUNCTYPE(c_long, c_void_p, c_wchar_p)(vt[20])
    SetWorkingDirectory = ctypes.WINFUNCTYPE(c_long, c_void_p, c_wchar_p)(vt[9])
    SetDescription = ctypes.WINFUNCTYPE(c_long, c_void_p, c_wchar_p)(vt[7])
    SetArguments = ctypes.WINFUNCTYPE(c_long, c_void_p, c_wchar_p)(vt[11])
    SetIconLocation = ctypes.WINFUNCTYPE(c_long, c_void_p, c_wchar_p, ctypes.c_int)(vt[17])
    QueryInterface = ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(vt[0])

    SetPath(p, target)
    if args:
        SetArguments(p, args)
    if workdir:
        SetWorkingDirectory(p, workdir)
    if desc:
        SetDescription(p, desc)
    if icon and os.path.exists(icon):
        SetIconLocation(p, icon, 0)

    pf = c_void_p()
    hr = QueryInterface(p, byref(iid_pf), byref(pf))
    if hr != 0 or not pf:
        return "QueryInterface(IPersistFile) failed hr=%s" % hr

    vt2 = vtable(pf)
    Save = ctypes.WINFUNCTYPE(c_long, c_void_p, c_wchar_p, c_bool)(vt2[6])
    hr = Save(pf, lnk_path, True)
    return "ok" if hr == 0 else "Save failed hr=%s" % hr


# ---------------- 安装 / 卸载 ----------------
def install():
    src = payload_dir()
    dst = target_dir()
    exe_src = os.path.join(src, EXE_NAME)
    if not os.path.isfile(exe_src):
        # 兼容打包时未改名的产物
        alt = os.path.join(src, "DiskWorkbench.exe")
        if os.path.isfile(alt):
            exe_src = alt
    if not os.path.isfile(exe_src):
        msg("安装包不完整：找不到主程序。\n\n%s" % exe_src, APP_NAME, MB_OK | MB_ICONERROR)
        return 1

    if msg("将把「%s」安装到：\n%s\n\n并创建桌面快捷方式。\n\n继续吗？"
           % (APP_NAME, dst), APP_NAME + " 安装", MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 0

    try:
        os.makedirs(dst, exist_ok=True)
        # 保留旧缓存，避免每次升级都全盘重扫
        keep = os.path.join(dst, "cache")
        cache_bak = None
        if os.path.isdir(keep):
            cache_bak = keep + ".keep"
            if os.path.isdir(cache_bak):
                shutil.rmtree(cache_bak, ignore_errors=True)
            shutil.move(keep, cache_bak)

        # 升级时先删掉旧版程序文件（cache 已挪走），否则 WebView2 / clr 等
        # 上一代残留会一直堆在 _internal 里，装完还占几十 MB
        for name in ("_internal", "index.html"):
            p = os.path.join(dst, name)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        for name in os.listdir(src):
            s = os.path.join(src, name)
            d = os.path.join(dst, name)
            if name == "卸载.exe":
                continue          # 旧版残留的 23MB 重复安装包，不再安装
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

        if cache_bak and os.path.isdir(cache_bak):
            if os.path.isdir(keep):
                shutil.rmtree(keep, ignore_errors=True)
            shutil.move(cache_bak, keep)

        # 清掉旧版留下的 23MB 重复安装包（现在卸载走主程序 --uninstall）
        stale = os.path.join(dst, "卸载.exe")
        if os.path.isfile(stale):
            try:
                os.remove(stale)
            except OSError:
                pass

        # 图标资源
        ico_src = os.path.join(os.path.dirname(src), "app.ico")
        ico_dst = os.path.join(dst, "app.ico")
        if os.path.isfile(ico_src) and not os.path.isfile(ico_dst):
            shutil.copy2(ico_src, ico_dst)

        exe = os.path.join(dst, EXE_NAME)

        # 桌面快捷方式
        desk = known_folder("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")
        if desk:
            make_shortcut(exe, os.path.join(desk, APP_NAME + ".lnk"),
                          dst, APP_NAME, ico_dst if os.path.isfile(ico_dst) else "")

        # 开始菜单
        start = known_folder("{625B53C3-AB48-4EC1-BA1F-A1EF4146FC19}")
        if start:
            group = os.path.join(start, "Programs", APP_NAME)
            os.makedirs(group, exist_ok=True)
            make_shortcut(exe, os.path.join(group, APP_NAME + ".lnk"),
                          dst, APP_NAME, ico_dst if os.path.isfile(ico_dst) else "")
            # 卸载入口直接复用主程序（--uninstall），不再额外塞一份 23MB 的安装包
            make_shortcut(exe, os.path.join(group, "卸载 " + APP_NAME + ".lnk"),
                          dst, "卸载 " + APP_NAME, "", "--uninstall")

    except Exception as e:
        msg("安装失败：\n\n%s" % e, APP_NAME, MB_OK | MB_ICONERROR)
        return 1

    if msg("安装完成！\n\n桌面已创建快捷方式，直接双击即可打开。\n\n现在启动吗？",
           APP_NAME, MB_YESNO | MB_ICONQUESTION) == IDYES:
        try:
            os.startfile(os.path.join(dst, EXE_NAME))
        except OSError:
            pass
    return 0


def uninstall():
    dst = target_dir()
    if not os.path.isdir(dst):
        msg("没有找到安装目录，可能已经卸载过了。", APP_NAME)
        return 0
    if msg("确定要卸载「%s」吗？\n\n安装目录：%s\n（你的磁盘缓存和回收站记录会被一并删除）"
           % (APP_NAME, dst), APP_NAME + " 卸载", MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 0

    for fid in ("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
                "{625B53C3-AB48-4EC1-BA1F-A1EF4146FC19}"):
        base = known_folder(fid)
        if not base:
            continue
        cands = [os.path.join(base, APP_NAME + ".lnk"),
                 os.path.join(base, "Programs", APP_NAME)]
        for c in cands:
            if os.path.isdir(c):
                shutil.rmtree(c, ignore_errors=True)
            elif os.path.isfile(c):
                try:
                    os.remove(c)
                except OSError:
                    pass

    # 自己不能删自己所在目录，交给外部命令延迟删除
    try:
        subprocess.Popen('cmd /c timeout /t 2 >nul & rmdir /s /q "%s"' % dst,
                         shell=True, creationflags=0x08000000)
    except Exception:
        pass
    msg("已卸载。", APP_NAME)
    return 0


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        sys.exit(uninstall())
    sys.exit(install())
