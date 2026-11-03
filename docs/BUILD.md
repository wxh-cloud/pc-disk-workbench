# 打包与分发

本项目有三种分发形态，按"从简单到完整"排列。

---

## 0. 前置

```bash
pip install -r requirements-optional.txt   # pywebview + pyinstaller
```

全部命令都在**仓库根目录**执行。

---

## 1. 原生 GDI 版（推荐，体积小、内存低）

```bash
# ① 打包
pyinstaller build/Native.spec
#    产物：build/dist_native/NativeDiskWorkbench/

# ② 组装成可分发目录（补上明文 server.py、图标、cache 目录）
python build/assemble_native.py
#    产物：build/release10/app/个人磁盘工作台.exe + _internal/ + server.py
```

组装脚本会把 `server.py` **以明文**放进 `app/`，启动器在运行时用 `importlib` 加载它。好处是：以后只改后端，替换一个 `.py` 文件即可，不必重新打包。

直接分发 `build/release10/app/` 这个文件夹即可运行（免安装）。

---

## 2. WebView2 窗口版

```bash
pyinstaller build/DiskWorkbench.spec     # → build/dist18/DiskWorkbench/
python build/assemble.py                # → build/release9/app/
```

启动器 `launcher_native.py` 会：

1. 设置 `WB_APP_ROOT`（让缓存落在 exe 同级而不是临时解压目录）；
2. 注入精简 WebView2 参数（见下）；
3. 进程内启动本地服务（`server.bind_server(8733)`，自动顺延端口）；
4. 开一个 pywebview 窗口指向 `http://127.0.0.1:<port>/`，并起看门狗把窗口最大化。

> 用户机器需要 Edge WebView2 运行时。Win11 自带，Win10 通常随 Edge 已安装。

---

## 3. 安装包

把 `release10/app/` 整个塞进一个自解压安装器：

```bash
# ① 生成安装器 exe（内含整个 app 目录）
pyinstaller build/Setup10.spec
#    产物：build/dist/Setup10.exe（名字里带版本号更好，如 个人磁盘工作台_安装包_20261024.exe）

# ② 静默安装（用于自动化 / 自己升级）
Setup10.exe --silent

# ③ 卸载（也可从开始菜单的卸载入口触发）
"%LOCALAPPDATA%\Programs\PersonalDiskWorkbench\个人磁盘工作台.exe" --uninstall
```

安装位置：`%LOCALAPPDATA%\Programs\PersonalDiskWorkbench\`
副作用：桌面快捷方式、开始菜单组（含卸载项）。

---

## 4. 发布清单

发 Release 之前逐条过一遍：

- [ ] `python server.py` 能正常起，浏览器打开 `127.0.0.1:<port>` 正常
- [ ] `python app_win.py` 能正常起（原生版回归）
- [ ] 「移到回收站」对**受保护路径**仍被拒绝（试 `C:\`、`C:\Windows`）
- [ ] 安装包能装、能开、能从开始菜单卸载
- [ ] `.gitignore` 生效：`git status` 里没有 `cache/`、`release*/`、`dist*/`
- [ ] 版本号与 CHANGELOG 已更新，tag 名与安装包文件名一致
- [ ] 安装包作为 Release 资产上传（**不要提交进仓库**）

---

## 5. 踩过的坑（重要）

### ❌ 不要排除 `http.server`

打包参数里如果把 `http.server` 排进 `excludes`，服务端起不来——它在运行时才 import，PyInstaller 静态分析抓不到。`Native.spec` 里排除的是 `tkinter / unittest / pydoc` 这类，**刻意不含 `http.server`**。

### ❌ 不要给 WebView2 加 `--single-process`

`launcher_native.py` 的 `_LEAN_ARGS` 刻意没有 `--single-process`。实测加上之后约 30 秒渲染进程崩溃、整个应用退出。

精简内存的正确姿势（已内置）：

```
--disable-gpu --renderer-process-limit=1 --process-per-site
--js-flags=--max-old-space-size=128
--disable-extensions --disable-sync --no-first-run
--disable-background-networking
```

实测私有内存从 ~617 MB 降到 ~418 MB。再往下压就别指望 WebView2 了——空白页本身就要 ~224 MB 工作集，要更低只能走 `app_win.py` 的原生渲染。

### ⚠️ `.bat` 必须 CRLF

`Start-Workbench.bat` 如果存成 LF 换行，`cmd` 会把它连成一行解析，中文注释还会被当命令执行，报一堆乱码错误。仓库里已经用 `.gitattributes` 强制 `*.bat text eol=crlf`，**手工编辑后别让编辑器把换行改回 LF**。

### ⚠️ 端口会被 Windows 保留区间吃掉

8733 不是总能绑。`bind_server()` 会从 8733 起顺延最多 300 个端口，`--print-port` 让启动脚本先探到真实端口。查当前保留区间：

```bash
netsh interface ipv4 show excludedportrange protocol=tcp
```

### ⚠️ 改索引 schema 必须递增版本号

`IndexEngine.SCHEMA_VERSION` 是硬开关，改表结构不递增它，旧库会带着旧结构继续用，搜索会报错。递增后旧库会被 `DROP` 重建（索引是可再生数据，不做迁移）。
