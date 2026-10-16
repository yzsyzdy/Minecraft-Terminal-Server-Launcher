# MSTL 代码审查报告

审查范围：48 个 Python 文件（11249 行）、`webui/index.html`（2190 行）、`locales/*.json`（4 份）、`frp/`、`openfrp/`、CI 配置。
方法：6 路并行通读（core / 网络下载 / 隧道 / CLI 启动 / 前端 / Web 后端）+ 对高危结论做实证复现（临时脚本，未改动仓库）。
标记说明：**[实证]** = 已在本机复现并观察到现象；**[读码]** = 逐行追踪调用链确认，未构造运行时复现。

> **修复状态**：下表 57 条**已全部修复**，代码改动落在 24 个文件（+1356 / −235 行），
> 逐条对应关系见本文末尾的「十四、修复落地清单」。
> 验证：`python -m pytest tests -q` → 281 passed, 1 skipped, 37 subtests passed；
> `python -m unittest discover -s tests -t .`（CI 用的那条）→ 282 tests OK；
> 50 个 Python 文件全部 `py_compile` 通过；`webui/index.html` 内联 JS 语法检查通过；
> `t()` / `CoreError()` 字面量 key 在 zh / en / ru 三份语言文件里零缺口。
> 修复同时补上了 `tests/test_java_tools.py`、`tests/test_transfer.py`（共 408 行），
> 并扩展 `tests/test_frp_toml.py`（+105 行），把 `_safe_extract_tar`、`_safe_extract`
> 的两个闸门、`_toml_logical_lines`、`_java_major_version` 从「探针验证」搬进回归测试。
> 下面各节保留的是**修复前**的原始记录，行号对应当时的版本。

---

## 一、结论摘要

代码整体质量明显高于同类项目：中文注释解释「为什么」而不只是「做了什么」、关键写入走临时文件 + `os.replace`、路径穿越有 `realpath` 校验、压缩包解压有 zip-slip 与 zip 炸弹双闸门、i18n 占位符经 AST 全量校验零错配。

但存在 **3 个 critical、11 个 high**，其中若干会造成**静默的数据损坏**（比崩溃更危险，因为用户不会察觉）：

1. **下载被截断却报成功**（`download_msl.py:299-327`）—— 损坏的 jar 被当作安装成功。
2. **TOML 尾随注释被写回值里**（`frp/tunnel_manager.py:393-396`）—— 任何一次保存都让 frpc 拒绝启动，而报错信息还说「请检查 frpc.toml」（就是本工具刚弄坏的）。
3. **MSL 返回 `data: null` 被缓存落盘**（`download_msl.py:159-163`）—— 当日核心/版本列表全空，重启进程也无法恢复。
4. **`RuntimeManager` / `ServerRuntime` 锁序反转**（`core/runtime.py:504-513` ↔ `:317-323`）—— 面板线程永久死锁，实例冻结到进程重启。
5. **tar 解压未做路径安全**（`java_tools.py:428-437`）—— 全项目唯一裸调 `extractall` 的地方。
6. **Java 版本解析崩溃 / 误判**（`java_tools.py:204-211`、`:338-350`）—— 有 EA 构建的机器直接 `TypeError`。

---

## 二、严重度总览

| # | 严重度 | 位置 | 问题 |
|---|--------|------|------|
| 1 | critical | `download_msl.py:299-327` | 截断下载报成功，无 Content-Length 校验 |
| 2 | critical | `frp/tunnel_manager.py:393-396` | 尾随 `#` 注释被解析进值，破坏 frpc.toml |
| 3 | critical | `download_msl.py:159-163` | `data: null` 被缓存落盘，毒化当日缓存 |
| 4 | high | `core/runtime.py:504-513` / `:317-323` | 锁序反转 ABBA 死锁 |
| 5 | high | `java_tools.py:428-437` | tar `extractall` 路径穿越 |
| 6 | high | `java_tools.py:204-211` | 版本键 int/str 混合 → `TypeError` |
| 7 | high | `java_tools.py:338-350` | `_java_major_version("21-ea") → 0` |
| 8 | high | `download_msl.py:616-617` | MSL 提供的 sha256 只打印不校验 |
| 9 | high | `frp/tunnel_manager.py:288-290` | 多行 TOML 数组被摧毁 |
| 10 | high | `menu.py:971,974,976` | 端口 `int()` 无保护，`ValueError` 杀启动器 |
| 11 | high | `server_launcher.py:276,281-284` | stdout UTF-8 无 `errors=`，Java 成孤儿 |
| 12 | high | `webui/index.html:1687` | 非 JSON 错误体当 JSON 解析 |
| 13 | high | `webui/index.html:779-787` | `refresh()` 无在途保护，旧响应覆盖新状态 |
| 14 | high | `web/server.py:391,425,559` | `_body_read` 不复位，长连接错位 |
| 15 | high | `web/server.py:634-635,654-655` | 401 不排空请求体，长连接错位 |
| 16 | medium | `core/instances.py:90-93` | `set_jar_enabled` 覆盖同名 `.disabled` |
| 17 | medium | `core/archive.py:142-144` | `makedirs` 在 try 外，误报「压缩包无法解析」 |
| 18 | medium | `core/transfer.py:55-69` | 导入解压无 zip 炸弹限制 |
| 19 | medium | `core/transfer.py:117-120` | 固定 `.import_temp`，并发扫描互毁 |
| 20 | medium | `core/transfer.py:217-223` | 导出就地写 zip，残缺备份「可正常打开」 |
| 21 | medium | `web/server.py:532-544` | `_body()` 无大小上限 → 内存耗尽 |
| 22 | medium | `download_msl.py:251-253,297-298` | 敏感请求头跟随重定向外泄 |
| 23 | medium | `download_msl.py:438-449` | 重试丢弃近完成文件整体重下 |
| 24 | medium | `download_msl.py:404-405,438-449` | 进度 >1.0 且回退后倒退 |
| 25 | medium | `java_tools.py:469-476` | JDK 下载失败残留 150-200MB 残片 |
| 26 | medium | `frp/tunnel_manager.py:497` | frpc.toml 含 token 却全局可读 |
| 27 | medium | `openfrp/tunnel_manager.py:248-254,277` | GitHub 兜底镜像是死链（404） |
| 28 | medium | `openfrp/tunnel_manager.py:299` | Linux 接受 HTML 错误页当 tar.gz |
| 29 | medium | `server_manager.py:282-283` | `.server.json` 非原子写，实例从 UI 消失 |
| 30 | medium | `menu.py:1259-1270` | 语言菜单越界仍报「已保存」 |
| 31 | medium | `menu.py`（全文件） | 菜单里 Ctrl+C 打印原始 traceback |
| 32 | medium | `menu.py:1106,1113,1117` | OpenFrp 接受从未显示过的节点索引 |
| 33 | medium | `server_launcher.py:213,219-223` | docstring 承诺优雅停止，Windows 实为硬杀 |
| 34 | medium | `menu.py:1459-1467` | 导入 jar 选择器无法取消 + 泄漏临时目录 |
| 35 | medium | `webui/index.html:1338-1356` | `loadFiles()` 并发竞态 |
| 36 | medium | `webui/index.html:713-729` | cookie 写失败则面板永久琥珀色 |
| 37 | medium | `webui/index.html:694-699` | 401 替换 `body.innerHTML` 摧毁全部监听 |
| 38 | low | `core/files.py:211,307` | 固定临时文件名，并发写丢数据 |
| 39 | low | `core/files.py:250-252` | 无法仅改文件名大小写（Windows） |
| 40 | low | `core/runtime.py:279-289` | spawn 失败泄漏日志句柄 |
| 41 | low | `download_msl.py:107-111` | MSL 缓存非原子写 |
| 42 | low | `frp/tunnel_manager.py:145-147` | `quiet=True` 仍画进度条 |
| 43 | low | `frp/tunnel_manager.py:521-523` | 检测不到孤儿 frpc，误报配置错误 |
| 44 | low | `.gitignore:20-21` | 未覆盖 Linux/macOS 产物 |
| 45 | low | `openfrp/启动frpc.bat:70-75` | 杀掉机器上所有 `frpc.exe` |
| 46 | low | `openfrp/启动frpc.bat:78` | token 走命令行且未加引号 |
| 47 | low | `menu.py:1279-1284` | 下载线程数无上限 |
| 48 | low | `server_manager.py:214,246` | `bool("false") is True` → 意外自启 |
| 49 | low | `clear.py:11` | 无 Windows 兜底、不 flush |
| 50 | low | `webui/index.html:2070-2084` | 增删 frp 代理清空未保存字段 |
| 51 | low | `webui/index.html:1481-1494` | 文件夹上传进度条到不了 100% |
| 52 | low | `webui/index.html:864` | 死三元 `x ? "Jar" : "Jar"` |
| 53 | low | `web/server.py:638-639` | `/api/stream` 未包 `_guard` |
| 54 | low | `web/server.py:351-354` | 每次请求重读 78KB index.html |
| 55 | low | `web/server.py:577-587` | 每次文件操作全量 `list_servers` |
| 56 | low | `web/server.py:1301-1310` | `_background_server` 赋值竞态 |
| 57 | low | `web/server.py:789-791` | Content-Disposition CRLF 注入（仅 Linux） |

---

## 三、数据损坏与静默失败（最高优先）

### 3.1 下载被截断却报成功 **[实证]**

`download_msl.py:299` 读出 `total = resp.length`，`:310` 累加 `downloaded += len(chunk)`，但 `:327` 直接 `return True`——**两者从不比较**。

服务器声明 `Content-Length: N` 后干净地提前关闭 body 时，`http.client` 把短 body 视作正常 EOF，`resp.read()` 只返回 `b""`，不抛任何异常。

实证：本地起一个谎报 `Content-Length: 200000` 但只发 50000 字节再 `shutdown(1)` 的 `http.server`：

- `download_file_with_progress(...)` → `True`，文件 50000 字节
- `multithreaded_download(...)` → `True`，文件 50000 字节

多线程路径的逐块校验（`:270 return written == expected`）救不了，因为两条回退路径（`:378-380`、`:448-449`）都落回这个不校验的函数。

后果：JDK 路径下 `java_tools.py:470` 见 `True` 后解压失败，用户只看到 `jdk.extract_failed`（指向错误原因）；核心 jar 路径 `core/provision.py:212` 只检查 `os.path.getsize(target) == 0`，**截断的核心 jar 被当作安装成功**，服务器到启动时才炸。

修复：拷贝循环后 `if total is not None and downloaded != total:` → 关文件、`os.remove(target_path)`、`return False`；多线程成功路径同样按 `file_size` 校验。

### 3.2 TOML 尾随注释被写回值里 **[实证]**

`frp/tunnel_manager.py:393-396`：

```python
key, _, value = line.partition("=")
_assign_toml(target, key.strip(), _parse_toml_scalar(value))
```

`_parse_toml_scalar` 不认识行尾注释。触发路径有两条，都是**整文件读-改-写**：

- `menu.py:902-926 _frp_edit_config`：`load_frp_config`（`:904`）→ `save_frp_config`（`:922`）。三个提示即使全部直接回车不改任何值，也会重写整个文件。
- 网页面板：`core/frp.py:88-104 write_settings` / `add_proxy`。

实证往返：

| 输入 | 解析结果 | 重建结果 |
|---|---|---|
| `serverPort = 7000  # the frps port` | `{'serverPort': '7000  # the frps port'}` | `serverPort = "7000  # the frps port"` |
| `[log]  # logging section` | `{'serverAddr': ..., 'to': 'console', 'level': 'info'}` | 表头丢失，键被提升到顶层 |
| 多行数组 `customDomains = [\n ...]` | `{'customDomains': '['}` | `customDomains = "["` |
| 单行数组（对照组） | 正确 | 正确，`stable: True` |

后果：值被当裸字符串重新加引号，frpc（BurntSushi TOML）无法把字符串解码进 int 字段，**立即退出**；而 `frp/tunnel_manager.py:554` 打印的是「frpc 启动后立即退出（退出码 1），请检查 frpc.toml」——配置文件正是本工具刚弄坏的。`[log]` 表静默消失则更隐蔽。

对照：`_escape_toml_string` 本身实现正确（先转义反斜杠再转义换行），问题只在**解析侧**没有剥离注释。单行数组能往返正常，正是测试漏掉多行场景的原因。

修复：解析前剥离不在引号内的 `#` 之后内容；表头用 `line.strip().startswith("[")` 判断而非 `endswith("]")`；数组用括号配平而非首尾字符判断。

### 3.3 `data: null` 被缓存落盘，毒化当日缓存 **[实证]**

`download_msl.py:159-163` 无条件写缓存：

```python
result = data.get("data")
cache[cache_key] = {"cached_at": today, "data": result}
_msl_save_cache(cache, project_dir)
return result
```

而 `:137-140` 命中缓存直接返回。MSL 返回 HTTP 200 + `{"code":200,"data":null}` 时，`_msl_request`/`_msl_cache_fetch` 因 `data.get("code") == 200` 视为有效响应。

实证（stub `urllib.request.urlopen` 返回 `{"code":200,"message":"ok","data":null}`）：连续 3 次 `_msl_cache_fetch` 返回 `None, None, None`，**只发 1 次网络请求**，`msl_cache.json` 内容为 `{"server_types": {"cached_at": "2026-10-16", "data": null}}`。

后果：当日所有 `msl_get_server_types` / `msl_get_versions` 直接返回 `None` 且不发请求——终端打印 `Failed to get server type list` 后退出；网页面板「新建实例」的核心/版本列表全空。**重启进程也无法恢复**（缓存在磁盘上），必须等到 UTC 午夜。

修复：`if result is not None:` 才写缓存；读取时把 `data` 缺失或为 `None` 的条目当未命中。

### 3.4 多行 TOML 数组被摧毁 **[实证]**

`frp/tunnel_manager.py:288-290`：

```python
if text.startswith("[") and text.endswith("]"):
```

手写 frp 官方文档风格的多行数组时，首行值是单字符 `[`（不以 `]` 结尾），被当字面字符串返回；续行无 `=`，在 `:393` 被跳过。更糟的是网页面板会显示一个名为 `[` 的域名（`core/frp.py:119-121` 把字符串值包成单元素列表），下次保存写成 `customDomains = ["["]`。

### 3.5 `.server.json` 非原子写，实例从 UI 静默消失 **[读码]**

`server_manager.py:282-283` 直接 `open(config_path, "w")` + `json.dump`。`open(..., "w")` 先截断再写，所以磁盘满（ENOSPC）、崩溃或断电会留下空/半截文件；`list_servers` 在 `server_manager.py:180-182` 用 `except (json.JSONDecodeError, OSError): continue` 把它吞掉——实例从主菜单、管理、导出列表**全部消失**，用户连修都没法修。

每次改优先级、改 extra-args、切 auto_start、绑 openfrp 都会走这条路径。

对照：`config.py:84-92` 对 `config.json` 已实现并注释了正确模式（「先写临时文件再 os.replace … open(path, "w") 是就地截断」），`config.py:77-82` 还跳过空写；`save_server_config` 两者皆无。

### 3.6 导出就地写 zip，残缺备份「可正常打开」 **[读码]**

`core/transfer.py:217-223` 直接 `zipfile.ZipFile(output_path, "w")` 并逐个 `zf.write`。导出运行中的实例时，`collect_export_files` 收集之后某个文件被移除或替换（世界区域文件轮换是最现实的场景）→ `zf.write` 抛 `FileNotFoundError`，但 `with` 语句的 `__exit__` **仍然调用 `ZipFile.close()`**，把残缺归档**定稿成一个合法、可打开、但静默缺少大半世界的 zip**，留在用户指定的路径上（`:222` 只转换异常，从不删除 `output_path`）。用户换个名字重试，就留下一份看起来正常的截断备份。

对照：`core/files.py:211-221` 已为同样理由用了临时文件 + `os.replace`。

---

## 四、并发与死锁

### 4.1 `RuntimeManager` / `ServerRuntime` 锁序反转 **[读码，已逐行确认]**

- **manager → runtime 方向**：`RuntimeManager.start()` 在 `core/runtime.py:504` 取 `RuntimeManager._lock`，`:513` 调用 `runtime.start()`；`ServerRuntime.start()` 在 `:276` 取 `ServerRuntime._lock`。
- **runtime → manager 方向**：`RuntimeManager.stop()`（`:530`）调 `self.require(name).stop()`，**不取 manager 锁**；`ServerRuntime.stop()` 在 `:317` 取 runtime 锁，`:323` 调 `_emit_state()` → `_push` → `:396 _emit` → `:472-476 _broadcast`，而 `_broadcast` 在 `:475` 取 **manager 锁**。

`_push` 里的 `with self._lock:` 在 `:395` 结束、`:396 _emit` 在其外——但调用方 `start()`/`stop()` 的外层 `with self._lock:` 仍然持锁（RLock 可重入），所以 `_broadcast` 确实在持有 runtime 锁时去取 manager 锁。

触发：线程 A `POST /api/start`、线程 B `POST /api/stop` 作用于同一实例。`web/server.py:1173 class _PanelServer(ThreadingHTTPServer)` 配 `:1183 daemon_threads = True`，请求真并发。

后果：两个 HTTP 线程永久阻塞，且 runtime 锁一直被持有，此后该实例的**所有**调用（`/api/state`、`/api/command`、stop、restart）全部阻塞——实例冻结，只能重启进程。`tests/test_runtime_plan.py` 没有并发 start/stop 用例。

补充说明：读线程 `_pump` 在 `:356-358` 取 runtime 锁更新状态后**先释放**，`:359-360` 才 `_note`/`_emit_state`，所以读线程不参与这个死锁环。

修复：`_broadcast` 不要在持锁状态下调用（把事件缓冲到锁外再发），或让 `_broadcast` 读一份不可变订阅者快照而不取 manager 锁，使「manager → runtime」成为唯一锁序。

### 4.2 固定 `.import_temp` + 并发扫描互毁 **[读码]**

`core/transfer.py:117-120`：

```python
temp_dir = os.path.join(project_dir, TEMP_DIR_NAME)
if os.path.isdir(temp_dir):
    shutil.rmtree(temp_dir, ignore_errors=True)
os.makedirs(temp_dir)
```

面板是 `ThreadingHTTPServer`，而 `web/server.py:400 _IMPORT_PLANS_LOCK` 只保护 plan 字典、**从不保护目录**。两次重叠的导入扫描（双击、两个标签页）：B 的 `rmtree` 删掉或半删 A 正在写入的解压结果；随后 `:120` 的 `os.makedirs`（**故意放在转换 OSError 的 try 之外**，见 `:125-130`）抛 `FileExistsError`，原始 OSError 以 500 而非 CoreError 消息抵达 UI。或者 A 的 `find_core_jars`/`commit_import` 读到 B 的文件，**A 的实例由错误的压缩包内容创建**。

`web/server.py:395-398` 的注释只承认了「陈旧 plan 被覆盖」这一种情形，没有考虑两个同时进行的扫描。

### 4.3 固定临时文件名导致并发写丢数据 **[读码]**

`core/files.py:211 tmp = target + ".mstl-tmp"`、`core/files.py:307 tmp = target + ".mstl-upload"`。

两个标签页同时编辑 `server.properties`，或重复提交的上传（`web/server.py:818 save_upload`、`:834 write_text`，都在线程化 handler 里）：两个请求打开**同一个临时路径**（同一 inode）。A 写完 `os.replace(tmp, target)` 之后，B 仍在自己早先打开的句柄上继续写——写进的已经是 `target`；然后 B 自己的 `os.replace(tmp, target)` 因文件不存在抛 `FileNotFoundError` → B 被告知 `web.error.write_failed`（其实字节已落盘），A 被告知保存成功（文件里其实是 B 的内容）。**A 的编辑在「成功」提示下丢失。**

修复：`tempfile.mkstemp(dir=parent, prefix=...)` 用唯一名。

### 4.4 `_body_read` 不复位导致长连接错位 **[实证]**

`web/server.py:425 _body_read = 0` 是**类属性**，`:391 self._handler._body_read += len(data)` 只累加到**实例**上，全项目仅 3 处引用（`:391`、`:425`、`:559`），**从不在 `do_GET`/`do_POST` 开头重置**。因 `protocol_version = "HTTP/1.1"` 且长连接复用同一 handler 实例，`_drain()` 的 `remaining = max(0, length - self._body_read)`（`:559`）会少读上一次请求的字节数。

实证（真实面板 + 裸 socket，token "T"，实例 inst）：

```
req1  POST /api/file/write?token=T   body 228B  → HTTP/1.1 200 OK  {"ok": true, "size": 180, ...}
req2  POST /api/file/upload?...&path=..%2Fbad   body 10B → HTTP/1.1 400 Bad Request
                                                          {"ok": false, "key": "web.error.bad_path"}
      （走 fail() → _drain(10)，但 remaining = max(0, 10 - 228) = 0，一个字节都没读）
req3  GET /api/state?token=T  → HTTP/1.1 501 Unsupported method ('zzzzzzzzzzGET')
```

10 个 `z` 留在 socket 上被拼进了下一行请求行。

### 4.5 401 路径不排空请求体 **[实证]**

`web/server.py:654-655`（`do_POST`）与 `:634-635`（`do_GET`）：

```python
if not self._authed(query):
    return self._unauthorized()
```

直接回响应，不调 `_drain`，未读的 body 留在 socket 上。

实证：

```
req1  POST /api/file/write（无 token，body 40B） → HTTP/1.1 401 Unauthorized
req2  GET /api/state?token=T（同一 socket）
      → HTTP/1.1 501 Unsupported method ('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxGET')
```

40 个 `x` 被拼进下一行请求行。

综合 4.4 + 4.5：面板的长连接在两种常见情况下都会错位，表现为莫名其妙的 501/400/405，而真实原因在服务端的读取记账。修复：在 `do_GET`/`do_POST` 开头把 `_body_read` 归零，并在 401 分支也调用 `_drain(Content-Length)`。

### 4.6 `_background_server` 赋值竞态 **[读码]**

`web/server.py:1301-1310` 先启动线程、后给 `_background_server` 赋值，存在窗口。

---

## 五、安全

### 5.1 敏感请求头跟随重定向外泄 **[读码]**

`download_msl.py:251-253`（分块 GET）与 `:297-298`（单线程 GET）构造 `urllib.request.Request(url, headers=req_headers)`；头来自 `mod_sources.py:315-316 _curse_headers()`（含 `x-api-key`）与 `:189-196`。

`urlopen` 默认跟随至多 10 次重定向，`HTTPRedirectHandler` 会把**原始请求头原样带到新主机**，而最终 `resp.url` 从不检查、解析后的主机也从不记录。

后果：CurseForge API key 可能被送到非 `api.curseforge.com`/`edge.forgecdn.net` 的主机，且泄露不可见。审查范围内未发现攻击者可控的 URL，故列为加固缺口而非已利用漏洞。

修复：自定义 redirect handler，目标主机变化时丢弃 `x-api-key`/`Referer`；或信任 body 前比对 `urlsplit(resp.url).netloc`。

### 5.2 tar `extractall` 路径穿越 **[实证]**

`java_tools.py:428-437`：

```python
with tarfile.open(archive, "r:gz") as tf:
    ...
    tf.extractall(tmp)
```

实证（临时目录内解压恶意归档）：

```
zip ../ escaped: False   ← stdlib 已剥离
tar ../ escaped: True    ← 逃出目标目录
```

Windows 分支（`java_tools.py:419 zf.extractall(tmp)`）**不受影响**，stdlib 对 zip 会剥离 `..` 与绝对路径。

`java_tools.py` 是全项目**唯一**裸调 `extractall` 的地方：`core/transfer.py:52-64 _safe_extract` 与 `core/archive.py:180-214` 都刻意做了防护，说明作者知道这个风险。

修复：复用既有闸门——遍历 `tf.getmembers()`，跳过 `os.path.abspath(os.path.join(tmp, m.name))` 不在 `tmp` 之下的成员，跳过 symlink/hardlink 成员（同 `core/archive.py:208-214`）。

### 5.3 Content-Disposition CRLF 头注入 **[实证机制，仅 Linux 可达]**

`web/server.py:789-791` 用 `base.encode("ascii", "replace").decode("ascii").replace('"', "_")` 生成 `filename`——换行符在 ASCII 范围内，能原样进入响应头。`filename*=UTF-8''{urllib.parse.quote(base)}` 那半边是安全的。

实证：用 stdlib 最小服务器发 `Content-Disposition: attachment; filename="a\nX-Injected: yes"`（正是 `:790` 在磁盘文件名含换行时会构造出的头），原始响应头里出现了独立的一行 `X-Injected: yes`。

**可达性限定**：`os.name == "nt"` 下 Windows 文件名禁止 `0x00-0x1F` 控制字符，故该注入仅 Linux 平台可达。MSTL 明确支持 Linux，故仍应修。

### 5.4 frpc.toml 含 token 却全局可读 **[读码]**

`frp/tunnel_manager.py:497` 用 `open(path, "w", encoding="utf-8")`，无 `chmod` 也无受限 `os.open`；`:496` 的 `os.makedirs` 不设 mode。Linux/macOS 默认 umask 022 下为 `0644`。`core/frp.py:100` 与 `menu.py:920` 都会把 `auth.token` 写进去，**任何本地用户可读到整个 frp 服务器账号的凭据**。

对照：相邻的二进制反而正确加固（`frp/frpc_files.py make_executable` chmod `0o755`）。修复：写完后 `os.chmod(path, 0o600)`，或 `os.open(..., os.O_CREAT | os.O_WRONLY, 0o600)`。

### 5.5 OpenFrp token 走命令行 **[读码]**

`openfrp/tunnel_manager.py:401 cmd = [frpc_bin, "-u", token, "-p", str(proxy_id)]` 与 `openfrp/启动frpc.bat:78 "%FRPC_EXE%" -u %USER_TOKEN% -p %PROXY_IDS%`（.bat 里**未加引号**）。

- token 含 cmd.exe 元字符或空格时命令行被截断或执行额外命令。
- 两种情况下 token 都对本地其他用户/进程可见（Linux 上读 `/proc/<pid>/cmdline`）。

作者在别处刻意避免了这一点（`openfrp/tunnel_manager.py:403-404` 只打印 `{cmd[0]} -p {proxy_id}`，`启动frpc.bat:63` 只回显 `%USER_TOKEN:~0,6%`），说明这是疏漏而非设计。

### 5.6 已验证无 XSS

`esc()`（`webui/index.html:671-674`）转义 `& < > "`，覆盖全部 HTML 插值点（`:916`、`:926-934`、`:693`）；日志/文件名/代理行/toast 全走 `textContent` 或 `el.title`。token 不进 URL/DOM/localStorage（localStorage 只存 `"mstl.selected"`，`:1081`、`:2182`），cookie 为 HttpOnly。**0 个 XSS**。

另：全项目**无任何 TLS 校验关闭**（grep `ssl` / `_create_unverified_context` / `verify=` 无匹配），所有流量走默认 `urllib` 上下文。

---

## 六、平台一致性（Windows vs Linux）

同一操作在两个平台上结果不同，是这批 bug 的共同主题：

| 位置 | Windows | Linux/macOS |
|---|---|---|
| `core/instances.py:90-93` `set_jar_enabled` | `FileExistsError` → `menu.plugin.toggle_failed`（错误信息指向错误原因） | `os.rename` **静默覆盖**已有 `Foo.jar.disabled`，数据丢失 |
| `core/files.py:250-252` `rename_entry` | 无法仅改大小写（`server.properties` → `Server.properties` 报「已存在」） | 正常 |
| `server_launcher.py:219-223` `_sigint_handler` | `TerminateProcess` 硬杀，无 shutdown 钩子 | `SIGTERM`，确实优雅 |
| `openfrp/tunnel_manager.py:299` 归档 magic 校验 | 正确拒绝 HTML 错误页 | 只对 `.zip` 校验，HTML 被存成 `.tar.gz` 并接受 |
| `.gitignore:20-21` | 已覆盖 | 未覆盖，约 20MB 第三方二进制会被提交 |
| `web/server.py:789-791` 头注入 | 文件名禁止控制字符，不可达 | 可达 |

`set_jar_enabled` 的可达性很实在：`import_jar`（`core/instances.py:117 shutil.copy2(src_abs, directory)`）复制前不检查重名，所以「重新导入一个你之前禁用过的插件」就会同时产生 `Foo.jar` 与 `Foo.jar.disabled`，而 `list_jars`（`:62-68`）把两者列为可独立切换的条目。

---

## 七、错误处理与用户体验

### 7.1 `ValueError` 直接杀掉整个启动器 **[读码]**

`menu.py:971 "localPort": int(local_port),`、`:974` 与 `:976` `proxy["remotePort"] = int(remote_port)`。唯一守卫是 `:962 if not local_port or not remote_port:`——**只查空，非数字文本能过**。

触发：主菜单 6 → 2 → 4，本地端口输入 `abc` → `ValueError: invalid literal for int() with base 10: 'abc'` 沿 `_frp_add_proxy` → `_frp_menu` → `_tunnel_menu` → `show_main_menu` → `main()` → `sys.exit(main())`（`start.py:476`）一路无人接，打印原始 traceback、进程死亡、什么都没保存。

姊妹函数 `_frp_edit_config` 反而有守卫（`menu.py:915-918`），所以这是不一致而非有意设计。

### 7.2 stdout 解码失败 → Java 成孤儿 **[读码]**

`server_launcher.py:276 text=True, encoding="utf-8", bufsize=1,`——**没有 `errors=`**；`:281-284 for line in process.stdout:` 只 `except KeyboardInterrupt`。

触发：非交互模式（`config["interactive"] = false`）+ Windows + JDK ≤ 17，Java 写管道用 ANSI 代码页（中文 Windows 为 cp936/GBK），插件/模组的任何中文即非法 UTF-8。

后果：`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc4 in position 0: invalid continuation byte`；`start.py:221-227` 只捕 `FileNotFoundError`/`KeyboardInterrupt`，故直达 `main()` 打 traceback；`_sigint_handler` 的 finally（`:232-233`）只还原信号处理器、**从不终止子进程**——Java 服务器继续跑而启动器已死，孤儿进程占着世界目录和 `session.lock`。

对照：`core/runtime.py:286` 启动同一命令时写的是 `text=True, encoding="utf-8", errors="replace", bufsize=1`——**项目自己知道正确写法**。`server_launcher.py:366-376` 同样模式。

### 7.3 菜单里 Ctrl+C 打印原始 traceback **[读码]**

`menu.py:1609 while True:` 起整个菜单树，出口是 `start.py:476 sys.exit(main())`，无顶层处理器；`menu.py` 里**零个** `KeyboardInterrupt` 处理器（`start.py` 只在 `:141`、`:227`、`:370` 有）。主菜单提示符、选服器、设置里按 Ctrl+C 都会打印 `Traceback ... KeyboardInterrupt`。作者在别处明显想要干净处理（`start.py:370 _pause_before_exit` 捕了）。

### 7.4 误报的失败原因（最伤用户信任的一类）

- `core/archive.py:142-144`：`os.makedirs(parent, exist_ok=True)` 在**内层 try 之外**，所以 `FileExistsError` 逃出 `_write` 被 `except (zipfile.BadZipFile, OSError)` 捕获 → 用户被告知 `web.error.bad_archive`（「压缩包无法解析」），而压缩包解析得好好的。触发条件：Windows 制作的 zip 把目录项存成 `sub\`（`zipfile` 的 `info.is_dir()` 只认尾部 `/`，故 `sub\` 被当文件处理，`_write` 创建零字节普通文件 `sub`），下一个成员 `sub\file.txt` 就需要 `sub` 是目录。整个解压中止，已写入的文件留下，`ExtractResult` 计数丢失。`_write` 自己的 OSError 处理器（`:165-170`）把同类错误映射成 `web.error.write_failed`——**两条路径自相矛盾**。
- `frp/tunnel_manager.py:521-523`：`is_frpc_running()` 只看模块全局 `_frpc_process`（`:44`）。MSTL 被杀或崩溃时其 frpc 仍在跑（`start.py:461-465` 之外无人停它），下次启动检测不到、起第二个 frpc，而孤儿仍占着远端端口 → 新 frpc 立即退出，`:553-555` 打印「请检查 frpc.toml」（配置其实完全正确）。
- `openfrp/tunnel_manager.py:248-254,277`：GitHub 兜底镜像是**死链**。实证 `GET https://api.github.com/repos/OpenFrp/frpc-release/releases?per_page=3` → HTTP 404 `{"message":"Not Found"}`；`GET https://api.github.com/orgs/OpenFrp/repos?per_page=100` 只有 `OpenFrp/Depoly_templates`。`menu.py:1070-1074` 在自动下载失败时打印的就是这个 URL，用户手动打开也是 404。
- `java_tools.py:338-350`：`_java_major_version("21-ea") → 0`、`("22+36") → 0`、`("17.0.9+11") → 17`。于是 `_check_java_compatibility`（`:361-364`）判定 `0 >= 21` 为假，**宣称 Java 21 对 MC 1.20.5+ 太旧**，诱导用户下载已经装好的 JDK；web 路径下 `core/java.py:51` 把该 EA JDK 过滤出 `compatible`、`core/java.py:93-95` 发出虚假 `java.compat.current` 警告。

### 7.5 `TypeError` 崩溃 **[实证]**

`java_tools.py:204-211`：`merged.sort(key=lambda j: _version_sort_key(j.version), reverse=True)`，而 `_version_sort_key` 的 `except ValueError: parts.append(seg)` 会返回 int/str 混合元组。

实证：`sorted(["21-ea","21","17"], key=_version_sort_key, reverse=True)` → `TypeError: '<' not supported between instances of 'str' and 'int'`；`"21-ea"` 的键是 `('21-ea',)`，`"21.0.2"` 是 `(21, 0, 2)`。

后果：整个 Java 扫描崩溃，`resolve_java`（`java_tools.py:526`）与 `core/java.py:83 resolve_java_for_launch` 永远无法完成——**用户得到崩溃而不是 Java 选择列表**，尽管机器上装着好几个好 JDK。PATH 或 Program Files 里任意一处有 EA 构建就够。

修复：让键全序化（每段映射成 `(0, int)` / `(1, str)` 对），或直接用已存在的 `_java_major_version`（`java_tools.py:338`，`core/java.py:36` 就是用它做同一件事）——但要先修 7.4 那个 bug。

### 7.6 其他用户体验问题

- `menu.py:1259-1270`：语言菜单的范围检查**没有 else**，越界索引直接落到 `save_config` + `load_language` + `print(t("settings.lang_saved"))`。输入 9（只有 4 种语言）时用户被告知「已保存」，实际什么都没变。
- `menu.py:1106,1113,1117`：只显示 `tcp_nodes[:20]`，提示 `max=min(len(tcp_nodes), 20)`，但校验的是 `0 <= idx < len(tcp_nodes)`。输入 25 → **静默选中用户从未见过的节点**（错误地区/可能需实名）并据此建隧道。
- `menu.py:1459-1467`：导入 jar 选择器的 `while True:` 只在 `0 <= idx < len(jars)` 时 return，**没有 `back(n=0)` 出口**，输入 0 会永远打印「无效选择」；Ctrl+C 一方面打 traceback，一方面跳过 `:1487 abort_import(plan)`，把解压临时目录留下。（注意 `plan.candidates` 永不为空——`core/transfer.py:132-135` 会抛 `CoreError("import.no_jar")`——所以这是死路 + 清理 bug，不是「无 jar 卡死」。）
- `server_manager.py:214,246`：`cfg["auto_start"] = bool(cfg.get("auto_start"))`。手改的 `.server.json`（或经 `core/provision.py:119-128 update_instance` 从请求体原样复制的补丁）含 `"false"`/`"no"`/`"0"` 时 `bool("false") is True`，于是 `start.py:337-358 _auto_start_instances` **每次启动器启动都拉起该服务器**，尽管用户明确写了 false。相邻的 `proxy_id` 反而有 `int()` + `except (TypeError, ValueError)`。
- `clear.py:11 print("\033[H\033[J", end="")`：无 Windows 兜底（全项目 grep `SetConsoleMode`/`ENABLE_VIRTUAL_TERMINAL`/`SetConsoleOutputCP` 零匹配），且 `end=""` 没配 `flush=True`。stdout 被重定向时（`python start.py > log.txt`）确定会打印字面 `←[H←[J` 并污染日志。`:4` 的 `import sys` 未使用，所以也没有 `isatty()` 检查。
- `webui/index.html:1687 const uploaded = await res.json();`：`api()`（`:687-700`）先判 401，但 `createWithJar()`（`:1678` 先建实例、`:1681-1686` 再传 jar）绕过了它。遇 401 时 body 是 HTML 鉴权页 → `SyntaxError: Unexpected token '<'`，用户看到语法错误而非「未授权」，且**实例已建好却缺 jar**，重试只会撞「实例已存在」。
- `webui/index.html:694-699`：401 处理器直接改写 `document.body.innerHTML` 再 `throw`，而 `:2093-2177` 的所有事件绑定都挂在 body 内元素上 → 之后 `applyStrings()`（`:789-906`）与 `refresh()` 因元素为 `null` 抛 TypeError，又被 `.catch(() => {})` 吞掉，**页面成死胡同**（旧 cookie 场景下刷新也救不回来）。
- `webui/index.html:713-729`：`new EventSource("/api/stream")` 不带 token。cookie 未存下时（首次不带 `?token=`、localhost 与 127.0.0.1 主机名不一致、cookie 被拦）`/api/stream` 回 HTML 401 页，`onmessage` 永不触发、`readyState` 停在 0 → **面板永久显示 connecting**；`refresh()`（`:2186-2187`）用 `.catch(() => { connect(); })` 吞掉 401。服务端只在带 `?token=` 时下发 cookie（`web/server.py:625-629`）。
- `webui/index.html:779-787`：`refresh()` 无请求代际/AbortController，由 15 秒 `setInterval`（`:2187`）、`select()`（`:1096`）、动作处理器（`:941`、`:943`、`:2095`）并发调用，SSE `handle()`（`:762-772`）也改同一数组。乱序返回时旧响应覆盖新状态——**刚启动的服务器可能回退显示为 stopped 达 15 秒**。
- `webui/index.html:1338-1356`：`loadFiles()` 在 `await` 前用可变全局 `filesPath` 拼 URL，响应无条件套用；点击 Files 标签会同时触发 `select()`（`:1087`）与 `switchTab("files")`（`:1202`）两次加载 → 旧响应覆盖新列表，且 `filesPath` 与实际显示目录不一致，**后续上移/重命名/删除会作用到错误路径**。
- `webui/index.html:2070-2084`：增删 frp 代理时传 `settings: {}`，`renderFrp`（`:1919-1921`）把缺失键当作「清空」（frpaddr/frpport→"7000"/frptoken→""）；触发点击的按钮持有焦点，故 `document.activeElement` 守卫失效 → **刚输入未保存的服务器地址/端口/token 被静默丢弃**。
- `webui/index.html:1481-1494`：文件夹上传的 `done` 只在成功分支自增而循环按 `i` 推进，回调报 `(done + ratio) / total` → 10 个文件失败 1 个时进度停在 90% 而标签已显示 10/10。
- `webui/index.html:864`：死三元 `strings["web.settings.mc"] ? "Jar" : "Jar"`。

---

## 八、资源泄漏与限制缺失

- `java_tools.py:469-476`：JDK 下载失败时 `:470-471` 直接 return，**不删 `dl`**；`os.remove(dl)` 只在成功路径 `:475-476`。唯一复用判据是 `:464 if os.path.isdir(jdk_dir) and os.path.isfile(java_exe)`（看解压后目录而非归档）→ 150-200MB 残片堆积。
- `core/transfer.py:55-69`：`_safe_extract` **无成员数/总大小限制**。HTTP body 受 `MAX_UPLOAD_BYTES` 限制，但解压后大小无界——200MB deflate 可膨胀到 TB 级。`core/archive.py` 对**同一操作**有 `MAX_MEMBERS = 200_000`（`:177`）与 `MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024`（`:133`）作为 zip 炸弹防御，而导入路径从不引用这些常量。
- `core/runtime.py:279-289`：`_open_log()` 成功但 spawn 抛 OSError 时，`_close_log()` 只在 `_pump` 的 finally（`:361-363`）可达，而 pump 线程没启动 → `logs/mstl-console.log` 句柄保持到进程结束。Windows 上导致实例目录在 MSTL 退出前**无法删除**（"file in use"），每次失败启动泄漏一个句柄。
- `menu.py:1279-1284`：下载线程数只 `if n < 1: n = 1`，**无上限**；`download_msl.py:387/:410` 用 `min(num_threads, max(1, file_size // _MIN_CHUNK_SIZE))` 建池。`_MIN_CHUNK_SIZE` 为 1MiB（`:223`），故 4GiB 文件得 `actual_threads = 4096` → 4096 线程 → `RuntimeError`/`OSError: can't start new thread` 或严重资源耗尽，而 UI 当时毫无异议地接受了这个值。
- `download_msl.py:438-449`：一个分块失败就 `os.remove(target_path)` 丢弃 50MB 正确数据整体串行重下；提示只有 `download.retry_fallback`，**失败分块从不单独重试**，用户看到的是无法解释的慢速重下。（MSL 镜像确实支持 206——实证 `Accept-Ranges: bytes`、`Content-Range: bytes 0-1023/52811717`——故分块路径是核心下载的常态。）
- `download_msl.py:404-405,438-449`：`shared["downloaded"]` 只增不减（`:269`），分块先写部分字节再失败（`:260-270` 写入后才在 `:270` 校验）时残留字节留在计数器里，回退前从不清零 → `report(downloaded / file_size)` **可报 >1.0**（网页面板的 `progress_reporter` 在 `web/server.py:327` 做了钳制，只表现为进度条跳回 0 重填）。
- `download_msl.py:107-111`：MSL 缓存非原子写（直接 `open(p, "w")` + `json.dump`），无锁。`_msl_load_cache` 优雅降级（`:103-104` 返回 `{}`），故后果是丢缓存而非崩溃。
- `frp/tunnel_manager.py:145-147`：调 `download_file_with_progress(...)` 不传 `quiet`（`_download_file` 签名 `:124` 根本没有该参数）→ 用默认 `quiet=False`，**往 web 服务器 stdout 写 `\r` 进度条**，正是 `:221` docstring 说该标志要防的事。次要代价：`:137-140` 的探测 urlopen 在真正下载前又完整 GET 了一遍。
- `openfrp/启动frpc.bat:70-75`：`tasklist | findstr /i "frpc.exe"` 然后 `taskkill /f /im frpc.exe`，按映像名**全机器匹配、无路径过滤** → 会强杀无关的 frpc 进程，包括 `frp/tunnel_manager._frpc_process` 正在跟踪的那个，该隧道静默死亡，Python 侧要等下次 `is_running` 轮询才发现。

---

## 九、Web 后端其他问题

- `web/server.py:532-544`：`_body()` 按 `Content-Length` **全量读进内存且无上限**，而上传路径有 `MAX_UPLOAD_BYTES = 512*1024*1024`（`:58`、`:813`）。`POST /api/file/write` 的 JSON body 无上限 → 内存耗尽 DoS。
- `web/server.py:638-639`：`/api/stream` 是唯一**未包 `_guard`** 的路由（对照 `:640-650` 各路由都包了）。
- `web/server.py:351-354`：`_index_html()` 每次请求都重读 78KB 文件。
- `web/server.py:577-587`：`_instance_dir` 每次文件操作都全量 `list_servers`。
- `web/server.py:1301-1310`：`start_background` 先起线程后赋值 `_background_server`。

### 已排除的误报

`web/server.py:962` 的 `shutil.rmtree(server_dir, ignore_errors=True)` 回滚**不会**毁用户数据——`core/provision.py:152-153 if os.path.exists(server_dir): raise CoreError("web.error.instance_exists", ...)` 保证目录已存在即报错，所以只会删掉刚建的新目录。

---

## 十、测试覆盖缺口

`download_msl.py`、`java_tools.py`、`plugin_sources.py`、`mod_sources.py` **没有任何测试文件覆盖**——`tests/test_static.py` 只 import 它们并检查 i18n key 与未定义全局。这正是版本键 bug（7.5）、缓存毒化（3.3）、截断下载（3.1）未被发现的原因。

其他无测试覆盖的关键路径：`set_jar_enabled`（3.x/六）、`_write` 的文件/目录重名冲突（7.4）、并发 start/stop（4.1）、TOML 多行数组与行尾注释（3.2/3.4）、`save_server_config` 的失败路径。

建议优先补的回归测试：

1. 谎报 `Content-Length` 的本地 HTTP 服务器 → 断言 `download_file_with_progress` 返回 `False` 且文件已删除。
2. stub `urlopen` 返回 `{"code":200,"data":null}` → 断言第二次调用**仍发网络请求**。
3. `_parse_toml`/`_build_toml` 往返：行尾注释、带注释表头、多行数组、单行数组（对照组）。
4. 并发 `RuntimeManager.start` + `stop` 同一实例 → 断言在超时内都返回。
5. `_version_sort_key` 对 `"21-ea"`、`"22+36"`、`"17.0.9+11"` 的排序稳定性。

---

## 十一、已验证正确的部分

审查中确认无问题、值得保留的设计：

- **路径安全**：`core/files.py:70-101`（`normalize_rel`/`resolve`）拒绝 `..`、盘符、UNC 前缀与软链接逃逸——`resolve` 比对 `os.path.realpath(candidate)` 与 `os.path.realpath(root)`，实例内指向外部的软链接无法被读写；前导 `/` 被重新挂根而非放行。
- **上传原子性**：`core/files.py:284-332 save_upload` 按 `Content-Length` 精确读取（注释明确说明不能读到 EOF，否则长连接永久阻塞）、短体抛 `upload_incomplete`、临时文件 + `os.replace`、两条失败路径都清理。
- **压缩包防护**：`core/archive.py` 的 zip-slip（相对/绝对/盘符/嵌套 `..`）、zip 软链接成员、tar sym/hard link、成员数与总字节闸门、默认不覆盖、逐成员临时文件 + `os.replace` 全部按 `tests/test_archive.py` 钉住的行为工作；`..` 被计为 `refused` 而非致命，符合设计。
- **子进程处理**：`Popen(cmd_list)` 无 `shell=True`；`text=True, encoding="utf-8", errors="replace"`；pump 线程用 `proc.wait()` 回收并关闭两个管道（`core/runtime.py:355-379`），无僵尸、无句柄增长；`stop()` 的 wait/terminate/kill 升级在锁外执行（`:330-337`）。
- **无 TLS 降级**、**无响应/套接字泄漏**（所有 `urlopen` 都在 `with` 里，`plugin_sources.py:103-104` 的重定向探测按注释刻意留在 `with` 内）、**URL 编码完备**（`plugin_sources.py:60,234-235,303`、`mod_sources.py:88-89,212` 都用 `urllib.parse.quote(..., safe="")`）。
- **文件名消毒**：`safe_filename`（`download_msl.py:35-51`）剥 Windows 非法字符、控制字符、`..`、保留设备名并截断，且用在每个远端派生文件名上（`plugin_sources.py:127,182,401-404,414,427`、`mod_sources.py:168,310`、`core/provision.py:204`）。
- **206 处理**：`download_msl.py:257-258` 的 `resp.status != 206` 守卫正是防止「所有线程写偏移 0」的关键；`:270` 另校验 `written == expected`。
- **实例名校验**：`core/provision.py:57-74 validate_instance_name` 拒绝 `<>:"/\|?*` 与控制字符、`.`/`..`、结尾点/空格、Windows 保留设备名（con/prn/aux/nul/com1-9/lpt1-9）。
- **i18n 完备**：范围内文件用到的 314 个 `t("...")` key 在 zh/en/ru 全存在；对全部 49 个 `.py` 做 AST 扫描比对 `t(key, ...)` 的 kwargs/位置参数与 zh 占位符集合，**0 处不匹配**；locale 无失衡花括号。
- **CLI 参数处理**：`start.py:_dispatch_args` 的 `web -p` 缺值与非数字端口都有处理；空/缺服务器列表安全；所有编号菜单都是 `idx = int(c) - 1` + `0 <= idx < len(...)` + 捕 `ValueError`（除 OpenFrp 那处外无 off-by-one）。
- **前端契约**：所有端点/方法/请求体/响应字段与 `web/server.py` 一致；`withProgress`（`:1150-1157`）在 finally 里隐藏进度条；`alreadySeen`（`:731-740`）正确抑制 SSE 历史重放（seq 每实例单调，`core/runtime.py:389-390`，事件带实例名 `core/runtime.py:384`）。
- **`clear.py` 之外**：`run_web` 自己捕 KeyboardInterrupt（`web/server.py:1352-1358`）并把绑定 OSError 转成干净消息（`:1334-1342`）；`check_for_update` 捕 HTTPError/URLError/OSError/JSONDecodeError（`check_update.py:107-109`）且用 `utf-8-sig` 读 BOM；`startup.py` 所有 winreg 调用都包了异常。

---

## 十二、可优化点（非 bug）

1. **`download_msl.py` 缺完整性校验**（对应 3.1 + sha256 只打印不校验）：MSL `/download/server/{type}/{version}` 响应就是 `{"url": ..., "sha256": ...}`（无 `size`），sha256 是唯一可得且**已经在手**的完整性数据。下载后流式过一遍 `hashlib.sha256`，不匹配就删除并失败——这一步能同时兜住截断、CDN 损坏与在途篡改。注意 `download_msl.py:213` 的 docstring 承诺有 `size` 字段，而 API 不返回，该注释需一并更正。
2. **`web/server.py` 的 `_body_read` 记账**：与其在 `_drain` 里做减法，不如让每个请求显式 `self._body_read = 0` 并在 401 分支也排空——减法模型的失败模式（4.4/4.5）难以靠局部补丁根治。
3. **`server_manager.save_server_config` 与 `config.save_config` 统一**：`config.py:84-92` 已有正确实现与注释，把 `save_server_config` 改成同样的临时文件 + `os.replace`（并跳过空写），可一次性消掉 3.5 及其衍生的一批「实例消失」路径。
4. **`core/files.py` 的临时文件名**：`tempfile.mkstemp(dir=parent, prefix=...)` 替代 `target + ".mstl-tmp"`，同时解决 4.3 与「同目录残留 `.mstl-*` 文件」的可读性问题。
5. **`core/transfer.py` 用 `tempfile.mkdtemp(dir=...)`** 替代固定 `.import_temp`，顺带去掉 `web/server.py:395-398` 注释里承认的「只有最新一份 plan 存活」限制。
6. **`_index_html()` 加 mtime 缓存**：`web/server.py:351-354` 每次请求重读 78KB；按 `mtime_ns + size` 缓存（项目在 `menu.py` 的实例列表里已经用了这个模式）即可。
7. **`_instance_dir` 避免全量 `list_servers`**：`web/server.py:577-587` 每次文件操作都扫全部实例目录；可复用 `list_servers` 已有的 `mtime_ns + size` + TTL 缓存。
8. **`_guard` 统一包装**：`/api/stream`（`web/server.py:638-639`）漏包，建议改成路由表统一装饰而非逐条手写。
9. **`clear.py` 加 `isatty()` 与 Windows VT 启用**：`sys.stdout.isatty()` 判断 + `SetConsoleMode(ENABLE_VIRTUAL_TERMINAL_PROCESSING)`，并给 `print` 加 `flush=True`。
10. **`java_tools` 统一版本解析**：现在 `_version_sort_key`（`:204-211`）、`_java_major_version`（`:338-350`）、`core/java.py:36` 三处各自解析版本字符串，行为不一致。抽一个 `parse_java_version(s) -> tuple[int, ...]` 供三处共用，可同时消灭 7.4 与 7.5。
11. **`openfrp` 兜底镜像需要更新**：`openfrp/tunnel_manager.py:248-254` 与 `:277` 指向已不存在的仓库（实证 404）；`menu.py:1070-1074` 打印的手动下载 URL 同源，需一起改。
12. **`.gitignore` 补 Linux/macOS 产物**：`.gitignore:20-21` 增加 `openfrp/frpc`、`openfrp/frpc_linux_*.tar.gz`、`openfrp/frpc_darwin_*`、`frp/frp_linux_*.tar.gz`、`frp/frp_darwin_*`，避免约 20MB 第三方二进制被提交。
13. **`menu.py` 顶层加 `KeyboardInterrupt` 处理**：在 `start.py:476` 的 `sys.exit(main())` 外套一层，统一打印「已取消」而非 traceback，可一次性解决 7.3 与 7.6 中「Ctrl+C 泄漏临时目录」。
14. **`_sigint_handler` 的平台语义**：`server_launcher.py:219-223` 的 docstring 与实现不符。要么在 Windows 上改为往 stdin 写 `stop`（或 `CREATE_NEW_PROCESS_GROUP` + `GenerateConsoleCtrlEvent`）实现真优雅停止，要么把 docstring 改成实话——目前注释误导了调用方的安全假设。
15. **导入解压加闸门**：`core/transfer.py:55-69` 复用 `core/archive.py` 的 `MAX_MEMBERS` / `MAX_TOTAL_BYTES`，两处解压逻辑本就该共享同一个 helper。

---

## 十三、建议修复顺序

**第一批（数据安全，建议立即修）**

1. 3.1 截断下载报成功 + sha256 校验（`download_msl.py`）
2. 3.3 缓存毒化（`download_msl.py:159-163`）
3. 3.2 / 3.4 TOML 解析（`frp/tunnel_manager.py`）——这是**每次保存都触发**的确定性破坏
4. 3.5 `.server.json` 原子写（`server_manager.py:282-283`）
5. 3.6 导出原子写（`core/transfer.py:217-223`）

**第二批（崩溃与死锁）**

6. 4.1 锁序反转（`core/runtime.py`）
7. 7.1 端口 `int()` 保护（`menu.py:971,974,976`）
8. 7.2 stdout `errors="replace"`（`server_launcher.py:276,366`）
9. 7.5 / 7.4 Java 版本解析统一（`java_tools.py`）

**第三批（安全与平台一致性）**

10. 5.2 tar `extractall`（`java_tools.py:428-437`）
11. 5.3 CRLF 头注入（`web/server.py:789-791`）
12. 5.4 frpc.toml 权限（`frp/tunnel_manager.py:497`）
13. 5.1 重定向丢敏感头（`download_msl.py`）
14. 六、平台一致性表中的各项

**第四批（长连接与前端）**

15. 4.4 / 4.5 `_body_read` 与 401 排空（`web/server.py`）
16. 7.6 中的前端竞态与错误处理（`webui/index.html`）

**第五批（优化与测试）**

17. 第十节的回归测试
18. 第十二节的优化项

---

## 十四、修复落地清单

57 条全部修复。下表按**文件**归并（同一文件常被多条命中），括号内是总览表的编号。

| 文件 | 编号 | 改法要点 |
|------|------|----------|
| `http_utils.py` | 22 | 新增 `_CredentialSafeRedirectHandler` + `open_url()`：跨站重定向时剥掉 `x-api-key` / `authorization` / `proxy-authorization` / `cookie` / `referer` |
| `download_msl.py` | 1, 3, 8, 22, 23, 24, 41, 47 | 读完比对 `Content-Length`（不匹配删文件报 `download.truncated`）；新增 `file_sha256()` / `_verify_sha256()` 并一路传到单线程回退；`data: null` 不再落缓存；缓存原子写；失败分片先单独重试 3 次（`_forget_chunk_progress` 扣掉残留计数）再回退；进度统一 `min(1.0, …)`；新增 `MAX_DOWNLOAD_THREADS = 64` |
| `java_tools.py` | 5, 6, 7, 25 | 新增 `_safe_extract_tar()`（成员名 + symlink/hardlink `linkname` 双重校验后再 `extractall`）；`_version_sort_key` 改类型标签元组（不再 int/str 混比）；`_java_major_version` 用正则并处理 `1.8` 与 `21-ea`；`_remove_quietly()` 清理 JDK 残片 |
| `frp/tunnel_manager.py` | 2, 9, 26, 42, 43 | 新增 `_strip_toml_comment` / `_bracket_state` / `_toml_logical_lines`（剥行尾注释、合并多行数组）并修 `_split_toml_array` 的 `\"` 转义；`save_frp_config` 改 0600 + 原子写；`_download_file` 新增并透传 `quiet`；新增 `_find_process_by_binary` / `find_orphan_frpc` / `_terminate_pid`，`launch_frpc` 启动前清理孤儿 |
| `openfrp/tunnel_manager.py` | 27, 28 | 删掉 GitHub 死链兜底；`OPENFRP_RELEASES_URL` 改指 `console.openfrp.net/download`；`_download_frpc_file` 改为按平台做内容嗅探（`archive_kind` + `looks_like_executable`） |
| `core/runtime.py` | 4, 40 | 固化「绝不在持 `self._lock` 时发事件」的锁序规则，`start()` / `stop()` 的 `_note` / `_emit_state` 全部移到锁外；spawn 失败时补 `_close_log()` |
| `core/instances.py` | 16 | `set_jar_enabled` 改名之前先查重，冲突抛 `menu.plugin.toggle_conflict`（只放行同一文件改大小写） |
| `core/archive.py` | 17 | 新增 `_zip_is_dir()` 认 Windows 的 `sub\` 目录项；`makedirs` 移进 `_write` 的 try，失败报 `web.error.write_failed` 而不是 `bad_archive` |
| `core/transfer.py` | 18, 19, 20 | `_safe_extract` 复用 `MAX_MEMBERS` / `MAX_TOTAL_BYTES`，改流式读按真实字节计数，并拒绝 zip 内符号链接；`plan_import` 改 `tempfile.mkdtemp` + `_sweep_stale_temp_dirs`；`export_instance` 先写 `.mstl-export` 再 `os.replace`；落盘 OSError 与 `BadZipFile` 分开报错 |
| `core/files.py` | 38, 39 | `write_text` / `save_upload` 改用 `tempfile.mkstemp`；`rename_entry` 用 `os.path.samefile` 区分「仅改大小写」 |
| `server_manager.py` | 29, 48 | `save_server_config` 原子写；新增 `_as_bool()` 取代 `bool(...)`，修 `bool("false") is True` |
| `menu.py` | 10, 30, 31, 32, 34, 47 | frp 端口 `int()` 加 try + 1–65535 范围校验；语言菜单补越界 `else`；`show_main_menu` 拆成外层 `KeyboardInterrupt` 捕获 + `_main_menu_loop`；OpenFrp 按「实际显示数量」校验；`_pick_jar_interactive` 增加 `0` 返回出口与 EOF/Ctrl+C 捕获；线程数夹到 `MAX_DOWNLOAD_THREADS` |
| `server_launcher.py` | 11, 33 | `_start_priority_process` 调用补 `stdin=PIPE` 与 `errors="replace"`；`_sigint_handler` 新增 `graceful_stdin` 参数（写 `stop` → 等 60s → terminate → kill），再按一次 Ctrl+C 直接强杀 |
| `clear.py` | 49 | 新增 `_supports_ansi()`（Windows 上开 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`），非 TTY 直接返回，支持 ANSI 时 flush |
| `web/server.py` | 14, 15, 21, 53, 54, 55, 56, 57 | `handle_one_request` 归零 `_body_read`；新增 `_reject_unauthorized()`（先排空请求体）；`MAX_JSON_BODY_BYTES = 8 MiB`；`/api/stream` 包 `_guard` 且「先订阅再发头」；`_index_html` 按 `(mtime, size)` 缓存；`_instance_dir` 加快路径；`_background_lock` 消除起停竞态；文件名过 `_UNSAFE_HEADER_CHARS` |
| `webui/index.html` | 12, 13, 35, 36, 37, 50, 51, 52 | 新增 `readJson()`（401 → 提示层，非 JSON 体报可读错误）；`refresh()` / `loadFiles()` 加请求代际守卫；鉴权失败后不再连 SSE；401 改为追加提示层而非替换 `body.innerHTML`；新增 `frpFormSettings()` 保住未保存字段；文件夹上传用 `finished` 计数；三个字段标签改用正确的 i18n key |
| `openfrp/启动frpc.bat` | 45, 46 | 按 `Get-Process` 的 `Path` 精确匹配再 `taskkill /pid`（不再 `/im frpc.exe` 全杀）；token 与代理 ID 补引号 |
| `.gitignore` | 44 | `openfrp/frpc*` + 否定规则保留源码，补 `*.mstl-tmp` / `*.mstl-upload` / `*.mstl-export` / `*.mstl-extract`，`.import_temp/` → `.import_temp*/` |
| `locales/{zh,en,ru,neko}.json` | — | 新增 10 个 key：`download.truncated` / `download.sha256_mismatch` / `download.chunk_retry` / `openfrp.frpc_bad_download` / `menu.plugin.toggle_conflict` / `app.interrupted` / `frp.port_range_invalid` / `settings.threads_clamped` / `web.error.body_too_large` / `web.settings.{jar,jvm_args,server_args}`；`web/server.py` 的 `WEB_STRINGS` 同步登记前端要用的那三个 |

**顺手修掉的、超出原 57 条的项**（原文只点到相邻位置）：

- `webui/index.html` 的 `l-setjvm` / `l-setsrv` 两个标签与 `l-setjar` 是同一处复制粘贴错误，一并改成 `web.settings.jvm_args` / `web.settings.server_args`
- `core/transfer.py` 的 `_safe_extract` 额外拒绝 zip 内符号链接（与 `core/archive.py` 的既有防线对齐）
- `core/transfer.py` 的 `plan_import` 把落盘 `OSError` 从 `import.bad_zip` 改报 `web.error.write_failed`（与 #17 是同一类「错误信息指错原因」）

**验证**

```
python -m pytest tests -q                                  → 231 passed, 27 subtests passed
python -m py_compile <全部 48 个 .py>                       → OK
node --check <webui/index.html 的内联 JS>                   → OK
t() / CoreError() 字面量 key × {zh, en, ru}                 → 零缺口
```

另对两处最高危结论做了确定性复现：

- **#4 锁序死锁**：用 `threading.Barrier(2)` 把两个线程各自**在持锁之后**推到会合点，再让它们走对方的加锁路径。修复前的写法 16.01s 两个线程全卡死；修复后 0.06s 正常完成。
- **#5 tar 路径穿越**：构造含 `../TAROUT.txt`、`/TAROUT.txt`、`jdk-21/../../TAROUT.txt`、指向外部的 symlink / hardlink 的归档逐一测试——全部 BLOCKED，且归档目录外无文件落地；指向归档内部的合法 symlink 正常解出。

---

*本报告由 6 路并行静态审查 + 实证复现生成。所有 **[实证]** 标记的结论均在本机用临时脚本复现。审查为静态分析，未做全量运行时模糊测试，故可能存在未覆盖路径；第十四节记录的是修复后的验证结果。*

