# 更新日志

本文件记录 MSTL 每个版本的改动，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

`v1.1.35` 及更早版本的说明见 [GitHub Releases](https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher/releases)。

## [未发布]

### 新增

- **多平台打包**：CI 不再只出 Windows 产物，现在一次打出四个：

  | 平台 | 产物 |
  |---|---|
  | Windows (x86_64) | `MSTL-v<版本>.zip`（与旧版同名） |
  | Linux (x86_64) | `MSTL-v<版本>-linux-x86_64.tar.gz` |
  | macOS (Apple 芯片) | `MSTL-v<版本>-macos-arm64.tar.gz` |
  | macOS (Intel) | `MSTL-v<版本>-macos-x86_64.tar.gz` |

  非 Windows 平台用 **tar.gz** 而不是 zip：zip 不保存 Unix 可执行位，解压出来直接是
  `Permission denied`。

- 每条腿构建完会**冒烟测试**一次产物（跑 `./MSTL --help`，退出码非 0 即失败），
  免得打出一个启动就崩的二进制还照发不误。
- 发版拆成独立的 `release` job，收齐所有平台的 artifact 后一次性建 Release；
  手动触发（`workflow_dispatch`）只产出 artifact，不再误发版。
- `Tests` 工作流改成 Windows / Linux / macOS 三平台矩阵（每个平台跑 3.11 与 3.13）。
  只在 Windows 上跑过的用例会把平台假设藏起来：隧道解压的两条用例拿 PE 的 `MZ`
  当合法可执行文件，Windows 上永远是绿的，Linux/macOS 上会被判成「拿错平台的包」
  而失败——加平台矩阵就是为了让这类问题在 PR 阶段就暴露。

### 说明

- **macOS 产物没有签名**（签名需要 Apple 开发者证书）。首次运行若被 Gatekeeper 拦下，
  右键 →「打开」，或 `xattr -d com.apple.quarantine ./MSTL`。
- **Linux 产物要求 glibc ≥ 2.35**（构建机是 Ubuntu 22.04）。Nuitka 的 standalone 产物
  不打包 glibc，更老的发行版请从源码运行。
- macOS 上暂时**不会自动扫描系统 JDK，也没有 JDK 自动下载**：请设置 `JAVA_HOME`，
  或在实例设置里直接填 `java_path`。

## [1.1.37] - 2026-10-16

本次跨度较大：新增**网页面板**（含文件管理器与新建实例）与**业务核心层**两块新东西，
另有一批会直接影响正常使用的修复，以及一次覆盖全部 57 条的代码审计。
配置文件向后兼容，覆盖安装即可。

### 新增

#### 测试与 CI

项目此前**一个测试都没有**，CI 只负责打包——上面那一堆「存得下、看得见、却没生效」
的问题全靠手工复现才发现。现在有了 `tests/`（282 个用例，只用标准库 `unittest`，
不需要装任何依赖）：

```bash
python -m unittest discover -s tests -t . -v
```

覆盖的是这次踩过的每一类契约：设置往返与 `/api/state` 回显、面板鉴权、
配置读写、实例扫描与缓存、`frpc.toml` 往返（用 `tomllib` 校验）、压缩包解压的
越界防护、打包版路径解析（用伪造的 `__compiled__` 模拟，不用真的编译）、
`version.json` 的 BOM 兼容。

另外 `tests/test_static.py` 把我手工跑过的静态检查固化了：未定义名扫描、
面板元素 id 与文案 key 的前后端一致性、JS 语法、语言文件覆盖。这几项各自都
在这次审计里抓到过真问题。

代码审计补上的几处防御原本只有临时探针验证过，现在都进了回归测试：
`tests/test_java_tools.py`（JDK 版本号解析与排序、tar 解压的路径穿越与
「先校验完再落地」）、`tests/test_transfer.py`（zip 越界与软链、成员数/总大小
两个压缩炸弹闸门、Windows 风格目录项）、`tests/test_frp_toml.py` 新增的
逻辑行合并用例（行尾注释、多行数组、转义引号）。

- 新增 `.github/workflows/tests.yml`：每次 push / PR 在 Python 3.11 与 3.13 上跑测试
- `build.yml` 里加了一道测试，发版前先跑，免得带着失败的测试发出去
- 代码审计报告落在 [`docs/advanced/code-review.md`](docs/advanced/code-review.md)，
  并接进文档站的「进阶」目录

#### 网页面板

浏览器里管服务器，不必守在终端跟前。

**三种进法**：主菜单 `[8] 网页面板`（双击 exe 进去就能看到，不用记参数；子菜单里能启动面板、
改监听地址 / 端口、重新生成访问 token）、`python start.py web`、以及 `python start.py --help`。

```bash
python start.py web                # 默认 http://127.0.0.1:8765
python start.py web --port 9000    # 换端口
```

- **实例列表与详情**：版本、核心、运行状态；选中后显示内存、PID / 退出码、Java、工作目录与拼好的启动命令
- **启停**：停止是优雅的，先发 `stop`，超时才 terminate，再不行才 kill
- **实时控制台**：SSE 逐行推送，同一条也追加写入实例目录下的 `logs/mstl-console.log`
- **发指令**：输入框内容原样送给服务端，等价于终端交互模式；另有 `list` / `save-all` / `help` 一键发送，`say` 只填前缀
- **控制台细节**：清屏、跟随滚动、回到底部、`WARN` / `ERROR` 分开着色、切换实例自动回放该实例的历史、断线重连不产生重复行
- **文件管理器**：按实例浏览目录树，就地编辑 `server.properties`、`ops.json`、各类 `.yml`；新建文件 / 文件夹、重命名、删除、上传、下载。二进制与超限文件自动改为下载
- **新建实例**：三种方式——从 MSL 镜像下载服务端核心（核心版本号自动填进 MC 版本）、用本地 jar、导入已有的 zip 存档（先扫描候选核心再选一个落地）；核心下载失败会回滚刚建的空目录
- **安全边界**：所有 `/api` 要求 token（定长比较，只认 `mstl_token` cookie 本身），默认只监听 `127.0.0.1`

文件管理器的路径全部锁在所选实例目录内：`..`、盘符、绝对路径一律拒绝，符号链接与
junction 指向实例之外同样拦下；写入走「临时文件 + 原子替换」，中途失败不会把原文件截断。

#### 业务核心层 `core/`

把「数据与文件的事」从界面里抽出来，CLI 从此只负责提问与打印。`core/` 不 print、
不 input、不认识终端也不认识浏览器。目前包含实例、压缩包进出、Java 解析、进程监管，
以及本次新增的文件读写与新建实例。将来的 TUI 或别的界面都能直接复用同一条路径。

配套的进程监管支持双向 PIPE、环形缓冲与事件广播，每个事件带实例内单调递增序号，供界面去重。

#### 原版 frp 内网穿透

除 OpenFrp 之外，现在也支持原版 frp：本地 `frpc.toml` 配置、增删改隧道、一键启停，
入口在主菜单 `[6] 内网穿透`。

#### 开机自启与自动启动服务器

- Windows 开机自启（写注册表，设置菜单里开关）
- 可指定「启动时自动拉起哪台服务器」，配好后跳过主菜单直接进服

#### 文档站

MkDocs Material 双语（中 / 英）文档站，涵盖使用指南、功能详解、项目架构与 FAQ，
推送到 `main` 后自动部署到 GitHub Pages。

#### 更新检测

启动时静默比对 GitHub Releases，有新版本时打印版本号与下载地址。

#### 通用实例

除了 Minecraft 服务端，现在还能把**任意一个长期运行的程序或命令**当成实例来管。实例新增 `kind` 字段
（`minecraft` / `generic`），`.server.json` 里的 `launch` 段描述通用实例怎么启动：

- `exec`：直接运行一个可执行文件，参数一项一个，**不经 shell**
- `cmd`：`cmd.exe /c "<命令>"`，管道、重定向、`&&` 都能用
- `powershell`：`powershell -NoProfile -Command "<命令>"`

`program` 与 `workdir` 都支持相对路径（按实例目录算）。通用实例享有和 Minecraft 实例一样的能力：
启停、实时控制台、`logs/mstl-console.log`、发指令、随实例自动开隧道、导出存档。

命令行与网页面板都有入口：主菜单 `[3] 新建实例` → `[2] 通用实例`；面板 `＋` → 「通用实例」标签。
配置改动的入口是面板里的 `⚙`，或直接编辑 `.server.json`。

实例的 `kind` 在创建后不可更改——切换种类等于把实例换成另一个东西，不该混在「保存设置」里悄悄发生。

#### frpc 下载失败时的手动兜底

以前下载不通就只是一句「下载失败」，用户只能自己猜去哪儿找。现在两条路线都会给出发布页地址、
该下的文件名，以及放/传到哪里：

- **网页面板**：frp 面板里常驻一行「下载不通？手动安装」——一个「打开 GitHub 发布页」按钮
  加一个「上传 frpc」按钮。上传的压缩包会按**内容**识别并自动解出里面的 frpc（裸二进制也收）。
  下载失败时的提示里也直接带上发布页地址。
- **命令行**：`下载 frpc` 失败时打印发布页、该下的文件名，并允许填入本地已下好的文件路径，
  由 MSTL 解出 `frpc` 放到位。OpenFrp 路线会打印它自己的发布页，并说明把压缩包丢进
  `openfrp/` 即可（启动时自动解压）。

上传/安装时会校验落地的东西**像不像本平台的可执行文件**：传错文件、或下错平台（在 Windows 上
拿了 linux 的包）会当场拒掉并清掉半成品，而不是装出一个一跑就报格式错误的 `frpc.exe`。

顺带修掉一个自己引入的隐患：上传请求体只被读了一部分就失败时，处理器会去补读**整个**请求体，
而 HTTP/1.1 是长连接、剩下的字节永远不会再来——那条请求线程会卡住。现在只补读尚未读过的部分
（已用「读到一半人为报错」的用例验证连接不再挂起）。

#### 文件管理器：上传文件夹与在线解压

- **上传文件夹**：工具栏新增「上传文件夹」，用浏览器的目录选择器，整个目录带着相对路径传上来
  （选 `world` 就得到 `world/level.dat`）。缺失的中间目录会自动建出来——浏览器给出的顺序不保证，
  `world/level.dat` 完全可能先于 `world/` 到达，为此给上传接口加了「顺带建父目录」。进度条显示
  `正在上传 12/340`。浏览器限制：空目录不会被报告，因此传不上来。
- **在线解压**：压缩包那一行多了「解压」按钮，就地解开。支持 zip 与 tar 系，**按内容识别而非后缀**。
  默认不覆盖已存在的文件（解压常用于往已有实例里补文件，悄悄盖掉 `server.properties` 比解压失败更糟），
  结果会回报跳过了几个。解压出来的成员受同一套路径约束：`..`、绝对路径、盘符一律拒绝，
  压缩包内的符号链接 / 硬链接直接跳过；另加成员数（20 万）与解压后体积（4 GiB）两道闸挡压缩炸弹。

#### 修复：打包漏掉 webui，面板在 exe 里是坏的

`build.bat` 与 CI 的 `build.yml` 都只 `--include-data-dir=locales=locales`，**没有带上 `webui/`**。
源码模式下一切正常，但打包出的 `MSTL.exe` 一打开网页面板就是 `webui/index.html missing`（HTTP 500）
——这一整块功能在发布产物里根本用不了。

- 两个打包脚本都补上 `--include-data-dir=webui=webui`
- CI 新增 `Verify bundled assets` 步骤，逐个检查 `webui\index.html`、`locales\zh.json`、
  `locales\en.json` 是否真的在 `start.dist` 里，缺一个就让构建失败
- 启动面板时也自查一遍随包资源，缺了直接打印 `[警告] 缺少随包资源: …`，
  不必等浏览器里报错才去猜
- 文档补上「运行时资源清单」表，说明每个资源缺了各自会坏成什么样

#### 面板随软件启动

主菜单 `[8] 网页面板` 的子菜单里多了一项「随软件启动」（对应 `config.json` 里的 `web.auto_start`）。
开启后，软件一起来面板就在**后台线程**里跑着：终端该进菜单进菜单、该起服务器起服务器，
互不干扰；退出时自动收摊。

#### 实例的「随软件启动而启动」

`.server.json` 新增 `auto_start`，在**实例设置**（面板 `⚙` / 命令行配置菜单）里开关。
开启后软件启动时会在**后台**把这台实例拉起来：照常写 `logs/mstl-console.log`，
网页面板里能看它的控制台、能停能发指令，终端还留在菜单上；关掉软件时会一并停止。

它和已有的全局 `auto_start_server` 是两回事，文档里已写明：

- `auto_start_server`（全局，一台）：跳过主菜单，**前台**跑那一台，终端归它用
- 实例的 `auto_start`（每台一个）：**后台**跑，终端仍可用，可被面板管理

两者同时命中同一台时只走前台那条，不会重复启动。为此进程内改为共用一个 `RuntimeManager`，
否则「后台拉起的实例」和「面板看到的实例」会是两批，面板会显示它未运行、还能再启动一次。

#### 下载与上传进度

面板底部新增进度条，覆盖三处以前完全没有反馈的操作：

- 下载服务端核心（几十 MB，以前只有一个转圈的按钮）
- 下载 / 更新 frpc 二进制
- 上传文件（文件管理器，上限 512 MB）与上传 frpc

下载类的 POST 仍是同步的，进度通过既有的 SSE 通道以新的 `{"type": "job", ...}` 事件推送；
上传进度由浏览器直接汇报（`XMLHttpRequest.upload.onprogress`）。为此给下载引擎加了
`on_progress` 回调，一路从 `download_msl` 透传到 `core`。

顺带修掉一个自己引入的隐患：上传请求体只被读了一部分就失败时，处理器原先会补读**整个**
请求体，而 HTTP/1.1 是长连接、剩下的字节永远不会再来——那条请求线程会一直卡着。现在用
`_BodyStream` 记账，只补读尚未读过的部分（已用「读到一半人为报错」的用例验证连接不再挂起）。

#### 网页面板：实例设置

选中实例后点标题栏的 `⚙`：Minecraft 实例可改 MC 版本、核心 jar、内存、额外 JVM / 服务端参数、进程优先级；
通用实例改启动方式、程序或命令、参数、工作目录；两者都可以填 OpenFrp 隧道绑定（token、隧道 ID、
是否随实例自动开启）。

#### 网页面板：frp 配置

顶栏 `frp` 按钮打开隧道面板，对应 `frp/frpc.toml`：查看 frpc 运行状态与二进制是否就绪、改
`serverAddr` / `serverPort` / `auth.token`、启动与停止 frpc、下载或更新 frpc 二进制、列出与删除隧道、
添加 `tcp` / `udp` / `http` / `https` 隧道（带参数校验）。面板只改这几个键，`frpc.toml` 里其它内容
读进来原样保留。管的是原版 frp；OpenFrp 云端隧道仍走命令行菜单，面板只参与实例绑定那一小步。

### 变更

#### 内部整理

- **两套隧道管理器的 frpc 处理合并**：`frp/` 与 `openfrp/` 各自维护了一份
  「识别平台 / 判断压缩包 / 提取 / 校验可执行文件」，6 个同名函数**逐字比对
  没有一个相同**，重复约 180 行，而且已经跑偏：原版 frp 那份不认 armv7
  （ARM 设备上会去下 amd64 的包），OpenFrp 那份不校验可执行文件（用户传个
  txt 进来也会被当成 frpc 装好、面板显示「已就绪」）。现在统一到
  `frp/frpc_files.py`，下载源、版本查询、进程启停仍各管各的。
  顺带把 OpenFrp 原来的「找不到就整包解压再递归搜索」换成了按 basename
  在压缩包内匹配——不再往用户数据目录里倒一整个第三方包，也不用回头再删。
- **`i18n` 补上缺省回退**：`t()` 查不到时依次退「当前语言 → 中文 → key 本身」。
  `zh.json` 里那个 `_fallback: true` 标记本来就是这意思，但一直没人读它，
  于是 neko 这种没翻全的语言会把 `frp.add_title` 这样的裸 key 直接显示给用户
  （共 58 个代码在用的 key）。现在会回退成中文。
- **`list_servers()` 加解析缓存**：按「文件 mtime + 大小」判断是否还有效，
  并加了 2 秒上限（Windows 文件时间戳粒度约 15ms，紧挨着的两次改写可能拿到
  同一个 mtime，大小又常常一样）。自家写盘在 `save_server_config()` 之后
  显式清缓存，手改文件靠时间戳。100 个实例实测 33ms → 16ms。
  顺带把每次都要 `deepcopy` 整个默认配置换成显式复制那几个可变容器。
- 清掉 5 个从未被调用的模块级函数（OpenFrp 的 `edit_tunnel` /
  `get_tunnels_by_auth` / `get_user_info` / `user_sign`，以及重构后失去引用的
  `paths.is_temp_onefile_dir`），以及若干已经没人用的 import。

- **面板端口配置**：`web.port` 填 `0` 现在表示「随便挑一个空闲端口」，不再被当成没填而回退到 8765
- **Windows 上关掉 `SO_REUSEADDR`**：它允许绑定到已被占用的端口，两个进程分着接同一个端口的
  请求，表现为面板偶发 401 或打不开而日志里一片安静。现在端口冲突会直接报「无法监听」。
  顺带一提，8765 也是 VS Code 的 debugpy 默认端口，调试 Python 时可能撞上
- **网页面板的入口更好找**：主菜单新增 `[8] 网页面板`，双击打包好的 exe 也能直接进面板；
  面板子菜单可改监听地址 / 端口、重新生成访问 token。`python start.py --help` 现在会打印用法
  （以前带上任何参数都会「安静地」打开终端菜单），认不出来的参数会明确报错并以状态码 2 退出；
  端口被占用时给出可读提示而不是抛 traceback，`start.py web` 也会以非 0 退出码反映启动失败
- **打包**：改用 Nuitka standalone 构建，并修复 onefile 模式下的路径定位
- **下载引擎**：新增静默模式（供没有终端的调用方使用）、分片完整性校验
- **i18n**：新增 82 条面板文案（中 / 英 / 俄 / 萌猫语）
- **仓库卫生**：移除一个从早期提交起就被误纳入版本控制的 `__pycache__/*.pyc`

### 修复

#### 搜索与下载

- 修复所有 SpigotMC 插件一律下载失败（调用了一个不存在的函数名，异常被吞掉）
- 修复带空格或中文的关键词搜不出任何结果（关键词未做 URL 转义）
- 修复 Modrinth 插件搜索永远为空（facets 参数不是合法 JSON）
- 修复 CurseForge 模组下载必定失败（CDN 地址拼错），并优先采用官方返回的下载地址
- CurseForge 文件下载现在会带上 API key
- 修复部分 CDN 忽略 Range 时，多线程下载得到「大小正确、内容损坏」的 jar
- 远端项目名里的 `/`、`..`、`:` 等字符不再把文件写到插件 / 模组目录之外

#### 内网穿透

- 启动服务器时不再漏掉原版 frp 隧道进程（原先只清理 OpenFrp）
- 修复 `frpc.toml` 读一次再写回会产出非法配置（`customDomains` 数组、含引号的 token 都会中招），导致 frpc 起不来
- 重复点「启动 frpc」不再留下无法停止的孤儿进程；frpc 启动即退时不再谎报成功
- OpenFrp 创建隧道失败时不再显示成「创建成功」
- Linux / macOS 下载 frpc 后补上可执行位（原先必然 Permission denied）

#### 网页面板

- 修复并发点「启动」可能把同一实例拉起两次（先起的进程会失去引用、无法停止）
- 修复控制台日志每写一行就开关一次文件（繁忙服务器上每秒数十次系统调用）
- 修复断线重连后控制台出现成片重复行
- 修复选中实例后控制台一片空白（建连时回放的历史被直接丢弃）
- 修复 **token 明明是对的却一直提示「访问 token 无效或缺失」**：出示 token 的三条通道
  （请求头、cookie、`?token=`）原来是谁先命中就只看谁，于是浏览器里留着一个旧 token 的
  cookie 时，地址栏里正确的新 token 会被直接无视——现在三条并列，任意一条对就放行，
  并用当前有效的 token 把旧 cookie 覆盖掉
- 修复在菜单里「重新生成访问 token」后，**正在运行的面板仍只认旧 token**、必须重启才生效；
  现在每个请求都现读一次配置，换完立刻生效，菜单也会把新地址打印出来
- 配置读不出来（`config.json` 被删或写坏）时，鉴权退回启动时那个 token 而不是直接放开——
  宁可继续要求旧 token，也不能因为一次读取失败就把面板敞开
- 401 页面补上排查方向（旧 cookie、`127.0.0.1` 与 `localhost` cookie 不通用、刚换过 token），
  面板页面内被踢下线时也显示同样的提示
- 修复新建实例时前端校验提示漏翻：实例名留空会显示英文兜底 `name required`、
  未选核心时提示为空（这两句后端本来就有翻译，只是没下发到前端）
- 修复**实例设置里的内存改完又被悄悄改回去**：`/api/state` 从「启动那一刻」的
  启动计划里回显 `min_mem` / `max_mem`，而实例一旦被启动过，那份快照就一直压着
  磁盘上的新值（通用实例的计划里干脆是 1G/4G 默认值）。于是对话框显示旧内存，
  用户随手保存（哪怕只是想改别的字段）就把自己刚改的数值写回了旧的
- 修复**从面板启动时「进程优先级」从未生效**：启动计划里根本没有这个字段，
  spawn 时写死 `normal`。现在优先级随计划一起传下去，非法值回落 `normal`
- 修复**从面板启动（或随软件启动）时不按设置开启 OpenFrp 隧道**：这段逻辑原来
  只写在终端前台启动里，而设置里那个勾的文案是「启动实例时自动开启隧道」。
  现在终端、面板、开机自动启动三条路走同一个函数
- 修复设置里改 `java_path` 存不进去：接口的字段白名单漏了它（`_PATCH_FIELDS`
  与 `/api/state` 都有，只有写入这一环没有）
- 面板启动失败时不再把「数据目录不可写」误报成「端口可能已被占用，去菜单里换一个」：
  这两件事原来共用同一个 `except OSError`，而照着换端口永远好不了。现在数据目录的
  准备单独一步，报的是真实原因

#### 配置与实例

- 修复修改一个实例的隧道绑定会污染默认值、进而影响之后加载的其它实例
- 修复配置里把「额外 JVM 参数」写成 `null` 或字符串时的崩溃与参数被拆成单字符
- 修复设置了 `MSTL_DATA_DIR` 时，开机自启指向一个不存在的 exe
- 修复**「下载线程数」是个假设置**：`multithreaded_download` 的默认值在函数定义时
  就固定成 16 了，之后再调 `set_download_threads` 也改不动，而且启动时从不应用
  `config.json` 里存的值——菜单里设置会提示「已保存」，实际永远 16 线程
- 实例没写 `java_path` 时，面板这条路现在也会认 `config.json` 里的全局值，
  与终端前台启动保持一致（原来只有终端认）
- 数据目录不可写时（例如把 exe 解压到 `Program Files`）给出明确提示并等一下再退出，
  而不是抛一段 traceback——双击启动的用户只会看到窗口一闪而过
- 启动阶段的致命错误现在真的会以退出码 1 结束（原来 `main()` 的返回值被丢掉，进程
  照样退出 0，脚本/计划任务那边看不出其实没起来）

#### 更新检查

- 修复**每个正式发布版的「检查更新」都是哑的**：CI 用的 Windows PowerShell 5.1
  里 `Set-Content -Encoding UTF8` 写出的 `version.json` **带 BOM**，而 Python 侧用
  `encoding="utf-8"` 打开会抛 `JSONDecodeError`，又被那个 `except` 吞掉 → 读不到本地
  版本号 → 永远显示「已是最新版」，界面上一点报错都没有。现在读取用 `utf-8-sig`
  （带不带 BOM 都行），CI 侧改成写不带 BOM 的 UTF-8 并加了一条断言防它回来
- onefile 模式下会连 exe 旁边一起找 `version.json`（`bundled_dir()` 在 onefile 下
  是 %TEMP% 解压目录，而该文件并不在 `--include-data-dir` 里）

#### 其它

- 中文 Windows 的语言检测不再依赖已废弃且排定移除的 API
- 版本号 `1.0` 不再被判定比 `1.0.0` 旧，反复提示有新版本
- **搜索/下载时某个源出错不再完全静默**：`fetch_json` 已经把网络与解析失败降级成
  `None`，能冒到外层的那基本就是代码自己的 bug。原来一律 `pass`，「这个源坏了」与
  「真的没搜到」长得一模一样——SpigotMC 插件下载那个 `NameError` 就是这么藏了很久的
- 去掉两处 `os.getcwd()` 兜底（`java_tools.resolve_java`、菜单里的
  `_find_project_dir`）：打包版从快捷方式启动时当前目录是随机的，
  会把 Java 列表之类的数据读到别处去

#### 打包版（Nuitka）

Nuitka 的 `--standalone` **不设置 `sys.frozen`**（实测），而项目里所有「是不是打包版」
的判断都写成 `if sys.frozen`，于是打包版里这些分支一次都没走到过：

- 修复打包版**完全无视 `MSTL_DATA_DIR`**：把数据目录指到别处也没用，`config.json`
  与 `servers/` 一律落在 exe 旁边。现在这个开关与是否打包无关，优先级最高
- 修复打包版**开机自启写出一条永远跑不起来的命令**：原来会写
  `"<目录>\python.exe" "<目录>\start.py"`，而 dist 里既没有 `python.exe` 也没有
  `start.py`（`sys.executable` 在 standalone 下指向的就是那个不存在的 `python.exe`）。
  现在改为取 Nuitka 记下的原始 exe 路径，写出的就是真正的 `MSTL.exe`；
  万一取不到可靠路径，会明确返回失败而不是往注册表里塞一条坏命令
- 新增 `paths.is_packaged()` / `paths.exe_path()` 统一这两个判断，别再各自
  `getattr(sys, "frozen", False)`
- CI 的「校验打包资源」补上 `locales\ru.json` 与 `locales\neko.json`：运行时真的会读
  它们，缺了就是俄语/猫语用户看到一串 i18n key（原来只查 zh 与 en）

#### 代码审计：静默失败、并发与安全边界

对全部 48 个 Python 文件（11249 行）与网页面板做了一次逐行审计，结果与逐条证据见
[`docs/advanced/code-review.md`](docs/advanced/code-review.md)（57 条）。这一批修完了全部 57 条，
下面按「用户实际会撞上什么」归类。

**下载与安装（以前会安静地给你一个坏文件）**

- 修复**截断的下载被当成成功**：`download_file_with_progress` 读了 `Content-Length`
  却从不比对实际字节数，服务器声明长度后干净地提前收尾时 `http.client` 把短 body
  当成正常 EOF、不抛任何异常，于是残缺的 jar 一路装到位，直到服务端起不来才暴露。
  现在读完先比字节数，不匹配就删文件并报 `download.truncated`
- 修复**sha256 只打印不校验**：MSL 的下载接口是唯一提供完整性信息的地方，这个值
  以前只拿来显示。现在真的会校验，不匹配就删文件
- 修复**单个分片抖动就丢掉整个已下载文件**：多线程下载以前一有分片失败就删掉整个
  文件、退回单线程从头再来，几十上百 MB 白下。现在先单独重试失败的分片（最多 3 次），
  都不行才回退
- 修复分片重试时进度重复累加（进度条冲到 100% 以上再跳回 0），以及
  `on_progress` 与进度条可能算出大于 1 的比例
- 修复**`data: null` 毒化当天缓存**：MSL 偶尔回 `{"code":200,"data":null}`，旧版本把
  这个 `null` 连当天日期一起写进缓存，于是接下来一整天每次调用都命中空缓存、连网络
  请求都不发，服务端类型/版本列表全空，而且已经落盘、重启进程也救不回来
- 修复 MSL 缓存**非原子写**（写到一半崩掉留下半截 JSON，之后当天所有接口回退成「没有数据」）
- 修复**带凭据的请求跟着重定向跨站**：`x-api-key` / `Authorization` / `Cookie` 现在
  只在同站重定向时保留（CurseForge 的 `x-api-key` 就是这么漏出去的）
- 修复**下载线程数无上限**：4 GiB 的文件按 1 MiB 分片能算出 4096 个线程，
  `ThreadPoolExecutor` 直接 `can't start new thread`。现在上限 64
- 修复 JDK 解压残留（下载失败与成功两条路径都漏下归档文件）

**内网穿透**

- 修复**`frpc.toml` 里的行尾注释会把配置弄坏**：`serverPort = 7000  # the frps port`
  被解析成字符串 `"7000  # the frps port"`，保存时再被加引号写回去，frpc 无法把字符串
  解码进 int 字段、直接退出——而这份配置正是本工具刚弄坏的
- 修复**多行数组被整个丢掉**：官方文档风格的多行 `customDomains` 只剩首行的 `[`，
  被当成字面字符串存下来，续行直接跳过，保存后写成 `customDomains = "["`
- 修复单行数组里 `\"` 转义引号被误判为闭合，元素被逗号切碎
- 修复 `frpc.toml` **权限 0644**（里面有 `auth.token`，同一台机器上任何本地用户都能
  读到），改为 0600 且原子写
- 修复**上一次遗留的 frpc 占着远端端口**时误报「请检查 frpc.toml」（配置其实完全正确）：
  启动前先按可执行文件路径找到孤儿进程并停掉
- 修复 `quiet=True` 时进度条照画（网页面板调用时那些 `\r` 直接糊进服务端日志）
- 修复 OpenFrp 的 **GitHub 兜底是个死链**（`OpenFrp/frpc-release` 已不存在，
  实测 404），手动下载地址改为 `console.openfrp.net/download`
- 修复 OpenFrp 在 Linux 上**接受 HTML 错误页当 frpc 二进制**（原来只对 `.zip` 做魔数校验）
- `启动frpc.bat` 不再 `taskkill /f /im frpc.exe` **杀掉机器上所有 frpc**（包括用户为
  别的隧道起的、以及面板自己管着的那份），只停路径匹配的那个；token 也补上了引号

**网页面板**

- 修复**长连接上的请求错位**：`_body_read` 是类属性、按 handler 实例跨请求累积，
  `_drain()` 拿它算剩余字节，于是第二个请求起要么少读（残留字节把下一个请求顶歪）
  要么多读（阻塞在下一个请求上，连接卡死）。现在每个请求归零
- 修复 **401 不排空请求体**（换 token、cookie 过期、`localhost` 与 `127.0.0.1`
  主机名不一致时会反复触发，每被拒一次那条连接就错位一次）
- 修复 `_body()` **按 `Content-Length` 无上限读进内存**（客户端声称 9999999999
  就足以吃掉几个 G），JSON 接口现在限 8 MB
- 修复 `Content-Disposition` 的**响应头注入**：文件名在 Linux 上可含 `\r`/`\n`，
  现在统一把控制字符与引号替换掉
- 修复 **401 时前端把 `document.body.innerHTML` 整个换掉**，连带拆掉 body 内所有
  按钮的监听，之后 `applyStrings()` / `refresh()` 拿到 null 抛 `TypeError` 又被
  各处 `.catch(() => {})` 吞掉，页面成死胡同。现在改成覆盖一层提示层
- 修复**非 JSON 错误体被当 JSON 解析**：`createWithJar()` 遇 401 时 body 是 HTML
  鉴权页，用户看到的是 `SyntaxError: Unexpected token '<'`，而实例已经建好却缺 jar，
  重试只会撞「实例已存在」
- 修复 `refresh()` / `loadFiles()` **无在途保护**：15 秒轮询、切实例、动作回调与 SSE
  并发改同一份状态，旧响应乱序返回会覆盖新状态（刚启动的服务器可能回退显示成
  stopped 达 15 秒，`filesPath` 与实际显示目录不一致时上移/重命名/删除会作用到错路径）
- 修复 cookie 没存下时**面板永久卡在琥珀色「连接中」**（`EventSource` 不带 token，
  服务端只回 401 的 HTML 页，`onmessage` 永不触发而 `readyState` 停在 0）
- 修复增删 frp 代理会**静默清空未保存的服务器地址/端口/token**（触发点击的按钮
  自己持有焦点，`activeElement` 守卫拦不住）
- 修复文件夹上传**进度条永远到不了 100%**（只数成功数，失败一个就停在 90% 而标签
  已显示 10/10）
- 修复 `/api/stream` 未包 `_guard`（订阅失败时只剩往 SSE 流里塞状态行）、
  `/api/stream` 先发响应头再订阅（订阅抛异常时一个字节都送不出去）
- 修复实例设置里三个字段标签**全渲染成小节标题「Minecraft 设置」**（其中一个是
  死三元 `x ? "Jar" : "Jar"`）
- 每次请求重读 78 KB 的 `index.html` 改为按 `(mtime, size)` 缓存；文件管理器的每个
  操作不再全量扫描 `servers/`；`start_background` 的「检查是否起过」与「记下句柄」
  之间补上锁（原来是先起线程后赋值，空窗里的 `atexit` 关不掉面板、并发第二次调用
  会再绑一次同一端口）

**实例、文件与进程**

- 修复**锁序反转死锁**（`RuntimeManager.start()` 持 manager 锁求 runtime 锁，
  `ServerRuntime.stop()` 持 runtime 锁经事件回调求 manager 锁）：面板用的是
  `ThreadingHTTPServer` + `daemon_threads`，一次「启动」撞上一次「停止」就够把两个
  请求线程一起挂死。规则固化为「绝不在持 `self._lock` 时发事件」，并用
  `threading.Barrier` 写了一个确定性复现 ABBA 的探针
- 修复**启用/禁用插件会静默覆盖同名文件**：`Foo.jar` 与 `Foo.jar.disabled` 可以
  同时存在，POSIX 上 `os.rename` 静默覆盖（禁用插件反而把启用的 jar 抹掉），
  Windows 上抛 `FileExistsError` 却被翻译成「切换失败」——错误信息指着一个根本不是
  原因的原因
- 修复 Windows 制作的 zip（目录项写成 `sub\`）解压时报**「压缩包损坏」**：`ZipInfo.is_dir()`
  只认结尾的 `/`，`sub\` 被当成零字节普通文件建出来，紧接着 `sub\file.txt` 建不动
  父目录，`FileExistsError` 逃出 `_write` 被翻译成 `web.error.bad_archive`
- 修复导入整合包**没有任何压缩炸弹闸门**（`core/transfer.py` 的 `_safe_extract` 从不
  引用 `core/archive.py` 已有的 `MAX_MEMBERS` / `MAX_TOTAL_BYTES`），并补上拒绝 zip 内
  符号链接（越界检查只看成员名，先建一个指向 `/etc` 的链再写 `link/passwd` 即可逃逸）；
  解压改为流式读、按真实写入字节数计数（成员头声明的 `file_size` 可以撒谎）
- 修复导入临时目录**固定为 `.import_temp`**：开头先 `rmtree` 再 `makedirs`，两个并发
  导入会互相删掉对方正在解压的东西（`_IMPORT_PLANS_LOCK` 只保护 plan 字典）。现在
  每次导入用 `tempfile.mkdtemp`，超过 24 小时的陈旧目录顺手清掉
- 修复导出**就地写 zip**：中途抛异常时 `ZipFile.__exit__` 仍会写出中央目录，留下一个
  「打得开、内容却缺了一半」的备份——用户看到文件在那儿，会以为备份是好的
- 修复 `save_server_config` **非原子写**（写一半失败留下残缺 JSON，`load_server_config`
  解析失败会静默回退默认配置，实例在 UI 上「消失」成空白配置）
- 修复 `auto_start` 的 `bool("false") is True`（字符串 `"false"` 会被当成开启，
  实例意外自启），改为统一的 `_as_bool()` 解析
- 修复 `core/files.py` 的**固定临时文件名**（`.mstl-tmp` / `.mstl-upload`）在并发写
  同一文件时互相覆盖，改用 `tempfile.mkstemp`
- 修复 Windows/macOS 上**无法仅改文件名大小写**（大小写不敏感的文件系统里
  `os.path.exists("Foo.txt")` 在只有 `foo.txt` 时也为真），改用 `os.path.samefile` 区分
- 修复 spawn 失败时**泄漏日志句柄**（`_pump` 没启动，`_close_log()` 的唯一调用点永远
  到不了；Windows 上就是实例目录「文件被占用」删不掉）

**菜单与命令行**

- 修复 frp 端口输入非数字时**一路冒到 `main()` 打一屏 traceback 把启动器杀掉**，
  刚填的内容全丢（姊妹函数反而有守卫）
- 修复 `menu.py` **全文件零个 `KeyboardInterrupt` 处理器**：菜单树里任何提示符上按
  Ctrl+C 都是 traceback，现在统一当成「取消当前操作，退回主菜单」
- 修复语言菜单**越界无 else**（输入 9 也会落到 `save_config` + 提示「已保存」）
- 修复 OpenFrp 节点选择**只显示前 20 个却按全量校验**（输入 25 会静默选中一个用户
  从未见过的节点并据此建隧道）
- 修复导入 jar 选择器**没有返回出口**（Ctrl+C 会跳过 `abort_import(plan)`，
  留下解压临时目录）
- 修复 Java 版本解析崩溃与误判：`_version_sort_key` 把 `int` 与 `str` 直接比较会
  `TypeError`；`_java_major_version("21-ea")` 返回 0（`-ea` 版本被当成未知 Java，
  选不到任何实例）
- 修复 `java_tools.py` 的 **tar 解压路径穿越**（`extractall` 未校验成员名，
  符号链接与硬链接也没查 `linkname`），并让 JDK 残片在两条路径上都被清掉
- 修复 `server_launcher.py` 的 stdout **缺 `errors=`**：Windows + JDK ≤ 17 下 Java
  按 ANSI 代码页（中文系统 cp936）写管道，插件里任何中文即非法 UTF-8，
  `UnicodeDecodeError` 直达 `main()`；且 `_sigint_handler` 的 finally **从不终止子进程**，
  Java 成孤儿占着世界目录和 `session.lock`。现在按 `errors="replace"` 解码，
  Ctrl+C 先写 `stop` 走优雅停止（再按一次才强杀）
- 修复 `clear.py` 在旧版 Windows 控制台上把 ANSI 转义序列**原样打出来**（没清屏还
  多一行乱码），且不 flush 导致清屏压在缓冲区里
- 修复 `.gitignore` 未覆盖 Linux/macOS 产物与本次新增的原子写中间文件

### 升级提示

- `config.json` 与 `.server.json` 均向后兼容，缺失字段会自动补默认值，直接覆盖安装即可
- 升级后首次运行 `python start.py web` 会生成 `web.token` 并写入 `config.json`
- **打包版用户**：如果你曾在打包版里开过「开机自启」，注册表里那条命令是坏的（指向
  不存在的 `python.exe`），升级后设置页会显示成「已关闭」。重新开一次即可写对
- 面板默认只监听 `127.0.0.1`。**它能在实例目录里任意读写文件，等于把实例目录交给浏览器**——
  要经 frp 穿到公网，请务必在前面加一层反向代理与 HTTPS
- 文件管理器的边界：单文件编辑上限 2 MB，单次上传上限 512 MB，
  一次只操作一个文件（没有批量选择，也不能整体上传目录）
- 通用实例可以配置成执行任意命令，这是功能本身；它的信任边界和「面板能发控制台指令」
  「能改 `.server.json`」是同一条——拿到面板 token 的人本来就能在这台机器上跑东西。

[1.1.37]: https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher/compare/v1.1.36...v1.1.37
