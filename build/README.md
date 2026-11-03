# build/ —— 打包与分发

这个目录只放**可复现的打包脚本**，不放产物。运行前请确认你已安装：

```bash
pip install -r ../requirements-optional.txt
```

日常调试（直接跑源码）不需要进这个目录，见仓库根目录的 README。

## 产物与脚本对应关系

| 脚本 | 作用 |
| --- | --- |
| `launcher_native.py` | WebView2 窗口启动器：起一个窗口 + 在进程内加载 `../server.py` |
| `Native.spec` | 原生 GDI 版 PyInstaller 配置 → `dist_native/NativeDiskWorkbench/` |
| `DiskWorkbench.spec` | WebView2 版 PyInstaller 配置 → `dist18/DiskWorkbench/` |
| `assemble_native.py` | 把 `dist_native/` 组装成 `release10/app/` |
| `assemble.py` | 把 `dist18/` 组装成 `release9/app/` |
| `Setup10.spec` | 安装器 PyInstaller 配置（把 `release10/app` 整个塞进去） |
| `setup_app.py` | 安装 / 卸载逻辑（`--silent` 静默安装，`--uninstall` 卸载） |
| `app.ico` | 全项目共用图标 |

完整流程与踩坑记录见 [../docs/BUILD.md](../docs/BUILD.md)。

> `dist*/`、`release*/`、`backup/` 都在 `.gitignore` 里——它们是本机产物，不进仓库。
