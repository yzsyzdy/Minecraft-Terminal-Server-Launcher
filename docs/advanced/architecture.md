# 项目架构

## 设计前提

三条约束决定了大部分代码的样子：

1. **零第三方依赖**。只用 Python 标准库，`urllib.request` 当 HTTP 客户端，`json` 存配置，`zipfile` 解压。因此没有 `requests`，也就没有自动重试和连接池，网络容错都得自己写
2. **终端界面**。没有 GUI 框架，菜单就是 `print` 加 `input`，所有文案必须走 i18n
3. **要能打包成单文件 exe**。Nuitka 打包后 `__file__`、`sys.executable`、`sys.argv[0]` 的含义都会变，路径逻辑被单独抽成了 `paths.py`

## 分层

代码分三层：

1. **`core/`** 是业务核心，只做数据与文件，不 `print` 也不 `input`。出事了抛 `CoreError`，有话说就走 `Reporter`
2. **根目录的各业务模块** 是它的实现细节：实例存储、MSL 下载、Java、插件模组源、隧道
3. **`menu.py` 与 `start.py`** 是命令行界面，只负责提问与打印

这么做是为了后面的 TUI 与 Web 面板：新界面只依赖 `core/`，业务代码一行不用改。

## 模块划分

### 核心层 core/

| 模块 | 职责 |
|---|---|
| `core/errors.py` | 带 i18n key 的业务异常 `CoreError`，界面层负责翻译呈现 |
| `core/report.py` | core 到界面的单向事件通道：`log` / `progress` / `warn` |
| `core/instances.py` | 实例与 jar：扫描、启用/禁用、删除、导入 |
| `core/files.py` | 实例内文件读写：目录列举、文本读写、新建/改名/删除、上传落地，路径一律锁在实例目录内 |
| `core/provision.py` | 新建实例：建目录、从 MSL 装核心、扫描并导入存档 |
| `core/frp.py` | frpc.toml 读写与进程起停：连接设置、隧道增删、状态查询 |
| `core/transfer.py` | 压缩包进出：`plan_import` / `commit_import` / `export_instance` |
| `core/java.py` | 非交互式 Java 解析：显式路径 → 上次选择 → 缓存 → 现场扫描 |
| `core/runtime.py` | 启动计划与进程监管：PIPE 双向读写、环形缓冲、事件广播；按 `kind` 分流出 Minecraft（Java + jar）与通用实例（可执行文件 / 命令）两条启动路径 |

导入特意拆成两步，是因为「多个候选核心该选哪个」不该由 core 拍板。`plan_import` 只负责解压并列出候选，界面层问完用户再调 `commit_import` 落地。`core/provision.py` 的 `scan_archive` / `import_archive` 就是这条路径上面向网页面板的一层薄封装。

### 入口与配置

| 模块 | 职责 |
|---|---|
| `start.py` | 程序入口，串起更新检查、配置加载、菜单、启动流程、EULA 循环、frpc 清理 |
| `main.py` | 公共 API 汇总，re-export 各子模块的对外接口，兼容旧式 `from main import ...` |
| `config.py` | `config.json` 读写，带默认值合并 |
| `constants.py` | 所有外部 API 地址与 User-Agent 集中定义 |
| `i18n.py` | 翻译查找，`t("key", **kwargs)` 取文案 |
| `paths.py` | 跨模式路径定位，区分「用户数据目录」和「打包资源目录」 |
| `clear.py` | 跨平台清屏 |

### 业务

| 模块 | 职责 |
|---|---|
| `server_manager.py` | 实例存储：目录扫描、`.server.json` 读写、服务端类型识别、导出分类与文件收集 |
| `download_msl.py` | 调 MSL API 取服务端类型与版本、每日缓存、多线程下载引擎 |
| `java_tools.py` | Java 检测（环境变量 + 常见安装路径）、版本解析、兼容性判断、JDK 下载解压 |
| `server_launcher.py` | 拼 JVM 命令、启动子进程、交互控制台、进程优先级、SIGINT 处理 |
| `menu.py` | 所有交互菜单，体量最大的一个模块 |

### 数据源与周边

| 模块 | 职责 |
|---|---|
| `plugin_sources.py` | SpigotMC（Spiget）、Modrinth、Hangar 的搜索与下载，含外部托管解析 |
| `mod_sources.py` | Modrinth、CurseForge 的搜索与下载，版本与加载器过滤 |
| `http_utils.py` | 统一的 `fetch_json`，GET / POST 共用，失败返回 `None` |
| `check_update.py` | 读 `version.json` 比对 GitHub Releases 最新版 |
| `startup.py` | Windows 注册表开机自启，非 Windows 直接返回不可用 |
| `openfrp/` | OpenFrp 云端隧道：token 鉴权、隧道增删改查、frpc 自动下载、`FrpcManager` 单例 |
| `frp/` | 本地 frp：`frpc.toml` 解析与生成、代理增删、frpc 启动 |

`openfrp/` 与 `frp/` 是两个并列实现，服务于「云端托管隧道」和「自备 frpc 配置」两种不同用法，没有互相继承或复用关系。

### 界面层

| 模块 | 职责 |
|---|---|
| `start.py` | 终端入口；`python start.py web` 直接切到网页面板 |
| `menu.py` | 终端交互菜单：只负责提问与打印 |
| `web/server.py` | 网页面板后端：HTTP 路由、token 鉴权、SSE 推送 |
| `webui/index.html` | 面板前端，单文件，无构建步骤 |

## 启动时序

```
start.py  main()
  ├─ clear_screen()
  ├─ check_for_update()              静默检查，有新版就打一行提示
  ├─ load_config() / load_language()  语言为空时按系统语言推断
  ├─ ensure_servers_folder()
  ├─ auto_start_server 命中 → 跳过菜单直接选实例
  │  否则 show_main_menu()
  └─ _start_server()
       ├─ resolve_java()             缓存 → 扫描 → 必要时下载
       ├─ 校验核心 jar 存在
       ├─ openfrp.auto_start 为真 → launch_frpc()
       ├─ 打印实例信息摘要
       └─ while True:
            ├─ start_server_interactive() 或 start_minecraft_server()
            ├─ 非 EULA 退出 → break
            └─ 因 eula=false 退出 → 询问 → 同意则改写并 restart
```

服务器退出后无论退出码如何，`main()` 都会调用 `stop_frpc()` 收掉隧道进程。

## 两个容易踩的工程细节

### 打包后的路径

Nuitka onefile 模式运行时会把自身解压到 `%TEMP%/onefile_xxxx`，此时 `sys.executable` 指向临时目录，程序退出后这个目录就被删了。如果把用户配置写在那里，等于每次运行都丢失配置。

`paths.py` 用这套策略区分：

- **用户数据目录**（`config.json`、`servers/`、缓存）：源码模式是项目根目录；onefile 模式通过 `MSTL_DATA_DIR` 环境变量或父进程 `NUITKA_ONEFILE_PARENT` 的 PID 反查原始 exe 路径来定位
- **打包资源目录**（`locales/`）：跟随 `__file__`，也就是解压后的临时目录，因为这批文件是只读资源

`i18n.py` 找 `locales/` 时特意注明不要用 `sys.executable`，原因就在这里。

### 多线程下载

`download_msl.py` 先用一次请求探测目标是否支持 Range，支持则按 `download_threads` 分块并发拉取再拼接，不支持则退回单线程顺序下载。分块数与线程数的关系、失败重试都在这个模块里，`set_download_threads()` 是唯一的调节入口，菜单里的「下载线程」设置最终也落到它。

## 扩展指引

- **加一个服务端类型**：`download_msl.py` 里类型来自 MSL API，一般不用改代码，镜像站上新即可用；需要额外处理时改 `_server_display_name()` 与分类逻辑
- **加一个插件源**：照 `plugin_sources.py` 里的 `hangar_search` 写一个函数，返回 `PluginResult` 列表，再挂进 `search_all()`
- **加一门语言**：往 `locales/` 放一个 `<code>.json`，键与 `zh.json` 对齐，`available_languages()` 会自动发现它
- **加一条菜单项**：`menu.py` 的 `show_main_menu()` 里加分支，文案同时补进所有 locale 文件
