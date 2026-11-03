# 本地 HTTP API 参考

后端是一个 `ThreadingHTTPServer`，**只监听 `127.0.0.1`**。所有路径都以 `/api/` 开头，返回 JSON（`/api/download` 例外，返回文件流）。

- 请求体：`POST` 一律 `Content-Type: application/json`，响应 `{"ok": true, ...}` 或 `{"ok": false, "error": "..."}`
- 端口：默认 `8733`，不可绑定时自动顺延（见 [ARCHITECTURE.md](./ARCHITECTURE.md#6-端口选择)）
- 中文参数：GET 查询串里的中文需要先 URL 编码，服务端会 `unquote` 解码

---

## GET

| 端点 | 参数 | 返回 |
| --- | --- | --- |
| `/api/health` | — | `{ok: true}`，用于启动脚本探活 |
| `/api/drives` | — | `{drives: [{letter, path, label, total, used, free, usedPct, cached}]}` |
| `/api/cache` | `path=` 或 `key=` | `{ok, data}`，`ok=false` 表示没有缓存 |
| `/api/history` | `path=`, `limit=40` | `{ok, points: [...]}`，同一路径的历史体积趋势 |
| `/api/scan/status` | `job=`（必填） | `{ok, state, error, dirs, files, errors, elapsed, result}`，`state ∈ running/done/aborted/error` |
| `/api/scan/stop` | `job=` | `{ok: true}`，把任务标记为 `aborted` |
| `/api/search` | 见下 | 搜索结果的 `folders[] / files[] + meta` |
| `/api/engine` | `refresh=1` | `{ok, engine, via, detail, download, cli, index}`，探测 Everything 是否可用 |
| `/api/index/start` | `roots=C:\|D:\` | `{ok, index}`，启动后台全盘索建 |
| `/api/index/status` | — | `{ok, index}`，`index.status ∈ idle/building/ready/error` |
| `/api/report/all_drives` | — | 全盘汇总报告（供分享页渲染） |
| `/api/where` | — | `{ok, root, share, cache, port}`，便于调试定位运行目录 |
| `/api/download` | `path=` | **文件流**（`application/octet-stream`），供拖拽落地 |
| `/api/list_dir` | `path=`, `q=`, `recursive=1` | `{ok, base, dirs[], files[], truncated}`，每项含 `{n, p, d, s, m, rel}` |
| `/api/share/list` | — | `{ok, dir, files[]}`，`share/` 目录下最近 50 个导出物 |

### `/api/search` 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `q` | `""` | 关键字（URL 编码） |
| `limit` | `14` | 返回条数，上限 5000 |
| `sort` | `size` | `size` / `name` / `mtime` |
| `order` | `desc` | `desc` / `asc` |
| `engine` | `builtin` | `builtin` 实时遍历 / `fast` 或 `index` 查 SQLite 索引 / `everything` 走 Everything |
| `kind` | `all` | `all` / `dir` / `file` |
| `ext` | — | 扩展名过滤，逗号分隔，如 `pdf,docx` |
| `inpath` | `0` | `1` 表示只按路径匹配（文件夹全盘搜索联动用） |
| `refresh` | `0` | `1` 表示丢弃遍历缓存重扫 |

响应结构：

```json
{
  "ok": true,
  "q": "报告",
  "folders": [{ "n": "报告", "p": "D:\\文档\\报告", "d": 1, "s": 0, "m": 1730000000 }],
  "files":   [{ "n": "年报.xlsx", "p": "D:\\文档\\报告\\年报.xlsx", "d": 0, "s": 1048576, "m": 1730000000 }],
  "meta": { "dirCount": 1, "fileCount": 1, "engine": "fast-index", "elapsed": 0.01 }
}
```

---

## POST

### 扫描

```jsonc
// POST /api/scan/start
{
  "path": "E:\\",        // 或 "ALL" —— 扫描全部本地固定盘并合并成一棵树
  "min_node": 0,          // 结果树里节点体积门槛（字节）
  "min_file": 52428800,   // 大文件榜单门槛，默认 50 MB
  "top_files": 200        // 大文件榜单条数
}
// → { "ok": true, "job": "E_143012", "key": "E", "roots": ["E:\\"], "merged": false }
```

### 清理

```jsonc
// POST /api/trash  —— 唯一有破坏性的接口，只会「移入回收站」
{ "path": "E:\\临时\\旧项目" }
// → { "ok": true, "path": "...", "files": 1234, "size": 5368709120, "warning": null }
// 受保护路径 → { "ok": false, "error": "不能删除盘符根目录" }
```

### 打开 / 定位

| 端点 | 请求体 | 说明 |
| --- | --- | --- |
| `/api/reveal` | `{path}` | 在资源管理器中选中该项 |
| `/api/open_file` | `{path}` | 用系统默认程序打开文件 |
| `/api/open` | `{app: "recyclebin"}` 或 `{app: "workbuddy"}` | 打开回收站 / 打开 WorkBuddy 客户端（未安装则返回失败） |

### 剪贴板

```jsonc
// POST /api/clipboard/files —— 把真实文件（含文件夹）放进 Windows 剪贴板（CF_HDROP）
{ "paths": ["E:\\视频\\demo.mp4", "E:\\项目\\资料"] }
// → { "ok": true }
```

粘贴到微信 / QQ / 资源管理器时是「文件本体」，而不是一段路径文本。

### 分享

| 端点 | 请求体 | 说明 |
| --- | --- | --- |
| `/api/share/save` | `{name, content, b64?}` | 把生成的报告/长图写入 `share/`，`b64=true` 表示内容是 base64（图片） |
| `/api/share/package` | `{}` | 把整个工具打包成 zip，返回 `{ok, path}` |
| `/api/share/open_app` | `{app: "weixin"\|"wxwork"\|"dingtalk"}` | 通过协议唤起对应的桌面聊天软件 |

### 文件操作

| 端点 | 请求体 | 说明 |
| --- | --- | --- |
| `/api/mkdir` | `{path, name}` | 在 `path` 下新建文件夹；同名返回 409 |
| `/api/touch` | `{path, name}` | 新建空文件；同名返回 409 |

---

## 状态码约定

| 码 | 含义 |
| --- | --- |
| `200` | 成功（业务失败也是 200 + `ok:false`，见 `error`） |
| `400` | 参数错误（路径不存在、盘符无效等） |
| `404` | 未知接口 / 任务不存在 / 文件不存在 |
| `409` | 目标已存在 |
| `500` | 服务端异常（`error` 带原因） |

---

## 安全提醒

API **没有任何鉴权**，靠"只绑回环地址"作为边界。这意味着：

- 同一台机器上的**任何本地程序**都能调用这些接口（包括 `/api/trash`）；
- 因此 `/api/trash` 的受保护路径校验必须在**服务端**完成，不能只靠前端对话框——这也是它现在的实现方式；
- 不要把本服务暴露到 `0.0.0.0` 或做端口转发。
