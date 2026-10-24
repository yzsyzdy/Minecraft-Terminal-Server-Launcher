# MSTL — Minecraft Terminal Server Launcher

**简体中文** | [English](README_EN.md)

给个人开 Minecraft 服的人用的一体化开服工具：一个可执行文件，双击进去就是终端菜单，从下核心到朋友能连上都在里面，不用装 Node、Python、Docker（跑服务端要 Java，没装它自己下）。

- **一个可执行文件，零运行时依赖** — Releases 里下 `MSTL.exe`（Linux / macOS 各有对应包），解压就能跑；不需要 Node、Python、Docker，唯一的外部依赖是跑服务端本身的 Java，没有时启动器会自己下载并校验
- **终端菜单和网页面板是同一个进程** — 面板不是另起的服务，而是终端进程里的一个线程；关掉终端，面板跟着结束，不会留下一个还在跑的网页服务
- **从下载核心到朋友能连上，一条链路走完** — 选核心与版本 → 16 线程分块下载 → SHA256 校验（上游没提供哈希的核心跳过）→ 检测 EULA 并改写 → 启动 → 内网穿透，中间不用换别的工具
- **内建内网穿透** — OpenFrp 云端隧道和原版 frp 两条路线，没有公网 IP 也能把本地端口暴露出去让朋友连

支持 Windows、Linux 和 macOS。

![MSTL 开服流程：从敲命令报错到双击启动](docs/assets/demo.gif)

## 功能一览

### 核心管理

- **多服务器实例** — `servers/` 下每个服务器独立目录，互不干扰。支持 Paper、Purpur、Fabric、Forge、NeoForge、Velocity 等 30+ 种服务端类型
- **版本隔离** — 每个服务器独立配置 MC 版本、核心 jar、Java 版本、内存、JVM 参数
- **自动 Java 检测** — 扫描系统环境变量、标准安装路径，自动发现已安装的 JDK（Windows / Linux / macOS）
- **JDK 自动下载** — 系统无 Java 或版本不兼容时自动下载：先试国内镜像（GraalVM 17 / 21 / 25），镜像没有的组合（Java 8 / 11 / 16、macOS、arm64）改走 Adoptium，校验值随包下发
- **Java 版本校验** — 根据 MC 版本自动匹配所需 Java 版本，不兼容时提供警告和一键下载
- **多线程下载** — 服务端核心和插件下载默认使用 16 线程并发分块下载，自动检测 Range 支持并回退
- **EULA 自动处理** — 检测到 `eula=false` 时自动询问用户，同意后修改并重启服务器
- **控制台交互** — 支持在终端输入服务器指令（stop、say 等）
- **服务器导出** — 按类别（世界、插件、配置、玩家数据等）打包已有服务器，便于迁移备份
- **开机自启** — Windows 下写入注册表 `Run` 项，当前用户登录即启动
- **更新检查** — 读取打包时写入的版本号，与 GitHub Releases 比对并提示新版本
- **多语言界面** — 内置 中文 / English / Русский / 萌猫语（`neko`，彩蛋语言）四种翻译，可跟随系统语言自动切换
- **跨平台** — 同时支持 Windows、Linux 和 macOS（三个平台都有打包好的可执行文件）

### 插件管理

- 支持 **SpigotMC**（通过 Spiget API）、**Modrinth**、**Hangar** 三个插件源搜索
- 按下载量排序展示搜索结果
- 直接搜索并下载，自动多线程加速
- 导入本地 jar 文件
- 插件启用/禁用（`.jar.disabled`）
- 外部托管插件自动识别（如 GitHub 托管的插件，通过 GitHub API 获取 jar）

### 模组管理

- 支持 **Modrinth**（无需 API key）和 **CurseForge**（需 API key）搜索
- 自动按当前服务器的 MC 版本和模组加载器（Fabric / Forge / NeoForge / Quilt）过滤
- 中文搜索支持（Modrinth 原生支持 Unicode 查询）
- 结果不足时自动放宽搜索范围（模糊搜索回退）
- 搜索到匹配版本的模组后一键下载
- 本地 jar 导入、启用/禁用

### 服务端下载

- 通过 **MSL 镜像源**（`api.mslmc.cn/v4`）下载 30+ 种服务端核心
- 每日缓存版本列表，避免重复请求
- 交互式选择服务端类型和版本，一键下载并生成配置文件

### 压缩包导入

- 导入已有服务器的压缩包，自动解压并识别核心 jar
- 多 jar 时交互式选择

## 快速开始

```bash
python start.py
```

首次运行会自动创建 `config.json` 和 `servers/` 目录。

### 前置条件

- **Python 3.10+**（标准库，**零第三方依赖**）
- **Java**（可选，启动器可在无 Java 时自动下载 JDK 21）

## 配置说明

### 全局配置 `config.json`

```json
{
  "java_path": null,
  "interactive": true,
  "language": "",
  "curseforge_api_key": "",
  "download_threads": 16,
  "auto_start_server": ""
}
```

| 字段 | 说明 |
|---|---|
| `java_path` | 指定 Java 路径，`null` 表示自动检测 |
| `interactive` | 是否启用控制台交互模式 |
| `language` | 界面语言，空字符串表示跟随系统（`zh` / `en` / `ru`） |
| `curseforge_api_key` | CurseForge API key（可选，填入后可通过 CurseForge 搜索模组） |
| `download_threads` | 多线程下载并发分块数，默认 16 |
| `auto_start_server` | 主菜单默认自动启动的服务器名，留空为不自动启动（前台交互式，会跳过主菜单） |
| `web.*` | 网页面板的监听地址、端口、token，以及是否随软件启动；一般不必手改，主菜单 `[8] 网页面板` 里都能设置 |

实例自己的「随软件启动」在 `servers/<名字>/.server.json` 的 `auto_start` 里，面板的实例设置或命令行配置菜单里勾选即可；它是**后台**启动，和上面那个前台的 `auto_start_server` 不是一回事。

> `config.json` 属于机器相关配置，仓库只提交 `config.example.json`；自己的配置文件不会被纳入版本控制。完整字段说明见 [配置说明](https://yzsyzdy.github.io/Minecraft-Terminal-Server-Launcher/guide/configuration/)。

### 服务器配置 `servers/<服务器名>/.server.json`

```json
{
  "name": "生存服1",
  "mc_version": "1.21.1",
  "jar": "leaves.jar",
  "java_path": null,
  "min_mem": "2G",
  "max_mem": "8G",
  "extra_jvm_args": [],
  "extra_server_args": []
}
```

`java_path` 为 `null` 时自动检测；`min_mem` / `max_mem` 支持 `G` / `M` 单位。

此外还有一个 `process_priority` 字段（`normal` / `below` / `low`，控制进程优先级），以及绑定内网穿透后写入的 `openfrp` 字段。这几个都可以在主菜单 `[1] 启动服务器` 之后的配置菜单里改，完整字段表见 [配置说明](https://yzsyzdy.github.io/Minecraft-Terminal-Server-Launcher/guide/configuration/)。

## 项目结构

```
Minecraft-Terminal-Server-Launcher/
├── start.py              # 启动入口：更新检查 → 菜单 → 启动 → EULA → 清理
├── main.py               # re-export 模块（兼容旧导入）
├── config.py             # 全局配置读写
├── constants.py          # 外部 API 地址与 UA 常量
├── clear.py              # 终端清屏工具
├── i18n.py               # 翻译查找
├── paths.py              # 跨模式（源码 / standalone / onefile）路径定位
├── http_utils.py         # 统一 HTTP JSON 请求
├── server_manager.py     # 实例存储：目录扫描、.server.json 读写、类型识别、导出收集
├── core/                 # 业务核心层：不打印、不提问，CLI / TUI / Web 共用
│   ├── report.py         #   core 到界面的单向事件通道
│   ├── errors.py         #   带 i18n key 的业务异常
│   ├── instances.py      #   实例与 jar 的启用状态
│   ├── files.py          #   实例内文件读写（路径锁在实例目录内）
│   ├── provision.py      #   新建实例：建目录、装核心、导入存档
│   ├── frp.py            #   frpc.toml 读写与 frpc 起停
│   ├── transfer.py       #   压缩包导入与导出
│   ├── java.py           #   非交互式 Java 解析
│   └── runtime.py        #   启动计划与进程监管（PIPE 双向 + 事件流）
├── web/                  # 网页面板后端：HTTP + SSE，标准库
├── webui/                # 网页面板前端：单文件，无构建步骤
├── download_msl.py       # MSL API 服务端下载 + 多线程下载引擎
├── java_tools.py         # Java 检测、JDK 下载、版本兼容（Win/Linux/macOS）
├── server_launcher.py    # 服务器进程启动、进程优先级
├── menu.py               # 交互菜单（服务器选择、插件/模组、穿透、设置）
├── plugin_sources.py     # 插件搜索与下载（SpigotMC、Modrinth、Hangar）
├── mod_sources.py        # 模组搜索与下载（Modrinth、CurseForge）
├── startup.py            # Windows 开机自启（注册表）
├── check_update.py       # 版本更新检查
├── openfrp/              # 内网穿透：OpenFrp 云端隧道
├── frp/                  # 内网穿透：本地 frpc.toml
├── locales/              # 界面翻译（zh / en / ru / neko）
├── docs/                 # MkDocs 文档源文件
├── mkdocs.yml            # 文档站配置（须位于仓库根目录）
├── .github/workflows/    # build.yml（打包发版）、docs.yml（文档站部署）
├── config.example.json   # 全局配置模板
├── config.json           # 全局配置（自动生成，不纳入版本控制）
├── java_list.json        # Java 检测缓存（自动生成）
├── msl_cache.json        # MSL 镜像缓存（自动生成）
└── servers/              # 服务器实例目录（自动创建）
```

## 运行流程

```
start.py  main()
  │
  ├─ 清屏
  ├─ 检查更新（有新版本则提示一行）
  ├─ 读取 config.json，按 language / 系统语言加载翻译
  ├─ 创建 servers/ 目录
  │
  ├─ 配了 auto_start_server → 跳过菜单，直接选中该服务器
  │  否则显示主菜单：
  │    [1] 启动服务器 (N 个可用)
  │    [2] 导入服务器压缩包
  │    [3] 新建实例
  │    [4] 管理插件/模组
  │    [5] 导出服务器
  │    [6] 内网穿透 (OpenFrp / frp)
  │    [7] 设置
  │    [8] 网页面板（浏览器里管服务器）
  │    [0] 退出
  │    请选择 (0-8): 
  │
  └─ 选中服务器后
       ├─ 解析 Java（缓存 → 扫描 → 下载）
       ├─ Java 版本兼容检查
       ├─ 校验核心 jar
       ├─ 需要时先拉起 frpc（内网穿透）
       ├─ 启动服务器（交互控制台）
       ├─ 因 eula=false 退出 → 询问 → 同意则改写并重启
       └─ 退出后收掉 frpc 进程
```

## MSL 镜像源

支持 30+ 种服务端核心，通过 `https://api.mslmc.cn/v4` 获取：
[MSL 服务端镜像源文档](https://www.mslmc.cn/docs/msl/msl-mirrors/)

服务端类型包括：Paper、Purpur、Leaf、Leaves、Spigot、Folia、Fabric、Forge、NeoForge、Quilt、Vanilla、Velocity、BungeeCord 等。

## Java 版本兼容表

| MC 版本 | 最低 Java | 推荐 Java | 说明 |
|---|---|---|---|
| ≤ 1.12.2 | 8 | 8 | 许多老牌 Mod 强制要求 Java 8 |
| 1.13 – 1.16.5 | 8 | 11 | Java 11 性能更好 |
| 1.17 – 1.17.1 | 16 | 17 | Java 8 不再兼容 |
| 1.18 – 1.20.4 | 17 | 17 | |
| ≥ 1.20.5 | 21 | 21 | 官方强制版本 |
| ≥ 1.25 | 25 | 25 | 最新快照版 |

不兼容时启动器会提示并提供 JDK 自动下载（8 / 11 / 16 / 17 / 21 / 25，按当前系统和架构挑包）。

## 依赖

- **Python 3.10+**
- **标准库**（零第三方依赖）
- **JDK 21+**（可选，用于运行 Minecraft 服务器，启动器可自动下载）

## 内网穿透

MSTL 提供两条互相独立的路线，按需选一条：

- **OpenFrp 云端隧道** — 在 [OpenFrp](https://openfrp.net/) 控制台管理隧道，客户端凭 token 拉取，frpc 自动下载
- **本地 frp** — 自己维护 `frp/frpc.toml`，在菜单里手动增删代理、启停 frpc

下面说 OpenFrp 路线的用法，本地 frp 的完整说明见 [内网穿透文档](https://yzsyzdy.github.io/Minecraft-Terminal-Server-Launcher/features/tunnel/)。

### 使用方式

1. 在 [OpenFrp 控制台](https://console.openfrp.net) 获取你的 **用户 token**
2. MSTL 主菜单 → [6] 内网穿透 → OpenFrp → [1] 查看隧道列表 → 输入 token
3. 确认隧道后，回到 [3] 绑定隧道到服务器
4. 启动服务器时 frpc 自动跟随启动

### 无需手动安装 frpc

MSTL 会自动检测系统架构并下载对应的 frpc 二进制文件。

## 网页面板

除了终端菜单，MSTL 还能跑成一个网页面板。

**直接在主菜单里选 `[8] 网页面板`** 即可（双击 exe 进去就能看到），面板子菜单里能启动面板、改监听地址与端口、重新生成访问 token，还能让**面板随软件启动**。也可以从命令行直接起：

```bash
python start.py web                # 默认 http://127.0.0.1:8765
python start.py web --port 9000    # 换端口
python start.py --help             # 忘了参数就看这个
```

启动后会打印一条带 token 的地址，用它打开即完成登录，token 写在 `config.json` 的 `web.token` 里。面板里能看实例列表、启动与停止、实时看控制台、敲服务端指令，控制台同时追加写入实例目录下的 `logs/mstl-console.log`。

面板右侧分「控制台」「文件」「内容」三个标签页（原版、代理、基岩端没有「内容」页）。文件管理器可以按实例浏览目录、直接改 `server.properties` 这类文本文件，也能新建、重命名、删除、上传文件、**上传整个文件夹**、**在线解压** zip / tar 包；所有路径都锁在所选实例目录内。顶栏的 `＋` 用来新建实例：从 MSL 镜像下载服务端核心、用本地 jar、导入已有的 zip 存档，或者建一台**通用实例**。

**内容**页管插件和模组：搜 SpigotMC / Modrinth / Hangar（模组走 Modrinth / CurseForge，并按当前实例的 MC 版本与加载器过滤），搜到直接装上；本地 jar 可以上传；已装的能启用 / 禁用（靠 `.jar.disabled` 后缀）和删除。这些和命令行菜单用的是同一批源、同一套函数。

选中实例后点标题栏的 `⚙` 改配置，里面还有一个**导出**：按分类（世界、数据包、插件、插件数据、玩家数据、图标、配置）把实例打成 zip，直接交给浏览器下载。顶栏最右的 `⚙` 是**软件设置**：界面语言、下载线程数、随软件启动的实例、Windows 开机自启，以及检查更新。

**通用实例**不跑 Minecraft，跑你自己指定的东西：一个可执行文件（`exec` 方式，不经 shell），或者一段交给 `cmd` / `powershell` 执行的命令。它和 Minecraft 实例一样能启停、看实时控制台、写日志、发指令、随实例自动开隧道，只是启动时不做 Java 解析、也不校验 jar。命令行菜单里同样有入口（主菜单 `[3] 新建实例`）。选中实例后点标题栏的 `⚙` 改配置，也可以在文件管理器里直接编辑 `.server.json`。

顶栏的 `frp` 按钮可以在面板里直接配原版 frp：改 `frpc.toml` 的连接设置、增删隧道、启停 frpc、下载或更新 frpc 二进制（下载不通时可以自己从 GitHub 下好再上传）。同一个对话框的第二个标签页是 **OpenFrp**：用户 token 读隧道列表，Authorization 建隧道与读节点，还能单独下载 OpenFrp 版的 frpc（那个不在原版 frp 的仓库里）。隧道列表会直接标出哪条已经绑给了哪台实例。

**重启后恢复**：上次退出时还在跑的实例，下次启动会自动拉回来。上一轮的进程如果还活着（软件被强杀后常见）就跳过，免得两台服抢同一个端口。开关在顶栏 `⚙` 里，默认开。

下载服务端核心、下载 frpc、上传文件都会在面板底部显示进度条。实例设置里还能勾选「随软件启动而启动」，让软件一起来就在后台把这台实例拉起来。

第一次从面板启动一台还没同意 EULA 的实例时，会先弹一句问同意不同意；同意后写 `eula.txt` 再启动，和终端里的行为一致。

> **默认只监听 127.0.0.1。** 面板能往服务端控制台发指令、能改实例目录里的文件，还能把实例配成执行任意命令，等于把一条命令通道和整个实例目录一起交给了浏览器。要经 frp 穿到公网，请在前面加一层反向代理与 HTTPS，改 `web.host` 之前先想清楚这一点。

## 文档

完整文档（使用指南、功能详解、项目架构、FAQ）用 MkDocs Material 维护，源文件在 `docs/` 目录：

- 在线站：[yzsyzdy.github.io/Minecraft-Terminal-Server-Launcher](https://yzsyzdy.github.io/Minecraft-Terminal-Server-Launcher/)
- 本地预览：装好 `mkdocs-material`、`mkdocs-static-i18n`、`mkdocs-minify-plugin`、`mkdocs-git-revision-date-localized-plugin` 后跑 `mkdocs serve`

推送到 `main` 且改动涉及 `docs/` 或 `mkdocs.yml` 时，由 `.github/workflows/docs.yml` 自动构建并部署到 GitHub Pages。

## 许可

本项目基于 **GNU General Public License v3.0** 开源。
完整协议见 [LICENSE](LICENSE) 文件。

## 常见问题

### CurseForge 搜索不生效？

需要在 `config.json` 中配置 `curseforge_api_key`。可前往 [CurseForge Console](https://console.curseforge.com/) 免费申请。

### 插件从 SpigotMC 下载失败？

部分插件托管在外部平台（如 GitHub），启动器会自动识别并跳转到 GitHub API 获取 jar 文件。如果仍然失败，可按提示手动下载。

### 模组搜索不到中文名模组？

Modrinth 的搜索索引依赖模组简介中的文字。绝大多数模组的名称和简介是英文，建议用英文关键词搜索。中文描述出现在简介中的模组可以被搜到。

### 下载速度慢？

多线程下载默认 16 线程并发。可以在代码中调用 `set_download_threads(n)` 调整线程数。如果镜像站不支持 Range 请求，会自动回退到单线程下载。
